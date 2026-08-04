# darkarchon — Design Spec

> Unified monitoring dashboard for multi-PC LLM workers.
> This document is the set of design decisions finalized through discussion with the design director.
> Claude Code uses this document as the single source of truth for implementation.

---

## 0. Product Identity

A tool that gathers, on a single screen, the state of multiple LLM agents (Claude Code, etc.) running simultaneously across several PCs on the same LAN, and **notifies you only at "the moment your decision is needed."**

### Persona
- Runs 4 Claude Code instances on the main PC + 4 on a secondary PC, each working in a different repository
- Wants to see at a glance — without hopping between screens — which worker has finished / which is stuck waiting for a response / which is in progress
- Doesn't want notifications that a task is done. Wants notifications only at moments that genuinely require a user decision

### Key Differentiators
1. **Auto-discovery** — automatically detects LLM CLIs in tmux sessions already in use. No worker registration required
2. **LLM-agnostic** — beyond Claude Code, future Codex and Gemini CLI are caught the same way
3. **Read-only** — the dashboard never sends commands. It is an information tool. Actions happen in the user's own terminal
4. **Notification minimalism** — simple state changes like busy→idle are ignored. Only moments that require "a human decision"

---

## 1. Design Principles

These are the criteria for every design decision. When in doubt, return to these principles.

| Principle | Meaning |
|---|---|
| **Ambient, not active** | Invisible by default, speaks visually only when a decision is needed |
| **Color encodes urgency** | Only amber (action needed) + red (error). Everything else is grayscale |
| **Read-only is sacred** | The dashboard is information only; actions happen in the user's own terminal |
| **No repeating animation** | No spinner/blinking. Every transition within 1 second, once only |

---

## 2. Visual System

### 2.1 Color Tokens

**Dark mode only.** Light mode is not implemented.

#### Base
```
--bg-page:        #0a0a0a   /* full-screen background */
--bg-card:        #1a1a1a   /* worker card */
--bg-card-idle:   #161616   /* idle card (slightly darker) */
--bg-panel:       #141414   /* detail side panel */
--bg-terminal:    #0a0a0a   /* terminal tail area */
```

#### Text
```
--text-primary:    #f5f5f3   /* worker name, primary info */
--text-secondary:  #c8c8c4   /* idle card name */
--text-muted:      #a0a09c   /* state label (busy, etc.) */
--text-meta:       #888      /* secondary info */
--text-tertiary:   #666      /* metadata */
--text-faint:      #555      /* tmux target, time */
--text-faintest:   #4a4a4a   /* idle card meta */
```

#### Border
```
--border-default:    rgba(255,255,255,0.08)
--border-stronger:   rgba(255,255,255,0.12)
--border-subtle:     rgba(255,255,255,0.06)
--divider:           rgba(255,255,255,0.08)
```

#### Accent colors (Semantic — use only these three)
```
--amber:        #EF9F27     /* awaiting_user — most important */
--amber-bg:     rgba(239,159,39,0.06)   /* awaiting zone background */
--amber-border: rgba(239,159,39,0.55)
--amber-glow:   rgba(239,159,39,0.18)   /* pulse box-shadow */
--amber-text:   #2C1B00     /* text on amber background */

--red:          #E24B4A     /* rate_limited */
--red-border:   rgba(226,75,74,0.5)

--blue:         #378ADD     /* dispatch bar */
--blue-text:    #9FB3DD     /* dispatch meta text */
--blue-bg:      rgba(120,140,200,0.12)
--blue-accent:  rgba(120,140,200,0.18)   /* orchestrator inner glow */
```

**Important**: Adding colors beyond these is prohibited. If a new meaning arises, reuse an existing color or express it in grayscale.

### 2.2 Typography
- System sans-serif (`system-ui, -apple-system, sans-serif`)
- Only two weights: `400` (regular), `500` (medium)
- `600+` prohibited — too heavy on a dark background
- Terminal tail is monospace
- All text is sentence case (UPPERCASE only for SECTION labels, together with letter-spacing)

