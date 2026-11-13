"""Standing-level calibration: find this robot's standing trim and save it.

    python level_calibrate.py --port COM14            # measure, adjust, save
    python level_calibrate.py --port COM14 --check    # measure only, nothing moves

What it does (firmware with CAPS TRIM1, tools/servoInitPose):
1. Reads the body tilt (IMU roll / pitch) with the servos where they are now.
2. If the tilt is outside the tolerance, changes the standing GAMMA trim of the
   four legs by the smallest amount that removes it, in small steps, measuring
   after every step (``TRIM SET``, servos move slowly).
3. When the body is level, stores the trim in the robot (``TRIM SAVE``). Every
   later boot uses it as part of the initial (neutral) pose.

"Smallest amount": tilt has two degrees of freedom (roll, pitch) and there are
four legs. The change with the smallest sum of squares that corrects both is
  delta[leg] = side[leg] * a + front[leg] * b
(a from roll, b from pitch). The two unused patterns (all legs together = body
height, diagonal twist) do not change the tilt and are left alone.

Safety:
- Needs the robot standing on a flat floor, servo power on, nobody touching it.
- Refuses to run while the robot is armed, walking or homing; the firmware
  refuses TRIM SET in those states as well.
- Limits: at most STEP_MAX_DEG per axis per step, at most CHANGE_MAX_DEG total
  change per leg in one run, firmware limit +-15 deg.
- If the tilt gets worse, the robot is being moved, the IMU is not valid or
  anything else goes wrong (also Ctrl+C), the starting trim is put back and
  nothing is saved.
- The USB gateway and notebooks must be closed first (one program per port).
"""
from __future__ import annotations

import argparse
import sys
import time

LEGS = ("LF", "RF", "LH", "RH")
LEG_SIDE = (-1, +1, -1, +1)    # right = +1 (same as LEVEL_LEG_SIDE in level_control.ino)
LEG_FRONT = (+1, +1, -1, -1)   # front = +1
# Measured on this robot (servoInitPose.ino comments, 2026-11-11): about 0.9 deg
# of roll and 0.5 deg of pitch per degree of split trim. Only used to size the
# steps; every step is measured, and the gain below under-corrects on purpose.
ROLL_DEG_PER_TRIM = 0.9
PITCH_DEG_PER_TRIM = 0.5
STEP_GAIN = 0.7
STEP_MAX_DEG = 3.0       # largest a / b per step
CHANGE_MAX_DEG = 8.0     # largest total change per leg in one run
TRIM_LIMIT_DEG = 15.0    # firmware STAND_TRIM_MAX_DEG
START_TILT_MAX_DEG = 15.0
WORSE_ABORT_DEG = 1.5    # abort if the tilt grows this much over the best so far
SAMPLE_COUNT = 10
SAMPLE_INTERVAL_S = 0.15
STILL_SPREAD_DEG = 1.0   # roll/pitch may vary this much while "standing still"
SETTLE_S = 1.5           # wait after the servos reached the new trim (IMU filter)


class CalibrationError(RuntimeError):
    pass


def fields(line: str) -> dict:
    return dict(item.split("=", 1) for item in line.split()[1:] if "=" in item)


def parse_trim(text: str) -> list[float]:
    values = [float(v) for v in text.split("/")]
    if len(values) != 4:
        raise ValueError(text)
    return values


def plan_step(roll: float, pitch: float, tolerance: float) -> list[float]:
    """Trim change per leg (LF, RF, LH, RH) for one step. roll > 0 = right side
    low, pitch > 0 = nose low; a higher GAMMA raises that corner. An axis that is
    already inside the tolerance is not touched."""
    def amount(tilt: float, sensitivity: float) -> float:
        if abs(tilt) <= tolerance:
            return 0.0
        return max(-STEP_MAX_DEG, min(STEP_MAX_DEG, STEP_GAIN * tilt / sensitivity))

    a = amount(roll, ROLL_DEG_PER_TRIM)
    b = amount(pitch, PITCH_DEG_PER_TRIM)
    return [LEG_SIDE[i] * a + LEG_FRONT[i] * b for i in range(4)]


