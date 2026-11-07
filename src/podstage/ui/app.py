"""podstage server management window (PyQt6).

One main window plus a system tray icon — closing the window parks the process
in the tray so a running stream survives, and only the tray's Quit ends it:

  Session    start/stop, live telemetry, stream quality
  Sandboxen  client profiles + isolated Steam HOMEs (create/login/delete)
  Setup      doctor checks with one-click (pkexec) fixes, udev rules install
  Logs       journald tail of the runtime container

Backend logic lives in ``core.*``; the UI only wires it to widgets and keeps
every blocking call on worker threads (see ``ui.workers``). Root-gated steps
go through pkexec — the GUI never needs a terminal.

Run under the Qt-capable Python (brew's, with PyQt6) via ``ui.sh``.
"""

import os
import sys

try:
    from PyQt6.QtCore import QCoreApplication, QObject, Qt, pyqtSignal
    from PyQt6.QtGui import QAction, QGuiApplication, QIcon
    from PyQt6.QtWidgets import (
        QApplication,
        QFrame,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QMainWindow,
        QMenu,
        QMessageBox,
        QStackedWidget,
        QSystemTrayIcon,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    print("PyQt6 is not installed. Run via ./ui.sh (brew's python3), or: "
          "pip install -e '.[ui]'", file=sys.stderr)
    raise SystemExit(1)

from .. import __version__
from ..config import AppConfig
from ..core import monitor, runtime
from . import theme, tray_model
from .i18n import tr
from .pages.logs_page import LogsPage
from .pages.sandbox_page import SandboxPage
from .pages.session_page import SessionPage
from .pages.setup_page import SetupPage
from .workers import PollWorker, start_action


class AppContext(QObject):
    """Shared config + change notification for all pages."""

    config_changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.config = AppConfig.load_or_seed()
        # Apply the persisted language before any page builds its widgets, so
        # every tr() call below already resolves to the chosen language.
        from .i18n import set_language
        set_language(self.config.language)

    def save(self) -> None:
        self.config.save()
        self.config_changed.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("podstage")
        # Start roomy enough that no button/label is clipped, and never let it
        # shrink below where the wider rows (setup checks, session controls +
        # preview) still fit.
        self.resize(1000, 800)
        self.setMinimumSize(920, 680)
        self._ctx = AppContext()
        self._poll: PollWorker | None = None
        self._pool: list = []          # keeps the quit-teardown worker alive
        self._quitting = False         # a quit sequence has started
        self._running = False          # last snapshot's session state
        self._client: str | None = None
        self._tray: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._tray_actions: dict[str, QAction] = {}
        self._tray_available = False
        self._tray_notified = False    # the keep-running notice was shown

        self._session_page = SessionPage(self._ctx)
        self._sandbox_page = SandboxPage(self._ctx)
        self._setup_page = SetupPage(self._ctx)
        self._logs_page = LogsPage(self._ctx)
        self._build()
        self._build_tray()

        self._start_poll()

    # -- layout ----------------------------------------------------------
    def _build(self) -> None:
        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(160)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 16, 12, 12)
        side.setSpacing(8)
        brand = QLabel("podstage")
        brand.setObjectName("brand")
        side.addWidget(brand)
        self._nav = QListWidget()
        self._nav.setObjectName("nav")
        self._nav.addItems([tr("Session"), tr("Sandboxes"), tr("Setup"), tr("Logs")])
        self._nav.currentRowChanged.connect(self._on_nav)
        side.addWidget(self._nav, 1)
        self._global_state = QLabel(tr("○ stopped"))
        self._global_state.setObjectName("globalState")
        self._global_state.setWordWrap(True)  # "running | <profile>" exceeds 160 px
        side.addWidget(self._global_state)
        version = QLabel(f"v{__version__}")
        version.setProperty("muted", True)
        side.addWidget(version)
        h.addWidget(sidebar)

        self._stack = QStackedWidget()
        for page in (self._session_page, self._sandbox_page,
                     self._setup_page, self._logs_page):
            self._stack.addWidget(page)
        h.addWidget(self._stack, 1)

        self.setCentralWidget(central)
        self.setStyleSheet(theme.qss())
        self._nav.setCurrentRow(0)

    def _on_nav(self, row: int) -> None:
        self._stack.setCurrentIndex(row)

    # -- tray ------------------------------------------------------------
    def _build_tray(self) -> None:
        """Own a StatusNotifierItem when the host has one.

        Without a tray host the icon has nowhere to live, so ``_tray_available``
        stays False and the close button keeps its old quit meaning
        (``tray_model.close_action``).
        """
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray_available = True
        self._tray = QSystemTrayIcon(_app_icon(), self)
        self._tray_menu = QMenu(self)
        for entry in tray_model.menu(running=False, window_visible=False):
            action = QAction(entry.label, self._tray_menu)
            action.setEnabled(entry.enabled)
            action.triggered.connect(
                lambda _checked=False, a=entry.action: self._on_tray_action(a))
            self._tray_menu.addAction(action)
            self._tray_actions[entry.action] = action
        self._tray.setContextMenu(self._tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        self._refresh_tray()

    def _refresh_tray(self) -> None:
        """Re-render the menu + tooltip from the current state."""
        if self._tray is None:
            return
        for entry in tray_model.menu(running=self._running,
                                     window_visible=self.isVisible(),
                                     pending=self._session_page.pending):
            action = self._tray_actions[entry.action]
            action.setText(entry.label)
            action.setEnabled(entry.enabled)
        self._tray.setToolTip(tray_model.tooltip(self._running, self._client))

    def _on_tray_activated(self, reason:
                           QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_window()

    def _on_tray_action(self, action: str) -> None:
        if action == tray_model.OPEN_WINDOW:
            self._show_window()
        elif action == tray_model.QUIT:
            self._begin_quit()
        elif action == tray_model.TOGGLE_SESSION:
            # Show the window first: start/stop can raise its own questions
            # (bootstrap, one Steam at a time, resolution), and a modal dialog
            # over a hidden window would be invisible.
            self._show_window()
            if self._running:
                self._session_page.stop_session()
            else:
                self._session_page.start_session()
            # The action just disabled itself; do not wait for the next snapshot
            # to say so.
            self._refresh_tray()

    def _toggle_window(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self._show_window()

    def _show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _notify_hidden(self) -> None:
        notice = tray_model.first_hide_notice(self._tray_notified)
        if notice is None or self._tray is None:
            return
        self._tray_notified = True
        self._tray.showMessage(notice[0], notice[1],
                               QSystemTrayIcon.MessageIcon.Information, 6000)

    # -- polling ---------------------------------------------------------
    def _start_poll(self) -> None:
        self._poll = PollWorker()
        self._poll.updated.connect(self._on_snapshot)
        self._poll.start()

    def _on_snapshot(self, snap: monitor.Snapshot) -> None:
        self._session_page.on_snapshot(snap)
        self._sandbox_page.on_snapshot(snap)
        self._running = snap.running
        self._client = snap.client_profile
        if snap.running:
            owner = f" | {snap.client_profile}" if snap.client_profile else ""
            self._global_state.setText(tr("● running") + owner)
            self._global_state.setProperty("state", "running")
        else:
            self._global_state.setText(tr("○ stopped"))
            self._global_state.setProperty("state", "stopped")
        theme.repolish(self._global_state)
        self._apply_poll_cadence()
        self._refresh_tray()

    def _apply_poll_cadence(self) -> None:
        """Match the poll cost to what the user can actually see."""
        if self._poll is not None:
            self._poll.set_interval(
                monitor.poll_interval_ms(self._running, self.isVisible()))

    def mousePressEvent(self, event) -> None:
        """Clicking empty space drops the focus from whatever field holds it.

        Qt keeps the caret in a field until another focusable widget takes
        it. Widgets that do not accept mouse presses let the event bubble up
        here, which is the "clicked on nothing" case. Clearing focus also
        fires editingFinished, so a typed value is committed.
        """
        focused = self.focusWidget()
        if focused is not None:
            focused.clearFocus()
        super().mousePressEvent(event)

    # -- lifecycle -------------------------------------------------------
    def closeEvent(self, event) -> None:
        """Closing the window parks podstage in the tray instead of quitting.

        The stream outlives the window; only the tray menu's Quit (or the close
        button on a host without a tray) ends the process, and that path still
        asks before stopping a running container.
        """
        event.ignore()
        if self._quitting:
            return  # the quit sequence owns the exit from here
        if tray_model.close_action(self._tray_available) == "hide":
            self.hide()
            self._notify_hidden()
            return
        self._begin_quit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_poll_cadence()
        self._refresh_tray()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._apply_poll_cadence()
        self._refresh_tray()

    def _begin_quit(self) -> None:
        """Stop a running session on a worker, then exit the process.

        The container stop runs off the UI thread so a modal-free exit still
        shows "stopping …" while ``podman stop`` works.
        """
        if self._quitting:
            return
        st = runtime.status()
        if st.running and not self._confirm_quit(st):
            return
        self._quitting = True
        if self._poll is not None:
            self._poll.stop()
            self._poll.wait(1500)
        self._logs_page.shutdown()
        if self._tray is not None:
            self._tray.hide()
        if not st.running:
            QApplication.quit()
            return
        self._global_state.setText(tr("stopping …"))
        start_action(self._pool, self._stop_container, "Stop",
                     self._on_teardown_done)

    def _confirm_quit(self, st) -> bool:
        owner = f" ({st.client})" if st.client else ""
        answer = QMessageBox.warning(
            self, tr("Quit podstage?"),
            tr("A streaming session is running{owner}. Quitting stops the "
               "container and ends the stream.\n\nStop it and quit?", owner=owner),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Yes

    @staticmethod
    def _stop_container() -> str:
        try:
            runtime.stop(timeout=5)
        except RuntimeError:
            pass  # best-effort teardown on quit
        return "stopped"

    def _on_teardown_done(self, _ok: bool, _msg: str) -> None:
        QApplication.quit()


def _light_background() -> bool | None:
    """Whether Qt knows the colour scheme, and which one it is.

    ``None`` means "cannot tell": a bare Wayland session reports
    ``ColorScheme.Unknown`` and a *light* default palette, which is why the
    palette is not a fallback — on a dark desktop without a platform theme it
    would answer "light" and hand back the inverted icon.
    """
    hints = QGuiApplication.styleHints()
    probe = getattr(hints, "colorScheme", None)  # Qt < 6.5 has no such hint
    scheme: object = probe() if probe is not None else None
    known = getattr(Qt, "ColorScheme", None)
    if known is None:
        return None
    if scheme == known.Light:
        return True
    if scheme == known.Dark:
        return False
    return None


def _app_icon() -> QIcon:
    from ..core import desktop
    src = desktop.icon_src(light_background=_light_background())
    if src.exists():
        return QIcon(str(src))
    return QIcon.fromTheme("applications-games")


def main(argv: list[str] | None = None) -> int:
    # Qt plugin dir from ui.sh — deliberately not QT_PLUGIN_PATH: children
    # (xdg-open → kde-open, a host Qt app) would inherit that and abort on
    # the ABI-foreign plugins.
    plugin_path = os.environ.pop("PS_QT_PLUGIN_PATH", "")
    if plugin_path:
        QCoreApplication.addLibraryPath(plugin_path)
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("podstage")
    app.setDesktopFileName("podstage")  # Wayland maps the window to the .desktop icon
    app.setWindowIcon(_app_icon())
    # Hiding the window must not end the process: the tray is the way back, and
    # Quit (tray menu, or the close button on a tray-less host) is the only exit.
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
