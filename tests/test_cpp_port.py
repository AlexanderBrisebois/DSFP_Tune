"""The C++ port (cpp/src/dsp, used by the AU/VST3 plugin) must match the Python reference.

Runs the `dsfp_offline` tool built from cpp/ (skipped if it hasn't been built):

    cmake -S cpp -B cpp/build/core -G Ninja -DDSFP_BUILD_PLUGIN=OFF && cmake --build cpp/build/core

The pitch hold (dsfp_tune/analysis/pitch_hold.py) isn't in the C++ yet, so the corrector is compared
with Python's running plain YIN (see py_corrector).
"""

import csv
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from dsfp_tune.analysis import YinStream, yin_track
from dsfp_tune.correction import PitchCorrector
from dsfp_tune.synthesis import freq_shift
from dsfp_tune.theory import Key

ROOT = Path(__file__).resolve().parents[1]
SR = 44100


def _find_tool():
    env = os.environ.get("DSFP_OFFLINE")
    candidates = [Path(env)] if env else []
    candidates += [ROOT / "cpp/build/core/dsfp_offline", ROOT / "cpp/build/cmake/dsfp_offline"]
    return next((p for p in candidates if p.is_file()), None)


TOOL = _find_tool()
pytestmark = pytest.mark.skipif(TOOL is None, reason="C++ dsfp_offline not built (see module docstring)")


def run(mode, x, tmp_path, *opts, sr=SR):
    src, out = tmp_path / "in.f32", tmp_path / ("out.csv" if mode == "yin" else "out.f32")
    np.asarray(x, dtype="<f4").tofile(src)
    args = [str(TOOL), mode, str(src), str(out), "--sr", str(sr), *map(str, opts)]
    subprocess.run(args, check=True, capture_output=True, timeout=120)
    if mode == "yin":
        with open(out) as f:
            rows = list(csv.DictReader(f))
        return {k: np.array([float(r[k]) for r in rows]) for k in rows[0]}
    return np.fromfile(out, dtype="<f4").astype(np.float64)


def f32(x):
    """What the C++ tool sees: the signal rounded to float32."""
    return np.asarray(x, dtype=np.float32).astype(np.float64)


def voice(f0_of_t, seconds, sr=SR, harmonics=(0.5, 1, 0.7, 0.5, 0.3, 0.2, 0.1)):
    t = np.arange(int(seconds * sr)) / sr
    phase = 2 * np.pi * np.cumsum(np.broadcast_to(f0_of_t(t), t.shape)) / sr
    return f32(0.15 * sum(a * np.sin((k + 1) * phase) for k, a in enumerate(harmonics)))


def held(midi, cents=0.0, vibrato=0.0, seconds=1.0, sr=SR):
    f = 440 * 2 ** ((midi - 69) / 12)
    return voice(lambda t: f * 2 ** ((cents + vibrato * np.sin(2 * np.pi * 5.5 * t)) / 1200), seconds, sr)


def phrase(sr=SR):
    rng = np.random.default_rng(7)
    parts = [held(57, 35, 25, 0.8, sr), np.zeros(int(0.15 * sr)), held(60, -40, 20, 0.7, sr),
             held(64, 30, 0, 0.5, sr), 0.05 * rng.standard_normal(int(0.2 * sr)), held(62, -25, 30, 0.8, sr)]
    return f32(np.concatenate(parts) + 0.002 * rng.standard_normal(sum(map(len, parts))))


def py_corrector(**kwargs):
    """Python PitchCorrector as the C++ port works today: plain YIN, without the pitch hold."""
    pc = PitchCorrector(SR, **kwargs)
    pc.yin = YinStream(SR, hop_length=pc.hop_length)
    return pc


# --- YIN ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["sine", "voice_vibrato", "missing_fundamental", "noise", "phrase", "quiet"])
def test_yin_matches_python(tmp_path, name):
    rng = np.random.default_rng(1)
    t = np.arange(SR) / SR
    x = {
        "sine": f32(0.4 * np.sin(2 * np.pi * 196 * t)),
        "voice_vibrato": held(57, 20, 40),
        "missing_fundamental": f32(sum(np.sin(2 * np.pi * 200 * k * t) for k in (2, 3, 4)) / 3),
        "noise": f32(0.3 * rng.standard_normal(SR)),
        "phrase": phrase(),
        "quiet": f32(held(57) * 10 ** (-65 / 20)),
    }[name]
    py = yin_track(x, SR, hop_length=256)
    cc = run("yin", x, tmp_path, "--hop", 256)
    assert len(cc["f0"]) == len(py.f0)
    np.testing.assert_array_equal(cc["voiced"].astype(bool), py.voiced)
    v = py.voiced
    np.testing.assert_allclose(cc["f0"][v], py.f0[v], rtol=1e-9)  # measured: ~1e-16
    np.testing.assert_allclose(cc["aperiodicity"], py.aperiodicity, atol=1e-9)


def test_yin_other_sample_rate(tmp_path):
    x = held(60, 15, 20, sr=48000)
    py = yin_track(x, 48000, hop_length=256)
    cc = run("yin", x, tmp_path, "--hop", 256, sr=48000)
    np.testing.assert_array_equal(cc["voiced"].astype(bool), py.voiced)
    np.testing.assert_allclose(cc["f0"][py.voiced], py.f0[py.voiced], rtol=1e-9)


# --- frequency shifter -------------------------------------------------------------------------------


