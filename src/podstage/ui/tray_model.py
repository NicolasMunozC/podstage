"""What the tray decides, kept free of PyQt6.

``QSystemTrayIcon`` and ``QMenu`` cannot be exercised under pytest (they need a
running ``QApplication`` and a StatusNotifier host), so every choice the tray
makes — the menu shape, the labels, the tooltip, what closing the window means,
when the one-time notice fires — is a pure function here and gets pinned by
``tests/test_tray_model.py``. ``ui.app`` only renders these results into Qt
objects.
"""

from dataclasses import dataclass

from .i18n import tr

OPEN_WINDOW = "open-window"
TOGGLE_SESSION = "toggle-session"
QUIT = "quit"


@dataclass(frozen=True)
class Entry:
    """One tray menu row: the action id the window switches on, and its label."""

    action: str
    label: str
    enabled: bool = True


def menu(running: bool, window_visible: bool,
         pending: str | None = None) -> tuple[Entry, ...]:
    """The tray menu for the current state, in display order.

    ``Open window`` is disabled while the window is already visible; the single
    session entry is what start and stop share, so the menu never grows a
    second button that could contradict the first. ``pending`` mirrors the
    session page's in-flight action (``"start"``/``"stop"``): its buttons are
    disabled during it, and the tray must not offer a second start underneath.
    """
    return (
        Entry(OPEN_WINDOW, tr("Open window"), not window_visible),
        _session_entry(running, pending),
        Entry(QUIT, tr("Quit")),
    )


def _session_entry(running: bool, pending: str | None) -> Entry:
    if pending == "start":
        return Entry(TOGGLE_SESSION, tr("starting …"), False)
    if pending == "stop":
        return Entry(TOGGLE_SESSION, tr("stopping …"), False)
    return Entry(TOGGLE_SESSION,
                 tr("Stop session") if running else tr("Start session"))


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
