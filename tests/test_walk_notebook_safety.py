import ast
import html
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


NOTEBOOK = Path(__file__).resolve().parents[1] / "robot_dog_4way_walk.ipynb"


class FakeSerial:
    def __init__(self, is_open=True):
        self.is_open = is_open
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        self.is_open = False


class FakeOutput:
    def __init__(self):
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeButton:
    def __init__(self):
        self.disabled = False
        self.button_style = ""


def load_safety_callbacks(send_command):
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    cell = next(cell for cell in notebook["cells"] if cell.get("id") == "walk-controller")
    tree = ast.parse("".join(cell["source"]))
    callbacks = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {
            "stop_walking", "disconnect_board", "update_controls", "toggle_direction",
            "wait_for_settled", "heartbeat_loop",
        }
    ]
    namespace = {
        "esp": FakeSerial(),
        "movement_enabled": True,
        "active_direction": "FORWARD",
        "motion_state_unknown": False,
        "walk_status": SimpleNamespace(value="보행 중"),
        "port_status": SimpleNamespace(value="Connected"),
        "connect_button": FakeButton(),
        "disconnect_button": FakeButton(),
        "arm_button": FakeButton(),
        "disarm_button": FakeButton(),
        "walk_buttons": {name: FakeButton() for name in ("FORWARD", "BACKWARD", "LEFT", "RIGHT")},
        "stop_button": FakeButton(),
        "ui_output": FakeOutput(),
        "clear_output": lambda **_: None,
        "html": html,
        "time": SimpleNamespace(monotonic=time.monotonic, sleep=lambda _: None),
        "update_controls": lambda: None,
        "send_command": send_command,
        "start_heartbeat": lambda: namespace["heartbeat_starts"].append(namespace["active_direction"]),
        "heartbeat_starts": [],
        "HEARTBEAT_INTERVAL_S": 0.25,
        "print": lambda message: namespace["ui_output"].messages.append(message),
    }
    exec(compile(ast.Module(body=callbacks, type_ignores=[]), str(NOTEBOOK), "exec"), namespace)
    return namespace


