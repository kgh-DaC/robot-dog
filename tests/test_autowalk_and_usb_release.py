"""Power-on auto walk setting and the page's "USB disconnect" (2026-11-14).

Uses the fake firmware from test_usb_gateway (no real serial port) plus static
checks of the firmware source for the rules the auto walk must keep.
"""
import re
import unittest
from pathlib import Path

from test_usb_gateway import OTHER, OWNER, FakeFirmware, make_link
from usb_gateway import LinkError

ROOT = Path(__file__).resolve().parents[1]
SKETCH = ROOT / "esp32-robot-dog-code-master" / "tools" / "servoInitPose"


class AutoFirmware(FakeFirmware):
    """FakeFirmware plus the AUTOWALK setting (CAPS AUTOWALK1)."""

    def __init__(self):
        super().__init__()
        self.auto_on = False
        self.auto_phase = "IDLE"

    def _handle(self, line):
        parts = line.split()
        if parts[0] == "CAPS":
            return [super()._handle(line)[0] + " TRIM1 AUTOWALK1"]
        if parts[0] == "AUTOWALK":
            self.tick()
            if len(parts) == 1:
                return [f"AUTOWALK enabled={int(self.auto_on)} phase={self.auto_phase} delay=5000 run=10000"]
            if parts[1] == "ON" and self.armed:
                return ["ERR armed"]
            self.auto_on = parts[1] == "ON"
            return [f"OK AUTOWALK {parts[1]}"]
        return super()._handle(line)


class UsbReleaseTests(unittest.TestCase):
    def test_disconnect_stops_the_robot_then_closes_and_stays_released(self):
        link, fw = make_link()
        link.command("ARM", OWNER)
        link.command("WALK", OWNER, "FORWARD")
        self.assertEqual(link.command("DISCONNECT", OTHER), {"ok": True, "settled": True})
        self.assertFalse(fw.armed)
        self.assertEqual(fw.walk, "STOPPED")
        self.assertLess(fw.log.index("WALK STOP"), fw.log.index("<close>"))
        fw.is_open = True          # the port could be opened again ...
        opened = len(fw.log)
        fw.clock.t += 60           # ... but polls must not reconnect by themselves
        status = link.status(OWNER)
        self.assertEqual((status["connected"], status["error"], status.get("released")),
                         (False, "usb_released", True))
        self.assertEqual(len(fw.log), opened)
        with self.assertRaises(LinkError) as ctx:
            link.command("ARM", OWNER)
        self.assertEqual(ctx.exception.code, "usb_released")

    def test_connect_reopens_without_arming(self):
        link, fw = make_link()
        link.command("ARM", OWNER)
        link.command("DISCONNECT", OWNER)
        fw.is_open = True
        self.assertEqual(link.command("CONNECT", OWNER), {"ok": True})
        status = link.status(OWNER)
        self.assertTrue(status["connected"])
        self.assertNotIn("released", status)
        self.assertFalse(status["armed"])
        self.assertFalse(status["youControl"])
        self.assertNotIn("ARM", fw.log[fw.log.index("<close>"):])

    def test_disconnect_needs_a_valid_session(self):
        link, fw = make_link()
        with self.assertRaises(LinkError) as ctx:
            link.command("DISCONNECT", "x")
        self.assertEqual(ctx.exception.code, "invalid_session")
        self.assertTrue(link.connected)


