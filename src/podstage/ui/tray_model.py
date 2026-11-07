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
START_MENU = "start-session"
START_PREFIX = "start:"
STOP_SESSION = "stop-session"
SESSION_STATUS = "session-status"
QUIT = "quit"


@dataclass(frozen=True)
class Entry:
    """One tray menu row.

    ``children`` turns the row into a submenu; the parent's ``action`` is then
    only an id (the window never dispatches it), and ``enabled`` decides whether
    the submenu opens at all.
    """

    action: str
    label: str
    enabled: bool = True
    children: tuple["Entry", ...] = ()


def menu(running: bool, window_visible: bool, pending: str | None = None,
         sandboxes: tuple[str, ...] = (), client: str | None = None,
         ) -> tuple[Entry, ...]:
    """The tray menu for the current state, in display order.

    A session's own controls replace each other rather than coexisting: while
    something streams the menu shows the state and offers ``Stop session``, and
    while nothing does it offers ``Start session`` with one entry per sandbox.
    ``pending`` mirrors the in-flight action (``"start"``/``"stop"``) so the
    entry that fired cannot be fired again underneath itself.
    """
    entries = [Entry(OPEN_WINDOW, tr("Open window"), not window_visible)]
    if running:
        entries.append(Entry(SESSION_STATUS, _status_label(client), False))
        entries.append(Entry(
            STOP_SESSION,
            tr("stopping …") if pending == "stop" else tr("Stop session"),
            pending is None))
    else:
        entries.append(_start_entry(pending, sandboxes))
    entries.append(Entry(QUIT, tr("Quit")))
    return tuple(entries)


def _status_label(client: str | None) -> str:
    if client:
        return tr("Session (running) — {client}", client=client)
    return tr("Session (running)")


def _start_entry(pending: str | None, sandboxes: tuple[str, ...]) -> Entry:
    if pending == "start":
        return Entry(START_MENU, tr("starting …"), False)
    if not sandboxes:
        return Entry(START_MENU, tr("No sandboxes yet"), False)
    return Entry(START_MENU, tr("Start session"), True,
                 tuple(Entry(f"{START_PREFIX}{name}", name) for name in sandboxes))


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
