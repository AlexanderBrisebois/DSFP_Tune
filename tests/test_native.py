"""The C++ kernels behind Yin and FrequencyShifter (dsfp_tune._native) must match the NumPy code.

Skipped unless the extension is built (bash scripts/build_native.sh). The NumPy reference is the
same Python object with its ``_native`` handle removed.
"""

import sys

import numpy as np
import pytest

from dsfp_tune import native
from dsfp_tune.analysis import Yin, yin_track
from dsfp_tune.correction import correct_pitch
from dsfp_tune.synthesis import FrequencyShifter

pytestmark = pytest.mark.skipif(not native.AVAILABLE, reason="dsfp_tune._native not built")

SR = 44100


def _voice(seconds=1.0, f=220.0, cents=35.0, vibrato=25.0):
    t = np.arange(int(seconds * SR)) / SR
    f0 = f * 2 ** ((cents + vibrato * np.sin(2 * np.pi * 5.5 * t)) / 1200)
    phase = 2 * np.pi * np.cumsum(f0) / SR
    rng = np.random.default_rng(3)
    x = 0.15 * sum(a * np.sin((k + 1) * phase) for k, a in enumerate((0.5, 1, 0.7, 0.5, 0.3)))
    return x + 0.002 * rng.standard_normal(len(t))


def _numpy(obj):
    obj._native = None
    return obj


def test_backend_reported():
    assert native.backend() == "c++"
    assert Yin(SR)._native is not None and FrequencyShifter()._native is not None


@pytest.mark.parametrize("kwargs", [{}, {"f_min": 80, "f_max": 800, "threshold": 0.15}, {"silence_db": None},
                                    {"window": 400}])
def test_yin_detect_matches_numpy(kwargs):
    x = _voice()
    cc, py = Yin(SR, **kwargs), _numpy(Yin(SR, **kwargs))
    n = cc.frame_length
    for start in range(0, len(x) - n, 997):
        a, b = cc.detect(x[start : start + n]), py.detect(x[start : start + n])
        assert a.voiced == b.voiced
        np.testing.assert_allclose(a.aperiodicity, b.aperiodicity, atol=1e-9)
        if b.voiced:
            np.testing.assert_allclose(a.f0, b.f0, rtol=1e-9)


def test_yin_silence_and_float32_input():
    yin = Yin(SR)
    assert not yin.detect(np.zeros(yin.frame_length)).voiced
    x = _voice().astype(np.float32)
    est = yin.detect(x[: yin.frame_length + 10])  # longer frames are cut, float32 is accepted
    assert est.voiced and abs(est.f0 - 220 * 2 ** (35 / 1200)) < 15


@pytest.mark.parametrize("ratio", [1.0, 2 ** (1 / 12), 2 ** (-2 / 12), 2 ** (0.3 / 12), 1.5])
@pytest.mark.parametrize("n_fft", [1024, 2048])
def test_shifter_matches_numpy(ratio, n_fft):
    x = _voice()
    cc, py = FrequencyShifter(n_fft), _numpy(FrequencyShifter(n_fft))
    blocks = np.split(x, [100, 613, 4000, 4001, 20000])  # odd block sizes
    a = np.concatenate([cc.process(b, ratio) for b in blocks])
    b = np.concatenate([py.process(b, ratio) for b in blocks])
    np.testing.assert_allclose(a, b, atol=1e-9)


def test_shifter_ratio_changes_and_reset():
    x = _voice(0.5)
    cc, py = FrequencyShifter(), _numpy(FrequencyShifter())
    for i, b in enumerate(np.array_split(x, 40)):
        r = 2 ** (((i % 5) - 2) / 12)
        np.testing.assert_allclose(cc.process(b, r), py.process(b, r), atol=1e-9)
    cc.reset()
    py.reset()
    np.testing.assert_allclose(cc.process(x, 1.2), py.process(x, 1.2), atol=1e-9)
    with pytest.raises(ValueError):
        cc.process(x, 5.0)  # range check stays in Python


def test_whole_corrector_matches_numpy(monkeypatch):
    x = _voice(1.5)
    fast = correct_pitch(x, SR, key="A minor", harmony=0.5)
    for mod in ("dsfp_tune.analysis.yin", "dsfp_tune.synthesis.freq_shift"):  # NumPy everywhere
        monkeypatch.setattr(sys.modules[mod], "_native", None)
    slow = correct_pitch(x, SR, key="A minor", harmony=0.5)
    np.testing.assert_array_equal(fast.voiced, slow.voiced)
    np.testing.assert_array_equal(fast.target_midi, slow.target_midi)
    np.testing.assert_allclose(fast.samples, slow.samples, atol=1e-5)


def test_yin_track_uses_native():
    x = _voice()
    track = yin_track(x, SR)
    assert track.voiced.mean() > 0.9
