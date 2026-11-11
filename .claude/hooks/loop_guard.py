"""PreToolUse guard for the robot dog loop harness.

While a Ralph loop is active (the plugin's state file exists) nobody is
watching each step, so shell commands that could move the real robot or
re-flash it are denied. Outside a loop this script allows everything.

Input: hook JSON on stdin. Output: a deny decision as JSON, or nothing.
"""
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
STATE_NAME = ".ralph-loop.local.md"

# (pattern, reason) - matched case-insensitively against the shell command.
BLOCKED = [
    (r"esptool", "펌웨어 플래시 도구(esptool)"),
    (r"arduino-cli(\.exe)?\b[^|;&]*\bupload\b", "펌웨어 업로드(arduino-cli upload)"),
    (r"arduino-cli(\.exe)?\b[^|;&]*\s(-u|--upload)\b", "컴파일 후 업로드(arduino-cli compile --upload)"),
    (r"\bCOM\d+\b", "직렬 포트(COMn) 접근"),
    (r"serial\.Serial|\bpyserial\b|\bminiterm\b", "직렬 포트 접근(pyserial)"),
    (r"usb_gateway\.py", "USB 게이트웨이 실행·재시작"),
    (r"api/command", "로봇 명령 엔드포인트(api/command) 호출"),
    (r"192\.168\.4\.1", "ESP32 핫스팟 주소 접근"),
]


def loop_active(cwd: str) -> bool:
    candidates = [PROJECT / ".claude" / STATE_NAME]
    if cwd:
        here = Path(cwd)
        candidates += [p / ".claude" / STATE_NAME for p in (here, *here.parents)]
    return any(p.is_file() for p in candidates)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if data.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = str((data.get("tool_input") or {}).get("command") or "")
    if not command or not loop_active(str(data.get("cwd") or "")):
        return 0
    for pattern, reason in BLOCKED:
        if re.search(pattern, command, re.IGNORECASE):
            message = (
                "루프 실행 중 차단: " + reason + ". 실물 로봇 동작·펌웨어 업로드·직렬 포트 작업은 "
                "루프에서 하지 않는다. _workspace/loop/BACKLOG.md의 '사용자 확인 필요'에 적고 다음 항목으로 넘어간다. "
                "(사용자가 /cancel-ralph로 루프를 끝낸 뒤 직접 승인하면 할 수 있다.)"
            )
            json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": message}},
                      sys.stdout, ensure_ascii=True)
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