class WalkNotebookSafetyTests(unittest.TestCase):
    def test_stop_failure_disarms_ui_but_keeps_serial_open_for_retry(self):
        commands = []

        def send_command(command):
            commands.append(command)
            raise TimeoutError("no response")

        state = load_safety_callbacks(send_command)
        serial_connection = state["esp"]

        with self.assertRaisesRegex(RuntimeError, "시리얼 연결을 유지합니다"):
            state["stop_walking"]()

        self.assertEqual(commands, ["WALK STOP"])
        self.assertFalse(state["movement_enabled"])
        self.assertIsNone(state["active_direction"])
        self.assertTrue(state["motion_state_unknown"])
        self.assertTrue(serial_connection.is_open)
        self.assertEqual(state["walk_status"].value, "정지 확인 실패 - 로봇 상태 불명, 전원 차단 필요")
        self.assertTrue(state["arm_button"].disabled)
        self.assertFalse(state["stop_button"].disabled)

    def test_disconnect_does_not_close_port_when_stop_is_unconfirmed(self):
        def send_command(_):
            raise TimeoutError("no response")

        state = load_safety_callbacks(send_command)
        serial_connection = state["esp"]
        state["disconnect_board"](None)

        self.assertIs(state["esp"], serial_connection)
        self.assertTrue(serial_connection.is_open)
        self.assertEqual(serial_connection.close_calls, 0)
        self.assertFalse(state["movement_enabled"])
        self.assertIn("연결 유지", state["port_status"].value)
        self.assertIn("정지 응답을 받지 못해 연결을 유지합니다", state["ui_output"].messages[-1])

    def test_disconnect_closes_port_only_after_stop_acknowledgement(self):
        commands = []

        def send_command(command):
            commands.append(command)
            return "SETTLED 1" if command == "SETTLED" else "OK WALK STOPPED"

        state = load_safety_callbacks(send_command)
        serial_connection = state["esp"]
        state["disconnect_board"](None)

        self.assertEqual(commands, ["WALK STOP", "SETTLED"])
        self.assertFalse(serial_connection.is_open)
        self.assertEqual(serial_connection.close_calls, 1)
        self.assertIsNone(state["esp"])
        self.assertFalse(state["movement_enabled"])
        self.assertTrue(state["motion_state_unknown"])
        self.assertTrue(state["arm_button"].disabled)
        self.assertIn("STOP 확인 후 연결을 해제했습니다", state["ui_output"].messages[-1])

    def test_disconnect_waits_for_settled_pose_before_closing(self):
        commands = []
        settled_replies = iter(["SETTLED 0", "SETTLED 0", "SETTLED 1"])

        def send_command(command):
            commands.append(command)
            if command == "SETTLED":
                self.assertTrue(serial_connection.is_open, "port closed before pose settled")
                return next(settled_replies)
            return "OK WALK STOPPED"

        state = load_safety_callbacks(send_command)
        serial_connection = state["esp"]
        state["disconnect_board"](None)

        self.assertEqual(commands, ["WALK STOP", "SETTLED", "SETTLED", "SETTLED"])
        self.assertFalse(serial_connection.is_open)
        self.assertIn("중립 자세 도착과 저장도 확인했습니다", state["ui_output"].messages[-1])

    def test_disconnect_warns_when_settle_is_unconfirmed(self):
        def send_command(command):
            if command == "SETTLED":
                raise RuntimeError("ERR unknown_command")
            return "OK WALK STOPPED"

        state = load_safety_callbacks(send_command)
        serial_connection = state["esp"]
        state["disconnect_board"](None)

        self.assertFalse(serial_connection.is_open)
        self.assertIn("중립 자세 도착/저장은 확인하지 못했습니다", state["ui_output"].messages[-1])

    def test_arm_stays_disabled_until_stop_state_is_confirmed(self):
        state = load_safety_callbacks(lambda _: "OK WALK STOPPED")
        state["motion_state_unknown"] = True
        state["update_controls"]()

        self.assertTrue(state["arm_button"].disabled)
        self.assertFalse(state["stop_button"].disabled)

        state["stop_walking"]()
        state["update_controls"]()
        self.assertFalse(state["motion_state_unknown"])
        self.assertFalse(state["arm_button"].disabled)

    def test_stop_without_connection_does_not_claim_success(self):
        state = load_safety_callbacks(lambda _: self.fail("must not send without connection"))
        state["esp"] = None

        with self.assertRaisesRegex(RuntimeError, "연결이 없어 STOP 응답을 확인할 수 없습니다"):
            state["stop_walking"]()

        self.assertFalse(state["movement_enabled"])
        self.assertEqual(state["walk_status"].value, "정지 확인 불가 - 로봇 상태 불명")

    def test_move_response_failure_sends_stop_and_stays_disarmed(self):
        commands = []

        def send_command(command, require_motion=False):
            commands.append(command)
            if command == "WALK RIGHT":
                raise TimeoutError("move response lost")
            return "OK WALK STOPPED"

        state = load_safety_callbacks(send_command)
        with self.assertRaisesRegex(RuntimeError, "STOP을 확인했습니다"):
            state["toggle_direction"]("RIGHT")

        self.assertEqual(commands, ["WALK RIGHT", "WALK STOP"])
        self.assertFalse(state["movement_enabled"])
        self.assertFalse(state["motion_state_unknown"])
        self.assertIsNone(state["active_direction"])

    def test_move_and_stop_response_failures_latch_unknown_state(self):
        commands = []

        def send_command(command, require_motion=False):
            commands.append(command)
            raise TimeoutError("response lost")

        state = load_safety_callbacks(send_command)
        with self.assertRaisesRegex(RuntimeError, "전원을 차단하세요"):
            state["toggle_direction"]("RIGHT")

        self.assertEqual(commands, ["WALK RIGHT", "WALK STOP"])
        self.assertFalse(state["movement_enabled"])
        self.assertTrue(state["motion_state_unknown"])
        self.assertTrue(state["esp"].is_open)
        self.assertEqual(state["walk_status"].value, "정지 확인 실패 - 로봇 상태 불명, 전원 차단 필요")


    def test_successful_walk_starts_heartbeat(self):
        state = load_safety_callbacks(lambda command, require_motion=False: f"OK {command}")
        state["active_direction"] = None
        state["toggle_direction"]("LEFT")
        self.assertEqual(state["active_direction"], "LEFT")
        self.assertEqual(state["heartbeat_starts"], ["LEFT"])

    def test_heartbeat_runs_while_walking_and_stops_with_direction(self):
        commands = []

        def send_command(command):
            commands.append(command)
            if len(commands) == 3:
                state["active_direction"] = None  # user pressed STOP
            return "OK HB"

        state = load_safety_callbacks(send_command)
        state["heartbeat_loop"](state["esp"])
        self.assertEqual(commands, ["HB", "HB", "HB"])
        self.assertTrue(state["movement_enabled"])  # a normal stop is not a failure

    def test_heartbeat_failure_disarms_ui_and_latches_unknown_state(self):
        def send_command(command):
            raise RuntimeError("ERR not_armed")

        state = load_safety_callbacks(send_command)
        state["heartbeat_loop"](state["esp"])
        self.assertFalse(state["movement_enabled"])
        self.assertIsNone(state["active_direction"])
        self.assertTrue(state["motion_state_unknown"])
        self.assertIn("하트비트 실패", state["walk_status"].value)

    def test_heartbeat_exits_when_connection_changes(self):
        state = load_safety_callbacks(lambda _: self.fail("must not send on an old connection"))
        state["heartbeat_loop"](FakeSerial())
        self.assertTrue(state["movement_enabled"])

    def test_connect_requires_lease_firmware(self):
        notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        cell = next(cell for cell in notebook["cells"] if cell.get("id") == "walk-controller")
        self.assertIn('{"WALK4", "LEASE1"} <= set(capabilities.split())', "".join(cell["source"]))


if __name__ == "__main__":
    unittest.main()
