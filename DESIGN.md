# darkarchon — Design Spec

> 멀티 PC LLM 워커 통합 모니터링 대시보드.
> 이 문서는 디자인 디렉터와의 논의를 거쳐 확정된 디자인 결정 모음입니다.
> 클로드 코드는 이 문서를 구현의 단일 출처(single source of truth)로 사용합니다.

---

## 0. 제품 정체성

같은 LAN의 여러 PC에서 동시에 돌아가는 여러 LLM 에이전트(Claude Code 등)의 상태를 한 화면에 모아 보고, **"내 결정이 필요한 순간"만 알림을 받는** 도구.

### 페르소나
- 메인 PC에 Claude Code 4개 + 보조 PC에 4개 띄워두고 각각 다른 저장소에서 작업
- 어떤 워커가 끝났는지 / 멈춰서 응답을 기다리는지 / 진행 중인지를 화면 안 옮겨다니고 한눈에 보고 싶음
- 작업 끝났다는 알림은 받기 싫음. 진짜 사용자가 결정해야 할 순간만 알림 받고 싶음

### 핵심 차별점
1. **자동 발견** — 이미 사용 중인 tmux 세션의 LLM CLI를 자동 감지. 워커 등록 불필요
2. **LLM-agnostic** — Claude Code 외에 미래의 Codex, Gemini CLI도 같은 방식으로 잡힘
3. **Read-only** — 대시보드에서 명령 안 보냄. 정보 도구. 액션은 사용자 본인 터미널에서
4. **알림 최소주의** — busy→idle 같은 단순 상태 변화는 무시. "사람의 결정"이 필요한 순간만

---

## 1. 디자인 원칙 (Design Principles)

모든 디자인 결정의 기준입니다. 의심스러울 때 이 원칙들로 돌아가세요.

| 원칙 | 의미 |
|---|---|
| **Ambient, not active** | 평소엔 보이지 않게, 결정이 필요할 때만 시각으로 말함 |
| **Color encodes urgency** | 앰버(액션 필요) + 레드(에러)만. 나머지는 회색조 |
| **Read-only is sacred** | 대시보드는 정보만, 액션은 사용자 본인 터미널에서 |
| **No repeating animation** | spinner/blinking 금지. 모든 transition 1초 이내, 1회만 |

---

## 2. 시각 시스템 (Visual System)

### 2.1 컬러 토큰

**다크 모드 only.** 라이트 모드 구현하지 않음.

#### 베이스
```
--bg-page:        #0a0a0a   /* 화면 전체 배경 */
--bg-card:        #1a1a1a   /* 워커 카드 */
--bg-card-idle:   #161616   /* idle 카드 (살짝 더 어둠) */
--bg-panel:       #141414   /* 디테일 사이드패널 */
--bg-terminal:    #0a0a0a   /* terminal tail 영역 */
```

#### 텍스트
```
--text-primary:    #f5f5f3   /* 워커 이름, 주요 정보 */
--text-secondary:  #c8c8c4   /* idle 카드 이름 */
--text-muted:      #a0a09c   /* 상태 라벨 (busy 등) */
--text-meta:       #888      /* 보조 정보 */
--text-tertiary:   #666      /* 메타데이터 */
--text-faint:      #555      /* tmux target, 시간 */
--text-faintest:   #4a4a4a   /* idle 카드 메타 */
```

#### 보더
```
--border-default:    rgba(255,255,255,0.08)
--border-stronger:   rgba(255,255,255,0.12)
--border-subtle:     rgba(255,255,255,0.06)
--divider:           rgba(255,255,255,0.08)
```

#### 강조색 (Semantic — 이 셋만 사용)
```
--amber:        #EF9F27     /* awaiting_user — 가장 중요 */
--amber-bg:     rgba(239,159,39,0.06)   /* awaiting zone 배경 */
--amber-border: rgba(239,159,39,0.55)
--amber-glow:   rgba(239,159,39,0.18)   /* 펄스 box-shadow */
--amber-text:   #2C1B00     /* amber 배경 위 텍스트 */

--red:          #E24B4A     /* rate_limited */
--red-border:   rgba(226,75,74,0.5)

--blue:         #378ADD     /* dispatch bar */
--blue-text:    #9FB3DD     /* dispatch 메타 텍스트 */
--blue-bg:      rgba(120,140,200,0.12)
--blue-accent:  rgba(120,140,200,0.18)   /* orchestrator inner glow */
```

