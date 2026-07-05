# darkarchon 하드닝 설계

> 2026-07-05. 상태 감지 · dispatch 레이스 · 완료 프로토콜 · IO 취약성 5개 약점을
> 하나의 정합적 설계로 통합. 이 문서가 구현의 단일 출처.

## 배경 — 고치려는 약점

현행 darkarchon 은 코어 루프가 안정적이지만, 가장 자주 실행되는 판정 로직이 가장
깨지기 쉬운 신호원(Claude Code TUI 화면 스크래핑) 위에 서 있다.

1. **상태 감지 사본 divergence** — busy/idle 판정 로직이 3벌로 갈라져 있다:
   `dispatch-safe.sh`(셸 정규식 `CLAUDE_ACTIVE_PATTERN`), `check-worker-state.sh`
   (셸 정규식 `✽` 글리프 요구 — 이미 옛 패턴), `detectors/claude.py`(테스트된
   Python, observer 측). 셸 두 사본은 서로, 그리고 Python detector 와 어긋난다.
   관측된 실버그: `check-worker-state.sh` 의 `✽` 요구는 글리프가 다르면 busy 를
   idle 로 오판; 셸 쪽 `still running` 매칭은 "N shells still running"(실제 idle)을
   busy 로 오판.
2. **Dispatch TOCTOU** — busy 체크와 실제 dispatch 사이에 락이 없어, 두
   orchestrator 가 동시에 체크를 통과하면 한 워커에 이중 dispatch.
3. **완료 프로토콜이 LLM 순응 의존** — "Read p → Write r → print DONE" 을 워커가
   안 지키면 `TASK_TIMEOUT=300s` 벽에 부딪혀야만 실패. README 의 "몇 시간짜리
   독립 작업" 유스케이스와 300초 기본값이 모순.
4. **`/tmp/ee` 취약성** — 멀티유저 머신에서 프롬프트 노출(권한 미제한), 재부팅 소실.

## 결정된 제약 (brainstorming Q&A)

- **워커 실행 모델: TUI 유지.** 모든 워커는 인터랙티브 tmux 세션. attach/대화/invite
  가 핵심 가치. headless dispatch 모드는 도입하지 않음.
- **invite 워커: 스크래핑 fallback.** 이미 실행 중인 세션엔 hook 을 주입할 수 없다
  (`--settings` 는 launch 플래그이고, Claude Code 는 시작 시점에 hook 설정을
  스냅샷으로 고정 — 실행 중 외부 주입 불가). 따라서 invite/codex/구버전 워커는
  기존 스크래핑 경로로 계속 동작.
- **상태 판정 단일화: Python 코어 리졸버.** 셸 정규식 사본을 제거하고, 테스트된
  Python detector 를 중심으로 통합.

## §1. 상태 리졸버 단일화 — `lib/worker_state.py`

"워커 X 의 현재 상태는?" 에 답하는 유일한 모듈.

### 판정 순서 (레이어)

1. **Liveness** — tmux 세션/윈도우 부재, 또는 heartbeat pid 죽음/stale → `dead`.
   (`lib/heartbeat.py` 재사용)
2. **Hook 이벤트 파일** — `$STATE_DIR/states/<safe_name>.json` 이 존재하고
   heartbeat 가 살아있으면(즉 hook 이 신선하면) busy/idle/awaiting_user/compacting
   의 **권위 소스**.
3. **스크래핑 fallback** — hook 파일이 없으면 tmux capture + 기존 detector
   (`detectors/claude.py` · `codex.py`, 순수 함수 그대로 재사용). capture 는
   리졸버가 수행.

### hook ↔ scrape 합성 규칙 (stateless, 매 호출 fresh)

```
resolve(name) →
  1. session 죽음 or heartbeat dead        → {state: dead}
  2. scrape = capture + detector(kind)      # 항상 계산 (de-escalation 용)
  3. hook = read_hook_state(name); fresh = hook 존재 and not dead
  4. if fresh:
       - hook.state == busy and scrape.state in (idle, unsent):
             → scrape           # hook 이 Stop 이벤트를 놓친 stuck-busy 자가치유
                                #   source = "scrape(hook-stale)"
       - hook.state == idle and scrape.state == unsent:
             → unsent           # 사용자가 프롬프트에 타이핑 중 (hook 은 못 봄)
                                #   source = "scrape-overlay"
       - else: → hook           # source = "hook"
  5. else: → scrape             # source = "scrape"
```

