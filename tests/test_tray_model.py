"""Tests for the tray decisions (Qt-free, so they run without PyQt6).

The tray itself cannot be asserted from pytest: ``QSystemTrayIcon`` needs a
running ``QApplication`` and a StatusNotifier host. Everything that the tray
*decides* — menu shape, labels, the start submenu, tooltip, what a close means,
when the one-time notice fires — lives in ``podstage.ui.tray_model`` and is
pinned here.
"""

import pytest

from podstage.ui import i18n, tray_model


def entry(entries, action: str):
    """The row with this action: position-independent, dividers move indexes."""
    return next(e for e in entries if e.action == action)


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language("en")


# -- menu while stopped -----------------------------------------------------

def test_the_top_level_is_window_sessions_quit():
    entries = tray_model.menu(running=False, window_visible=True,
                              sandboxes=("Steam Deck",))
    assert [e.action for e in entries] == [
        "open-window", "separator", "sessions", "separator", "quit"]
    assert [e.label for e in entries if e.label] == ["Open UI", "Sessions", "Quit"]


def test_sessions_lists_one_start_row_per_sandbox():
    sessions = entry(tray_model.menu(running=False, window_visible=True,
                                     sandboxes=("Steam Deck", "salón")), "sessions")
    assert sessions.enabled is True
    assert [c.action for c in sessions.children] == ["start:Steam Deck", "start:salón"]
    assert [c.label for c in sessions.children] == ["Steam Deck", "salón"]
    assert all(c.enabled for c in sessions.children)


def test_open_window_is_disabled_while_the_window_is_visible():
    hidden = entry(tray_model.menu(running=False, window_visible=False), "open-window")
    shown = entry(tray_model.menu(running=False, window_visible=True), "open-window")
    assert hidden.enabled is True
    assert shown.enabled is False


def test_sessions_is_disabled_while_a_start_is_in_flight():
    starting = entry(tray_model.menu(running=False, window_visible=True,
                                     pending="start", sandboxes=("a",)), "sessions")
    assert [(c.label, c.enabled) for c in starting.children] == [("starting …", False)]
    assert entry(tray_model.menu(running=False, window_visible=True,
                                 sandboxes=("a",)), "sessions").children[0].label == "a"


def test_without_sandboxes_sessions_says_so():
    sessions = entry(tray_model.menu(running=False, window_visible=True), "sessions")
    assert [(c.label, c.enabled) for c in sessions.children] == \
        [("No sandboxes yet", False)]


# -- menu while running -----------------------------------------------------

def test_running_sessions_shows_the_state_and_offers_stop():
    entries = tray_model.menu(running=True, window_visible=False,
                              client="Steam Deck", sandboxes=("Steam Deck",))
    assert [e.action for e in entries] == [
        "open-window", "separator", "sessions", "separator", "quit"]
    status, stop = entry(entries, "sessions").children
    assert status.label == "Session (running) — Steam Deck"
    assert status.enabled is False  # an indicator, not a button
    assert (stop.label, stop.enabled) == ("Stop session", True)


def test_running_without_a_known_client_still_says_running():
    status = entry(tray_model.menu(running=True, window_visible=True),
                   "sessions").children[0]
    assert status.label == "Session (running)"


def test_stopping_is_disabled_while_it_stops():
    stop = entry(tray_model.menu(running=True, window_visible=True,
                                 pending="stop"), "sessions").children[1]
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
    sessions = entry(tray_model.menu(running=False, window_visible=True,
                                     sandboxes=("Steam Deck",)), "sessions")
    assert tray_model.sandbox_of(sessions.children[0].action) == "Steam Deck"


def test_an_action_that_is_not_a_start_resolves_to_nothing():
    for action in ("open-window", "quit", "sessions"):
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


# -- the performance section ------------------------------------------------
# One row per metric, plain disabled lines: the tray mirrors what the Load card
# shows, it is not a hover opener.

def _metrics(**kw):
    return tray_model.Metrics(**kw)


def test_performance_rows_are_one_line_per_metric():
    rows = tray_model.performance_rows(_metrics(
        cpu_pct=23.4, ram_used_mb=6554, ram_total_mb=31744,
        gpu_pct=41, vram_used_mb=2150, vram_total_mb=8188,
        gpu_temp_c=58, cpu_temp_c=61, encoder_sessions=1, fps=59.8,
        game="Elden Ring"))
    assert rows == (
        "CPU 23% · 61 °C",
        "RAM 6.4 / 31.0 GB",
        "GPU 41% · 58 °C",
        "VRAM 2.1 / 8.0 GB",
        "1 session(s)",
        "FPS 60",
        "Game Elden Ring",
    )


def test_a_metric_without_data_is_left_out():
    rows = tray_model.performance_rows(_metrics(cpu_pct=12.0))
    assert rows == ("CPU 12%",)


def test_temperature_is_dropped_when_the_sensor_is_missing():
    rows = tray_model.performance_rows(_metrics(cpu_pct=12.0, cpu_temp_c=None))
    assert "CPU 12%" in rows
    assert not any("°C" in r for r in rows)


def test_no_data_at_all_says_so_instead_of_showing_nothing():
    assert tray_model.performance_rows(_metrics()) == ("—",)


def test_the_performance_section_sits_between_dividers_and_before_quit():
    entries = tray_model.menu(running=True, window_visible=True, client="shared",
                              metrics=_metrics(cpu_pct=10.0, gpu_pct=20))
    actions = [e.action for e in entries]
    assert actions == ["open-window", "separator", "sessions", "separator",
                       "performance", "perf:CPU 10%", "perf:GPU 20%",
                       "separator", "quit"]
    header = entries[4]
    assert (header.label, header.enabled) == ("Performance", False)
    assert all(e.enabled is False for e in entries[5:7])


def test_no_performance_rows_while_nothing_streams():
    entries = tray_model.menu(running=False, window_visible=True,
                              sandboxes=("shared",))
    assert [e.action for e in entries] == [
        "open-window", "separator", "sessions", "separator", "quit"]
