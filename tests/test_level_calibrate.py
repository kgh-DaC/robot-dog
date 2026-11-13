"""Standing-level calibration (level_calibrate.py) against a model of the
firmware's TRIM / IMU commands. No real serial port is opened."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "esp32-robot-dog-code-master" / "software" / "usb_gateway"))

import level_calibrate as lc  # noqa: E402

FIRMWARE = ROOT / "esp32-robot-dog-code-master" / "tools" / "servoInitPose"


class FakeRobot:
    """Firmware model: the tilt depends on the trim like the real robot
    (roll = roll0 - k_roll * a, pitch = pitch0 - k_pitch * b, where a/b are the
    side/front components of the trim change)."""

    def __init__(self, roll0=4.0, pitch0=-2.0, k_roll=0.9, k_pitch=0.5):
        self.base = [9.0, 1.0, -1.0, -9.0]
        self.trim = list(self.base)
        self.saved = list(self.base)
        self.roll0, self.pitch0, self.k_roll, self.k_pitch = roll0, pitch0, k_roll, k_pitch
        self.caps = "CAPS SERVO_POSE_V1 WALK4 LEASE1 WIFI1 IMU1 LEVEL1 STA1 TRIM1"
        self.armed = False
        self.imu_valid = True
        self.zeroed = True
        self.noise = []          # extra roll per IMU read (models a robot being moved)
        self.fail_on = None      # command prefix that gets no answer
        self.log = []

    def tilt(self):
        d = [self.trim[i] - self.base[i] for i in range(4)]
        a = sum(lc.LEG_SIDE[i] * d[i] for i in range(4)) / 4
        b = sum(lc.LEG_FRONT[i] * d[i] for i in range(4)) / 4
        return self.roll0 - self.k_roll * a, self.pitch0 - self.k_pitch * b

    def ask(self, command, expect, timeout_s=2.0):
        self.log.append(command)
        if self.fail_on and command.startswith(self.fail_on):
            raise lc.CalibrationError("no answer")
        parts = command.split()
        if command == "CAPS":
            return self.caps
        if command == "STATE":
            return f"STATE armed={int(self.armed)} ctrl=NONE walk=STOPPED homing=0 settled=1"
        if command == "STATUS":
            return "STATUS LF=90/45/90 RF=90/45/90 LH=90/45/90 RH=86/45/90"
        if command == "IMU":
            roll, pitch = self.tilt()
            roll += self.noise.pop(0) if self.noise else 0.0
            return (f"IMU ready=1 valid={int(self.imu_valid)} zeroed={int(self.zeroed)} rollmap=-Y pitchmap=+X "
                    f"roll={roll:.2f} pitch={pitch:.2f}")
        if command == "SETTLED":
            return "SETTLED 1"
        if command == "TRIM":
            text = lambda v: "/".join(f"{x:.1f}" for x in v)  # noqa: E731
            return f"TRIM cur={text(self.trim)} saved={text(self.saved)} source=DEFAULT max=15.0"
        if parts[:2] == ["TRIM", "SET"]:
            if self.armed:
                raise lc.CalibrationError("ERR controlled_by_remote")
            self.trim = [float(v) for v in parts[2:]]
            return "OK TRIM SET"
        if command == "TRIM SAVE":
            self.saved = list(self.trim)
            return "OK TRIM SAVE"
        raise AssertionError(command)


def run(robot, **kwargs):
    save = kwargs.pop("save", True)
    return lc.Calibrator(robot, sleep=lambda _: None, log=lambda _: None, **kwargs).run(save=save)


class PlanStepTest(unittest.TestCase):
    def test_right_low_raises_right_legs_and_lowers_left(self):
        d = lc.plan_step(2.0, 0.0, 1.0)             # roll > 0: right side low
        self.assertGreater(d[1], 0)
        self.assertEqual(d, [-d[1], d[1], -d[1], d[1]])

    def test_nose_low_raises_front_legs(self):
        d = lc.plan_step(0.0, 2.0, 1.0)
        self.assertGreater(d[0], 0)
        self.assertEqual(d, [d[0], d[0], -d[0], -d[0]])

    def test_axis_inside_tolerance_is_not_touched(self):
        self.assertEqual(lc.plan_step(0.9, -0.9, 1.0), [0.0] * 4)
        d = lc.plan_step(3.0, 0.5, 1.0)             # pitch is fine: only the roll pattern
        self.assertEqual(d[0], d[2])
        self.assertEqual(d[1], d[3])

    def test_step_is_minimum_norm_and_bounded(self):
        d = lc.plan_step(20.0, -20.0, 1.0)
        self.assertAlmostEqual(sum(d), 0.0)                     # body height unchanged
        self.assertAlmostEqual(d[0] - d[1] - d[2] + d[3], 0.0)  # no diagonal twist
        self.assertLessEqual(max(abs(v) for v in d), 2 * lc.STEP_MAX_DEG)


class CalibratorTest(unittest.TestCase):
    def test_levels_the_robot_and_saves(self):
        robot = FakeRobot(roll0=4.0, pitch0=-2.0)
        result = run(robot)
        roll, pitch = robot.tilt()
        self.assertLessEqual(abs(roll), 1.0)
        self.assertLessEqual(abs(pitch), 1.0)
        self.assertTrue(result["saved"])
        self.assertEqual(robot.saved, robot.trim)
        self.assertEqual(robot.log[-1], "TRIM SAVE")
        self.assertLessEqual(max(abs(result["final"][i] - result["start"][i]) for i in range(4)), lc.CHANGE_MAX_DEG)

    def test_already_level_changes_and_saves_nothing(self):
        robot = FakeRobot(roll0=0.6, pitch0=-0.4)
        result = run(robot)
        self.assertEqual(result["steps"], 0)
        self.assertFalse(result["saved"])
        self.assertFalse([c for c in robot.log if c.startswith("TRIM S")])

    def test_no_save_option_leaves_flash_alone(self):
        robot = FakeRobot()
        result = run(robot, save=False)
        self.assertGreater(result["steps"], 0)
        self.assertFalse(result["saved"])
        self.assertEqual(robot.saved, robot.base)

    def test_wrong_direction_restores_the_start_and_does_not_save(self):
        robot = FakeRobot(roll0=4.0, pitch0=0.0, k_roll=-0.9)   # robot reacts the opposite way
        with self.assertRaises(lc.CalibrationError):
            run(robot)
        self.assertEqual(robot.trim, robot.base)
        self.assertNotIn("TRIM SAVE", robot.log)

    def test_weak_response_stops_at_the_change_limit_and_restores(self):
        robot = FakeRobot(roll0=6.0, pitch0=0.0, k_roll=0.05)
        with self.assertRaises(lc.CalibrationError):
            run(robot)
        self.assertEqual(robot.trim, robot.base)
        self.assertNotIn("TRIM SAVE", robot.log)
        for command in robot.log:
            if command.startswith("TRIM SET"):
                values = [float(v) for v in command.split()[2:]]
                self.assertLessEqual(max(abs(values[i] - robot.base[i]) for i in range(4)), lc.CHANGE_MAX_DEG)

    def test_refuses_armed_robot_old_firmware_and_uncalibrated_imu(self):
        for change in ({"armed": True}, {"caps": "CAPS SERVO_POSE_V1 WALK4 LEASE1 IMU1"}, {"zeroed": False},
                       {"imu_valid": False}):
            robot = FakeRobot()
            for key, value in change.items():
                setattr(robot, key, value)
            with self.assertRaises(lc.CalibrationError):
                run(robot)
            self.assertFalse([c for c in robot.log if c.startswith("TRIM S")], change)

    def test_moving_robot_is_not_measured(self):
        robot = FakeRobot()
        robot.noise = [0, 3.0] * 30                 # roll jumps by 3 deg between samples
        with self.assertRaises(lc.CalibrationError):
            run(robot)
        self.assertFalse([c for c in robot.log if c.startswith("TRIM S")])

    def test_huge_start_tilt_is_refused(self):
        robot = FakeRobot(roll0=18.0)
        with self.assertRaises(lc.CalibrationError):
            run(robot)
        self.assertFalse([c for c in robot.log if c.startswith("TRIM S")])

    def test_lost_link_after_a_step_tries_to_restore(self):
        robot = FakeRobot()
        robot.fail_on = "SETTLED"
        with self.assertRaises(lc.CalibrationError):
            run(robot)
        self.assertEqual(robot.trim, robot.base)
        self.assertNotIn("TRIM SAVE", robot.log)


class FirmwareSourceTest(unittest.TestCase):
    """Static checks of the firmware side of the TRIM contract."""

    def setUp(self):
        self.main = (FIRMWARE / "servoInitPose.ino").read_text(encoding="utf-8")

    def function(self, name):
        start = self.main.index(name + "(")
        return self.main[start:self.main.index("\n}\n", start)]

    def test_trim_is_loaded_before_the_first_servo_pulse(self):
        setup = self.main[self.main.index("void setup()"):]
        self.assertLess(setup.index("loadStandTrim();"), setup.index("pulseForServo(i)"))

    def test_trim_changes_are_refused_while_anything_may_move(self):
        blocked = self.function("const char *standTrimBlocked")
        for condition in ("armed", "walkDirection != WALK_STOPPED", "startupHoming"):
            self.assertIn(condition, blocked)
        self.assertIn("standTrimBlocked()", self.function("const char *standTrimSet"))
        self.assertIn("standTrimBlocked()", self.function("const char *standTrimSave"))

    def test_trim_values_are_range_checked_on_set_and_on_load(self):
        self.assertIn("STAND_TRIM_MAX_DEG", self.function("bool standTrimValid"))
        self.assertIn("isfinite", self.function("bool standTrimValid"))
        self.assertIn("standTrimValid(values)", self.function("const char *standTrimSet"))
        self.assertIn("standTrimValid(values)", self.function("void loadStandTrim"))
        self.assertEqual(lc.TRIM_LIMIT_DEG, 15.0)
        self.assertIn("STAND_TRIM_MAX_DEG = 15.0f", self.main)

    def test_trim_is_serial_only_and_announced_in_caps(self):
        self.assertIn("TRIM1", self.main[self.main.index('"CAPS"'):][:200])
        web = (FIRMWARE / "web_control.ino").read_text(encoding="utf-8")
        self.assertNotIn("standTrimSet", web)
        self.assertNotIn("standTrimSave", web)

    def test_leg_order_and_signs_match_the_firmware(self):
        level = (FIRMWARE / "level_control.ino").read_text(encoding="utf-8")
        self.assertIn("LEVEL_LEG_SIDE[4] = {-1, +1, -1, +1}", level)
        self.assertIn("LEVEL_LEG_FRONT[4] = {+1, +1, -1, -1}", level)
        self.assertIn("LEVEL_GAMMA_EXTEND_SIGN = 1.0f", level)
        self.assertEqual(lc.LEG_SIDE, (-1, 1, -1, 1))
        self.assertEqual(lc.LEG_FRONT, (1, 1, -1, -1))


if __name__ == "__main__":
    unittest.main()
