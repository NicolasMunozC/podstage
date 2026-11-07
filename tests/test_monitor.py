"""Tests for the telemetry parsers (no hardware, no subprocesses)."""

import json
import os
import time

from podstage.core import monitor

# intel_gpu_top -J streams an array of period samples; the first one right
# after startup is typically all zeros.
_IGT_STREAM = """[
{
  "period": {"duration": 300.1, "unit": "ms"},
  "engines": {
    "Render/3D/0": {"busy": 0.0, "sema": 0.0, "wait": 0.0, "unit": "%"},
    "Video/0": {"busy": 0.0, "sema": 0.0, "wait": 0.0, "unit": "%"}
  }
},
{
  "period": {"duration": 300.0, "unit": "ms"},
  "engines": {
    "Render/3D/0": {"busy": 42.6, "sema": 0.0, "wait": 0.0, "unit": "%"},
    "Video/0": {"busy": 12.0, "sema": 0.0, "wait": 0.0, "unit": "%"}
  }
}
"""


def test_parse_intel_gpu_top_uses_last_sample():
    stats = monitor._parse_intel_gpu_top(_IGT_STREAM)
    assert stats is not None
    assert stats.util_pct == 43  # 42.6 rounded, from the LAST sample
    assert stats.name == "Intel GPU"
    assert stats.mem_used_mb is None  # i915/xe expose no VRAM counters
    assert stats.encoder_sessions is None


def test_parse_intel_gpu_top_tolerates_truncated_tail():
    # The sampling timeout usually kills the tool mid-object; the trailing
    # fragment must be ignored, not crash the parser.
    truncated = _IGT_STREAM + ',\n{\n  "period": {"duration": 3'
    stats = monitor._parse_intel_gpu_top(truncated)
    assert stats is not None and stats.util_pct == 43


def test_parse_intel_gpu_top_empty_or_garbage():
    assert monitor._parse_intel_gpu_top("") is None
    assert monitor._parse_intel_gpu_top("intel_gpu_top: PMU failed") is None
    assert monitor._parse_intel_gpu_top('{"engines": {}}') is None


# -- game perf (the in-container probe's JSON in the shared tmpfs) ----------

def _write_perf(tmp_path, payload: str, age_s: float = 0.0):
    path = tmp_path / "perf.json"
    path.write_text(payload)
    if age_s:
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
    return path


def test_game_perf_reads_probe_sample(tmp_path):
    _write_perf(tmp_path, json.dumps({
        "schema": 1, "source": "gamescope_control", "ts": 1, "app_id": 620,
        "samples": 59, "fps": 59.4}))
    perf = monitor.game_perf(tmp_path / "perf.json")
    assert perf is not None
    assert perf.samples == 59 and perf.fps == 59.4


def test_game_perf_idle_window_is_not_an_error(tmp_path):
    # Probe alive, nothing presented: a real answer, distinct from "no probe".
    _write_perf(tmp_path, json.dumps({"schema": 1, "app_id": 769, "samples": 0}))
    perf = monitor.game_perf(tmp_path / "perf.json")
    assert perf is not None
    assert perf.samples == 0 and perf.fps is None


def test_game_perf_stale_file_is_dropped(tmp_path):
    _write_perf(tmp_path, json.dumps({"samples": 60, "fps": 60.0}),
                age_s=monitor.PERF_MAX_AGE_S + 5)
    assert monitor.game_perf(tmp_path / "perf.json") is None


def test_game_perf_missing_or_garbage(tmp_path):
    assert monitor.game_perf(tmp_path / "perf.json") is None  # probe never ran
    _write_perf(tmp_path, '{"samples": 12, "fps":')  # caught mid-write
    assert monitor.game_perf(tmp_path / "perf.json") is None
    _write_perf(tmp_path, "[1, 2, 3]")  # valid JSON, wrong shape
    assert monitor.game_perf(tmp_path / "perf.json") is None


def test_game_perf_ignores_nonsense_values(tmp_path):
    _write_perf(tmp_path, json.dumps({"app_id": 0, "samples": 3, "fps": 0}))
    perf = monitor.game_perf(tmp_path / "perf.json")
    assert perf is not None
    assert perf.fps is None


# -- host CPU / RAM ---------------------------------------------------------
#
# Whole machine on purpose: the session's own cgroup used to be located
# through the labwc command line, which the moonshine backend never runs, so
# both meters simply stayed empty there.

_STAT = ("cpu  100 20 30 400 50 0 0 0 0 0\n"
         "cpu0 50 10 15 200 25 0 0 0 0 0\n"
         "intr 1234\n")

_MEMINFO = ("MemTotal:       32432124 kB\n"
            "MemFree:         1000000 kB\n"
            "MemAvailable:   17000000 kB\n"
            "Buffers:          123456 kB\n")