**중요**: 이 외 색상 추가 금지. 새 의미가 생기면 기존 색을 재사용하거나 회색조로 표현.

### 2.2 타이포그래피
- 시스템 sans-serif (`system-ui, -apple-system, sans-serif`)
- 두 weight만: `400` (regular), `500` (medium)
- `600+` 금지 — 다크 배경에서 너무 무거움
- terminal tail은 monospace
- 모든 텍스트는 sentence case (UPPERCASE는 SECTION 라벨에만 letter-spacing과 함께)

### 2.3 사이즈 토큰  (v2: row layout)
```
워커 이름:       13px / 500     (v1: 14px)
상태 라벨:       11px / 400
detail 메시지:   12px / 400 italic  (v1: 13px)
tmux target:    11px / 400 (메타, mono)  (v1: 10px)
mailbox 인라인:  10px / 400
호스트 헤더:     16px / 500     (v1: 20px — v2 압축)
팀 라벨:         11px / 500 (uppercase, letter-spacing: 1.2px)
패널 워커 이름:  15px / 500
패널 섹션 라벨:  10px / 500 (uppercase, letter-spacing: 0.8px)
```

### 2.4 스페이싱 (Hierarchy)  (v2: row layout)
호스트 / 팀 / 워커의 시각 위계를 **박스 중첩이 아니라 spacing**으로 만듭니다.
v2 row 레이아웃은 한 화면 overview 가 핵심이라 v1 카드 시절보다 모든 간격을
절반 정도로 압축했습니다.

```
호스트 간 간격:              24px   (v1: 48px)
호스트 헤더 → 첫 팀 라벨:    12px   (v1: 24px)
팀 라벨 → 첫 row:            6px    (v1: 12px)
같은 팀 안 row 간 간격:       0px    (v1: 8px — row 자체가 28px 고정 행)
다른 팀 간 간격:             16px   (v1: 32px)
호스트 헤더 padding-bottom:  8px    (얇은 가로선)
```

### 2.5 모양 (Radius)
- row: 모서리 없음 (좌측 4px vertical bar만 가짐, v2)
- 패널: `8px`
- 버튼/배지: `8px`
- pill 배지: `8px`
- 상태 dot: `50%` (원)

---

## 3. 정보 구조 (Information Architecture)   (v2: row layout)

### 3.1 화면 계층

```
화면
├─ 호스트 (PC) — 가로선 + 호스트명 (박스 없음)
│   └─ 팀 (tmux session) — 작은 라벨 (박스 없음)
│       └─ 워커 — row (높이 28px, 박스 없음, 좌측 4px vertical bar)
```

**원칙 (v2 갱신)**:
- v1 의 "카드만 박스" 원칙을 더 압축. row 도 박스 없음. 시각 위계는
  좌측 4px vertical bar 색으로만 표현.
- 한 row 는 28px 고정 — 2 호스트 × 8 워커가 1440×900 viewport 에 스크롤
  없이 들어가야 한다는 페르소나 요구가 1차 제약.
- "정보가 많은 카드는 자연스럽게 커짐" (v1 Tier 시스템) 은 폐기. row 는
  항상 같은 높이, detail 은 우측 컬럼에서 truncate.

### 3.2 호스트 헤더 구조

```
🖥 main                      6 workers · 2 teams · last ping 3s   3 hidden
─────────────────────────────────────────────────────────────────────────
```
- 좌측: 아이콘(ti-device-desktop) + 호스트명 (16px / 500 — v2 압축)
- 우측: 메타데이터 (11px / 회색)
- 우측 끝: "N hidden" 토글 (dead row 숨김 카운트, 클릭 시 펼침)
- 아래: 얇은 가로선 (1px solid var(--divider))

### 3.3 팀 라벨

```
MYTEAM
```
- UPPERCASE
- 11px / 500
- letter-spacing: 1.2px
- color: var(--text-tertiary)
- 박스 없음, margin-bottom: 12px

### 3.4 워커 row — 그리드 컬럼

v2 row 는 단일 28px 행이며 다음 grid template 으로 정렬됩니다.
호스트가 달라도 모든 row 가 같은 grid 를 공유 → 컬럼이 같은 x 좌표로 떨어집니다.