@pytest.mark.parametrize("ratio", [1.0, 2 ** (1 / 12), 2 ** (-2 / 12), 2 ** (0.3 / 12), 2 ** (-0.07 / 12), 1.5])
@pytest.mark.parametrize("n_fft", [1024, 2048])
def test_shifter_matches_python(tmp_path, ratio, n_fft):
    x = phrase()[: SR]
    py = freq_shift(x, ratio, n_fft=n_fft)
    cc = run("shift", x, tmp_path, "--ratio", repr(ratio), "--n-fft", n_fft)
    assert len(cc) == len(py)
    np.testing.assert_allclose(cc, py, atol=1e-6)  # measured: float32 rounding (~1.5e-8)


# --- full pitch corrector --------------------------------------------------------------------------------

CASES = {
    "default": ({}, []),
    "a_minor_instant": (dict(key=Key("A", "minor"), retune_ms=0), ["--key", "9:minor", "--retune-ms", 0]),
    "gentle_half": (dict(retune_ms=150, amount=0.5, release_ms=300),
                    ["--retune-ms", 150, "--amount", 0.5, "--release-ms", 300]),
    "c_major_sticky": (dict(key=Key("C", "major"), hysteresis=0.4, note_window_ms=250, note_jump=1.5, hold_ms=150),
                       ["--key", "0:major", "--hysteresis", 0.4, "--note-window-ms", 250, "--note-jump", 1.5, "--hold-ms", 150]),
    "pentatonic_tight": (dict(key=Key("E", "minor_pentatonic"), max_correction=0.6, release_ms=0),
                         ["--key", "4:minor_pentatonic", "--max-correction", 0.6, "--release-ms", 0]),
    "fft1024": (dict(n_fft=1024), ["--n-fft", 1024]),
}


@pytest.mark.parametrize("case", list(CASES))
def test_corrector_matches_python(tmp_path, case):
    py_kwargs, cc_opts = CASES[case]
    x = phrase()
    pc = py_corrector(keep_history=True, **py_kwargs)
    lat = pc.latency
    py_out = pc.process(np.concatenate([x, np.zeros(lat)]))[lat : lat + len(x)]

    frames = tmp_path / "frames.csv"
    cc_out = run("correct", x, tmp_path, "--frames", frames, "--block", 384, *cc_opts)
    with open(frames) as f:
        rows = list(csv.DictReader(f))

    hist = pc.history
    assert len(rows) == len(hist)
    cc_voiced = np.array([r["voiced"] == "1" for r in rows])
    np.testing.assert_array_equal(cc_voiced, [h.voiced for h in hist])
    np.testing.assert_array_equal(  # same target note every frame (NaN = not correcting)
        np.nan_to_num(np.array([float(r["target_midi"]) for r in rows]), nan=-1),
        np.nan_to_num(np.array([h.target_midi for h in hist]), nan=-1))
    np.testing.assert_allclose([float(r["correction"]) for r in rows], [h.correction for h in hist], atol=1e-9)
    assert len(cc_out) == len(x)
    np.testing.assert_allclose(cc_out, py_out, atol=1e-6)
    assert np.abs([h.correction for h in hist]).max() > 0.1  # the case actually corrected something


# --- the built plugin binaries (AU / VST3), loaded by a real plugin host --------------------------------

BUILD = ROOT / "cpp/build/cmake"
HOST = BUILD / "dsfp_plugin_host_artefacts/Release/dsfp_plugin_host"
PLUGINS = {
    "vst3": BUILD / "DSFPTune_artefacts/Release/VST3/DSFP Tune.vst3",
    "au": BUILD / "DSFPTune_artefacts/Release/AU/DSFP Tune.component",
}
PLUGIN_CASES = {
    # plugin parameters (display names / text values)          equivalent Python PitchCorrector arguments
    "defaults": ([], {}),
    # The plugin's Release knob is independent (default 25 ms); in Python release_ms defaults to retune_ms.
    "a_minor_instant": (["Snap to=Nearest note in key", "Key=A", "Scale=Minor", "Attack=0"],
                        dict(key=Key("A", "minor"), retune_ms=0, release_ms=25)),
    "knobs": (["Amount=50", "Release=200", "Stickiness=30", "Note window=200", "Note jump=100", "Hold=100",
               "Max correction=300"],
              dict(amount=0.5, release_ms=200, hysteresis=0.3, note_window_ms=200, note_jump=1.0, hold_ms=100,
                   max_correction=3.0)),
}


@pytest.mark.parametrize("case", list(PLUGIN_CASES))
@pytest.mark.parametrize("fmt", list(PLUGINS))
def test_built_plugin_matches_python(tmp_path, fmt, case):
    """Render audio through the real AU / VST3 binary and compare with the Python corrector."""
    if not (HOST.is_file() and PLUGINS[fmt].exists()):
        pytest.skip("plugin not built (see cpp/README.md)")
    params, py_kwargs = PLUGIN_CASES[case]
    x = phrase()
    src, out = tmp_path / "in.f32", tmp_path / "out.f32"
    x.astype("<f4").tofile(src)
    res = subprocess.run([str(HOST), str(PLUGINS[fmt]), str(src), str(out), str(SR), "333", *params],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    y = np.fromfile(out, dtype="<f4").astype(np.float64)

    # The plugin uses 1024-point frames at 44.1/48 kHz (lower latency than the Python default of 2048).
    pc = py_corrector(n_fft=1024, **py_kwargs)
    lat = pc.latency
    ref = pc.process(np.concatenate([x, np.zeros(lat)]))[lat : lat + len(x)]
    assert len(y) == len(x)
    np.testing.assert_allclose(y, ref, atol=1e-6)  # float32 audio buffers in the host
    assert np.max(np.abs(y - x)) > 0.01  # and it really changed the audio