### 2.3 Size Tokens  (v2: row layout)
```
worker name:       13px / 500     (v1: 14px)
state label:       11px / 400
detail message:    12px / 400 italic  (v1: 13px)
tmux target:       11px / 400 (meta, mono)  (v1: 10px)
mailbox inline:    10px / 400
host header:       16px / 500     (v1: 20px — compressed in v2)
team label:        11px / 500 (uppercase, letter-spacing: 1.2px)
panel worker name: 15px / 500
panel section label: 10px / 500 (uppercase, letter-spacing: 0.8px)
```

### 2.4 Spacing (Hierarchy)  (v2: row layout)
Build the visual hierarchy of host / team / worker **through spacing, not nested boxes.**
Because the single-screen overview is central to the v2 row layout, every gap is
compressed to roughly half of what it was in the v1 card era.

```
gap between hosts:              24px   (v1: 48px)
host header → first team label: 12px   (v1: 24px)
team label → first row:         6px    (v1: 12px)
gap between rows in same team:  0px    (v1: 8px — row itself is a fixed 28px line)
gap between different teams:    16px   (v1: 32px)
host header padding-bottom:     8px    (thin horizontal line)
```

### 2.5 Shape (Radius)
- row: no corners (has only a left 4px vertical bar, v2)
- panel: `8px`
- button/badge: `8px`
- pill badge: `8px`
- state dot: `50%` (circle)

---

## 3. Information Architecture   (v2: row layout)

### 3.1 Screen Hierarchy

```
Screen
├─ Host (PC) — horizontal line + host name (no box)
│   └─ Team (tmux session) — small label (no box)
│       └─ Worker — row (height 28px, no box, left 4px vertical bar)
```

**Principles (v2 update)**:
- Compresses v1's "only cards are boxes" principle further. Rows are boxless too. Visual
  hierarchy is expressed solely through the color of the left 4px vertical bar.
- A row is a fixed 28px — the primary constraint is the persona requirement that 2 hosts × 8
  workers fit into a 1440×900 viewport without scrolling.
- "Information-rich cards naturally grow" (the v1 Tier system) is discarded. Rows are
  always the same height, and detail is truncated in the right column.

### 3.2 Host Header Structure

```
🖥 main                      6 workers · 2 teams · last ping 3s   3 hidden
─────────────────────────────────────────────────────────────────────────
```
- Left: icon (ti-device-desktop) + host name (16px / 500 — compressed in v2)
- Right: metadata (11px / gray)
- Far right: "N hidden" toggle (count of hidden dead rows, expands on click)
- Below: thin horizontal line (1px solid var(--divider))

### 3.3 Team Label

```
MYTEAM
```
- UPPERCASE
- 11px / 500
- letter-spacing: 1.2px
- color: var(--text-tertiary)
- no box, margin-bottom: 12px

### 3.4 Worker row — Grid Columns

A v2 row is a single 28px line, aligned with the following grid template.
Every row shares the same grid even across different hosts → columns land at the same x coordinate.

```
grid-template-columns:
  4px              [left vertical bar]
  minmax(160px, 1fr)  [name + ORCH]
  200px            [tmux target]
  60px             [mailbox badge (empty space if none)]
  100px            [state dot+label + dispatch arrow]
  minmax(0, 1fr)   [detail — truncate with ellipsis]
gap: 12px
height: 28px
align-items: center
```

| Column | Content | Visual treatment |
|---|---|---|
| (1) left bar | single channel for visual hierarchy | 4px wide × 28px tall, color per table below |
| (2) name | worker name + (when present) `[ORCH]` badge | 13px / 500, primary, ORCH 9px blue pill |
| (3) tmux target | `myteam:2.1` | 11px mono, `#666` |
| (4) mailbox | `📩 2` (only when pending > 0) | 10px, right-aligned, empty cell if none |
| (5) state | dot 6px + label 11px + dispatch `→`/`←` (when present) | label color per state — dispatch arrow is 11px `#378ADD` 500w, fade-in 0.3s |
| (6) detail | awaiting command / busy thinking label | 12px italic — awaiting=amber, otherwise=muted. Single-line truncate `text-overflow: ellipsis` |

