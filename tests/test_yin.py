import math

import numpy as np
import pytest

from dsfp_tune.analysis import Yin, yin_track
from dsfp_tune.analysis.yin import (
    absolute_threshold,
    cumulative_mean_normalized_difference,
    difference_function,
    difference_function_direct,
    parabolic_interpolation,
)

SR = 44100


def cents(f, ref):
    return 1200 * np.log2(np.asarray(f) / ref)


def tone(f0, seconds=0.2, sr=SR, harmonics=(1.0,), phase=0.3):
    """Sum of harmonics with the given amplitudes (index 0 = fundamental)."""
    t = np.arange(int(seconds * sr)) / sr
    x = sum(a * np.sin(2 * np.pi * f0 * (k + 1) * t + phase * k) for k, a in enumerate(harmonics))
    return (0.5 * x / np.max(np.abs(x))).astype(np.float32)


# --- individual steps -------------------------------------------------------


def test_fft_difference_matches_direct():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(1500)
    for window, tau_max in [(300, 400), (512, 512), (1000, 499)]:
        fast = difference_function(x, window, tau_max)
        slow = difference_function_direct(x, window, tau_max)
        np.testing.assert_allclose(fast, slow, rtol=1e-9, atol=1e-8)


def test_difference_rejects_short_frame_and_small_fft():
    with pytest.raises(ValueError):
        difference_function(np.zeros(100), 80, 40)
    with pytest.raises(ValueError):
        difference_function(np.zeros(200), 80, 40, n_fft=64)


def test_cmnd_starts_at_one_and_dips_at_period():
    period = 100
    x = np.sin(2 * np.pi * np.arange(1000) / period)
    cmnd = cumulative_mean_normalized_difference(difference_function(x, 300, 400))
    assert cmnd[0] == 1.0
    assert cmnd[period] < 1e-6
    tau, found = absolute_threshold(cmnd, tau_min=20, threshold=0.1)
    assert found and tau == period  # first dip, not 2*period


def test_cmnd_silence_is_aperiodic():
    cmnd = cumulative_mean_normalized_difference(np.zeros(50))
    assert np.all(cmnd == 1.0)


def test_parabolic_interpolation_finds_vertex():
    xs = np.arange(10, dtype=float)
    y = (xs - 4.3) ** 2 + 2.0
    x_min, y_min = parabolic_interpolation(y, 4)
    assert x_min == pytest.approx(4.3)
    assert y_min == pytest.approx(2.0)
    assert parabolic_interpolation(y, 0) == (0.0, y[0])  # edge: unchanged


# --- detector ----------------------------------------------------------------


@pytest.mark.parametrize("f0", [82.41, 110.0, 196.0, 261.63, 440.0, 659.26, 987.77])
def test_pure_tone_accuracy(f0):
    yin = Yin(SR, f_min=70, f_max=1000)
    est = yin.detect(tone(f0)[: yin.frame_length])
    assert est.voiced
    assert abs(cents(est.f0, f0)) < 5
    assert est.confidence > 0.9


@pytest.mark.parametrize("f0", [110.0, 220.0, 440.0])
def test_weak_fundamental_no_octave_error(f0):
    # Fundamental much quieter than its harmonics, like many sung vowels.
    x = tone(f0, harmonics=(0.1, 1.0, 0.8, 0.5, 0.3))
    est = Yin(SR).detect(x)
    assert est.voiced
    assert abs(cents(est.f0, f0)) < 10


def test_missing_fundamental():
    f0 = 200.0
    x = tone(f0, harmonics=(0.0, 1.0, 1.0, 1.0))  # only 400, 600, 800 Hz present
    est = Yin(SR).detect(x)
    assert est.voiced
    assert abs(cents(est.f0, f0)) < 10


def test_noise_is_unvoiced():
    rng = np.random.default_rng(1)
    x = 0.3 * rng.standard_normal(4000)
    est = Yin(SR).detect(x)
    assert not est.voiced and math.isnan(est.f0)