```
grid-template-columns:
  4px              [좌측 vertical bar]
  minmax(160px, 1fr)  [이름 + ORCH]
  200px            [tmux target]
  60px             [mailbox 배지 (없으면 빈 공간)]
  100px            [상태 dot+라벨 + dispatch 화살표]
  minmax(0, 1fr)   [detail — truncate with ellipsis]
gap: 12px
height: 28px
align-items: center
```

| 컬럼 | 내용 | 시각 처리 |
|---|---|---|
| (1) 좌 bar | 시각 위계 단일 채널 | 4px wide × 28px tall, 색은 아래 표 참조 |
| (2) name | 워커 이름 + (있을 때) `[ORCH]` 배지 | 13px / 500, primary, ORCH 9px blue pill |
| (3) tmux target | `myteam:2.1` | 11px mono, `#666` |
| (4) mailbox | `📩 2` (pending > 0 일 때만) | 10px, 우측 정렬, 없으면 빈 셀 |
| (5) 상태 | dot 6px + 라벨 11px + dispatch `→`/`←` (있을 때) | 라벨 색은 상태별 — dispatch 화살표는 11px `#378ADD` 500w, fade-in 0.3s |
| (6) detail | awaiting 명령어 / busy thinking 라벨 | 12px italic — awaiting=amber, 그 외=muted. 한 줄 truncate `text-overflow: ellipsis` |

**좌 bar 색 (시각 위계 단일 채널)**:

| 상태 | bar | row 배경 |
|---|---|---|
| `awaiting_user:*` | `#EF9F27` (amber) | `rgba(239,159,39,0.06)` |
| `rate_limited` | `#E24B4A` (red) | `rgba(226,75,74,0.04)` |
| `orchestrator` 이고 위 두 상태 아님 | `rgba(120,140,200,0.55)` (subtle blue) | 없음 |
| `dead` / `unknown` | 없음 | 없음 |
| 그 외 (busy / compacting / idle) | 없음 | 없음 |

ORCH + awaiting 동시일 때 bar 는 amber 가 우선. ORCH 표시는 (2) 컬럼의
`[ORCH]` 배지로 충분히 식별됩니다.

**철칙 (v2 갱신)**:
- Tier 2 가 없을 때 placeholder 금지 (그대로).
- v1 의 `scale(0.97)` / `scale(자연 확장)` 모두 폐기 — row 는 항상 28px.
- 정보 압축 우선. detail 이 truncate 되어도 OK (전체는 사이드패널에서).

### 3.5 row 레이아웃 예시

**awaiting_user row (amber bar + amber row 배경)**:
```
█ backend            myteam:2.1         • awaiting   Permission required: Bash command
```

**rate_limited row (red bar)**:
```
█ backend            myteam-feature_a:2.1   • rate limited  Retry in 12m
```

**orchestrator + busy row (subtle blue bar)**:
```
█ dashboard [ORCH]            dashboard:1.1     📩 2  • busy →       Whisking…
```

**평범 idle row (bar 없음, 배경 없음)**:
```
  frontend                  myteam:3.1         • idle
```

**dispatch 표시 (v2)** — 더 이상 카드 상단 컬러 바 아님:
- 상태 컬럼 안 dot+라벨 옆에 화살표 1글자:
  - 송신 active: `→` (`#378ADD`)
  - 수신 active: `←`
- 화살표는 fade-in 0.3s / fade-out 0.3s. dispatch flag 가 살아있는 동안만 보임.
- 동시 송수신은 `← →` 또는 `↔` 한 글자 (구현 단순화: 두 글자 5px gap).

---

## 4. 워커 상태 시스템 (State System)

### 4.1 7가지 상태

| State | 의미 | 색 | 정렬 우선순위 | OS 알림 |
|---|---|---|---|---|
| `awaiting_user:typed` | Permission prompt 등 입력 대기 | **앰버** | **1순위** | ✅ + 카드 펄스 |
| `awaiting_user:question` | 워커가 사용자한테 명시적 질문 | **앰버** | **1순위** | ✅ + 카드 펄스 |
| `rate_limited` | API limit 도달 | **레드** | 2순위 | ✅ (1회) |
| `busy` | 작업 중 | 회색 (muted) | 3순위 | ❌ |
| `compacting` | /compact 중 | 회색 + 정적 indicator | 3순위 | ❌ |
| `idle` | 대기 중 | 회색 (가장 muted) | 4순위 | ❌ |
| `dead` / `unknown` | 죽음/LLM 미상 | 50% opacity | 5분 후 자동 숨김 | ❌ |

