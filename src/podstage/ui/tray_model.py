"""What the tray decides, kept free of PyQt6.

``QSystemTrayIcon`` and ``QMenu`` cannot be exercised under pytest (they need a
running ``QApplication`` and a StatusNotifier host), so every choice the tray
makes — the menu shape, the set of sandboxes offered, the labels, the tooltip,
what closing the window means, when the one-time notice fires — is a pure
function here and gets pinned by ``tests/test_tray_model.py``. ``ui.app`` only
renders these results into Qt objects.
"""

from dataclasses import dataclass

from .i18n import tr

OPEN_WINDOW = "open-window"
SEPARATOR = "separator"
SESSIONS_MENU = "sessions"
PERFORMANCE = "performance"
PERF_PREFIX = "perf:"
START_PREFIX = "start:"
STOP_SESSION = "stop-session"
SESSION_STATUS = "session-status"
QUIT = "quit"


@dataclass(frozen=True)
class Entry:
    """One tray menu row.

    ``children`` turns the row into a submenu; the parent's ``action`` is then
    only an id, and ``enabled`` decides whether the submenu opens at all.
    """

    action: str
    label: str
    enabled: bool = True
    children: tuple["Entry", ...] = ()


@dataclass(frozen=True)
class Metrics:
    """The numbers the Load card shows, as the tray needs them."""

    cpu_pct: float | None = None
    ram_used_mb: int | None = None
    ram_total_mb: int | None = None
    gpu_pct: int | None = None
    vram_used_mb: int | None = None
    vram_total_mb: int | None = None
    gpu_temp_c: int | None = None
    cpu_temp_c: int | None = None
    encoder_sessions: int | None = None
    fps: float | None = None
    game: str | None = None


def menu(running: bool, window_visible: bool, pending: str | None = None,
         sandboxes: tuple[str, ...] = (), client: str | None = None,
         metrics: "Metrics | None" = None,
         ) -> tuple[Entry, ...]:
    """The tray menu: the window, one ``Sessions`` submenu, quit.

    Everything session-shaped lives behind ``Sessions`` so the top level stays
    three rows: with a session running it shows its state and offers
    ``Stop session``, and with nothing running it lists one row per sandbox.
    ``pending`` mirrors the in-flight action (``"start"``/``"stop"``) so the row
    that fired cannot be fired again underneath itself. ``metrics`` adds the
    read-only performance section below the sessions, one row per number.
    """
    entries = [
        Entry(OPEN_WINDOW, tr("Open UI"), not window_visible),
        Entry(SEPARATOR, ""),
        Entry(SESSIONS_MENU, tr("Sessions"), True,
              _session_entries(running, pending, sandboxes, client)),
        Entry(SEPARATOR, ""),
    ]
    if metrics is not None:
        entries.append(Entry(PERFORMANCE, tr("Performance"), False))
        entries.extend(Entry(f"{PERF_PREFIX}{row}", row, False)
                       for row in performance_rows(metrics))
        entries.append(Entry(SEPARATOR, ""))
    entries.append(Entry(QUIT, tr("Quit")))
    return tuple(entries)


def performance_rows(metrics: Metrics) -> tuple[str, ...]:
    """One disabled row per metric: CPU, RAM, GPU, VRAM, sessions, fps, game.

    Anything the host does not report is simply absent — a row that says nothing
    is worse than no row. With nothing at all, one dash says so.
    """
    rows: list[str] = []
    if metrics.cpu_pct is not None:
        rows.append(_with_temp(tr("CPU {pct}%", pct=round(metrics.cpu_pct)),
                               metrics.cpu_temp_c))
    if metrics.ram_used_mb is not None and metrics.ram_total_mb:
        rows.append(tr("RAM {used} / {total} GB",
                       used=_gb(metrics.ram_used_mb),
                       total=_gb(metrics.ram_total_mb)))
    if metrics.gpu_pct is not None:
        rows.append(_with_temp(tr("GPU {pct}%", pct=metrics.gpu_pct),
                               metrics.gpu_temp_c))
    if metrics.vram_used_mb is not None and metrics.vram_total_mb:
        rows.append(tr("VRAM {used} / {total} GB",
                       used=_gb(metrics.vram_used_mb),
                       total=_gb(metrics.vram_total_mb)))
    if metrics.encoder_sessions is not None:
        rows.append(tr("{n} session(s)", n=metrics.encoder_sessions))
    if metrics.fps is not None:
        rows.append(tr("FPS {fps}", fps=round(metrics.fps)))
    if metrics.game:
        rows.append(tr("Game {name}", name=metrics.game))
    return tuple(rows) if rows else ("—",)