**Left bar color (single channel for visual hierarchy)**:

| State | bar | row background |
|---|---|---|
| `awaiting_user:*` | `#EF9F27` (amber) | `rgba(239,159,39,0.06)` |
| `rate_limited` | `#E24B4A` (red) | `rgba(226,75,74,0.04)` |
| `orchestrator` and not either of the two states above | `rgba(120,140,200,0.55)` (subtle blue) | none |
| `dead` / `unknown` | none | none |
| otherwise (busy / compacting / idle) | none | none |

When ORCH + awaiting occur simultaneously, the bar gives amber priority. The ORCH indication
is sufficiently identified by the `[ORCH]` badge in column (2).

**Iron rules (v2 update)**:
- No placeholder when Tier 2 is absent (unchanged).
- v1's `scale(0.97)` / `scale(natural expansion)` are both discarded — rows are always 28px.
- Information compression first. It's OK if detail is truncated (the full text is in the side panel).

### 3.5 Row Layout Examples

**awaiting_user row (amber bar + amber row background)**:
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

**ordinary idle row (no bar, no background)**:
```
  frontend                  myteam:3.1         • idle
```

**dispatch indication (v2)** — no longer a color bar at the top of the card:
- A single arrow character next to the dot+label inside the state column:
  - send active: `→` (`#378ADD`)
  - receive active: `←`
- The arrow uses fade-in 0.3s / fade-out 0.3s. Visible only while the dispatch flag is alive.
- Simultaneous send/receive is a single glyph `← →` or `↔` (implementation simplification: two glyphs with a 5px gap).

---

## 4. Worker State System

### 4.1 Seven States

| State | Meaning | Color | Sort priority | OS notification |
|---|---|---|---|---|
| `awaiting_user:typed` | Waiting for input such as a permission prompt | **amber** | **1st** | ✅ + card pulse |
| `awaiting_user:question` | Worker explicitly asks the user a question | **amber** | **1st** | ✅ + card pulse |
| `rate_limited` | API limit reached | **red** | 2nd | ✅ (once) |
| `busy` | Working | gray (muted) | 3rd | ❌ |
| `compacting` | During /compact | gray + static indicator | 3rd | ❌ |
| `idle` | Waiting | gray (most muted) | 4th | ❌ |
| `dead` / `unknown` | Dead / LLM unknown | 50% opacity | auto-hidden after 5 min | ❌ |

### 4.2 State Display Details

**dot + label pattern**:
```html
<span style="display: inline-flex; align-items: center; gap: 5px;">
  <span style="width: 6px; height: 6px; border-radius: 50%; background: [state color];"></span>
  [state label]
</span>
```

**card border color (only awaiting/rate_limited get color)**:
- `awaiting_user`: `1px solid var(--amber-border)`
- `rate_limited`: `1px solid var(--red-border)`
- otherwise: `1px solid var(--border-default)`
- orchestrator: additionally `box-shadow: inset 0 0 0 1px var(--blue-accent)`

**compacting indicator**:
- A gray static bar at the top-left of the card (height 2px, width 40%, `background: #5F5E5A`)
- No spinner. A static indicator.

### 4.3 Sort Logic

```
1. awaiting_user:typed / awaiting_user:question (reverse chronological, most recent on top)
2. rate_limited
3. idle + unseenDone — "done, unreviewed" (most recently finished on top)
4. busy / compacting (by name)
5. idle (by name)
6. dead / unknown (hidden after 5 min)
```

- Sorting operates **only within a team.** It does not cross teams
- When an awaiting occurs within the same team, it smoothly moves to the top (0.6s ease-out)

### 4.4 Unseen-done (unread results)

"Done" is not a worker state — the state machine correctly reports `idle` —
but an **unread event**: the hub stamps `finished_at` on every busy→idle
transition and `acked_at` whenever the user demonstrably saw the worker
(focused tmux pane, detail-panel open, explicit ack via `POST /api/ack`).
`finished_at > acked_at` ⇒ the row renders as **done** (green left bar,
`done · 3m` label) while idle, or a small green ✓ badge if the worker is
already busy on its next task. A fixed top-right chip summarizes the fleet
(`⚠ N awaiting · ✓ M done` + clear-all) and the same counts prefix
`document.title` so the browser tab shows them without focus.