def _patch_proc(monkeypatch, stat: str, meminfo: str):
    real = monitor.Path

    class _P(type(real("/"))):
        def read_text(self, *a, **kw):
            if str(self) == "/proc/stat":
                return stat
            if str(self) == "/proc/meminfo":
                return meminfo
            return real(str(self)).read_text(*a, **kw)

    monkeypatch.setattr(monitor, "Path", _P)


def test_proc_stat_cpu_counts_iowait_as_idle(monkeypatch):
    """iowait is not the CPU doing work; counting it busy would show a machine
    waiting on disk as saturated. Only the aggregate ``cpu`` line counts, the
    per-core lines below it would double everything."""
    _patch_proc(monkeypatch, _STAT, _MEMINFO)
    total = 100 + 20 + 30 + 400 + 50
    assert monitor._proc_stat_cpu() == (100 + 20 + 30, total)


def test_proc_stat_cpu_rejects_a_garbage_first_line(monkeypatch):
    _patch_proc(monkeypatch, "not a cpu line\n", _MEMINFO)
    assert monitor._proc_stat_cpu() is None


def test_meminfo_used_excludes_reclaimable_cache(monkeypatch):
    """"In use" means MemTotal - MemAvailable: page cache is reclaimable and
    counting it as used would show every idle machine as nearly full."""
    _patch_proc(monkeypatch, _STAT, _MEMINFO)
    used, total = monitor._meminfo_mb()
    assert total == 32432124 >> 10
    assert used == (32432124 - 17000000) >> 10


def test_host_stats_survives_unreadable_proc(monkeypatch):
    """A missing reading must blank one meter, not raise into the poll thread."""
    monkeypatch.setattr(monitor, "_proc_stat_cpu", lambda: None)
    monkeypatch.setattr(monitor, "_meminfo_mb", lambda: (None, None))
    stats = monitor.host_stats(sample_interval=0)
    assert (stats.cpu_pct, stats.mem_used_mb, stats.mem_total_mb) == (None, None, None)


def test_host_stats_cpu_percentage_over_the_interval(monkeypatch):
    """Two samples, busy grew by 50 of 100 total jiffies."""
    samples = iter([(100, 1000), (150, 1100)])
    monkeypatch.setattr(monitor, "_proc_stat_cpu", lambda: next(samples))
    monkeypatch.setattr(monitor, "_meminfo_mb", lambda: (8, 16))
    stats = monitor.host_stats(sample_interval=0)
    assert stats.cpu_pct == 50.0
    assert (stats.mem_used_mb, stats.mem_total_mb) == (8, 16)


# -- GPU telemetry (nvidia-smi CSV) ----------------------------------------

def _patch_nvidia_smi(monkeypatch, out: str, rc: int = 0) -> list[list[str]]:
    """Stand in for the subprocess and record the command line it was given."""
    calls: list[list[str]] = []

    def fake_run(cmd, timeout=5):
        calls.append(cmd)
        return rc, out

    monkeypatch.setattr(monitor, "_run", fake_run)
    return calls


def test_nvidia_parse_reads_temperature_from_the_same_query(monkeypatch):
    """Temperature rides along in the existing nvidia-smi call: the poll runs
    every couple of seconds, a second subprocess per tick is not free."""
    calls = _patch_nvidia_smi(
        monkeypatch,
        "NVIDIA GeForce RTX 4070, 37, 1234, 12282, 1, 52")
    stats = monitor._nvidia_gpu_stats()
    assert len(calls) == 1  # not a second query for the temperature
    assert "temperature.gpu" in calls[0][1]
    assert stats is not None
    assert stats.temp_c == 52
    assert (stats.util_pct, stats.mem_used_mb, stats.mem_total_mb) == (37, 1234, 12282)
    assert stats.encoder_sessions == 1


def test_nvidia_parse_without_temperature_field_keeps_temp_none(monkeypatch):
    # An older/hand-trimmed row must not shift the other columns or crash.
    _patch_nvidia_smi(monkeypatch, "NVIDIA GeForce RTX 4070, 37, 1234, 12282, 1")
    stats = monitor._nvidia_gpu_stats()
    assert stats is not None
    assert stats.temp_c is None
    assert (stats.util_pct, stats.mem_used_mb, stats.mem_total_mb) == (37, 1234, 12282)


def test_nvidia_parse_unreadable_temperature_is_none(monkeypatch):
    # nvidia-smi prints "[N/A]" for fields the driver does not report.
    _patch_nvidia_smi(monkeypatch, "NVIDIA GeForce RTX 4070, 37, 1234, 12282, 0, [N/A]")
    stats = monitor._nvidia_gpu_stats()
    assert stats is not None and stats.temp_c is None


# -- temperatures -----------------------------------------------------------
# hwmon and /sys/class/thermal both report millidegrees. Every lookup is
# optional: no sensor means no number, never an exception in the poll thread.

def _write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")