def _with_temp(label: str, temp_c: int | None) -> str:
    return tr("{label} · {temp} °C", label=label, temp=temp_c) if temp_c is not None else label


def _gb(mb: int) -> str:
    return f"{mb / 1024:.1f}"


def _session_entries(running: bool, pending: str | None,
                     sandboxes: tuple[str, ...], client: str | None,
                     ) -> tuple[Entry, ...]:
    """The rows inside ``Sessions``."""
    if running:
        return (
            Entry(SESSION_STATUS, _status_label(client), False),
            Entry(STOP_SESSION,
                  tr("stopping …") if pending == "stop" else tr("Stop session"),
                  pending is None),
        )
    if pending == "start":
        return (Entry(SESSIONS_MENU, tr("starting …"), False),)
    if not sandboxes:
        return (Entry(SESSIONS_MENU, tr("No sandboxes yet"), False),)
    return tuple(Entry(f"{START_PREFIX}{name}", name) for name in sandboxes)


def _status_label(client: str | None) -> str:
    if client:
        return tr("Session (running) — {client}", client=client)
    return tr("Session (running)")


def sandbox_of(action: str) -> str | None:
    """The sandbox name a ``start:<name>`` menu action carries, else None."""
    if action.startswith(START_PREFIX):
        return action[len(START_PREFIX):]
    return None


def headless_refusal(*, asks_resolution: bool, sandbox_steam_running: bool,
                     ) -> str | None:
    """Why a start from the tray has to be refused, or None when it may run.

    The tray starts sessions without opening the window, so it cannot ask the
    two questions the session page asks (which resolution, and whether the
    desktop's sandbox Steam may be closed). Rather than open a modal dialog over
    a hidden window, these cases explain themselves in a notification and leave
    the window as the place to answer them.
    """
    if asks_resolution:
        return tr("This sandbox asks for a resolution at startup. Pick it in "
                  "the window.")
    if sandbox_steam_running:
        return tr("The sandbox Steam is open on the desktop. Close it there, "
                  "then start from here.")
    return None


def tooltip(running: bool, client: str | None = None) -> str:
    """Hover text: the one place the tray can show state without being opened."""
    if not running:
        return tr("podstage — stopped")
    if client:
        return tr("podstage — streaming to {client}", client=client)
    return tr("podstage — streaming")


def close_action(tray_available: bool) -> str:
    """What the window's close button means: ``"hide"`` or ``"quit"``.

    Hiding is only safe when a tray host can bring the window back. Without one
    — a bare Wayland session, a WM without StatusNotifier support — hiding
    would strand a process with no visible surface, so the close button keeps
    its old quit meaning instead.
    """
    return "hide" if tray_available else "quit"


def first_hide_notice(notified_before: bool) -> tuple[str, str] | None:
    """The (title, body) of the keep-running notice, or None once it was shown.

    Closing to the tray is the kind of behaviour users discover by accident, so
    the first hide says out loud that the stream survived and where to quit.
    """
    if notified_before:
        return None
    return (
        tr("podstage keeps running"),
        tr("The window is in the tray. A running session keeps streaming — "
           "quit from the tray menu to stop it."),
    )