핵심 근거:
- **typed-unsent(입력 잔존)는 hook 이벤트가 없다** → hook 워커라도 이 항목만은
  스크래핑으로 합성. hook.idle + scrape.unsent 일 때만 unsent.
- **stuck-busy 자가치유** — hook 이 Stop 을 한 번 놓쳐 busy 로 굳으면, scrape 가
  빈 프롬프트(idle)를 보는 순간 scrape 를 믿어 무한 block 방지. Claude 스피너는
  실제 작업 중엔 gerund+… 로 안정적으로 잡히므로 scrape 의 idle 오탐 위험은 낮다.

### 정규화된 상태 어휘

`dead, rate_limited, error, awaiting_user, compacting, busy, unsent, idle, unknown`

detector 출력 매핑: claude `typed`→`unsent`; codex `error`→`error`. hook 이벤트
매핑은 §2.

### CLI / 소비자

- `python3 lib/worker_state.py <name>` → `KEY=VALUE` 한 줄 (기존
  check-worker-state 포맷 + `source=hook|scrape|...` 필드), `--json` 옵션,
  `--verbose` 로 pane tail.
- registry(target/kind/cwd) 는 리졸버가 `parse_registry_file` 로 직접 읽음 →
  셸 래퍼는 STATE_DIR/이름만 넘기면 됨.
- `check-worker-state.sh` → 리졸버 호출 얇은 래퍼.
- `dispatch-safe.sh` Check 1~3 → 리졸버 1회 호출로 교체(셸 정규식 삭제).
- observer(`agent.py`) → hook overlay 함수 import (§6).

## §2. Hooks 기반 상태 이벤트 (spawn Claude 워커 한정)

### 주입

`start-worker-claude.sh` 가 mcp-config 와 동일 패턴으로
`$STATE_DIR/hooks-settings-<worker>.json` 을 생성하고
`claude --settings <file>` 로 전달. 파일은 **워커 repo 밖**(`$STATE_DIR`)에 있어
repo 의 자체 `.claude/` 설정과 자동 머지되고 아무것도 남기지 않는다. (이미
`--mcp-config` 로 검증된 주입 방식에 플래그 하나 추가.)

### 이벤트 → 상태 매핑

| Hook event         | 상태            | 비고 |
|--------------------|-----------------|------|
| `UserPromptSubmit` | busy            | 턴 시작 |
| `Stop`             | idle            | 턴 종료 |
| `Notification`     | awaiting_user   | 권한 요청 등 — 메시지를 detail 로 (대시보드 표시 품질 ↑) |
| `PreCompact`       | compacting      | |
| `SessionEnd`       | ended → dead    | |

마지막 이벤트가 이긴다(last-event-wins). liveness 가 상위 레이어라 stuck 이벤트는
heartbeat 죽음으로 override 되고, 살아있는데 이벤트를 놓친 경우는 §1 의 scrape
de-escalation 으로 치유.

### 수신기 — `lib/state-hook.sh <worker> <state>`

- stdin 의 hook JSON 에서 detail(예: Notification message) 추출.
- `$STATE_DIR/states/<safe>.json` 에 tmp 쓰고 `mv` 로 원자적 갱신.
- **항상 exit 0** — hook 실패가 워커를 막으면 안 됨. settings 검증 실패 시 Claude
  Code 가 파일을 무시하므로 워커는 무영향(스크래핑으로 자연 강등).

### 신뢰 규칙

hook 파일은 heartbeat 가 살아있을 때만 신뢰(§1 레이어 1 이 먼저). heartbeat 가
없는 워커(invite/codex/legacy)는 hook 파일도 없으므로 자연히 스크래핑.

## §3. Dispatch 락 (TOCTOU 제거)

- `$STATE_DIR/locks/dispatch-<safe>.lock/` — 기존 `.registry.lock` 과 같은
  mkdir 원자 패턴, 락 디렉토리 안에 owner pid 기록.
- busy 체크 **전에** 획득, task 완료/실패까지 유지(`trap EXIT` 로 해제).
- **stale steal** — 락 owner pid 가 죽어 있으면 탈취 후 진행.
- same-cwd 직렬화도 per-cwd 락(`locks/cwd-<hash>.lock`)으로 체크-후-dispatch
  틈을 봉합(§ 현행 Check 4 를 락으로 승격).
