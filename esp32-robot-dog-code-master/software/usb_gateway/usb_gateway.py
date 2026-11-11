"""USB gateway: serves the robot control web page and relays it to the ESP32.

    python usb_gateway.py --port COM14
    then open http://127.0.0.1:8787

Works with tools/servoInitPose/servoInitPose.ino (CAPS ... LEASE1). The page and
JSON API are the same as the ESP32 Wi-Fi web server, so one page serves both.

Safety model (enforced by the firmware, mirrored here):
- Motion requires an explicit ARM; nothing re-arms after reconnect or reboot.
- The armed browser session must send a heartbeat; the firmware stops and
  disarms if none arrives for 1 s (browser closed, gateway crashed, USB lost).
- STOP is accepted from any session at any time.
- The serial port is opened with DTR/RTS deasserted (no ESP32 reset) and is
  closed only after STOP and a settled, saved pose (no servo jump on reset).
- Listens on loopback only: there is no login, so it must not be exposed.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PAGE_PATH = Path(__file__).resolve().parents[1] / "robot_control_web" / "index.html"
SESSION_RE = re.compile(r"^[A-Za-z0-9]{16,32}$")
DIRECTIONS = {"FORWARD", "BACKWARD", "LEFT", "RIGHT", "STOP"}
MAX_BODY_BYTES = 1024
RECONNECT_INTERVAL_S = 3.0
POLL_HANDSHAKE_S = 4.0  # shorter than at startup: a page request is waiting
SENSOR_REFRESH_S = 0.5  # IMU/LEVEL lines are re-read at most this often (serial load)


def open_serial_without_reset(port: str, baud: int):
    import serial  # imported lazily so tests run without pyserial

    handle = serial.Serial()
    handle.port = port
    handle.baudrate = baud
    handle.timeout = 0.25
    handle.write_timeout = 2
    handle.dsrdtr = False
    handle.rtscts = False
    handle.dtr = False
    handle.rts = False
    handle.open()
    return handle


def _fields(line: str) -> dict:
    return dict(item.split("=", 1) for item in line.split()[1:] if "=" in item)


def _num(text: str | None) -> float | None:
    """Firmware writes "-" for unknown values; those become None (JSON null)."""
    try:
        return float(text) if text not in (None, "-") else None
    except ValueError:
        return None


def parse_imu_line(line: str) -> dict:
    """``IMU ready=1 chip=.. who=0x75 ...`` -> same shape as the Wi-Fi ``imu`` object."""
    f = _fields(line)
    return {
        "ready": f.get("ready") == "1",
        "chip": f.get("chip", ""),
        "whoAmI": f.get("who", ""),
        "address": f.get("addr", ""),
        "valid": f.get("valid") == "1",
        "ageMs": int(f.get("age", "-1")),
        "accG": {"x": _num(f.get("ax")), "y": _num(f.get("ay")), "z": _num(f.get("az"))},
        "axisDeg": {"x": _num(f.get("x")), "y": _num(f.get("y"))},
        "zeroed": f.get("zeroed") == "1",
        "rollMap": f.get("rollmap", "NONE"),
        "pitchMap": f.get("pitchmap", "NONE"),
        "rollDeg": _num(f.get("roll")),
        "pitchDeg": _num(f.get("pitch")),
    }


def parse_level_line(line: str) -> dict:
    """``LEVEL enabled=0 active=0 ...`` -> same shape as the Wi-Fi ``level`` object."""
    f = _fields(line)
    offsets = [_num(v) for v in f.get("offsets", "").split("/")]
    if len(offsets) != 4:
        offsets = [None] * 4
    return {
        "enabled": f.get("enabled") == "1",
        "active": f.get("active") == "1",
        "sensorUsable": f.get("sensor") == "1",
        "rollCompDeg": _num(f.get("rollcomp")),
        "pitchCompDeg": _num(f.get("pitchcomp")),
        "gammaOffsetDeg": dict(zip(("LF", "RF", "LH", "RH"), offsets)),
        "maxCompDeg": _num(f.get("max")),
        "tiltLimitDeg": _num(f.get("limit")),
    }


class LinkError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RobotLink:
    """Serial link to the firmware.

    Locks: ``_lock`` serialises request/response transactions; ``_write_lock``
    only guards raw writes, so STOP can be written immediately even while
    another thread waits for a reply; ``_connect_lock`` lets one thread open
    and handshake a new port without holding ``_lock`` (other requests fail
    fast with ``usb_connecting`` instead of waiting up to 12 s).
    """

    def __init__(self, port: str, baud: int = 115200, opener=open_serial_without_reset,
                 clock=time.monotonic, sleep=time.sleep):
        self.port = port
        self.baud = baud
        self._opener = opener
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._stops_pending = 0
        self.caps: set[str] = set()
        self._sensor_cache: tuple[float, dict | None, dict | None] | None = None
        self._serial = None
        self._last_attempt = None
        self.owner_session: str | None = None
        self.wifi: dict | None = None
        self.last_error = "not_connected"

    # ---- serial plumbing -------------------------------------------------
    @property
    def connected(self) -> bool:
        handle = self._serial
        return handle is not None and handle.is_open

    @staticmethod
    def _readline(handle) -> str:
        return handle.readline().decode("ascii", errors="replace").strip()

    def _exchange(self, handle, command: str, expect: str, exact: bool, timeout_s: float) -> str:
        """Send one command and wait for its reply line.

        Stale input is discarded first so an earlier late reply is not taken as
        this one; ``exact`` replies must match the whole line.
        """
        with self._write_lock:
            handle.reset_input_buffer()
            handle.write((command + "\r\n").encode("ascii"))
        deadline = self._clock() + timeout_s
        while self._clock() < deadline:
            line = self._readline(handle)
            if not line or line.startswith("EVENT "):
                continue
            if line.startswith("ERR"):
                raise LinkError(line.split(maxsplit=1)[1] if " " in line else line)
            if (line == expect) if exact else line.startswith(expect):
                return line
        raise LinkError("no_response")

    def _transact(self, command: str, expect: str, exact: bool = False, timeout_s: float = 2.0) -> str:
        with self._lock:
            if not self.connected:
                raise LinkError("usb_disconnected")
            try:
                return self._exchange(self._serial, command, expect, exact, timeout_s)
            except OSError as exc:
                self._drop(f"serial_error: {exc}")
                raise LinkError("usb_disconnected") from exc

    def _drop(self, reason: str) -> None:
        with self._lock:
            self.last_error = reason
            self.owner_session = None
            if self._serial is not None:
                try:
                    self._serial.close()
                except OSError:
                    pass
            self._serial = None

    def _handshake(self, handle, timeout_s: float) -> dict | None:
        deadline = self._clock() + timeout_s
        next_hello = 0.0
        while True:
            if self._clock() >= deadline:
                raise LinkError("no_ready_from_firmware")
            if self._clock() >= next_hello:
                handle.write(b"HELLO\r\n")
                next_hello = self._clock() + 1
            if self._readline(handle) == "READY SERVO_POSE_V1":
                break
        caps = self._exchange(handle, "CAPS", "CAPS ", False, 2.0).split()
        if "WALK4" not in caps or "LEASE1" not in caps:
            raise LinkError("firmware_without_lease_support")
        self.caps = set(caps)
        if "WIFI1" not in caps:
            return None
        info = self._exchange(handle, "WIFI", "WIFI ", False, 2.0)
        fields = dict(item.split("=", 1) for item in info.split()[1:] if "=" in item)
        if "ssid" not in fields:
            return None
        return {"ssid": fields["ssid"], "password": fields.get("pass", "")}

    def connect(self, handshake_s: float = 12.0) -> None:
        """Open and handshake without holding ``_lock`` (STOP and status stay responsive)."""
        if not self._connect_lock.acquire(blocking=False):
            raise LinkError("usb_connecting")
        try:
            if self.connected:
                return
            self._last_attempt = self._clock()
            try:
                handle = self._opener(self.port, self.baud)
            except OSError as exc:
                self.last_error = f"open_failed: {exc}"
                raise LinkError("usb_disconnected") from exc
            try:
                wifi = self._handshake(handle, handshake_s)
            except (LinkError, OSError) as exc:
                self.last_error = getattr(exc, "code", None) or str(exc)
                try:
                    handle.close()
                except OSError:
                    pass
                raise LinkError("usb_disconnected") from exc
            with self._lock:
                self._serial = handle
                self.wifi = wifi
                self.owner_session = None
                self.last_error = ""
        finally:
            self._connect_lock.release()

    def _ensure_connected(self) -> None:
        """Reconnect from a request thread. Never call this with ``_lock`` held."""
        if self.connected:
            return
        if self._last_attempt is not None and self._clock() - self._last_attempt < RECONNECT_INTERVAL_S:
            raise LinkError("usb_disconnected")
        self.connect(handshake_s=POLL_HANDSHAKE_S)

    def _sensor_status(self) -> tuple[dict | None, dict | None]:
        """IMU and LEVEL objects, re-read at most every SENSOR_REFRESH_S. Caller holds _lock."""
        now = self._clock()
        cached = self._sensor_cache
        if cached is not None and now - cached[0] < SENSOR_REFRESH_S:
            return cached[1], cached[2]
        imu = parse_imu_line(self._transact("IMU", "IMU ")) if "IMU1" in self.caps else None
        level = parse_level_line(self._transact("LEVEL", "LEVEL ")) if "LEVEL1" in self.caps else None
        self._sensor_cache = (now, imu, level)
        return imu, level

    def _write_stop_now(self) -> None:
        """Write STOP without waiting for an in-flight transaction to finish."""
        handle = self._serial
        if handle is None or not handle.is_open:
            raise LinkError("usb_disconnected")
        try:
            with self._write_lock:
                handle.write(b"WALK STOP\r\n")
        except OSError as exc:
            self._drop(f"serial_error: {exc}")
            raise LinkError("usb_disconnected") from exc

    # ---- API ---------------------------------------------------------------
    def status(self, session: str | None) -> dict:
        """``connected`` is the serial port state. When ``error`` is present the
        robot state is unknown and no state fields are included."""
        try:
            self._ensure_connected()
            with self._lock:
                state_line = self._transact("STATE", "STATE ")
                joints_line = self._transact("STATUS", "STATUS ")
                state = dict(item.split("=", 1) for item in state_line.split()[1:] if "=" in item)
                angles = {}
                for item in joints_line.split()[1:]:
                    leg, _, values = item.partition("=")
                    angles[leg] = [int(v) for v in values.split("/")]
                imu, level = self._sensor_status()
                uptime = int(state.get("uptime", "0") or 0)
                armed = state.get("armed") == "1"
                if not armed or state.get("ctrl") != "SERIAL":
                    self.owner_session = None
                owner = self.owner_session
        except LinkError as exc:
            return {"link": "usb", "connected": self.connected, "serialPort": self.port,
                    "error": exc.code if self.connected or exc.code == "usb_connecting"
                    else (self.last_error or exc.code)}
        except ValueError:
            return {"link": "usb", "connected": self.connected, "serialPort": self.port,
                    "error": "bad_status_reply"}
        return {
            "link": "usb",
            "connected": True,
            "serialPort": self.port,
            "armed": armed,
            "controller": state.get("ctrl", "NONE"),
            "youControl": bool(session) and armed and state.get("ctrl") == "SERIAL"
            and session == owner,
            "walk": state.get("walk", "STOPPED"),
            "homing": state.get("homing") == "1",
            "settled": state.get("settled") == "1",
            "resetReason": state.get("reset", ""),
            "lastStop": state.get("stop", ""),
            "uptimeMs": uptime,
            "leaseTimeoutMs": 1000,
            "angles": angles,
            "wifi": self.wifi,
            "imu": imu,
            "level": level,
        }

    def command(self, cmd: str, session: str | None, direction: str | None = None,
                level_on: str | None = None) -> dict:
        if cmd == "STOP":
            # Written at once (no waiting behind a poll), then repeated under the
            # lock to get a confirmed reply. STOP is idempotent on the firmware.
            # While a STOP is pending, ARM/HB/WALK are refused so a request that
            # wins the lock race cannot re-arm or walk before the confirmation.
            with self._write_lock:
                self._stops_pending += 1
            try:
                self._write_stop_now()
                with self._lock:
                    self._transact("WALK STOP", "OK WALK STOPPED", exact=True)
                    self.owner_session = None
            finally:
                with self._write_lock:
                    self._stops_pending -= 1
            return {"ok": True}
        if not session or not SESSION_RE.match(session):
            raise LinkError("invalid_session")
        if cmd == "ARM":
            self._ensure_connected()
        with self._lock:
            if self._stops_pending:
                raise LinkError("stop_in_progress")
            if cmd == "ARM":
                if "armed=1" not in self._transact("STATE", "STATE ").split():
                    self.owner_session = None  # lease expired or stopped elsewhere
                if self.owner_session not in (None, session):
                    raise LinkError("controlled_by_other")
                self._transact("ARM", "OK ARM", exact=True)
                self.owner_session = session
                return {"ok": True}
            if cmd == "LEVEL":
                if level_on not in ("1", "0"):
                    raise LinkError("invalid_level")
                if self.owner_session not in (None, session):
                    raise LinkError("controlled_by_other")
                if level_on == "1" and self.owner_session != session:
                    raise LinkError("not_armed")  # only the armed page may switch leveling on
                mode = "ON" if level_on == "1" else "OFF"
                self._transact(f"LEVEL {mode}", f"OK LEVEL {mode}", exact=True)
                self._sensor_cache = None
                return {"ok": True}
            if session != self.owner_session:
                raise LinkError("not_armed")
            if cmd == "HB":
                self._transact("HB", "OK HB", exact=True)
            elif cmd == "DISARM":
                self._transact("DISARM", "OK DISARM", exact=True)
                self.owner_session = None
            elif cmd == "WALK":
                if direction not in DIRECTIONS:
                    raise LinkError("invalid_direction")
                self._transact(f"WALKHB {direction}", f"OK WALKHB {direction}", exact=True)
            else:
                raise LinkError("unknown_command")
            return {"ok": True}

    def shutdown(self, settle_timeout_s: float = 6.0) -> bool:
        """STOP, wait for a settled+saved pose, then close. Returns settle success."""
        with self._lock:
            if not self.connected:
                return False
            settled = False
            try:
                self._transact("WALK STOP", "OK WALK STOPPED", exact=True)
                deadline = self._clock() + settle_timeout_s
                while self._clock() < deadline:
                    if self._transact("SETTLED", "SETTLED ") == "SETTLED 1":
                        settled = True
                        break
                    self._sleep(0.2)
            except LinkError:
                pass
            self._drop("closed")
            return settled


ERROR_STATUS = {"usb_disconnected": 503, "usb_connecting": 503, "no_response": 504, "invalid_session": 400,
                "invalid_direction": 400, "invalid_level": 400, "unknown_command": 400}


def make_handler(link: RobotLink, allowed_hosts: set[str], page_path: Path = PAGE_PATH):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RobotDogUsbGateway/1"

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: dict) -> None:
            self._send(status, json.dumps(value).encode("utf-8"), "application/json")

        def _host_ok(self) -> bool:
            # Blocks DNS-rebinding pages from reaching the gateway.
            if self.headers.get("Host", "") in allowed_hosts:
                return True
            self._json(403, {"ok": False, "error": "bad_host"})
            return False

        def do_GET(self):
            if not self._host_ok():
                return
            url = urlsplit(self.path)
            if url.path in ("/", "/index.html"):
                self._send(200, page_path.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/status":
                session = parse_qs(url.query).get("session", [None])[0]
                self._json(200, link.status(session))
            else:
                self._json(404, {"ok": False, "error": "not_found"})

        def do_POST(self):
            if not self._host_ok():
                return
            if urlsplit(self.path).path != "/api/command":
                self._json(404, {"ok": False, "error": "not_found"})
                return
            if self.headers.get("X-Robot-Control") != "1":
                self._json(403, {"ok": False, "error": "missing_control_header"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                self._json(413, {"ok": False, "error": "bad_body_size"})
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
            field = lambda name: form.get(name, [None])[0]  # noqa: E731
            try:
                result = link.command(field("cmd") or "", field("session"), field("dir"), field("on"))
            except LinkError as exc:
                self._json(ERROR_STATUS.get(exc.code, 409), {"ok": False, "error": exc.code})
                return
            self._json(200, result)

    return Handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Robot dog USB web gateway")
    parser.add_argument("--port", default="COM14", help="ESP32 serial port (default COM14)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--http-port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1", help="IPv4 loopback address to listen on (127.x.x.x)")
    args = parser.parse_args(argv)

    try:
        host_ip = ipaddress.ip_address(args.host)
    except ValueError:
        host_ip = None
    if host_ip is None or host_ip.version != 4 or not host_ip.is_loopback:
        print("This gateway has no login; it only listens on an IPv4 loopback address (127.x.x.x).",
              file=sys.stderr)
        return 2

    link = RobotLink(args.port, args.baud)
    try:
        link.connect()
        print(f"Connected to ESP32 on {args.port}.")
        if link.wifi:
            print(f"ESP32 Wi-Fi: SSID {link.wifi['ssid']} / password {link.wifi['password']} / http://192.168.4.1")
    except LinkError:
        print(f"ESP32 not ready on {args.port} ({link.last_error}); will retry when the page polls.")

    allowed = {f"{args.host}:{args.http_port}", f"localhost:{args.http_port}"}
    server = ThreadingHTTPServer((args.host, args.http_port), make_handler(link, allowed))
    print(f"Open http://{args.host}:{args.http_port}  (Ctrl+C to stop; robot stops before exit)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        settled = link.shutdown()
        print("Stopped. Pose settled and saved." if settled else "Stopped. Settled pose not confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
