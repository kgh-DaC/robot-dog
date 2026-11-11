# 루프 시작 방법

아래 명령을 Claude Code 입력창에 그대로 붙여 넣는다. 횟수 상한(`--max-iterations`)은 작업 목록 항목 수의 두 배 정도로 잡는다.

```
/ralph-loop "robot-dog-loop 스킬의 '한 회차의 절차'를 그대로 따른다. _workspace/loop/BACKLOG.md에서 첫 미완료 항목 하나만 처리하고, 검증 결과를 _workspace/loop/LOG.md에 덧붙인다. 펌웨어 업로드, 직렬 포트 접근, 로봇 명령은 하지 않는다(훅이 막으면 우회하지 말고 사용자 확인 필요에 적는다). 미완료 항목이 하나도 없고 python -m unittest discover tests가 전부 통과할 때만 <promise>ROBOT LOOP DONE</promise>를 출력한다." --max-iterations 12 --completion-promise "ROBOT LOOP DONE"
```

- 중단: `/cancel-ralph`
- 진행 확인: `_workspace/loop/BACKLOG.md`의 체크 상태와 `_workspace/loop/LOG.md`
- 작업 추가: `BACKLOG.md`의 "작업"에 항목을 추가한다(완료 기준 필수). 루프가 도는 중에 추가해도 다음 회차에 반영된다.
- 루프가 도는 동안에는 `.claude/hooks/loop_guard.py`가 실물 관련 셸 명령을 막는다. 사용자가 직접 실물 작업을 하려면 먼저 루프를 중단한다.
