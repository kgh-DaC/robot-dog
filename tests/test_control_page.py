"""Runs the shared control page script in Node with a fake DOM (tests/page_harness.js)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "esp32-robot-dog-code-master" / "software" / "robot_control_web" / "index.html"
HARNESS = Path(__file__).resolve().parent / "page_harness.js"
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not installed")
class ControlPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, str(HARNESS), str(PAGE)], capture_output=True, text=True,
                             encoding="utf-8", timeout=60, check=True)
        cls.report = json.loads(out.stdout)

    def test_heartbeat_sent_while_state_is_fresh(self):
        self.assertGreaterEqual(self.report["freshHb"], 4)
        self.assertEqual(self.report["fresh"]["armed"], "활성 (이 페이지)")
        self.assertFalse(self.report["fresh"]["fwd"])

    def test_no_heartbeat_when_status_requests_fail_or_hang(self):
        for mode in ("fail", "hang"):
            with self.subTest(mode=mode):
                self.assertEqual(self.report[mode]["hbAfterStale"], 0)

    def test_stale_state_is_shown_as_unknown_and_controls_disabled(self):
        for mode in ("fail", "hang"):
            with self.subTest(mode=mode):
                view = self.report[mode]["view"]
                self.assertEqual(view["armed"], "알 수 없음")
                self.assertEqual(view["walk"], "알 수 없음")
                self.assertNotIn("90", view["joints"])
                self.assertNotIn("undefined", json.dumps(view, ensure_ascii=False))
                self.assertTrue(view["arm"])
                self.assertTrue(view["fwd"])
                self.assertIn("연결 끊김", view["link"])

    def test_drawing_failure_does_not_freeze_the_status_text(self):
        # The model and comparison canvases throw; the status text must still follow the state.
        fresh, stale = self.report["drawFail"]["fresh"], self.report["drawFail"]["stale"]
        self.assertEqual(fresh["armed"], "활성 (이 페이지)")
        self.assertEqual(fresh["walk"], "왼쪽")
        self.assertEqual(stale["armed"], "알 수 없음")
        self.assertEqual(stale["walk"], "알 수 없음")
        self.assertIn("연결 끊김", stale["link"])
        self.assertTrue(stale["arm"])                        # controls stay disabled
        self.assertTrue(stale["fwd"])
        self.assertEqual(stale["sensor"], "센서 알 수 없음")   # the cards after the failing one still update
        self.assertEqual(stale["roll"], "알 수 없음")
        self.assertNotIn("<td>90</td>", stale["joints"])
        self.assertEqual(self.report["drawFail"]["staleTrim"], "")


@unittest.skipUnless(NODE, "node is not installed")
class LevelMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, str(HARNESS), str(PAGE)], capture_output=True, text=True,
                             encoding="utf-8", timeout=60, check=True)
        cls.report = json.loads(out.stdout)

    def test_mapped_values_and_compensation_are_shown(self):
        view = self.report["fresh"]
        self.assertEqual((view["roll"], view["pitch"]), ("+2.5°", "+1.3°"))
        self.assertEqual(view["rollDir"], "오른쪽이 낮음")
        self.assertEqual((view["rollComp"], view["oLF"]), ("+1.5°", "-2.0°"))
        self.assertEqual(view["level"], "수평 유지 동작 중")
        self.assertEqual(view["toggle"], "수평 유지 끄기")

    def test_chart_is_drawn(self):
        self.assertGreater(self.report["chartLines"], 10)

    def test_toggle_sends_level_command(self):
        self.assertEqual(len(self.report["levelPost"]), 1)
        self.assertIn("cmd=LEVEL&on=0", self.report["levelPost"][0])

    def test_unmapped_sensor_axes_are_labelled_not_called_roll(self):
        view = self.report["unmapped"]
        self.assertEqual(view["map"], "축 설정 필요")
        self.assertEqual(view["rollLabel"], "센서 X축 (축 미설정)")
        self.assertEqual(view["roll"], "+2.5°")      # raw X axis
        self.assertEqual(view["rollDir"].strip(), "")  # no body direction claimed

    def test_stale_level_values_are_unknown(self):
        for mode in ("fail", "hang"):
            with self.subTest(mode=mode):
                view = self.report[mode]["view"]
                self.assertEqual((view["roll"], view["pitch"]), ("알 수 없음", "알 수 없음"))
                self.assertEqual((view["rollComp"], view["oLF"]), ("-", "-"))
                self.assertEqual(view["level"], "수평 유지 알 수 없음")


def _dist(p, q):
    return sum((a - b) ** 2 for a, b in zip(p, q)) ** 0.5


@unittest.skipUnless(NODE, "node is not installed")
class JointModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, str(HARNESS), str(PAGE)], capture_output=True, text=True,
                             encoding="utf-8", timeout=60, check=True)
        cls.report = json.loads(out.stdout)
        cls.fk = cls.report["fk"]
        cls.geo = cls.fk["geo"]

    def test_neutral_pose_puts_feet_under_the_shoulders(self):
        depth = (self.geo["L2"] + self.geo["L3"]) * 0.5 ** 0.5
        for leg in self.fk["neutral"]:
            self.assertAlmostEqual(leg["foot"][0], leg["hip"][0], places=6)
            self.assertAlmostEqual(leg["hip"][1] - leg["foot"][1], (self.geo["L2"] - self.geo["L3"]) * 0.5 ** 0.5, places=6)
            self.assertAlmostEqual(leg["foot"][2], -depth, places=6)

    def test_link_lengths_are_preserved(self):
        poses = self.fk["neutral"] + self.fk["gammaSweep"] + [self.fk["straight"], self.fk["betaUp"],
                                                              self.fk["alphaUp"]["LF"], self.fk["alphaUp"]["RF"]]
        for leg in poses:
            self.assertAlmostEqual(_dist(leg["hip"], leg["knee"]), self.geo["L2"], places=6)
            self.assertAlmostEqual(_dist(leg["knee"], leg["foot"]), self.geo["L3"], places=6)

    def test_shoulders_are_80_mm_apart_and_legs_mirror_left_right(self):
        lf, rf, lh, rh = self.fk["neutral"]
        self.assertAlmostEqual(rf["hip"][0] - lf["hip"][0], 80.0)
        for left, right in ((lf, rf), (lh, rh)):
            for part in ("hip", "knee", "foot"):
                self.assertAlmostEqual(left[part][0], -right[part][0], places=6)
                self.assertAlmostEqual(left[part][1], right[part][1], places=6)
                self.assertAlmostEqual(left[part][2], right[part][2], places=6)

    def test_all_knees_bend_backward_and_larger_beta_moves_the_knee_back(self):
        for leg in self.fk["neutral"]:
            self.assertLess(leg["knee"][1], leg["hip"][1])
        self.assertLess(self.fk["betaUp"]["knee"][1], self.fk["neutral"][1]["knee"][1])

    def test_larger_alpha_moves_the_foot_inward_without_changing_leg_length(self):
        lf, rf, neutral = self.fk["alphaUp"]["LF"], self.fk["alphaUp"]["RF"], self.fk["neutral"][0]
        self.assertGreater(lf["foot"][0], lf["hip"][0])   # left leg moves toward +x (inward)
        self.assertLess(rf["foot"][0], rf["hip"][0])      # right leg moves toward -x (inward)
        self.assertAlmostEqual(_dist(lf["hip"], lf["foot"]), _dist(neutral["hip"], neutral["foot"]), places=6)
        self.assertAlmostEqual(lf["foot"][1], neutral["foot"][1], places=6)

    def test_larger_gamma_makes_the_leg_longer(self):
        lengths = [_dist(leg["hip"], leg["foot"]) for leg in self.fk["gammaSweep"]]
        self.assertEqual(lengths, sorted(lengths))
        self.assertAlmostEqual(_dist(self.fk["straight"]["hip"], self.fk["straight"]["foot"]),
                               self.geo["L2"] + self.geo["L3"], places=6)

    def test_level_offset_is_added_only_when_requested(self):
        self.assertEqual(self.fk["withCorr"]["LF"], [90, 45, 88])
        self.assertEqual(self.fk["withCorr"]["RH"], [90, 45, 92])
        self.assertEqual(self.fk["noCorr"]["LF"], [90, 45, 90])
        self.assertIsNone(self.fk["missing"]["LF"])
        self.assertIsNone(self.fk["missing"]["RF"])

    def test_stand_trim_is_added_only_when_reported_and_requested(self):
        self.assertEqual(self.fk["withTrim"]["LF"], [90, 45, 97])     # 90 - 2 (level) + 9 (trim)
        self.assertEqual(self.fk["withTrim"]["RH"], [90, 45, 83])     # 90 + 2 - 9
        self.assertEqual(self.fk["trimNoCorr"]["LF"], [90, 45, 90])
        self.assertEqual(self.fk["badTrim"]["LF"], [90, 45, 88])      # incomplete trim = unknown, not used
        model = self.report["model"]
        self.assertIn("알 수 없음", model["trimNoteOld"])
        self.assertIn("포함되지 않습니다", model["trimNoteOld"])
        self.assertIn("LF +9.0°", model["trimNoteNew"])
        self.assertIn("RH -9.0°", model["trimNoteNew"])
        self.assertIn("모형에 포함됨", model["trimNoteNew"])
        self.assertIn("넣지 않음", model["trimNoteOff"])
        self.assertIn("→ 97.0", model["jointsWithTrim"])
        for text in (model["trimNoteOld"], model["trimNoteNew"], model["trimNoteOff"], model["jointsWithTrim"]):
            self.assertNotIn("NaN", text)
            self.assertNotIn("undefined", text)

    def test_missing_level_offset_is_unknown_not_zero(self):
        model = self.report["model"]
        self.assertNotIn("수평 보정: 알 수 없음", model["levelNoteKnown"])
        self.assertIn("수평 보정: 알 수 없음", model["levelNoteMissing"])
        self.assertNotIn("NaN", model["levelNoteMissing"])
        self.assertEqual(self.fk["noLevel"]["LF"], [90, 45, 99])      # level not added (trim 9 only), flagged above
        self.assertEqual(self.fk["badLevel"]["LF"], [90, 45, 99])     # one bad value = all unknown
        self.assertEqual(self.fk["withCorr"]["LF"], [90, 45, 88])     # known: unchanged

    def test_model_and_table_show_all_legs(self):
        self.assertGreater(self.report["model"]["lines"], 20)
        for html in (self.report["model"]["joints"], self.report["model"]["outOfRange"]):
            for leg in ("LF", "RF", "LH", "RH"):
                self.assertIn("<th>" + leg + "</th>", html)
            self.assertNotIn("NaN", html)
            self.assertNotIn("undefined", html)
        self.assertIn("170", self.report["model"]["outOfRange"])

    def test_stale_model_table_has_no_values(self):
        for mode in ("fail", "hang"):
            self.assertEqual(self.report[mode]["view"]["joints"].count("<td>-</td>"), 20)


@unittest.skipUnless(NODE, "node is not installed")
class LevelCompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, str(HARNESS), str(PAGE)], capture_output=True, text=True,
                             encoding="utf-8", timeout=60, check=True)
        cls.report = json.loads(out.stdout)

    def test_empty_and_one_sided_records_do_not_invent_a_difference(self):
        self.assertEqual(self.report["cmpEmpty"]["summary"], "아직 기록이 없습니다.")
        self.assertIn("활성화하면 기록합니다", self.report["cmpEmpty"]["state"])
        self.assertIn("켜짐 구간이 더 필요합니다", self.report["cmpOffOnly"]["summary"])
        self.assertIn("켜짐 0개", self.report["cmpOffOnly"]["summary"])

    def test_enabled_but_inactive_leveling_is_not_counted_as_on(self):
        self.assertIn("켜짐 0개", self.report["cmpInactive"]["summary"])
        self.assertIn("아직 동작하지 않습니다", self.report["cmpInactive"]["state"])

    def test_statistics_and_reduction_for_off_and_on_segments(self):
        view = self.report["cmpBoth"]
        self.assertIn("앞으로 보행 기준", view["summary"])
        self.assertIn("좌우 RMS 3.00° → 1.00° (67% 감소)", view["summary"])
        self.assertIn("앞뒤 RMS 2.00° → 1.00° (50% 감소)", view["summary"])
        self.assertIn("기록 중: 수평 유지 켜짐 · 앞으로 보행", view["state"])
        self.assertIn("<td>-2.0°</td>", view["body"])       # pitch mean while OFF keeps its sign
        self.assertGreater(self.report["cmpLines"]["roll"], 20)
        self.assertGreater(self.report["cmpLines"]["pitch"], 20)

    def test_csv_export_has_header_and_rows(self):
        head = self.report["cmpCsvHead"]
        self.assertEqual(head[0], "condition,level,elapsed_s,roll_deg,pitch_deg,used_in_stats")
        self.assertEqual(head[1], "FORWARD,off,0.00,3,-2,0")

    def test_different_motions_are_not_compared(self):
        view = self.report["cmpOtherCond"]
        self.assertIn("서 있기", view["summary"])
        self.assertIn("켜짐 구간이 더 필요합니다", view["summary"])
        self.assertNotIn("감소", view["summary"])

    def test_unmapped_sensor_is_not_recorded(self):
        self.assertIn("축 설정이 필요합니다", self.report["cmpUnmapped"]["state"])

    def test_no_nan_and_no_commands_from_the_comparison(self):
        for key in ("cmpEmpty", "cmpOffOnly", "cmpInactive", "cmpBoth", "cmpOtherCond", "cmpUnmapped"):
            text = json.dumps(self.report[key], ensure_ascii=False)
            self.assertNotIn("NaN", text)
            self.assertNotIn("undefined", text)
        self.assertEqual(self.report["cmpSentCommands"], 0)

    def test_stats_function_with_mixed_sign_samples(self):
        # a = [3, -1, -4, 2], b = [-2, 2, -6, 6] at t = 0, 250, 500, 750 ms
        a, b = self.report["cmpDirect"]["a"], self.report["cmpDirect"]["b"]
        self.assertEqual(a["n"], 4)
        self.assertAlmostEqual(a["mean"], 0.0)              # signs cancel in the mean ...
        self.assertAlmostEqual(a["meanAbs"], 2.5)           # ... but not in the absolute mean
        self.assertAlmostEqual(a["rms"], (30 / 4) ** 0.5)
        self.assertAlmostEqual(a["maxAbs"], 4.0)            # largest magnitude is the negative sample
        self.assertAlmostEqual(a["p2p"], 7.0)
        self.assertAlmostEqual(a["sec"], 0.75)
        self.assertAlmostEqual(b["mean"], 0.0)
        self.assertAlmostEqual(b["meanAbs"], 4.0)
        self.assertAlmostEqual(b["rms"], (80 / 4) ** 0.5)
        self.assertAlmostEqual(b["maxAbs"], 6.0)
        self.assertAlmostEqual(b["p2p"], 12.0)

    def test_stats_function_with_no_or_one_sample(self):
        self.assertIsNone(self.report["cmpDirect"]["empty"])
        one = self.report["cmpDirect"]["single"]
        self.assertEqual(one["n"], 1)
        self.assertAlmostEqual(one["mean"], -1.5)
        self.assertAlmostEqual(one["meanAbs"], 1.5)
        self.assertAlmostEqual(one["rms"], 1.5)
        self.assertAlmostEqual(one["p2p"], 0.0)
        self.assertAlmostEqual(one["sec"], 0.0)

    def test_first_second_after_a_switch_is_excluded_from_statistics(self):
        rows, view = self.report["cmpSettle"]["rows"], self.report["cmpSettle"]["view"]
        skipped = [r for r in rows if not r["used"]]
        used = [r for r in rows if r["used"]]
        self.assertGreaterEqual(len(skipped), 2)
        self.assertGreaterEqual(len(used), 4)
        self.assertEqual(rows[0]["sec"], 0.0)
        for r in rows:
            self.assertEqual(bool(r["used"]), r["sec"] >= 1.0, r)
        self.assertIn("꺼짐 %d개" % len(used), view["summary"])   # only the used samples are counted

    def test_segment_is_limited_to_the_last_60_seconds(self):
        rows, view = self.report["cmpWindow"]["rows"], self.report["cmpWindow"]["view"]
        self.assertGreater(len(rows), len(self.report["cmpSettle"]["rows"]))
        self.assertGreater(rows[-1]["sec"], 59.0)           # 73 s were recorded ...
        self.assertLessEqual(rows[-1]["sec"], 60.0)         # ... but only 60 s are kept
        self.assertTrue(all(r["used"] for r in rows))       # the settle second has scrolled out
        self.assertIn("꺼짐 %d개" % len(rows), view["summary"])
        self.assertIn("<td>%.1f초</td>" % rows[-1]["sec"], view["body"])


@unittest.skipUnless(NODE, "node is not installed")
class ConnectionModeTests(unittest.TestCase):
    """USB gateway / phone hotspot switching (page served by the gateway or by the robot)."""

    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, str(HARNESS), str(PAGE), "gateway"], capture_output=True, text=True,
                             encoding="utf-8", timeout=60, check=True)
        cls.g = json.loads(out.stdout)
        out = subprocess.run([NODE, str(HARNESS), str(PAGE)], capture_output=True, text=True,
                             encoding="utf-8", timeout=60, check=True)
        cls.robot = json.loads(out.stdout)["robotPage"]

    def test_gateway_page_starts_on_usb_with_relative_urls(self):
        v = self.g["initial"]
        self.assertTrue(v["url"].startswith("api/status?"))
        self.assertEqual(v["target"], "USB 게이트웨이 → COM14")
        self.assertEqual(v["addr"], "172.20.10.3")             # suggested from the robot's hotspot IP
        self.assertIn("172.20.10.3", v["wifiInfo"])

    def test_hotspot_settings_shown_over_usb_and_password_not_kept(self):
        v = self.g["initial"]
        self.assertFalse(v["hsSetHidden"])
        self.assertEqual(v["hsState"], "연결됨 · 민수의 iPhone · 172.20.10.3 (robotdog.local)")
        ws = self.g["wifiSet"]
        self.assertEqual(len(ws["bodies"]), 1)
        self.assertIn("cmd=WIFI_SET&ssid=%EB%AF%BC%EC%88%98%EC%9D%98+iPhone&pass=hotspot123", ws["bodies"][0])
        self.assertEqual(ws["url"], "api/command")              # only through the USB gateway
        self.assertEqual(ws["passAfter"], "")                   # field cleared after sending
        self.assertEqual(len(self.g["wifiClear"]), 1)
        self.assertNotIn("hotspot123", json.dumps(self.g["hot"]))

    def test_switch_refused_while_this_page_controls(self):
        v = self.g["refusedArmed"]
        self.assertIn("바꿀 수 없습니다", v["msg"])
        self.assertTrue(v["url"].startswith("api/status?"))
        self.assertTrue(v["target"].startswith("USB"))

    def test_invalid_address_is_rejected(self):
        v = self.g["badAddr"]
        self.assertIn("올바르지 않습니다", v["msg"])
        self.assertTrue(v["url"].startswith("api/status?"))

    def test_late_reply_from_old_target_is_ignored(self):
        v = self.g["lateUsbReply"]
        self.assertEqual(v["link"], "연결 확인 중")             # not "USB 게이트웨이 연결됨"
        self.assertTrue(v["hsSetHidden"])

    def test_hotspot_mode_calls_the_robot_directly_and_is_saved(self):
        v = self.g["hot"]
        self.assertTrue(v["url"].startswith("http://172.20.10.3/api/status?"))   # "http://" and "/" stripped from input
        self.assertEqual(v["link"], "ESP32 Wi-Fi 연결됨")
        self.assertTrue(v["hsSetHidden"])
        self.assertEqual(json.loads(v["stored"]), {"mode": "hot", "host": "172.20.10.3"})
        self.assertEqual(self.g["armUrl"], "http://172.20.10.3/api/command")

    def test_switch_back_to_usb_and_reload_restores_mode(self):
        v = self.g["back"]
        self.assertTrue(v["url"].startswith("api/status?"))
        self.assertEqual(json.loads(v["stored"])["mode"], "usb")
        r = self.g["reloaded"]
        self.assertTrue(r["url"].startswith("http://robotdog.local/api/status?"))
        self.assertEqual(r["addr"], "robotdog.local")

    def test_switch_refused_while_a_command_waits_for_its_reply(self):
        self.assertIn("명령을 처리하는 중", self.g["busy"]["msg"])
        self.assertTrue(self.g["busy"]["url"].startswith("api/status?"))
        self.assertTrue(self.g["afterBusy"]["target"].startswith("USB"))

    def test_gateway_on_allowed_origin_has_no_cors_warning(self):
        self.assertNotIn("주의", self.g["hot"]["note"])

    def test_robot_served_page_only_explains_usb(self):
        self.assertTrue(self.robot["initial"]["addrRowHidden"])
        v = self.robot["usb"]
        self.assertIn("http://127.0.0.1:8787", v["msg"])
        self.assertTrue(v["url"].startswith("api/status?"))
        self.assertEqual(v["link"], "ESP32 Wi-Fi 연결됨")


class EmbeddedPageTests(unittest.TestCase):
    def test_firmware_header_matches_page(self):
        import sys
        sys.path.insert(0, str(PAGE.parent))
        import embed_page
        header = embed_page.TARGET.read_text(encoding="utf-8")
        self.assertEqual(header, embed_page.build_header(PAGE.read_text(encoding="utf-8")),
                         "web_page.h is stale: run software/robot_control_web/embed_page.py")


if __name__ == "__main__":
    unittest.main()