class AutoWalkGatewayTests(unittest.TestCase):
    def test_setting_is_reported_and_switched(self):
        link, fw = make_link(AutoFirmware())
        self.assertEqual(link.status(OWNER)["autoWalk"],
                         {"enabled": False, "phase": "IDLE", "delayMs": 5000.0, "runMs": 10000.0})
        link.command("AUTOWALK", OWNER, level_on="1")
        self.assertTrue(fw.auto_on)
        self.assertTrue(link.status(OWNER)["autoWalk"]["enabled"])
        link.command("AUTOWALK", OWNER, level_on="0")
        self.assertFalse(fw.auto_on)

    def test_setting_never_arms_or_walks(self):
        link, fw = make_link(AutoFirmware())
        link.command("AUTOWALK", OWNER, level_on="1")
        self.assertFalse(fw.armed)
        self.assertFalse([c for c in fw.log if c.split()[0] in ("ARM", "WALK", "WALKHB")])

    def test_bad_value_and_old_firmware_are_refused(self):
        link, _ = make_link(AutoFirmware())
        with self.assertRaises(LinkError) as ctx:
            link.command("AUTOWALK", OWNER, level_on="yes")
        self.assertEqual(ctx.exception.code, "invalid_autowalk")
        old, fw = make_link()
        self.assertNotIn("autoWalk", old.status(OWNER))
        with self.assertRaises(LinkError) as ctx:
            old.command("AUTOWALK", OWNER, level_on="1")
        self.assertEqual(ctx.exception.code, "firmware_without_autowalk")
        self.assertNotIn("AUTOWALK ON", fw.log)

    def test_auto_run_is_not_this_pages_control_and_stop_still_works(self):
        link, fw = make_link(AutoFirmware())
        fw.armed, fw.ctrl, fw.walk, fw.lease_deadline = True, "AUTO", "FORWARD", fw.clock() + 100
        status = link.status(OWNER)
        self.assertEqual((status["armed"], status["controller"], status["youControl"]), (True, "AUTO", False))
        with self.assertRaises(LinkError):
            link.command("WALK", OWNER, "LEFT")
        link.command("STOP", None)
        self.assertFalse(fw.armed)


class AutoWalkFirmwareSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auto = (SKETCH / "auto_walk.ino").read_text(encoding="utf-8")
        cls.main = (SKETCH / "servoInitPose.ino").read_text(encoding="utf-8")

    def test_off_by_default_and_only_after_a_power_on_reset(self):
        self.assertIn('store.getBool("on", false)', self.auto)
        self.assertIn("autoWalkEnabled && esp_reset_reason() == ESP_RST_POWERON", self.auto)
        self.assertEqual(self.auto.count("AUTOWALK_WAITING;"), 1)  # armed only in autoWalkSetup()

    def test_run_is_bounded_and_ends_with_a_normal_stop(self):
        run = int(re.search(r"AUTOWALK_RUN_MS = (\d+);", self.auto).group(1))
        delay = int(re.search(r"AUTOWALK_DELAY_MS = (\d+);", self.auto).group(1))
        self.assertTrue(1000 <= run <= 30000)
        self.assertGreaterEqual(delay, 3000)
        self.assertIn('controlStop("autowalk_done")', self.auto)

    def test_uses_the_normal_arm_path_and_any_stop_or_arm_cancels(self):
        self.assertIn("controlArm(CTRL_AUTO)", self.auto)   # same checks as a person (homing, tilt, other controller)
        stop = self.main[self.main.index("void controlStop("):][:400]
        self.assertIn("autoWalkCancel();", stop)
        arm = self.main[self.main.index("const char *controlArm("):][:700]
        self.assertIn("if (source != CTRL_AUTO) autoWalkCancel();", arm)

    def test_switching_on_does_not_start_a_walk(self):
        setter = self.auto[self.auto.index("const char *autoWalkSet("):]
        setter = setter[:setter.index("void printAutoWalk")]
        self.assertNotIn("controlArm", setter)
        self.assertNotIn("AUTOWALK_WAITING;", setter)
        self.assertIn('if (on && armed) return "armed";', setter)

    def test_caps_and_loop_hooks(self):
        self.assertIn("AUTOWALK1", self.main[self.main.index('"CAPS"'):][:200])
        loop = self.main[self.main.index("void loop()"):]
        self.assertLess(loop.index("autoWalkUpdate();"), loop.index("updateLease();"))


if __name__ == "__main__":
    unittest.main()