def test_amd_gpu_temp_is_millidegrees_to_celsius(monkeypatch, tmp_path):
    dev = tmp_path / "device"
    _write(dev / "gpu_busy_percent", "17")
    _write(dev / "hwmon" / "hwmon3" / "temp1_input", "48000")
    monkeypatch.setattr(monitor, "_amd_card_dir", lambda: dev)
    stats = monitor._amd_gpu_stats()
    assert stats is not None
    assert stats.temp_c == 48
    assert stats.util_pct == 17


def test_amd_gpu_temp_missing_or_garbage_leaves_it_none(monkeypatch, tmp_path):
    dev = tmp_path / "device"
    _write(dev / "gpu_busy_percent", "17")
    _write(dev / "hwmon" / "hwmon3" / "temp1_input", "[N/A]")
    monkeypatch.setattr(monitor, "_amd_card_dir", lambda: dev)
    stats = monitor._amd_gpu_stats()
    assert stats is not None
    assert stats.temp_c is None
    assert stats.util_pct == 17  # one bad file does not blank the row


def test_cpu_temp_from_hwmon_sensor(tmp_path):
    hwmon, thermal = tmp_path / "hwmon", tmp_path / "thermal"
    _write(hwmon / "hwmon0" / "name", "nvme")
    _write(hwmon / "hwmon0" / "temp1_input", "81000")
    _write(hwmon / "hwmon1" / "name", "k10temp")
    _write(hwmon / "hwmon1" / "temp1_input", "61500")
    assert monitor.cpu_temp_c(hwmon, thermal) == 62  # round(61.5)


def test_cpu_temp_falls_back_to_a_cpu_thermal_zone(tmp_path):
    hwmon, thermal = tmp_path / "hwmon", tmp_path / "thermal"
    hwmon.mkdir()
    _write(thermal / "thermal_zone0" / "type", "acpitz")
    _write(thermal / "thermal_zone0" / "temp", "27000")
    _write(thermal / "thermal_zone1" / "type", "x86_pkg_temp")
    _write(thermal / "thermal_zone1" / "temp", "70000")
    assert monitor.cpu_temp_c(hwmon, thermal) == 70


def test_cpu_temp_is_none_when_no_sensor_exists(tmp_path):
    hwmon, thermal = tmp_path / "hwmon", tmp_path / "thermal"
    hwmon.mkdir()
    thermal.mkdir()
    assert monitor.cpu_temp_c(hwmon, thermal) is None
    assert monitor.cpu_temp_c(tmp_path / "absent", tmp_path / "absent") is None


def test_host_stats_carries_the_cpu_temp_without_extra_blocking(monkeypatch):
    """The 0.4 s CPU window is the only sleep in a poll; the sensors are file
    reads and must not add a second wait."""
    monkeypatch.setattr(monitor, "_meminfo_mb", lambda: (8, 16))
    monkeypatch.setattr(monitor, "cpu_temp_c", lambda: 55)
    sleeps = []
    monkeypatch.setattr(monitor.time, "sleep", lambda s: sleeps.append(s))
    samples = iter([(100, 1000), (150, 1100)])
    monkeypatch.setattr(monitor, "_proc_stat_cpu", lambda: next(samples))
    stats = monitor.host_stats(sample_interval=0.4)
    assert stats.cpu_temp_c == 55
    assert sleeps == [0.4]  # exactly the CPU window, nothing more


def test_host_stats_keeps_the_temp_when_proc_stat_is_unreadable(monkeypatch):
    monkeypatch.setattr(monitor, "_proc_stat_cpu", lambda: None)
    monkeypatch.setattr(monitor, "_meminfo_mb", lambda: (8, 16))
    monkeypatch.setattr(monitor, "cpu_temp_c", lambda: 55)
    stats = monitor.host_stats(sample_interval=0)
    assert (stats.cpu_pct, stats.mem_used_mb, stats.mem_total_mb) == (None, 8, 16)
    assert stats.cpu_temp_c == 55


# -- GUI poll cadence -------------------------------------------------------
# A snapshot costs ~0.4 s of sampling when a session runs and one podman
# inspect when it does not, so the cadence is the GUI's idle-power knob.

def test_visible_window_polls_at_the_interactive_rate():
    assert monitor.poll_interval_ms(running=True, window_visible=True) == 2000
    assert monitor.poll_interval_ms(running=False, window_visible=True) == 2000


def test_a_hidden_running_session_still_reports_state():
    # Slower than interactive, fast enough that the tray does not lie.
    assert monitor.poll_interval_ms(running=True, window_visible=False) == 5000


def test_a_hidden_idle_window_backs_off_hard():
    assert monitor.poll_interval_ms(running=False, window_visible=False) == 10000


def test_cadence_is_never_faster_than_the_interactive_rate():
    states = [(r, v) for r in (False, True) for v in (False, True)]
    assert min(monitor.poll_interval_ms(*s) for s in states) == 2000
