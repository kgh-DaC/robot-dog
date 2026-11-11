import http.client
import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "esp32-robot-dog-code-master" / "software" / "usb_gateway"))

from usb_gateway import LinkError, RobotLink, make_handler  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

OWNER = "a" * 32
OTHER = "b" * 32


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeFirmware:
    """Model of servoInitPose.ino's serial protocol: arm, 1 s lease, single
    controller (SERIAL/WIFI), homing, legacy WALK under the same lease."""

    LEASE_S = 1.0

    def __init__(self, clock=None):
        self.clock = clock or FakeClock()
        self.is_open = True
        self.armed = False
        self.ctrl = "NONE"
        self.walk = "STOPPED"
        self.homing = False
        self.level = False
        self.stop_reason = "boot"
        self.lease_deadline = 0.0
        self.settled_after = 0
        self.state_override = None
        self.silent = False
        self.log = []
        self._out = []

    # serial.Serial-like interface
    def write(self, data):
        line = data.decode("ascii").strip()
        self.log.append(line)
        if not self.silent:
            self._out.extend(self._handle(line))

    def readline(self):
        if self._out:
            return (self._out.pop(0) + "\r\n").encode("ascii")
        self.clock.t += 0.25  # serial read timeout elapses
        return b""

    def reset_input_buffer(self):
        self._out.clear()

    def close(self):
        self.log.append("<close>")
        self.is_open = False

    # firmware model
    def tick(self):
        if self.armed and self.clock() >= self.lease_deadline:
            self._stop("heartbeat_timeout")

    def _stop(self, reason):
        self.armed, self.ctrl, self.walk, self.stop_reason = False, "NONE", "STOPPED", reason
        self.level = False  # firmware: leveling off after every STOP/DISARM

    def _arm(self, source):
        if self.homing:
            return "homing_in_progress"
        if self.armed and self.ctrl != source:
            return "controlled_by_other"
        self.armed, self.ctrl = True, source
        self.lease_deadline = self.clock() + self.LEASE_S
        return None

    def _handle(self, line):
        self.tick()
        parts = line.split()
        cmd = parts[0]
        if cmd == "HELLO":
            return ["READY SERVO_POSE_V1"]
        if cmd == "CAPS":
            return ["CAPS SERVO_POSE_V1 WALK4 LEASE1 WIFI1 IMU1 LEVEL1"]
        if cmd == "IMU":
            return ["IMU ready=1 chip=MPU_COMPATIBLE who=0x75 addr=0x69 valid=1 age=12 ax=0.04 ay=-0.01 "
                    "az=0.99 x=2.50 y=-1.25 zeroed=1 rollmap=+X pitchmap=-Y roll=2.50 pitch=1.25"]
        if cmd == "LEVEL":
            if len(parts) == 1:
                return [f"LEVEL enabled={int(self.level)} active={int(self.level and self.armed)} sensor=1 "
                        "rollcomp=1.50 pitchcomp=-0.50 offsets=-2.00/1.00/-1.00/2.00 max=6.0 limit=20.0"]
            if self.armed and self.ctrl != "SERIAL":
                return ["ERR controlled_by_other"]
            if parts[1] == "ON" and not self.armed:
                return ["ERR not_armed"]
            self.level = parts[1] == "ON"
            return [f"OK LEVEL {parts[1]}"]
        if cmd == "WIFI":
            return ["WIFI ssid=RobotDog-ABCD pass=secretpass1 ip=192.168.4.1 clients=0"]
        if cmd == "STATE":
            if self.state_override:
                return [self.state_override]
            return [f"STATE armed={int(self.armed)} ctrl={self.ctrl} walk={self.walk} "
                    f"homing={int(self.homing)} settled={int(self.settled_after <= 0)} "
                    f"reset=POWERON stop={self.stop_reason} uptime=5"]
        if cmd == "STATUS":
            return ["STATUS LF=90/45/90 RF=90/45/90 LH=90/45/90 RH=90/45/90"]
        if cmd == "ARM":
            error = self._arm("SERIAL")
            return [f"ERR {error}"] if error else ["OK ARM"]
        if cmd == "HB":
            if not self.armed or self.ctrl != "SERIAL":
                return ["ERR not_armed"]
            self.lease_deadline = self.clock() + self.LEASE_S
            return ["OK HB"]
        if cmd == "WALKHB":
            if not self.armed or self.ctrl != "SERIAL":
                return ["ERR not_armed"]
            self.walk = "STOPPED" if parts[1] == "STOP" else parts[1]
            self.lease_deadline = self.clock() + self.LEASE_S
            return ["EVENT noise", f"OK WALKHB {parts[1]}"]
        if line == "WALK STOP":
            self._stop("serial_stop")
            return ["OK WALK STOPPED"]
        if cmd == "WALK":  # legacy notebook command: implicit serial ARM + lease
            if self.armed and self.ctrl != "SERIAL":
                return ["ERR controlled_by_remote"]
            error = self._arm("SERIAL")
            if error:
                return [f"ERR {error}"]
            self.walk = parts[1]
            return [f"OK WALK {parts[1]}"]
        if cmd == "DISARM":
            self._stop("disarm")
            return ["OK DISARM"]
        if cmd == "SETTLED":
            self.settled_after -= 1
            return ["SETTLED 1" if self.settled_after < 0 else "SETTLED 0"]
        return ["ERR unknown_command"]


