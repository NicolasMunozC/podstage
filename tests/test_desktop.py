from pathlib import Path

import pytest

from podstage.core import desktop


@pytest.fixture
def paths(tmp_path: Path, monkeypatch):
    launcher = tmp_path / "ui.sh"
    launcher.write_text("#!/bin/sh\n")
    icon_src = tmp_path / "podstage.svg"
    icon_src.write_text("<svg/>")
    monkeypatch.setattr(desktop, "LAUNCHER", launcher)
    monkeypatch.setattr(desktop, "ICON_SRC", icon_src)
    monkeypatch.setattr(desktop, "ICON_DEST", tmp_path / "icons/podstage.svg")
    monkeypatch.setattr(desktop, "AUTOSTART_FILE", tmp_path / "autostart/podstage.desktop")
    monkeypatch.setattr(desktop, "MENU_DIR", tmp_path / "applications")
    monkeypatch.setattr(desktop, "MENU_FILE", tmp_path / "applications/podstage.desktop")
    return tmp_path


def test_autostart_roundtrip(paths):
    assert not desktop.autostart_is_enabled()
    desktop.autostart_enable()
    assert desktop.autostart_is_enabled()
    assert "X-KDE-autostart-after" in desktop.AUTOSTART_FILE.read_text()
    desktop.autostart_disable()
    assert not desktop.autostart_is_enabled()
    desktop.autostart_disable()  # idempotent


def test_menu_roundtrip_installs_icon(paths, monkeypatch):
    monkeypatch.setattr(desktop.shutil, "which", lambda _n: None)  # skip db refresh
    assert not desktop.menu_is_installed()
    desktop.menu_install()
    assert desktop.menu_is_installed()
    text = desktop.MENU_FILE.read_text()
    assert "Categories=Game;Utility;" in text
    assert "Icon=podstage" in text
    assert desktop.ICON_DEST.exists()  # icon installed alongside
    desktop.menu_remove()
    assert not desktop.menu_is_installed()


def test_write_requires_launcher(paths):
    desktop.LAUNCHER.unlink()
    with pytest.raises(RuntimeError):
        desktop.autostart_enable()


def test_remove_all_clears_entries_and_icon(paths, monkeypatch):
    monkeypatch.setattr(desktop, "_refresh_menu", lambda: None)
    assert desktop.remove_all() == []          # nothing installed yet
    assert desktop.installed_paths() == []
    desktop.menu_install()
    desktop.autostart_enable()
    assert desktop.installed_paths() == [desktop.MENU_FILE, desktop.AUTOSTART_FILE,
                                         desktop.ICON_DEST]
    assert desktop.remove_all() == ["menu entry", "autostart", "icon"]
    assert desktop.installed_paths() == []


def test_entry_variants():
    assert "X-KDE-autostart-after" in desktop.desktop_entry(autostart=True)
    assert "Categories" in desktop.desktop_entry(autostart=False)


# -- which icon asset to draw ----------------------------------------------
# The pad keeps its blue body either way; only the details invert, because a
# cream cross disappears on a light panel. The app draws a QIcon, so the
# decision has to be made at draw time — the .desktop entry cannot adapt.

def test_unknown_background_uses_the_dark_asset(paths):
    assert desktop.icon_src(light_background=None) == desktop.ICON_SRC
    assert desktop.icon_src() == desktop.ICON_SRC


def test_light_background_uses_the_inverted_asset(paths, monkeypatch):
    light = paths / "podstage-light.svg"
    light.write_text("<svg/>")
    monkeypatch.setattr(desktop, "ICON_LIGHT_SRC", light)
    assert desktop.icon_src(light_background=True) == light


def test_a_light_asset_that_is_not_installed_falls_back(paths, monkeypatch):
    # Shipping the code without the file must not produce a blank icon.
    monkeypatch.setattr(desktop, "ICON_LIGHT_SRC", paths / "missing.svg")
    assert desktop.icon_src(light_background=True) == desktop.ICON_SRC


def test_the_dark_asset_ignores_a_light_background_claim(paths):
    assert desktop.icon_src(light_background=False) == desktop.ICON_SRC