class SerialRobot:
    """Line protocol to the firmware over an already open serial handle."""

    def __init__(self, handle, clock=time.monotonic):
        self.handle = handle
        self.clock = clock

    def ask(self, command: str, expect: str, timeout_s: float = 2.0) -> str:
        self.handle.reset_input_buffer()
        self.handle.write((command + "\r\n").encode("utf-8"))
        deadline = self.clock() + timeout_s
        while self.clock() < deadline:
            line = self.handle.readline().decode("utf-8", errors="replace").strip()
            if not line or line.startswith("EVENT "):
                continue
            if line.startswith("ERR"):
                raise CalibrationError(f"로봇이 '{command}' 명령을 거부했습니다: {line}")
            if line.startswith(expect):
                return line
        raise CalibrationError(f"'{command}'에 응답이 없습니다.")

    def hello(self, timeout_s: float = 12.0) -> None:
        deadline = self.clock() + timeout_s
        while self.clock() < deadline:
            try:
                self.ask("HELLO", "READY SERVO_POSE_V1", 1.0)
                return
            except CalibrationError:
                continue
        raise CalibrationError("펌웨어 응답이 없습니다. 포트와 전원을 확인하세요.")


class Calibrator:
    def __init__(self, robot, tolerance=1.0, max_steps=8, sleep=time.sleep, log=print):
        self.robot = robot
        self.tolerance = tolerance
        self.max_steps = max_steps
        self.sleep = sleep
        self.log = log

    # ---- reading ---------------------------------------------------------
    def check_ready(self) -> None:
        caps = self.robot.ask("CAPS", "CAPS ").split()
        if "TRIM1" not in caps or "IMU1" not in caps:
            raise CalibrationError("이 펌웨어에는 TRIM 명령이 없습니다. 새 펌웨어를 먼저 업로드하세요.")
        state = fields(self.robot.ask("STATE", "STATE "))
        if state.get("armed") != "0" or state.get("walk") != "STOPPED" or state.get("homing") != "0":
            raise CalibrationError("로봇이 활성화·보행·초기 자세 이동 중입니다. 정지한 뒤 다시 실행하세요.")
        imu = fields(self.robot.ask("IMU", "IMU "))
        if imu.get("ready") != "1" or imu.get("valid") != "1":
            raise CalibrationError("IMU 값을 읽을 수 없습니다.")
        if imu.get("zeroed") != "1" or "NONE" in (imu.get("rollmap", "NONE"), imu.get("pitchmap", "NONE")):
            raise CalibrationError("IMU 영점(IMUZERO)이나 축 설정(IMUMAP)이 저장되어 있지 않습니다.")

    def reference(self) -> tuple:
        """What must not change during a run: nobody arms the robot and nobody
        re-zeroes or re-maps the IMU (possible from the Wi-Fi page). The zero
        itself is not reported, so the raw-minus-zero offset is not checked;
        the flags and the axis map are."""
        state = fields(self.robot.ask("STATE", "STATE "))
        imu = fields(self.robot.ask("IMU", "IMU "))
        return (state.get("armed"), state.get("walk"), state.get("homing"),
                imu.get("zeroed"), imu.get("rollmap"), imu.get("pitchmap"))

    def check_unchanged(self, reference: tuple) -> None:
        if self.reference() != reference:
            raise CalibrationError("보정 중에 로봇 상태나 IMU 설정이 바뀌었습니다 (웹 페이지에서 조작했는지 확인하세요).")

    def read_trim(self) -> list[float]:
        return parse_trim(fields(self.robot.ask("TRIM", "TRIM "))["cur"])

    def measure(self) -> tuple[float, float]:
        """Mean roll/pitch over SAMPLE_COUNT samples; refuses a moving robot."""
        for _ in range(3):
            rolls, pitches = [], []
            for _ in range(SAMPLE_COUNT):
                imu = fields(self.robot.ask("IMU", "IMU "))
                if imu.get("valid") != "1":
                    raise CalibrationError("IMU 값이 유효하지 않습니다 (흔들림 또는 연결 문제).")
                try:
                    rolls.append(float(imu["roll"]))
                    pitches.append(float(imu["pitch"]))
                except (KeyError, ValueError):
                    raise CalibrationError("IMU 기울기 값을 읽을 수 없습니다.") from None
                self.sleep(SAMPLE_INTERVAL_S)
            if max(rolls) - min(rolls) <= STILL_SPREAD_DEG and max(pitches) - min(pitches) <= STILL_SPREAD_DEG:
                return sum(rolls) / len(rolls), sum(pitches) / len(pitches)
            self.log("  로봇이 움직이고 있습니다. 다시 측정합니다.")
            self.sleep(SETTLE_S)
        raise CalibrationError("로봇이 가만히 있지 않아 측정할 수 없습니다.")

    # ---- moving ----------------------------------------------------------
    def apply_trim(self, trim: list[float]) -> None:
        self.robot.ask("TRIM SET " + " ".join(f"{v:.1f}" for v in trim), "OK TRIM SET")
        for _ in range(40):  # servos slew slowly; wait until the pose is reached and stored
            if self.robot.ask("SETTLED", "SETTLED ").endswith("1"):
                break
            self.sleep(0.25)
        else:
            raise CalibrationError("서보가 새 자세에 도달하지 못했습니다.")
        self.sleep(SETTLE_S)

    def run(self, save: bool = True) -> dict:
        """Returns {"start", "final", "startTilt", "finalTilt", "steps", "saved"}."""
        self.check_ready()
        start = self.read_trim()
        status = self.robot.ask("STATUS", "STATUS ")
        self.log(f"현재 서보 각도: {status[len('STATUS '):]}")
        self.log("현재 보정값(GAMMA, 도): " + self._trim_text(start))
        roll, pitch = self.measure()
        start_tilt = (roll, pitch)
        self.log(f"현재 기울기: roll {roll:+.2f}° / pitch {pitch:+.2f}°  (허용 ±{self.tolerance:.1f}°)")
        if max(abs(roll), abs(pitch)) > START_TILT_MAX_DEG:
            raise CalibrationError("기울기가 너무 큽니다. 바닥과 로봇 자세를 확인하세요.")

        reference = self.reference()
        trim, steps = list(start), 0
        best = max(abs(roll), abs(pitch))
        try:
            while max(abs(roll), abs(pitch)) > self.tolerance:
                if steps >= self.max_steps:
                    raise CalibrationError(f"{self.max_steps}번 조정해도 수평이 되지 않았습니다.")
                delta = plan_step(roll, pitch, self.tolerance)
                trim = [round(trim[i] + delta[i], 1) for i in range(4)]
                if any(abs(trim[i] - start[i]) > CHANGE_MAX_DEG for i in range(4)):
                    raise CalibrationError(f"한 번에 허용한 조정 범위(다리당 ±{CHANGE_MAX_DEG:.0f}°)를 넘습니다.")
                if any(abs(v) > TRIM_LIMIT_DEG for v in trim):
                    raise CalibrationError(f"보정값 한계(±{TRIM_LIMIT_DEG:.0f}°)를 넘습니다.")
                self.check_unchanged(reference)
                steps += 1
                self.apply_trim(trim)
                roll, pitch = self.measure()
                self.log(f"  {steps}단계: 보정값 {self._trim_text(trim)} → roll {roll:+.2f}° / pitch {pitch:+.2f}°")
                tilt = max(abs(roll), abs(pitch))
                if tilt > best + WORSE_ABORT_DEG:
                    raise CalibrationError("조정했더니 기울기가 더 커졌습니다 (IMU 축 설정이나 바닥을 확인하세요).")
                best = min(best, tilt)
            saved = False
            if save and steps > 0:
                self.check_unchanged(reference)
                try:
                    self.robot.ask("TRIM SAVE", "OK TRIM SAVE")
                except CalibrationError:
                    # The reply may be lost after the robot did save: report what is really stored.
                    stored = parse_trim(fields(self.robot.ask("TRIM", "TRIM "))["saved"])
                    if stored != trim:
                        raise
                saved = True
        except BaseException:
            if steps > 0:
                self._restore(start)
            raise
        return {"start": start, "final": trim, "startTilt": start_tilt, "finalTilt": (roll, pitch),
                "steps": steps, "saved": saved}

    def _restore(self, start: list[float]) -> None:
        try:
            self.robot.ask("TRIM SET " + " ".join(f"{v:.1f}" for v in start), "OK TRIM SET")
            self.log("처음 보정값으로 되돌렸습니다: " + self._trim_text(start))
        except Exception as exc:  # report, keep the original error
            self.log(f"처음 보정값으로 되돌리지 못했습니다 ({exc}). 로봇을 다시 켜면 저장된 값으로 돌아갑니다.")

    @staticmethod
    def _trim_text(trim: list[float]) -> str:
        return " ".join(f"{LEGS[i]} {trim[i]:+.1f}" for i in range(4))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="로봇개 수평 보정: 서 있는 자세 보정값을 찾아 로봇에 저장")
    parser.add_argument("--port", default="COM14")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--tolerance", type=float, default=1.0, help="수평으로 보는 기울기 범위(도), 기본 1.0")
    parser.add_argument("--check", action="store_true", help="기울기만 측정 (서보를 움직이지 않음)")
    parser.add_argument("--no-save", action="store_true", help="조정만 하고 저장하지 않음 (다시 켜면 원래 값)")
    parser.add_argument("--yes", action="store_true", help="시작 전 확인 질문 생략")
    args = parser.parse_args(argv)
    if not 0.5 <= args.tolerance <= 5.0:
        parser.error("--tolerance는 0.5~5.0 사이여야 합니다.")

    from usb_gateway import open_serial_without_reset  # same folder; opens without resetting the ESP32

    if not args.check and not args.yes:
        print("로봇이 평평한 바닥에 네 발로 서 있고, 서보 전원이 켜져 있으며, 아무도 만지지 않아야 합니다.")
        print("다리가 조금씩 천천히 움직입니다. 전원 스위치에 손이 닿는 곳에 있으세요.")
        print("보정이 끝날 때까지 웹 페이지에서 로봇을 조작하지 마세요.")
        if input("시작할까요? (y/N) ").strip().lower() != "y":
            return 1
    try:
        handle = open_serial_without_reset(args.port, args.baud)
    except OSError as exc:
        print(f"{args.port}를 열 수 없습니다 (게이트웨이나 노트북이 쓰고 있는지 확인): {exc}")
        return 1
    try:
        robot = SerialRobot(handle)
        robot.hello()
        calibrator = Calibrator(robot, tolerance=args.tolerance)
        if args.check:
            calibrator.check_ready()
            print("현재 보정값(GAMMA, 도): " + calibrator._trim_text(calibrator.read_trim()))
            roll, pitch = calibrator.measure()
            level = max(abs(roll), abs(pitch)) <= args.tolerance
            print(f"기울기: roll {roll:+.2f}° / pitch {pitch:+.2f}° → " + ("수평입니다." if level else "수평이 아닙니다."))
            return 0
        result = calibrator.run(save=not args.no_save)
        if result["steps"] == 0:
            print("이미 수평입니다. 바꾼 것이 없습니다.")
        else:
            change = [result["final"][i] - result["start"][i] for i in range(4)]
            print("조정량(도): " + calibrator._trim_text(change))
            print("새 보정값: " + calibrator._trim_text(result["final"]))
            print("로봇에 저장했습니다. 다음 부팅부터 이 값이 초기 자세에 들어갑니다." if result["saved"]
                  else "저장하지 않았습니다. 로봇을 다시 켜면 이전 값으로 돌아갑니다.")
        return 0
    except CalibrationError as exc:
        print(f"중단: {exc}")
        return 2
    except KeyboardInterrupt:
        print("사용자가 중단했습니다.")
        return 2
    finally:
        handle.close()


if __name__ == "__main__":
    sys.exit(main())