def make_link(firmware=None):
    firmware = firmware or FakeFirmware()
    link = RobotLink("COM_TEST", opener=lambda *_: firmware, clock=firmware.clock,
                     sleep=lambda _: None)
    link.connect()
    return link, firmware


class RobotLinkTests(unittest.TestCase):
    def test_connect_reads_wifi_info_and_does_not_arm(self):
        link, fw = make_link()
        self.assertEqual(link.wifi, {"ssid": "RobotDog-ABCD", "password": "secretpass1"})
        self.assertFalse(fw.armed)
        self.assertNotIn("ARM", fw.log)

    def test_walk_requires_arm_from_same_session(self):
        link, fw = make_link()
        with self.assertRaises(LinkError) as ctx:
            link.command("WALK", OWNER, "FORWARD")
        self.assertEqual(ctx.exception.code, "not_armed")
        link.command("ARM", OWNER)
        link.command("WALK", OWNER, "FORWARD")
        self.assertEqual(fw.walk, "FORWARD")
        self.assertIn("WALKHB FORWARD", fw.log)
        with self.assertRaises(LinkError) as ctx:
            link.command("WALK", OTHER, "BACKWARD")
        self.assertEqual(ctx.exception.code, "not_armed")
        with self.assertRaises(LinkError) as ctx:
            link.command("ARM", OTHER)
        self.assertEqual(ctx.exception.code, "controlled_by_other")

    def test_stop_is_accepted_from_any_session(self):
        link, fw = make_link()
        link.command("ARM", OWNER)
        link.command("WALK", OWNER, "FORWARD")
        self.assertEqual(link.command("STOP", None), {"ok": True})
        self.assertFalse(fw.armed)
        self.assertEqual(fw.walk, "STOPPED")
        self.assertIsNone(link.owner_session)

    def test_status_reports_control_only_to_owner(self):
        link, _ = make_link()
        link.command("ARM", OWNER)
        self.assertTrue(link.status(OWNER)["youControl"])
        self.assertFalse(link.status(OTHER)["youControl"])
        self.assertEqual(link.status(OWNER)["angles"]["LF"], [90, 45, 90])

    def test_firmware_lease_expiry_clears_owner(self):
        link, fw = make_link()
        link.command("ARM", OWNER)
        fw.armed, fw.ctrl = False, "NONE"  # firmware heartbeat timeout
        self.assertFalse(link.status(OWNER)["youControl"])
        link.command("ARM", OTHER)  # a new session can take control afterwards
        self.assertEqual(link.owner_session, OTHER)

    def test_shutdown_stops_and_waits_for_settled_before_close(self):
        fw = FakeFirmware()
        fw.settled_after = 2
        link, _ = make_link(fw)
        self.assertTrue(link.shutdown())
        tail = fw.log[fw.log.index("WALK STOP"):]
        self.assertEqual(tail, ["WALK STOP", "SETTLED", "SETTLED", "SETTLED", "<close>"])

    def test_lease_expires_without_heartbeat_and_owner_is_cleared(self):
        link, fw = make_link()
        link.command("ARM", OWNER)
        link.command("WALK", OWNER, "FORWARD")
        fw.clock.t += 0.6
        link.command("HB", OWNER)          # renews the lease
        fw.clock.t += 0.6
        self.assertTrue(link.status(OWNER)["armed"])
        fw.clock.t += 1.1                  # no heartbeat for > 1 s
        status = link.status(OWNER)
        self.assertEqual((status["armed"], status["walk"], status["lastStop"]),
                         (False, "STOPPED", "heartbeat_timeout"))
        self.assertIsNone(link.owner_session)
        with self.assertRaises(LinkError) as ctx:
            link.command("WALK", OWNER, "FORWARD")
        self.assertEqual(ctx.exception.code, "not_armed")

    def test_arm_refused_while_homing_or_wifi_controls(self):
        link, fw = make_link()
        fw.homing = True
        with self.assertRaises(LinkError) as ctx:
            link.command("ARM", OWNER)
        self.assertEqual(ctx.exception.code, "homing_in_progress")
        fw.homing = False
        fw.armed, fw.ctrl, fw.lease_deadline = True, "WIFI", fw.clock() + 5
        with self.assertRaises(LinkError) as ctx:
            link.command("ARM", OWNER)
        self.assertEqual(ctx.exception.code, "controlled_by_other")
        self.assertIsNone(link.owner_session)
        status = link.status(OWNER)
        self.assertEqual((status["controller"], status["youControl"]), ("WIFI", False))

    def test_stop_is_written_before_taking_the_transaction_lock(self):
        link, fw = make_link()
        link.command("ARM", OWNER)
        link.command("WALK", OWNER, "FORWARD")
        written = []
        link._lock.acquire()               # a poll is in the middle of a transaction
        try:
            t = threading.Thread(target=lambda: written.append(link.command("STOP", None)))
            t.start()
            t.join(0.3)
            self.assertIn("WALK STOP", fw.log)  # already on the wire
            self.assertFalse(fw.armed)
        finally:
            link._lock.release()
        t.join(2)
        self.assertEqual(written, [{"ok": True}])
        self.assertEqual(fw.log[-2:], ["WALK STOP", "WALK STOP"])  # confirmed under the lock

    def test_status_includes_imu_and_level_like_wifi(self):
        link, _ = make_link()
        status = link.status(OWNER)
        imu, level = status["imu"], status["level"]
        self.assertEqual((imu["ready"], imu["valid"], imu["chip"]), (True, True, "MPU_COMPATIBLE"))
        self.assertEqual((imu["rollDeg"], imu["pitchDeg"]), (2.5, 1.25))
        self.assertEqual(imu["axisDeg"], {"x": 2.5, "y": -1.25})
        self.assertEqual((imu["rollMap"], imu["pitchMap"]), ("+X", "-Y"))
        self.assertEqual(level["gammaOffsetDeg"], {"LF": -2.0, "RF": 1.0, "LH": -1.0, "RH": 2.0})
        self.assertEqual((level["tiltLimitDeg"], level["maxCompDeg"]), (20.0, 6.0))

    def test_unknown_imu_values_become_null(self):
        from usb_gateway import parse_imu_line
        imu = parse_imu_line("IMU ready=0 chip=NONE who=0x00 addr=0x00 valid=0 age=-1 ax=- ay=- az=- "
                             "x=- y=- zeroed=0 rollmap=NONE pitchmap=NONE roll=- pitch=-")
        self.assertEqual((imu["ready"], imu["rollDeg"], imu["axisDeg"]["x"], imu["ageMs"]),
                         (False, None, None, -1))

    def test_level_toggle_only_by_owner_while_armed(self):
        link, fw = make_link()
        with self.assertRaises(LinkError) as ctx:   # nobody may pre-enable before ARM
            link.command("LEVEL", OWNER, level_on="1")
        self.assertEqual(ctx.exception.code, "not_armed")
        link.command("ARM", OWNER)
        self.assertEqual(link.command("LEVEL", OWNER, level_on="1"), {"ok": True})
        self.assertTrue(fw.level)
        with self.assertRaises(LinkError) as ctx:
            link.command("LEVEL", OTHER, level_on="0")
        self.assertEqual(ctx.exception.code, "controlled_by_other")
        link.command("LEVEL", OWNER, level_on="0")
        self.assertFalse(fw.level)
        with self.assertRaises(LinkError) as ctx:
            link.command("LEVEL", OWNER, level_on="maybe")
        self.assertEqual(ctx.exception.code, "invalid_level")
        link.command("LEVEL", OWNER, level_on="1")
        link.command("STOP", None)
        self.assertFalse(fw.level)                  # STOP switches leveling off

    def test_sensor_lines_are_polled_at_most_every_half_second(self):
        link, fw = make_link()
        link.status(OWNER)
        link.status(OWNER)
        self.assertEqual(fw.log.count("IMU"), 1)
        fw.clock.t += 0.6
        link.status(OWNER)
        self.assertEqual(fw.log.count("IMU"), 2)

    def test_arm_and_walk_refused_while_stop_is_pending(self):
        link, fw = make_link()
        link.command("ARM", OWNER)
        link._stops_pending = 1            # STOP written, confirmation not yet run
        for cmd, direction in (("ARM", None), ("WALK", "FORWARD"), ("HB", None)):
            with self.subTest(cmd=cmd):
                with self.assertRaises(LinkError) as ctx:
                    link.command(cmd, OWNER, direction)
                self.assertEqual(ctx.exception.code, "stop_in_progress")
        self.assertNotIn("WALKHB FORWARD", fw.log)
        link._stops_pending = 0
        link.command("STOP", None)
        self.assertEqual(link._stops_pending, 0)

    def test_reply_must_match_whole_line(self):
        link, fw = make_link()
        link.command("ARM", OWNER)
        original = fw._handle

        def wrong_reply(line):
            out = original(line)
            return ["OK WALKHB LEFT"] if line.startswith("WALKHB") else out
        fw._handle = wrong_reply
        with self.assertRaises(LinkError) as ctx:
            link.command("WALK", OWNER, "FORWARD")
        self.assertEqual(ctx.exception.code, "no_response")

    def test_stale_input_is_discarded_before_a_command(self):
        link, fw = make_link()
        fw._out.append("OK ARM")          # late reply from an earlier request
        fw.homing = True
        with self.assertRaises(LinkError) as ctx:
            link.command("ARM", OWNER)
        self.assertEqual(ctx.exception.code, "homing_in_progress")

    def test_status_reports_real_error_and_keeps_port_state(self):
        link, fw = make_link()
        fw.silent = True
        status = link.status(OWNER)
        self.assertEqual((status["connected"], status["error"]), (True, "no_response"))
        self.assertNotIn("armed", status)
        fw.silent = False
        fw.state_override = "STATE armed=0 ctrl=NONE walk=STOPPED uptime=abc"
        status = link.status(OWNER)
        self.assertEqual((status["connected"], status["error"]), (True, "bad_status_reply"))

    def test_status_while_another_thread_connects_fails_fast(self):
        fw = FakeFirmware()
        link = RobotLink("COM_TEST", opener=lambda *_: fw, clock=fw.clock, sleep=lambda _: None)
        link._connect_lock.acquire()       # handshake in progress elsewhere
        try:
            status = link.status(OWNER)
            self.assertEqual((status["connected"], status["error"]), (False, "usb_connecting"))
            with self.assertRaises(LinkError) as ctx:
                link.command("STOP", None)
            self.assertEqual(ctx.exception.code, "usb_disconnected")
        finally:
            link._connect_lock.release()

    def test_failed_handshake_closes_port(self):
        fw = FakeFirmware()
        fw.silent = True
        link = RobotLink("COM_TEST", opener=lambda *_: fw, clock=fw.clock, sleep=lambda _: None)
        with self.assertRaises(LinkError):
            link.connect(handshake_s=2)
        self.assertFalse(fw.is_open)
        self.assertFalse(link.connected)
        self.assertEqual(link.last_error, "no_ready_from_firmware")

    def test_invalid_direction_and_session_rejected(self):
        link, _ = make_link()
        link.command("ARM", OWNER)
        with self.assertRaises(LinkError):
            link.command("WALK", OWNER, "SPIN")
        with self.assertRaises(LinkError) as ctx:
            link.command("ARM", "bad session!")
        self.assertEqual(ctx.exception.code, "invalid_session")


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.link, self.fw = make_link()
        page = ROOT / "esp32-robot-dog-code-master" / "software" / "robot_control_web" / "index.html"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        port = self.server.server_address[1]
        self.host = f"127.0.0.1:{port}"
        self.server.RequestHandlerClass = make_handler(self.link, {self.host}, page)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        hdrs = {"Host": host or self.host}
        hdrs.update(headers or {})
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def post(self, params, headers=None, host=None):
        hdrs = {"Content-Type": "application/x-www-form-urlencoded", "X-Robot-Control": "1"}
        hdrs.update(headers or {})
        return self.request("POST", "/api/command", urlencode(params), hdrs, host)

    def test_page_and_status_served(self):
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("로봇개 보행 제어", body.decode("utf-8"))
        status, body = self.request("GET", f"/api/status?session={OWNER}")
        self.assertEqual(json.loads(body)["link"], "usb")

    def test_command_without_control_header_is_rejected(self):
        status, _ = self.post({"cmd": "ARM", "session": OWNER}, headers={"X-Robot-Control": "0"})
        self.assertEqual(status, 403)
        self.assertFalse(self.fw.armed)

    def test_foreign_host_header_is_rejected(self):
        status, _ = self.post({"cmd": "ARM", "session": OWNER}, host="evil.example:8787")
        self.assertEqual(status, 403)
        self.assertFalse(self.fw.armed)

    def test_arm_walk_stop_over_http(self):
        self.assertEqual(self.post({"cmd": "ARM", "session": OWNER})[0], 200)
        self.assertEqual(self.post({"cmd": "WALK", "dir": "FORWARD", "session": OWNER})[0], 200)
        self.assertEqual(self.fw.walk, "FORWARD")
        status, body = self.post({"cmd": "WALK", "dir": "LEFT", "session": OTHER})
        self.assertEqual((status, json.loads(body)["error"]), (409, "not_armed"))
        self.assertEqual(self.post({"cmd": "STOP"})[0], 200)
        self.assertFalse(self.fw.armed)


