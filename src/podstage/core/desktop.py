"""XDG desktop integration for the server GUI.

Two independent, user-level (no root) integrations, both plain ``.desktop``
files pointing at ``ui.sh``:

  * autostart — ~/.config/autostart/podstage.desktop → GUI launches at login
    ("PC on → server GUI is up").
  * application menu — ~/.local/share/applications/podstage.desktop → the
    GUI shows up in the distribution's app launcher.

Toggled from the GUI's setup page or with ``podstage desktop``; removed again
by ``podstage uninstall`` (see :mod:`podstage.core.teardown`).
"""

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = REPO_ROOT / "ui.sh"
ICON_SRC = REPO_ROOT / "assets/podstage.svg"
# Same pad with the details flipped from cream to near-black, for a light
# panel. Only the Qt icon picks it (see icon_src); the .desktop entry draws
# the one above, because a desktop file cannot react to its own background.
ICON_LIGHT_SRC = REPO_ROOT / "assets/podstage-light.svg"

AUTOSTART_FILE = Path.home() / ".config/autostart/podstage.desktop"
MENU_DIR = Path.home() / ".local/share/applications"
MENU_FILE = MENU_DIR / "podstage.desktop"
# hicolor scalable app icon → `Icon=podstage` resolves to it in any DE.
ICON_DEST = Path.home() / ".local/share/icons/hicolor/scalable/apps/podstage.svg"



def icon_src(*, light_background: bool | None = None) -> Path:
    """The icon asset to draw on the current background.

    ``light_background`` is the caller's knowledge of the theme: True when the
    surface under the icon is light, False when it is dark, None when it is not
    knowable (Qt reports ``ColorScheme.Unknown`` on a bare Wayland session). The
    cream details vanish on a light panel, so a light surface wants the inverted
    asset — but only when that asset is actually there.
    """
    if light_background and ICON_LIGHT_SRC.exists():
        return ICON_LIGHT_SRC
    return ICON_SRC

def desktop_entry(*, autostart: bool) -> str:
    """A .desktop entry launching ui.sh. ``autostart`` adds the KDE
    autostart-ordering hint; the menu variant adds launcher categories."""
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        # The launcher search most desktops ship matches the Name only, so the
        # word the user reaches for has to live there: "consola" is the command
        # it replaces. "podstage" stays in the same string so both find it.
        "Name=podstage (consola)",
        "GenericName=Game streaming console",
        "Comment=podstage Streaming-Server GUI",
        f"Exec={LAUNCHER}",
        "Icon=podstage",
        "Terminal=false",
        "StartupWMClass=podstage",
    ]
    if autostart:
        lines.append("X-KDE-autostart-after=panel")
    else:
        lines.append("Categories=Game;Utility;")
        lines.append("Keywords=consola;stream;moonlight;sunshine;steam;podstage;")
    return "\n".join(lines) + "\n"


def _ensure_icon() -> None:
    """Install the app icon so `Icon=podstage` resolves (idempotent)."""
    if not ICON_SRC.exists():
        return
    ICON_DEST.parent.mkdir(parents=True, exist_ok=True)
    if not ICON_DEST.exists() or ICON_DEST.read_bytes() != ICON_SRC.read_bytes():
        ICON_DEST.write_bytes(ICON_SRC.read_bytes())


def _write(path: Path, *, autostart: bool) -> None:
    if not LAUNCHER.exists():
        raise RuntimeError(f"launcher missing: {LAUNCHER}")
    _ensure_icon()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desktop_entry(autostart=autostart))


# -- autostart (login) ------------------------------------------------------

def autostart_is_enabled() -> bool:
    return AUTOSTART_FILE.exists()


def autostart_enable() -> None:
    _write(AUTOSTART_FILE, autostart=True)


def autostart_disable() -> None:
    AUTOSTART_FILE.unlink(missing_ok=True)


# -- application menu -------------------------------------------------------

def menu_is_installed() -> bool:
    return MENU_FILE.exists()


def menu_install() -> None:
    _write(MENU_FILE, autostart=False)
    _refresh_menu()


def menu_remove() -> None:
    MENU_FILE.unlink(missing_ok=True)
    _refresh_menu()


def _refresh_menu() -> None:
    """Nudge desktops that cache the menu database. Best-effort — most DEs
    pick up the file without it."""
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(MENU_DIR)],
                       capture_output=True, timeout=30, check=False)


# -- teardown ---------------------------------------------------------------

def installed_paths() -> list[Path]:
    """The integration files currently on disk, in removal order. All three
    live under podstage-specific names, so uninstall can drop them blindly."""
    return [p for p in (MENU_FILE, AUTOSTART_FILE, ICON_DEST) if p.exists()]


def remove_all() -> list[str]:
    """Remove both entries and the installed icon. Returns the labels of what
    was actually there."""
    removed = []
    if menu_is_installed():
        menu_remove()
        removed.append("menu entry")
    if autostart_is_enabled():
        autostart_disable()
        removed.append("autostart")
    if ICON_DEST.exists():
        ICON_DEST.unlink(missing_ok=True)
        removed.append("icon")
    return removed
