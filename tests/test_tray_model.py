"""Tests for the tray decisions (Qt-free, so they run without PyQt6).

The tray itself cannot be asserted from pytest: ``QSystemTrayIcon`` needs a
running ``QApplication`` and a StatusNotifier host. Everything that the tray
*decides* — menu shape, labels, the start submenu, tooltip, what a close means,
when the one-time notice fires — lives in ``podstage.ui.tray_model`` and is
pinned here.
"""

import pytest

from podstage.ui import i18n, tray_model


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language("en")


# -- menu while stopped -----------------------------------------------------

def test_stopped_menu_offers_one_start_entry_per_sandbox():
    entries = tray_model.menu(running=False, window_visible=True,
                              sandboxes=("Steam Deck", "salón"))
    assert [e.action for e in entries] == [
        "open-window", "start-session", "quit"]
    start = entries[1]
    assert (start.label, start.enabled) == ("Start session", True)
    assert [c.action for c in start.children] == ["start:Steam Deck", "start:salón"]
    assert [c.label for c in start.children] == ["Steam Deck", "salón"]
    assert all(c.enabled for c in start.children)


def test_open_window_is_disabled_while_the_window_is_visible():
    hidden = tray_model.menu(running=False, window_visible=False)[0]
    shown = tray_model.menu(running=False, window_visible=True)[0]
    assert hidden.enabled is True
    assert shown.enabled is False


def test_the_start_entry_is_disabled_while_an_action_is_in_flight():
    starting = tray_model.menu(running=False, window_visible=True,
                               pending="start", sandboxes=("a",))[1]
    assert (starting.label, starting.enabled) == ("starting …", False)
    assert tray_model.menu(running=False, window_visible=True,
                           sandboxes=("a",))[1].enabled is True


def test_without_sandboxes_there_is_nothing_to_start():
    start = tray_model.menu(running=False, window_visible=True)[1]
    assert (start.label, start.enabled, start.children) == \
        ("No sandboxes yet", False, ())


# -- menu while running -----------------------------------------------------

def test_running_menu_shows_the_state_and_offers_stop():
    entries = tray_model.menu(running=True, window_visible=False,
                              client="Steam Deck", sandboxes=("Steam Deck",))
    assert [e.action for e in entries] == [
        "open-window", "session-status", "stop-session", "quit"]
    status, stop = entries[1], entries[2]
    assert status.label == "Session (running) — Steam Deck"
    assert status.enabled is False  # an indicator, not a button
    assert (stop.label, stop.enabled) == ("Stop session", True)


def test_running_without_a_known_client_still_says_running():
    status = tray_model.menu(running=True, window_visible=True)[1]
    assert status.label == "Session (running)"


def test_stopping_is_disabled_while_it_stops():
    stop = tray_model.menu(running=True, window_visible=True, pending="stop")[2]
    assert (stop.label, stop.enabled) == ("stopping …", False)


def test_quit_is_always_offered():
    for running in (False, True):
        assert tray_model.menu(running=running, window_visible=False)[-1] == \
            tray_model.Entry("quit", "Quit", True)


# -- tooltip, close, notice -------------------------------------------------

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


# -- starting from the tray -------------------------------------------------

def test_a_tray_start_resolves_the_sandbox_from_the_action_id():
    start = tray_model.menu(running=False, window_visible=True,
                            sandboxes=("Steam Deck",))[1]
    assert tray_model.sandbox_of(start.children[0].action) == "Steam Deck"


def test_an_action_that_is_not_a_start_resolves_to_nothing():
    for action in ("open-window", "quit", "start-session"):
        assert tray_model.sandbox_of(action) is None


def test_a_sandbox_that_cannot_be_started_headless_says_why():
    # The tray must not open a modal dialog over a hidden window, so the cases
    # that need a choice come back as a notification instead of a question.
    assert "resolution" in tray_model.headless_refusal(
        asks_resolution=True, sandbox_steam_running=False).lower()
    assert "steam" in tray_model.headless_refusal(
        asks_resolution=False, sandbox_steam_running=True).lower()
    assert tray_model.headless_refusal(
        asks_resolution=False, sandbox_steam_running=False) is None