def test_silence_is_unvoiced():
    yin = Yin(SR)
    est = yin.detect(np.zeros(yin.frame_length))
    assert not est.voiced and math.isnan(est.f0)
    # Gate disabled: still unvoiced, and no NaN/inf warnings from 0/0.
    est = Yin(SR, silence_db=None).detect(np.zeros(yin.frame_length))
    assert not est.voiced


def test_quiet_tone_gated_unless_disabled():
    x = tone(220.0) * 10 ** (-70 / 20)  # about -76 dBFS RMS
    assert not Yin(SR).detect(x).voiced
    assert Yin(SR, silence_db=None).detect(x).voiced


def test_other_sample_rate():
    sr = 16000
    est = Yin(sr, f_max=800).detect(tone(330.0, sr=sr))
    assert abs(cents(est.f0, 330.0)) < 5


def test_frame_length_and_latency():
    yin = Yin(44100, f_min=80, f_max=1000)
    assert yin.tau_max == math.ceil(44100 / 80)
    assert yin.frame_length == yin.window + yin.tau_max
    assert yin.n_fft >= yin.frame_length
    assert yin.latency_seconds == pytest.approx(yin.frame_length / 44100)
    with pytest.raises(ValueError):
        Yin(SR).detect(np.zeros(yin.frame_length // 2))  # too short


@pytest.mark.parametrize(
    "kwargs",
    [dict(f_min=500, f_max=100), dict(f_min=0), dict(f_max=20000), dict(threshold=1.5)],
)
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        Yin(SR, **kwargs)


# --- tracking ----------------------------------------------------------------


def test_track_two_notes_with_gap():
    a3, e4 = 220.0, 329.63
    x = np.concatenate([tone(a3, 0.3), np.zeros(int(0.2 * SR), np.float32), tone(e4, 0.3)])
    tr = yin_track(x, SR, hop_length=256)

    assert len(tr.times) == len(tr.f0) == len(tr.voiced) == len(tr.aperiodicity)
    assert np.all(np.diff(tr.times) > 0)

    in_a = (tr.times > 0.05) & (tr.times < 0.25)
    in_gap = (tr.times > 0.38) & (tr.times < 0.42)
    in_e = (tr.times > 0.55) & (tr.times < 0.75)
    assert tr.voiced[in_a].all() and np.all(np.abs(cents(tr.f0[in_a], a3)) < 5)
    assert not tr.voiced[in_gap].any() and np.isnan(tr.f0[in_gap]).all()
    assert tr.voiced[in_e].all() and np.all(np.abs(cents(tr.f0[in_e], e4)) < 5)


def test_track_follows_vibrato():
    # 220 Hz with +/-50 cent vibrato at 5 Hz, like a held sung note.
    t = np.arange(int(1.0 * SR)) / SR
    cents_curve = 50 * np.sin(2 * np.pi * 5 * t)
    f_inst = 220.0 * 2 ** (cents_curve / 1200)
    x = 0.5 * np.sin(2 * np.pi * np.cumsum(f_inst) / SR)
    tr = yin_track(x, SR, hop_length=256)

    inner = (tr.times > 0.1) & (tr.times < 0.9)
    expected = 220.0 * 2 ** (50 * np.sin(2 * np.pi * 5 * tr.times[inner]) / 1200)
    assert tr.voiced[inner].all()
    # Each frame averages ~25 ms of a moving pitch, so allow some lag/smoothing error.
    assert np.max(np.abs(cents(tr.f0[inner], expected))) < 15


def test_track_short_and_invalid_input():
    tr = yin_track(np.zeros(10), SR)
    assert len(tr.f0) == 1 and not tr.voiced[0]
    with pytest.raises(ValueError):
        yin_track(np.zeros((100, 2)), SR)
    with pytest.raises(ValueError):
        yin_track(np.zeros(100), SR, hop_length=0)