### 4.2 상태 표시 디테일

**dot + 라벨 패턴**:
```html
<span style="display: inline-flex; align-items: center; gap: 5px;">
  <span style="width: 6px; height: 6px; border-radius: 50%; background: [상태색];"></span>
  [상태 라벨]
</span>
```

**카드 보더 색 (awaiting/rate_limited만 색)**:
- `awaiting_user`: `1px solid var(--amber-border)`
- `rate_limited`: `1px solid var(--red-border)`
- 그 외: `1px solid var(--border-default)`
- orchestrator: 추가로 `box-shadow: inset 0 0 0 1px var(--blue-accent)`

**compacting indicator**:
- 카드 상단 좌측에 회색 정적 바 (높이 2px, 너비 40%, `background: #5F5E5A`)
- spinner 안 됨. 정적 indicator.

### 4.3 정렬 로직

```
1. awaiting_user:typed / awaiting_user:question (시간 역순, 최근이 위)
2. rate_limited
3. busy / compacting (이름 순)
4. idle (이름 순)
5. dead / unknown (5분 후 숨김)
```

- 정렬은 **팀 안에서만** 작동. 팀을 넘나들지 않음
- 같은 팀 안에서 awaiting이 발생하면 부드럽게 최상단으로 이동 (0.6s ease-out)

---

## 5. 알림 시스템 (Notification System)

darkarchon의 핵심 가치. 4가지 순간을 각각 디자인합니다.

### 5.1 Moment 1 — In-dashboard 알림 (row 펄스, v2)

**트리거**: 워커가 `awaiting_user`로 전환

**효과 (v2 row 기준)**:
- row 배경이 `rgba(239,159,39,0.18)` (앰버 6%의 3배) 로 점프
- 동시에 좌측 4px vertical bar 가 `box-shadow: 0 0 8px var(--amber-glow)` 으로 glow
- 1.5초 동안 둘 다 페이드아웃 → 정상 awaiting 색 (`rgba(239,159,39,0.06)` 배경 + bar 그대로)
- 동시에 row 가 팀 안 최상단으로 부드럽게 이동 (0.6s ease-out)
- `rate_limited` 전환도 같은 패턴, red 톤 사용

v1 의 "카드 보더 펄스 + box-shadow outset 글로우" 는 row 에서는 박스가
없어서 그대로 옮길 수 없음. 대신 배경 강도와 좌측 bar glow 로 같은
무게감을 만듭니다.

### 5.2 Moment 2 — OS 푸시 알림 (Browser Notification API)

**발송 조건**:
```js
if (!(document.visibilityState === "visible" && document.hasFocus())) {
  // 푸시 발송
}
```
즉, **대시보드 탭이 보이고 포커스 있으면 OS 푸시 안 보냄** (이중 알림 방지).

**알림 포맷** (3줄):
```
🟡 backend
Permission required: Bash command
main · MYTEAM
```
- 줄 1: 워커 이름 (앞에 상태색 dot 이모지 — 앰버는 🟡, 레드는 🔴)
- 줄 2: 무엇을 기다리는지
- 줄 3: 어디서 (호스트 · 팀)

**Debounce 룰**:
- 3초 window 내에 awaiting이 N개 발생하면 1개 알림으로 합침
- 제목: `N workers awaiting input`
- 본문: `backend, writer, scout` (워커 이름 콤마 구분)
- 위치: `main` (호스트가 같으면) 또는 `Multiple hosts`

### 5.3 Moment 3 — 사용자 복귀 (New 배지)

**시나리오**: 사용자가 OS 푸시 보고 대시보드로 돌아왔을 때, 어느 카드가 새로 들어온 awaiting인지 구분 필요.