Color semantics: **amber = blocked on you (act now), green = result ready
(review when convenient), gray = truly idle.** Completions deliberately do
not interrupt (no pulse/push) — they accumulate quietly until reviewed.

---

## 5. Notification System

darkarchon's core value. Each of four moments is designed individually.

### 5.1 Moment 1 — In-dashboard notification (row pulse, v2)

**Trigger**: a worker transitions to `awaiting_user`

**Effect (v2 row basis)**:
- The row background jumps to `rgba(239,159,39,0.18)` (3× the amber 6%)
- Simultaneously the left 4px vertical bar glows with `box-shadow: 0 0 8px var(--amber-glow)`
- Both fade out over 1.5 seconds → normal awaiting color (`rgba(239,159,39,0.06)` background + bar unchanged)
- Simultaneously the row moves smoothly to the top of the team (0.6s ease-out)
- The `rate_limited` transition uses the same pattern with a red tone

v1's "card border pulse + box-shadow outset glow" cannot be carried over as-is to a row,
since there is no box. Instead, the same sense of weight is created through background
intensity and the left bar glow.

### 5.2 Moment 2 — OS push notification (Browser Notification API)

**Send condition**:
```js
if (!(document.visibilityState === "visible" && document.hasFocus())) {
  // send push
}
```
That is, **if the dashboard tab is visible and focused, no OS push is sent** (preventing double notification).

**Notification format** (3 lines):
```
🟡 backend
Permission required: Bash command
main · MYTEAM
```
- Line 1: worker name (with a state-color dot emoji in front — amber is 🟡, red is 🔴)
- Line 2: what it is waiting for
- Line 3: where (host · team)

**Debounce rules**:
- If N awaitings occur within a 3-second window, merge into one notification
- Title: `N workers awaiting input`
- Body: `backend, writer, scout` (worker names, comma-separated)
- Location: `main` (if the host is the same) or `Multiple hosts`

### 5.3 Moment 3 — User return (New badge)

**Scenario**: When the user sees the OS push and returns to the dashboard, they need to tell which card is the newly arrived awaiting.

