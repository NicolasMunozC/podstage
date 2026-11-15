"""Display isolation: the session must never own a display the host can reach.

The runtime container runs with ``--network host``, so it shares the host's
abstract AF_UNIX namespace as well as its network namespace. Its ``/tmp`` stays
private, which is why the *filesystem* sockets (``/tmp/.X11-unix/X0``) never
collide — but Xwayland also registers ``@/tmp/.X11-unix/X<n>`` in the abstract
namespace, and Xlib tries the abstract transport first. Left alone, the
session's Xwayland takes ``:0`` and any host client with ``DISPLAY=:0`` (an
Electron app, a browser) is composited by the session and streamed instead of
reaching the host's own X server.

``podstage-display-reserve`` holds the low display numbers non-listening before
the compositor starts: the session's X servers move up to a number the host
does not use, and a host client that aimed at ``:0`` is refused here, so it
falls back to the host's filesystem socket.

The socket tests need a namespace of their own: the names under test are the
real ones, and this host may already have an X server — or a running podstage
session — holding them. `_PROBE` therefore runs inside `unshare -Ur -n`, which
is the same mechanism the container gets from `--network host`, just reversed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from podstage.core import runtime

REPO = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO / "containers" / "runtime"
SOURCE = RUNTIME_DIR / "display-reserve.c"
BINARY = "podstage-display-reserve"
INSTALLED = "/usr/local/bin/" + BINARY

# Child process: holds the real X11 abstract names in its own network
# namespace and reports through its exit status. Kept as one script instead of
# many subprocess calls because a namespace cannot be shared between processes.
_PROBE = r'''
import socket, subprocess, sys, time

binary, mode = sys.argv[1], sys.argv[2]


def addr(n):
    return "\0/tmp/.X11-unix/X" + str(n)


def free(n):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(addr(n))
        return True
    except OSError:
        return False
    finally:
        s.close()


def refused(n):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect(addr(n))
        return False
    except ConnectionRefusedError:
        return True
    finally:
        s.close()


def wait_held(n, proc, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not free(n):
            return
        if proc.poll() is not None:
            sys.exit("helper exited early: " + proc.stderr.read())
        time.sleep(0.05)
    sys.exit("display :%d was never reserved" % n)


start = lambda: subprocess.Popen([binary, "2"], stderr=subprocess.PIPE, text=True)

if mode == "hold":
    proc = start()
    try:
        wait_held(0, proc)
        wait_held(1, proc)
        # Held WITHOUT listening: a client that lands here is refused instead
        # of being composited by the session.
        assert refused(0), ":0 answered a client instead of refusing it"
        assert refused(1), ":1 answered a client instead of refusing it"
        # The count is respected: nothing above it is taken from the host.
        assert free(9), "the count was not respected, :9 was taken too"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    # Releasing on exit is what makes this safe to stop.
    assert free(0), "the names were not released when the helper exited"
elif mode == "occupied":
    other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    other.bind(addr(0))
    other.listen(4)
    proc = start()
    try:
        wait_held(1, proc)
        assert proc.poll() is None, "an occupied display aborted the helper"
        # The server that already owns :0 keeps working: a skip, not a steal.
        assert not refused(0), "the X server already on :0 was disturbed"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        other.close()
elif mode == "bounds":
    # The count is honoured, and a bad count is rejected instead of silently
    # reserving nothing: the entrypoint validates it because the restart loop
    # would otherwise spin on exit 2.
    proc = subprocess.Popen([binary, "6"], stderr=subprocess.PIPE, text=True)
    try:
        wait_held(5, proc)
        assert free(6), "count 6 reserved :6 as well"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    for bad in ("abc", "65", "-1"):
        rc = subprocess.run([binary, bad], capture_output=True).returncode
        assert rc == 2, "count %r was accepted (rc %d)" % (bad, rc)
    zero = subprocess.Popen([binary, "0"], stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(0.3)
        assert zero.poll() is None, "count 0 made the helper exit"
        assert free(0), "count 0 reserved something"
    finally:
        zero.terminate()
        zero.wait(timeout=10)
else:
    sys.exit("unknown mode " + mode)
'''


def _have(*tools: str) -> bool:
    return all(shutil.which(t) for t in tools)


def _netns_works() -> bool:
    if not _have("gcc", "unshare"):
        return False
    done = subprocess.run(["unshare", "-Ur", "-n", "true"],
                          check=False, capture_output=True)
    return done.returncode == 0


needs_prereqs = pytest.mark.skipif(
    not _netns_works(),
    reason="needs gcc plus unprivileged 'unshare -Ur -n' for an isolated namespace",
)


def _build(tmp_path: Path) -> Path:
    out = tmp_path / BINARY
    done = subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(out), str(SOURCE)],
        check=False, capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    return out


def _probe(tmp_path: Path, mode: str) -> None:
    script = tmp_path / "probe.py"
    script.write_text(_PROBE)
    done = subprocess.run(
        ["unshare", "-Ur", "-n", "python3", str(script), str(_build(tmp_path)), mode],
        check=False, capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr or done.stdout


@needs_prereqs
def test_reserve_holds_the_low_display_numbers(tmp_path):
    _probe(tmp_path, "hold")


@needs_prereqs
def test_reserve_leaves_a_display_another_x_server_holds(tmp_path):
    _probe(tmp_path, "occupied")


@needs_prereqs
def test_reserve_count_and_bad_counts(tmp_path):
    _probe(tmp_path, "bounds")


def test_both_entrypoints_validate_the_reserve_count():
    for path in (RUNTIME_DIR / "entrypoint.sh",
                 REPO / "containers/moonshine/entrypoint.sh"):
        text = path.read_text()
        assert "*[!0-9]*" in text, f"{path} would hand a bad count to the helper"


def test_containerfile_builds_the_reserve_helper():
    text = (RUNTIME_DIR / "Containerfile").read_text()
    assert "display-reserve.c" in text
    assert any(INSTALLED in line for line in text.splitlines()), \
        f"the image must install {INSTALLED}"
    compile_line = next(line for line in text.splitlines() if INSTALLED in line)
    assert "-Werror" in compile_line


def test_entrypoint_reserves_displays_before_the_compositor():
    lines = (RUNTIME_DIR / "entrypoint.sh").read_text().splitlines()
    reserve = next(i for i, line in enumerate(lines) if BINARY in line)
    compositor = next(i for i, line in enumerate(lines)
                      if line.startswith("exec labwc"))
    assert reserve < compositor, \
        "the reservation must exist before labwc can spawn Xwayland"
    assert not lines[reserve].rstrip().endswith("|"), \
        "a pipe block-buffers the helper's lines out of the session log"
    assert any("PS_DISPLAY_RESERVE" in line for line in lines), \
        "the reservation needs an opt-out"


def test_the_moonshine_entrypoint_reserves_before_moonshine_starts():
    """The moonshine image derives from the runtime one, so it has the helper
    and the same leak: its gamescope brings up an Xwayland too."""
    lines = (REPO / "containers/moonshine/entrypoint.sh").read_text().splitlines()
    reserve = next(i for i, line in enumerate(lines) if BINARY in line)
    launch = next(i for i, line in enumerate(lines)
                  if line.startswith("    exec moonshine --no-health-check"))
    assert reserve < launch


def test_the_reserve_switch_reaches_the_container(monkeypatch):
    opts = runtime.RuntimeOptions(home_dir=Path("/tmp/home-x"), client="deck")
    monkeypatch.setenv("PS_DISPLAY_RESERVE", "disabled")
    env = runtime.container_env(opts, [], vendor="nvidia")
    assert env["PS_DISPLAY_RESERVE"] == "disabled"
