# Tray-resident UI (bandeja)

Branch: `feat/tray-resident-ui` · Base: `main` @ `132bfde` · Started 2026-11-08

The GUI keeps running in the system tray so closing the window no longer ends the
stream, and a hidden, idle GUI stops paying for telemetry it cannot show.

## Specs

S1 — Tray presence and menu. While the GUI process runs, it owns one tray icon
(StatusNotifierItem). Its menu has, in this order: `Open window` (disabled when
the window is visible), `Start session` / `Stop session` (one entry whose label and
enabled state follow the session), and `Quit`.

S2 — Activating the icon. A left click on the tray icon toggles the window: hidden
→ shown and raised, shown → hidden. The same toggle is what the `Open window` entry
does (it only ever shows).

S3 — Close goes to the tray. The window's close button hides the window and leaves
the process running, whether or not a session is running. It never stops the
container and never ends the process. The first hide shows one tray notification
saying podstage keeps running, so the behaviour is discoverable instead of silent.

S4 — Quit is explicit. `Quit` (tray menu) is the only path that ends the process.
With a running session it asks the same confirmation the close button asks today
and stops the container before exiting; with no session it exits directly.

S5 — Tray reflects session state. Every telemetry snapshot updates the tray: while a
session runs the icon carries a running tooltip and the menu offers `Stop session`;
otherwise the tooltip says stopped and the menu offers `Start session`. Start and
stop run on the existing worker path (`start_action`), so the window stays
responsive and a failure surfaces as it does from the session page.

S6 — Hidden idle cost. With the window hidden and no session running the poll backs
off from the interactive cadence to one cheap check, so an idle tray presence does
not burn CPU on telemetry nobody can see. A hidden window with a running session
keeps one full snapshot per backoff interval: the tray tooltip and the running
indicator must stay truthful without paying the interactive rate.

S7 — Behaviour preserved. The pre-existing paths keep working: the session page
start/stop buttons, teardown on quit, `logs_page.shutdown()`, the language switch,
and the type-checked Qt layer. Every new user-facing string goes through `tr()`
with a German catalog entry, so `test_no_orphan_translations` /
`test_placeholders_match_across_languages` keep passing.

## Tasks

| ID | Spec | Route | Commit |
| --- | --- | --- | --- |
| T1 | S1-S7 | inline | `feat: keep podstage resident in the tray` (this commit) |
| T2 | S1-S5 | inline | `feat: keep podstage resident in the tray` (this commit) |
| T3 | S6 | inline | `790a10c perf: sample telemetry on the cadence the window can show` |
| T4 | S7 | inline | (closes with T2/T3) |

## Log

L1 — User, this session, verbatim: `bandeja`

L2 — Agreed scope, carried over from the previous session's next step and
confirmed by L1: "tray icon + popup widget (start/stop session, switch sandbox,
open window), close-to-tray that does NOT kill the session, explicit Quit, and the
idle poll cost (3.08% of a core) as part of it. Branch `feat/tray-resident-ui` off
`main`, feature doc `odd/tasks/tray-resident-ui.md`."

L3 — Exploration (bounded, this session). `ui/app.py:190-232` — `MainWindow.closeEvent`
stops the container on a worker then closes; there is no tray and
`quitOnLastWindowClosed` is left at its `True` default. `ui/workers.py:18-41` —
`PollWorker` runs `monitor.snapshot()` every `POLL_MS = 2000` in 100 ms slices.
`core/monitor.py:336-350` — `snapshot()` returns early when nothing runs (so the
idle cost is `runtime.status()`'s `podman container inspect` every 2 s plus the
permanent `journalctl -f` child from `pages/logs_page.py:61`). `ui/i18n.py` — strings
go through `tr()`; only a German catalog ships; `tests/test_i18n.py:119` only rejects
orphan keys, so untranslated new keys would not fail the suite but would be sloppy.

L4 — Decision (autonomy, reversible). Close-to-tray is unconditional: hiding with no
session also keeps the process in the tray, because an X that sometimes quits and
sometimes hides is worse than a predictable one plus an explicit Quit. Rationale is
in S3/S4; the user can flip it to "hide only while a session runs" cheaply if it
turns out to be annoying.

L5 — Verification (T2). `pytest` 389 passed (7 new in `tests/test_tray_model.py`, 4
new in `tests/test_monitor.py`), `ruff check` clean, `pyright` 0 errors and the same
23 pre-existing warnings. Live on Hyprland/Caelestia: the process registers a
StatusNotifierItem (watcher went 4 → 5 items), `IconName` empty but `IconPixmap`
carries two pixmaps (22×22, 40×38) so the tray has a real icon, `Id=podstage`,
`Status=Active`, `ToolTip` = "podstage — stopped". Driven in-process (the same
`QCloseEvent` a WM close delivers, `_tray_available` forced true): close → window
hidden, poll interval 2000 → 10000 ms, process and poll thread alive; the
`Open window` action re-shows it; `Quit` with no session exits with code 0.
CPU ticks (utime+stime+cutime+cstime) 232 over ~14 s visible vs 29 over ~30 s
hidden-idle, i.e. the cadence change is what the numbers follow.

L6 — Not verified automatically: clicking the tray icon and picking a menu entry
are Qt popup interactions with no test hook. The menu, activate handler and Quit
wiring are typechecked and exercised through `_on_tray_action`, but the click
itself is the human's to eyeball.

L7 — Pre-existing nit seen during verification, out of scope: `LogsPage.shutdown`
kills the `journalctl` tail without `waitForFinished()`, so quitting prints
`QProcess: Destroyed while process ("journalctl") is still running`. It predates
this feature (the same line closes the previous session's `ui.log`); a one-line
fix if it ever matters.

L8 — Self-review found one gap and it is fixed in the same commit: the tray's
session entry stayed enabled while `SessionPage._pending` held an action, so a
click during a start could launch a second start (the page's buttons are disabled
during it; the menu was not). `tray_model.menu(..., pending=...)` now renders
`starting …` / `stopping …` disabled, `SessionPage.pending` exposes the state, and
`_on_tray_action` refreshes the menu right after the click instead of waiting for
the next snapshot. Pinned by
`test_the_toggle_is_disabled_while_an_action_is_in_flight`.

L9 — Native assessment (read-only, `gentle_review assess`, baseRef main):
risk medium, `reviewDue: false` (`under_budget`), plan = writer self-verification,
no independent verifier, writer profile large (runtime). No native review was
started because it is not due for this candidate.