- `_lib.sh` 에 `with_dispatch_lock` / `with_cwd_lock` 헬퍼 추가(기존
  `with_registry_lock` 과 동일 스타일).
- `dispatch-safe.sh` 는 `exec dispatch.sh` 대신 자식 호출로 바꿔 락을 유지한 채
  위임.

## §4. 완료 프로토콜 + timeout 재설계

- **완료 신호 = 결과 파일 우선** — codex 가 이미 쓰는 `[ -s r-<id>.txt ]` 를
  claude 경로에도 1차 신호로. DONE 마커 2회 카운트는 보조 확인/로깅으로 강등
  (scrollback/wrapping 취약성 제거).
- **활동 기반 timeout** — 리졸버가 busy 인 동안은 계속 대기. 두 종료 조건:
  - **hard cap** `TASK_MAX_SECONDS`(기본 3600) 초과 → timeout.
  - **turn-end without result** — 워커가 idle 로 돌아왔는데 결과 파일이 없음:
    1회 nudge(결과를 r-<id>.txt 에 Write 하라 재트리거) 후에도 없으면 조기 실패.
    스크래핑 fallback 워커는 idle 오탐 방지로 **연속 N회(기본 3) idle 관측** 시에만
    turn-end 로 판정.
- 효과: 장시간 작업 안 끊김 + 프로토콜 무시 워커는 300초가 아니라 ~1분 내 명확한
  실패. `TASK_TIMEOUT=300` 모순 해소(→ `TASK_MAX_SECONDS`, 기존 `TASK_TIMEOUT`
  는 하위호환 별칭으로 유지).

## §5. IO 디렉토리 하드닝

- `/tmp/ee` → `/tmp/${TOOL_PREFIX}-$UID/`(`mkdir -m 700`, 파일 umask 600).
  멀티유저 노출 차단. 경로 길이 ~35자로 200자 트리거 예산 내(기존 길이 가드 유지).
- 재부팅 소실 허용 — prompt/result 원본은 이미 `tasks.db` 에 저장되므로 데이터
  손실 아님. in-flight task 는 경로를 tasks.db 행에서 읽어 구경로 task 도 자연 소화.
- `_lib.sh` 에 `io_dir()` 헬퍼(경로 계산 + mkdir), dispatch.sh 가 사용.

## §6. Observer overlay / 테스트 / 마이그레이션

- **Observer hook overlay** — `agent.py` 가 tmux 스캔 결과에 hook 상태를 덧입혀
  대시보드도 awaiting_user 의 리치 detail(권한 메시지)을 얻음. heartbeat
  `annotate_workers` 와 같은 패턴의 `annotate_workers_with_hooks`(worker_state.py).
  hook 없으면 스캔 결과 그대로(무영향).
- **테스트** — `tests/test_worker_state.py`: hook>scrape 우선순위, dead 우선,
  stuck-busy de-escalation, idle+unsent overlay, staleness, `state-hook.sh` 원자성,
  락 steal. 기존 detector 테스트는 그대로 유효.
- **마이그레이션** — README 에 업그레이드 시 워커 재spawn 안내(기존 "Updating an
  agent host" 섹션 확장), `/tmp/ee` 경로 변경 언급. hook 은 재spawn 워커부터 적용,
  기존 워커는 스크래핑으로 계속 동작(무중단).

## 의도적으로 안 하는 것

- headless dispatch 모드 (Q&A 배제)
- invite 워커 settings 주입 / repo 파일 수정 (구조적으로 불가 + Q&A 배제)
- README 전략 재포지셔닝 (코드 무관, 별도 작업)
- 깨진 기존 pytest fixture 전면 수리 (범위 밖 — 새 테스트가 도는 만큼만 conftest 정리)

## 파일 변경 요약

신규: `lib/worker_state.py`, `lib/state-hook.sh`, `tests/test_worker_state.py`,
이 설계 문서.
수정: `lib/start-worker-claude.sh`(§2), `check-worker-state.sh`(§1),
`dispatch-safe.sh`(§1·§3), `lib/dispatch.sh`(§4·§5), `lib/_lib.sh`(락·io_dir),
`agent.py`(§6 overlay), `config.env`(TASK_MAX_SECONDS), `README.md`(마이그레이션).