class MainTests(unittest.TestCase):
    def test_only_ipv4_loopback_hosts_are_accepted(self):
        import usb_gateway
        for host in ("::1", "0.0.0.0", "192.168.0.10", "localhost-ish"):
            with self.subTest(host=host):
                self.assertEqual(usb_gateway.main(["--host", host]), 2)


class FirmwareSourceTests(unittest.TestCase):
    """Static checks of the ESP32 sketch (it cannot run here; compile separately)."""

    SKETCH = ROOT / "esp32-robot-dog-code-master" / "tools" / "servoInitPose"

    def test_wifi_api_handlers_check_host_header(self):
        src = (self.SKETCH / "web_control.ino").read_text(encoding="utf-8")
        for handler in ("void handleStatus()", "void handleCommand()"):
            body = src[src.index(handler):]
            body = body[:body.index("\n}\n")]
            self.assertIn("if (!hostOk()) return;", body.split("\n", 3)[2], handler)
        self.assertIn('host == "192.168.4.1" || host == "192.168.4.1:80"', src)

    def test_web_server_runs_outside_the_safety_loop(self):
        main = (self.SKETCH / "servoInitPose.ino").read_text(encoding="utf-8")
        loop = main[main.index("void loop()"):]
        self.assertNotIn("handleClient", loop)
        self.assertNotIn("webLoop", loop)
        web = (self.SKETCH / "web_control.ino").read_text(encoding="utf-8")
        self.assertIn("xTaskCreatePinnedToCore(webTask", web)

    def test_leveling_safety_hooks(self):
        main = (self.SKETCH / "servoInitPose.ino").read_text(encoding="utf-8")
        level = (self.SKETCH / "level_control.ino").read_text(encoding="utf-8")
        stop = main[main.index("void controlStop("):]
        stop = stop[:stop.index("\n}\n")]
        self.assertIn("levelOnStop();", stop)                    # STOP: leveling off, compensation cleared
        arm = main[main.index("const char *controlArm("):]
        arm = arm[:arm.index("\n}\n")]
        self.assertIn("levelArmCheck()", arm)                    # no ARM with an unusable sensor
        loop = main[main.index("void loop()"):]
        self.assertLess(loop.index("imuUpdate();"), loop.index("levelUpdate();"))
        self.assertIn('controlStop("tilt_limit")', level)
        self.assertIn('controlStop("imu_lost")', level)
        self.assertIn('controlStop("level_diverge")', level)
        self.assertIn('if (on && !armed) return "not_armed";', level)
        check = level[level.index("const char *levelArmCheck()"):]
        self.assertIn('"tilt_limit"', check[:check.index("\n}\n")])
        self.assertIn("bool levelEnabled = false;", level)       # off after every boot
        self.assertRegex(level, r"LEVEL_MAX_COMP_DEG = [0-9.]+f;")
        pulse = main[main.index("uint16_t pulseForServo("):]
        self.assertIn("levelOffsetDeg[servoIndex]", pulse[:pulse.index("\n}\n")])

    def test_imu_recovery_never_blocks_while_motion_is_possible(self):
        imu = (self.SKETCH / "imu_sensor.ino").read_text(encoding="utf-8")
        safe = imu[imu.index("bool imuSafeToBlock()"):]
        safe = safe[:safe.index("\n}\n")]
        for cond in ("!armed", "walkDirection == WALK_STOPPED", "!startupHoming"):
            self.assertIn(cond, safe)
        recover = imu[imu.index("void imuRecover("):]
        recover = recover[:recover.index("\n}\n")]
        # Every blocking re-init (chip reset delays) is preceded by the safety guard.
        parts = recover.split("imuProbeAndInit();")
        self.assertEqual(len(parts), 3)
        for before in parts[:2]:
            self.assertIn("imuSafeToBlock()", before[-250:])
        quick = imu[imu.index("bool imuQuickSwitch("):]
        quick = quick[:quick.index("\n}\n")]
        self.assertNotIn("delay(", quick)
        update = imu[imu.index("void imuUpdate()"):]
        self.assertIn("imuRecover(now);", update[:update.index("\n}\n")])

    def test_legacy_walk_goes_through_the_lease(self):
        main = (self.SKETCH / "servoInitPose.ino").read_text(encoding="utf-8")
        block = main[main.index('if (strcmp(command, "WALK") == 0)'):]
        block = block[:block.index('if (strcmp(command, "STATUS") == 0)')]
        self.assertIn("controlArm(CTRL_SERIAL)", block)
        self.assertIn("controlWalk(CTRL_SERIAL, parsed)", block)
        self.assertNotIn("walkDirection = WALK_", block)


if __name__ == "__main__":
    unittest.main()
