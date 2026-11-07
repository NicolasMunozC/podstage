"""Tests for the tray decisions (Qt-free, so they run without PyQt6).

The tray itself cannot be asserted from pytest: ``QSystemTrayIcon`` needs a
running ``QApplication`` and a StatusNotifier host. Everything that the tray
*decides* — menu shape, labels, tooltip, what a close means, when the one-time
notice fires — lives in ``podstage.ui.tray_model`` and is pinned here.
"""

import pytest

from podstage.ui import i18n, tray_model


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language("en")


def test_menu_order_and_actions_while_stopped():
    entries = tray_model.menu(running=False, window_visible=True)
    assert [e.action for e in entries] == ["open-window", "toggle-session", "quit"]
    assert [e.label for e in entries] == ["Open window", "Start session", "Quit"]


def test_toggle_entry_follows_the_session():
    stopped = tray_model.menu(running=False, window_visible=True)[1]
    running = tray_model.menu(running=True, window_visible=True)[1]
    assert (stopped.label, stopped.enabled) == ("Start session", True)
    assert (running.label, running.enabled) == ("Stop session", True)


def test_open_window_is_disabled_while_the_window_is_visible():
    hidden = tray_model.menu(running=False, window_visible=False)[0]
    shown = tray_model.menu(running=False, window_visible=True)[0]
    assert hidden.enabled is True
    assert shown.enabled is False


def test_quit_is_always_offered():
    for running in (False, True):
        assert tray_model.menu(running=running, window_visible=False)[-1] == \
            tray_model.Entry("quit", "Quit", True)


def test_tooltip_reports_the_session():
    assert tray_model.tooltip(False) == "podstage — stopped"
    assert tray_model.tooltip(True) == "podstage — streaming"
    assert tray_model.tooltip(True, "Steam Deck") == "podstage — streaming to Steam Deck"


def test_close_hides_only_when_a_tray_host_exists():
    # Hiding without a tray would leave a window nobody can bring back.
    assert tray_model.close_action(tray_available=True) == "hide"
    assert tray_model.close_action(tray_available=False) == "quit"


def test_the_keep_running_notice_fires_once():
    first = tray_model.first_hide_notice(notified_before=False)
    assert first is not None and first[0] == "podstage keeps running"
    assert tray_model.first_hide_notice(notified_before=True) is None


def test_the_toggle_is_disabled_while_an_action_is_in_flight():
    # The page disables its buttons during start/stop; the tray must not offer a
    # second start underneath them.
    starting = tray_model.menu(running=False, window_visible=True, pending="start")[1]
    stopping = tray_model.menu(running=True, window_visible=True, pending="stop")[1]
    assert (starting.label, starting.enabled) == ("starting …", False)
    assert (stopping.label, stopping.enabled) == ("stopping …", False)
    assert tray_model.menu(running=False, window_visible=True, pending=None)[1] \
        .enabled is True