**해결**: 알림으로 도착한 awaiting 카드 우측 상단에 **"new" 배지** 표시
- 배경: `--amber` (#EF9F27)
- 텍스트: `--amber-text` (#2C1B00)
- 폰트: 9px / 500
- 위치: `position: absolute; top: -6px; right: -6px;`
- 표시 시간: **10초** 후 자동 사라짐
- 사라질 때: fade-out (0.4s)

### 5.4 Moment 4 — 사후 정리

사용자가 워커에 응답한 후 (예: permission 승인 → busy 전환):
- 카드는 **자동으로** awaiting 위치에서 busy 위치로 이동
- "처리됨" 같은 toast/배지 표시 **안 함**
- 다른 awaiting 카드들은 그대로 유지

---

## 6. 디테일 사이드패널 (Detail Panel)

### 6.1 인터랙션
- **카드 클릭** → 우측에서 패널 슬라이드 인 (250ms ease-out)
- **다른 카드 클릭** → 패널 내용만 교체 (슬라이드 X, 즉시)
- **ESC / X / 패널 외 클릭** → 슬라이드 아웃
- **키보드 네비게이션** — 화살표 키로 카드 이동, Enter로 패널 열기
- **패널 열린 상태에서 워커 상태 변화** → 패널 내용 자동 업데이트 (실시간). 단, 패널이 자동으로 닫히지는 않음

### 6.2 폴링 주기
- 패널 열려있는 동안: **1~2초 간격**으로 워커 상태 polling
- 패널 닫히면: 5초 일반 폴링으로 복귀

### 6.3 패널 구조 (위에서 아래로)

```
┌──────────────────────────────────────────┐
│ 헤더                                  X  │
│   backend  [• typed]           │
│   main · kotlin-spring          │
├──────────────────────────────────────────┤
│ AWAITING (앰버 배경)                    │
│   Permission required: bash ./gradlew    │
├──────────────────────────────────────────┤
│ TMUX TARGET                              │
│   [ myteam:2.1            📋 copy ]│
│   클릭 → 클립보드 복사                  │
├──────────────────────────────────────────┤
│ RECENT OUTPUT          last 8 lines      │
│   ┌─────────────────────────────────┐   │
│   │ > Implementing OrderController  │   │
│   │ ✓ Added @RestController         │   │
│   │ ○ Running tests…                │   │
│   │   ./gradlew test                │   │
│   │   → 12 passed, 0 failed         │   │
│   │ ○ Permission needed: bash ...   │   │
│   └─────────────────────────────────┘   │
├──────────────────────────────────────────┤
│ IN-FLIGHT                                │
│   ← from dashboard          14s ago      │
├──────────────────────────────────────────┤
│ MAILBOX                       2 pending  │
├──────────────────────────────────────────┤
│ ACTIVITY (collapsed)                ▼   │
└──────────────────────────────────────────┘
```

### 6.4 섹션별 세부

**헤더**:
- 워커 이름 + 상태 pill (앰버 배경)
- 메타: 호스트 · 역할 (tmux target은 별도 섹션으로)
- 우측 X 아이콘 (닫기)

**Awaiting zone**:
- 배경: `var(--amber-bg)` (앰버 6% 투명도)
- "Permission required: " + 실제 명령어 (mono font + 코드 스타일 배경)
- **버튼 없음**. 정보만 표시.

**Tmux target** (Read-only 원칙 핵심):
- 큰 클릭 가능한 버튼 형태
- 좌측: tmux target (`myteam:2.1`) — monospace
- 우측: 📋 copy 라벨
- 클릭 시: 클립보드에 `myteam:2.1` 복사
- 클릭 피드백: 짧은 toast 또는 버튼 라벨 "copied" 1초 표시 후 복원
- 보조 텍스트: "본인 터미널에서 `tmux attach -t [paste]`"

**Recent output**:
- mono font, 10.5px
- 마지막 8줄
- color: `#b0b0ad` (터미널 느낌, 너무 밝지 않게)
- 배경: `var(--bg-terminal)` (#0a0a0a)
- ANSI 색 코드는 v1에서 무시 (단색). 추후 페르소나 피드백에 따라 추가

**In-flight**:
- 진행 중인 dispatch 정보
- `←` 아이콘 + `from [워커명]` + 경과 시간

**Mailbox**:
- pending 카운트 pill 배지
- 클릭하면 펼침 (v1에서는 카운트만 표시 가능)

**Activity** (Tier 3, collapsed):
- 워커 시작 시각, 누적 활동 수, 토큰 사용량 등
- 기본 접혀 있음. 클릭으로 펼침
- "3 events / 5m" 같은 요약만 항상 보임

---

## 7. 애니메이션 규칙 (Animation Rules)

### 7.1 절대 규칙
- **반복 금지** — spinner, blinking, pulsing loop 일체 없음
- 모든 transition: `ease-out`, 1초 이내, 1회만
- `prefers-reduced-motion: reduce` 미디어 쿼리 존중 — 즉시 전환

### 7.2 이벤트별 반응

| 트리거 | 효과 | 지속 시간 |
|---|---|---|
| `→ awaiting_user` 전환 | 테두리 앰버 펄스 + 최상단으로 이동 | 1.5s |
| `→ rate_limited` 전환 | 테두리 레드 펄스 | 1.5s |
| dispatch 송신 | 카드 상단 좌→우 컬러 바 스윕 | 0.8s |
| dispatch 수신 | 카드 상단 우→좌 컬러 바 스윕 | 0.8s |
| mailbox 새 메시지 | 우측 상단 배지 숫자 증가 + scale-up | 0.3s |
| `→ dead` 전환 | 50% opacity 페이드 + 최하단 이동 | 1.0s |
| 5분 후 dead 숨김 | fade-out + collapse | 1.0s |
| `→ idle` 전환 | scale 0.98로 축소 | 0.4s |
| `→ busy` 전환 | 변화 없음 (평범) | — |
| 신규 워커 발견 | fade-in + scale-up | 0.5s |
| 워커 사라짐 (host stale) | fade-out | 0.5s |
| 카드 → 패널 열기 | 우측에서 슬라이드 인 | 0.25s |
| 카드 위치 재정렬 | 부드러운 transform 이동 | 0.6s |

### 7.3 호버 / 포커스  (v2: row layout)
- row hover: 배경 `rgba(255,255,255,0.03)`, transition 0.1s ease-out
- 키보드 포커스: 배경 `rgba(255,255,255,0.06)` + 좌측 bar 4px → 6px wide
- 화살표 키 네비게이션: row 단위 (패널 닫힌/열린 양쪽 모두)
- transition: 0.1s ease-out (v1 카드 hover 의 0.15s 보다 살짝 빨라 row 단위
  스캐닝 속도와 맞춤)

---

## 8. 빈 상태 (Empty State)

### 8.1 첫 사용 (워커 0개)

```
┌─────────────────────────────────────────────┐
│                                             │
│              아직 발견된 워커가 없습니다   │
│                                             │
│   darkarchon은 tmux 세션에서 클로드가 떠   │
│   있는 것을 자동으로 찾습니다.             │
│                                             │
│   어떤 PC에서든 클로드 코드를 평소처럼     │
│   시작하면 여기에 나타납니다.              │
│                                             │
│   [연결된 호스트: 0]  [agent 실행 가이드 →]│
│                                             │
└─────────────────────────────────────────────┘
```

이 화면은 **자동 발견** 차별점을 사용자에게 가르치는 가장 좋은 순간. 튜토리얼로 활용.

### 8.2 호스트는 있는데 워커가 없는 경우
간단한 메시지: `main: tmux 세션에서 클로드가 감지되지 않았습니다`

### 8.3 모든 호스트가 stale인 경우
경고 톤으로: `모든 호스트가 응답하지 않습니다. agent가 실행 중인지 확인하세요.`

---

## 9. 데이터 흐름 (Data Flow)

```
각 PC에 agent 1개  ─┐
                    ├──HTTP POST──▶  Hub (메인 PC) ──▶  Web Dashboard
PC 안 tmux 스캔 ───┘                     │
                                         └──SSE──▶  실시간 알림 push
```

### 9.1 폴링 주기
- agent: 5초마다 자기 PC의 tmux 모든 pane 검사
- agent → hub: 5초마다 HTTP POST (상태 업데이트)
- hub → 대시보드: SSE로 실시간 push
- 대시보드 → hub: 일반 상태 5초 폴링 (SSE 끊긴 fallback)
- 디테일 패널 열려있는 동안: 해당 워커만 1~2초 폴링

### 9.2 hub 저장
- in-memory 저장 (영구 저장 안 함)
- 워커 상태, 최근 출력 tail (마지막 N줄), dispatch 큐, mailbox
- **host eviction**: agent POST 가 끊긴 host 는 `stale_after`(기본 30s) 후
  워커가 `dead` 로 표시되고, `evict_after`(기본 300s) 를 넘기면 hub 메모리
  (`_hosts`)에서 완전히 제거되어 대시보드에서 사라짐. agent 가 다시 POST 하면
  fresh 로 재등록. (§7.2 "5분 후 dead 숨김" 의 host 단위 대응)

### 9.3 보안
- 같은 LAN에서만 작동 (외부 노출 안 함)
- 대시보드에서 워커로 명령 전송 경로 **존재하지 않음** (Read-only)

---

## 10. 구현 우선순위 (Implementation Priority)

### Phase 1 — Core
1. 메인 대시보드 (호스트/팀/워커 레이아웃)
2. 워커 카드 7가지 상태 표시
3. 카드 정렬 (awaiting 최상단)
4. 호스트 헤더 + "N hidden" 토글

### Phase 2 — Notification
5. 카드 펄스 애니메이션
6. OS 푸시 알림 (visibilityState 체크 포함)
7. Debounce 룰
8. "new" 배지

### Phase 3 — Detail Panel
9. 사이드패널 슬라이드 인/아웃
10. Awaiting zone + 명령어 표시
11. Tmux target 복사 기능
12. Recent output (terminal tail)
13. In-flight / Mailbox 표시

### Phase 4 — Polish
14. 빈 상태 화면들
15. 키보드 네비게이션
16. `prefers-reduced-motion` 대응
17. 호버/포커스 상태

---

## 11. 의도적으로 제외한 것

이런 기능은 **만들지 않습니다.** 의도된 결정.

| 제외 항목 | 이유 |
|---|---|
| 라이트 모드 | 페르소나가 다크 환경에서 작업 |
| 카드에서 워커로 명령 전송 | Read-only 원칙 위반 |
| "Open in tmux" 버튼 | OS별 호환성 깨짐, 멀티 호스트 시 깨짐 |
| spinner / blinking 애니메이션 | 페르소나가 도구를 닫게 만듦 |
| "작업 완료" 알림 | 노이즈, 핵심 가치("결정 순간만") 위반 |
| 카드 사이 dispatch 라인 / 화살표 | 카드 6개 넘으면 스파게티 |
| dispatch 텍스트 라인 ("→ recipient 15s") | 컬러 바로 대체 |
| 호스트/팀 박스 중첩 | 3중 박스는 답답함, spacing으로 해결 |
| 모바일 우선 디자인 | 페르소나는 PC 앞 사용자, 모바일은 알림 받는 용도 |
| 설정 화면 (mute, 필터 등) | 페르소나가 실제로 요청하기 전까지 만들지 않음 |
| ANSI 색 코드 복원 (v1) | 단순함 우선, 페르소나 피드백 후 결정 |

---

## 12. 참고: 핵심 디자인 결정 요약 (Decision Log)

| 결정 | 이유 |
|---|---|
| 다크 모드 only | 페르소나가 이미 어두운 터미널 4~8개 보고 있음. 라이트는 망막 비명 |
| 색 2개만 (앰버, 레드) | 100개 카드 깔려도 시선이 자동으로 5개로 감 |
| 호스트/팀 박스 X, spacing으로 | 3중 박스는 시각적 부채 |
| `[ORCH]` 라벨 + 미묘한 글로우 | 너무 튀게 만들면 위계가 망가짐 |
| dispatch는 컬러 바, 텍스트 X | 시각 노이즈 최소화. 정확한 시간은 패널에서 |
| dead 카드 5분 후 숨김 | 화면 정돈. 필요 시 "N hidden"으로 복구 가능 |
| awaiting을 카테고리로 묶음 (typed + question) | 사용자 입장에서 둘 다 "내가 답해야 함" |
| OS 푸시 - 탭 보일 때 안 보냄 | 이중 알림 노이즈 방지 |
| 3초 debounce | 동시에 N개 알림 도르륵 뜨면 짜증 |
| "new" 배지 10초 | 사용자가 OS 알림 보고 와서 새로 온 것 구분 |
| Tmux target 클립보드 복사만 | "Open in tmux"는 OS 호환성/멀티 호스트 깨짐. 정직한 디자인 |
| Terminal tail이 패널 중심 | 페르소나의 결정 90%는 "지금 뭐 하고 있었지?" |
| 사이드패널 (모달 X) | 다른 워커 상태 보면서 결정 필요 |

---

**문서 끝.** 이 spec에 없는 시각적 결정이 필요하면 디자인 원칙(Section 1)으로 돌아가 판단하세요.