**Solution**: display a **"new" badge** at the top-right of the awaiting card that arrived via notification
- Background: `--amber` (#EF9F27)
- Text: `--amber-text` (#2C1B00)
- Font: 9px / 500
- Position: `position: absolute; top: -6px; right: -6px;`
- Display duration: automatically disappears after **10 seconds**
- On disappearing: fade-out (0.4s)

### 5.4 Moment 4 — Post-hoc cleanup

After the user responds to a worker (e.g., permission approved → transition to busy):
- The card **automatically** moves from the awaiting position to the busy position
- **No** "handled"-style toast/badge is shown
- Other awaiting cards remain as they are

---

## 6. Detail Side Panel

### 6.1 Interaction
- **Click a card** → panel slides in from the right (250ms ease-out)
- **Click a different card** → only the panel content is swapped (no slide, instant)
- **ESC / X / click outside the panel** → slide out
- **Keyboard navigation** — move between cards with arrow keys, open the panel with Enter
- **Worker state change while the panel is open** → panel content auto-updates (real time). However, the panel does not close automatically

### 6.2 Polling Interval
- While the panel is open: poll the worker state at **1–2 second intervals**
- When the panel closes: return to the normal 5-second polling

### 6.3 Panel Structure (top to bottom)

```
┌──────────────────────────────────────────┐
│ Header                                X  │
│   backend  [• typed]           │
│   main · kotlin-spring          │
├──────────────────────────────────────────┤
│ AWAITING (amber background)             │
│   Permission required: bash ./gradlew    │
├──────────────────────────────────────────┤
│ TMUX TARGET                              │
│   [ myteam:2.1            📋 copy ]│
│   click → copy to clipboard             │
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

### 6.4 Section Details

**Header**:
- worker name + state pill (amber background)
- meta: host · role (tmux target is a separate section)
- X icon on the right (close)

**Awaiting zone**:
- background: `var(--amber-bg)` (amber 6% opacity)
- "Permission required: " + the actual command (mono font + code-style background)
- **No button.** Information only.

**Tmux target** (core of the Read-only principle):
- a large clickable button form
- left: tmux target (`myteam:2.1`) — monospace
- right: 📋 copy label
- on click: copies `myteam:2.1` to the clipboard
- click feedback: a brief toast, or the button label shows "copied" for 1 second then restores
- helper text: "`tmux attach -t [paste]` in your own terminal"

**Recent output**:
- mono font, 10.5px
- last 8 lines
- color: `#b0b0ad` (terminal feel, not too bright)
- background: `var(--bg-terminal)` (#0a0a0a)
- ANSI color codes are ignored in v1 (monochrome). To be added later per persona feedback

**In-flight**:
- info on the dispatch in progress
- `←` icon + `from [worker name]` + elapsed time

**Mailbox**:
- pending count pill badge
- expands on click (in v1, showing only the count is acceptable)

**Activity** (Tier 3, collapsed):
- worker start time, cumulative activity count, token usage, etc.
- collapsed by default. expands on click
- only a summary like "3 events / 5m" is always visible

---

## 7. Animation Rules

### 7.1 Absolute Rules
- **No repetition** — no spinner, blinking, or pulsing loop whatsoever
- Every transition: `ease-out`, within 1 second, once only
- Respect the `prefers-reduced-motion: reduce` media query — instant transition

### 7.2 Reactions per Event

| Trigger | Effect | Duration |
|---|---|---|
| `→ awaiting_user` transition | amber border pulse + move to top | 1.5s |
| `→ rate_limited` transition | red border pulse | 1.5s |
| dispatch send | color bar sweep left→right at card top | 0.8s |
| dispatch receive | color bar sweep right→left at card top | 0.8s |
| new mailbox message | top-right badge count increment + scale-up | 0.3s |
| `→ dead` transition | 50% opacity fade + move to bottom | 1.0s |
| hide dead after 5 min | fade-out + collapse | 1.0s |
| `→ idle` transition | shrink to scale 0.98 | 0.4s |
| `→ busy` transition | no change (ordinary) | — |
| new worker discovered | fade-in + scale-up | 0.5s |
| worker disappears (host stale) | fade-out | 0.5s |
| card → open panel | slide in from the right | 0.25s |
| card re-sort | smooth transform move | 0.6s |

### 7.3 Hover / Focus  (v2: row layout)
- row hover: background `rgba(255,255,255,0.03)`, transition 0.1s ease-out
- keyboard focus: background `rgba(255,255,255,0.06)` + left bar 4px → 6px wide
- arrow-key navigation: per row (both when the panel is closed and open)
- transition: 0.1s ease-out (slightly faster than the 0.15s of v1 card hover, to match the
  per-row scanning speed)

---

## 8. Empty State

### 8.1 First use (0 workers)

```
┌─────────────────────────────────────────────┐
│                                             │
│              No workers discovered yet     │
│                                             │
│   darkarchon automatically finds Claude    │
│   running in tmux sessions.                │
│                                             │
│   Start Claude Code as usual on any PC     │
│   and it will appear here.                 │
│                                             │
│   [Connected hosts: 0]  [agent run guide →]│
│                                             │
└─────────────────────────────────────────────┘
```

This screen is the best moment to teach the user the **auto-discovery** differentiator. Use it as a tutorial.

### 8.2 Host exists but no workers
A simple message: `main: no Claude detected in tmux sessions`

### 8.3 All hosts are stale
In a warning tone: `All hosts are unresponsive. Check that the agent is running.`

---

## 9. Data Flow

```
1 agent per PC   ─┐
                    ├──HTTP POST──▶  Hub (main PC) ──▶  Web Dashboard
tmux scan within PC ┘                     │
                                         └──SSE──▶  real-time notification push
```

### 9.1 Polling Interval
- agent: scans every pane of its PC's tmux every 5 seconds
- agent → hub: HTTP POST every 5 seconds (state update)
- hub → dashboard: real-time push via SSE
- dashboard → hub: normal state polling every 5 seconds (fallback when SSE is disconnected)
- while the detail panel is open: poll only that worker every 1–2 seconds

### 9.2 Hub Storage
- in-memory storage (not persisted)
- worker state, recent output tail (last N lines), dispatch queue, mailbox
- **host eviction**: for a host whose agent POST has stopped, workers are marked `dead`
  after `stale_after` (default 30s), and once it exceeds `evict_after` (default 300s) it is
  completely removed from hub memory (`_hosts`) and disappears from the dashboard. If the
  agent POSTs again, it is re-registered as fresh. (The host-level counterpart to §7.2
  "hide dead after 5 min")

### 9.3 Security
- works only on the same LAN (no external exposure)
- there is **no** path to send commands from the dashboard to a worker (Read-only)

---

## 10. Implementation Priority

### Phase 1 — Core
1. Main dashboard (host/team/worker layout)
2. Seven worker card states display
3. Card sorting (awaiting on top)
4. Host header + "N hidden" toggle

### Phase 2 — Notification
5. Card pulse animation
6. OS push notification (including visibilityState check)
7. Debounce rules
8. "new" badge

### Phase 3 — Detail Panel
9. Side panel slide in/out
10. Awaiting zone + command display
11. Tmux target copy feature
12. Recent output (terminal tail)
13. In-flight / Mailbox display

### Phase 4 — Polish
14. Empty state screens
15. Keyboard navigation
16. `prefers-reduced-motion` handling
17. Hover/focus states

---

## 11. Intentionally Excluded

These features are **not built.** Intentional decisions.

| Excluded item | Reason |
|---|---|
| Light mode | Persona works in a dark environment |
| Sending commands from a card to a worker | Violates the Read-only principle |
| "Open in tmux" button | Breaks OS compatibility, breaks with multiple hosts |
| spinner / blinking animation | Makes the persona close the tool |
| "task complete" notification | Noise, violates the core value ("decision moments only") |
| dispatch lines / arrows between cards | Spaghetti once there are more than 6 cards |
| dispatch text line ("→ recipient 15s") | Replaced by the color bar |
| nested host/team boxes | Triple boxes feel cramped; solved with spacing |
| mobile-first design | The persona is a user at a PC; mobile is only for receiving notifications |
| settings screen (mute, filters, etc.) | Not built until the persona actually requests it |
| ANSI color code restoration (v1) | Simplicity first; decide after persona feedback |

---

## 12. Reference: Key Design Decisions Summary (Decision Log)

| Decision | Reason |
|---|---|
| Dark mode only | The persona is already looking at 4–8 dark terminals. Light would make the retinas scream |
| Only 2 colors (amber, red) | Even with 100 cards laid out, the eye is automatically drawn to 5 |
| No host/team boxes, use spacing | Triple boxes are visual debt |
| `[ORCH]` label + subtle glow | Making it too flashy breaks the hierarchy |
| dispatch is a color bar, no text | Minimize visual noise. The exact time is in the panel |
| hide dead cards after 5 min | Keeps the screen tidy. Recoverable via "N hidden" when needed |
| bundle awaiting into a category (typed + question) | From the user's standpoint, both mean "I need to answer" |
| OS push - don't send while the tab is visible | Prevents double-notification noise |
| 3-second debounce | Having N notifications roll in at once is annoying |
| "new" badge for 10 seconds | Lets the user, arriving from the OS notification, tell which just came in |
| Tmux target clipboard copy only | "Open in tmux" breaks OS compatibility / multiple hosts. Honest design |
| Terminal tail is the panel's center | 90% of the persona's decisions are "what was it doing just now?" |
| side panel (not a modal) | Decisions need to be made while viewing other workers' states |

---

**End of document.** If a visual decision not covered by this spec is needed, return to the Design Principles (Section 1) to judge.
