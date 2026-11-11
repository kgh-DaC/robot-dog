# 루프 시작 방법

## 먼저 확인 (한 번만)

1. **jq 설치.** Ralph Loop의 반복 훅(`stop-hook.sh`)은 `jq`가 있어야 동작한다. 없으면 명령은 실행되지만 한 회차 뒤에 반복되지 않는다.
   - 설치: 터미널에서 `winget install jqlang.jq` → Claude Code(VS Code 포함)를 완전히 껐다가 다시 켠다.
   - 확인: Claude Code 입력창에 `! jq --version` → 버전이 나오면 된다.
2. **플러그인 확인.** `/plugin`에서 `ralph-loop`이 켜져 있어야 한다.

## 시작 명령

명령 이름은 `/ralph-loop`이 아니라 **`/ralph-loop:ralph-loop`** 이다. 플러그인이 제공하는 명령은 `플러그인이름:명령이름` 형식이라서, `/ralph-loop`만 입력하면 "Unknown command"가 나온다. (플러그인 도움말에는 `/ralph-loop`으로 적혀 있지만 이 버전에서는 맞지 않는다.)

아래 한 줄을 Claude Code 입력창에 그대로 붙여 넣는다.

```
/ralph-loop:ralph-loop "robot-dog-loop 스킬의 한 회차 절차를 그대로 따른다. _workspace/loop/BACKLOG.md 에서 첫 미완료 항목 하나만 처리하고 결과를 _workspace/loop/LOG.md 에 덧붙인다. 펌웨어 업로드와 직렬 포트 접근과 로봇 명령은 하지 않는다. 미완료 항목이 없고 테스트가 전부 통과할 때만 완료 약속을 출력한다." --max-iterations 12 --completion-promise "ROBOT LOOP DONE"
```

- 프롬프트는 셸 명령의 인자로 전달되므로 프롬프트 안에 큰따옴표, 작은따옴표, 백틱, `$`, `<`, `>`, 줄바꿈을 넣지 않는다. 자세한 규칙은 프롬프트가 아니라 `robot-dog-loop` 스킬에 둔다.
- `--max-iterations`는 작업 목록 항목 수의 두 배 정도로 잡는다.
- 완료 약속은 Claude가 `<promise>ROBOT LOOP DONE</promise>`를 출력하는 것이다(스킬에 적혀 있다).

## 중단과 확인

- 중단: `/ralph-loop:cancel-ralph`
- 루프 상태 파일: `.claude/ralph-loop.local.md` (있으면 루프가 도는 중이다. 명령으로 중단이 안 되면 이 파일을 지운다.)
- 진행 확인: `_workspace/loop/BACKLOG.md`의 체크 상태와 `_workspace/loop/LOG.md`
- 작업 추가: `BACKLOG.md`의 "작업"에 항목을 추가한다(완료 기준 필수). 루프가 도는 중에 추가해도 다음 회차에 반영된다.
- 루프가 도는 동안에는 `.claude/hooks/loop_guard.py`가 실물 관련 셸 명령을 막는다. 사용자가 직접 실물 작업을 하려면 먼저 루프를 중단한다.

## 루프 없이 한 회차만

`/robot-dog-loop`을 입력하면 한 항목만 처리하고 멈춘다. jq가 없어도 된다.
