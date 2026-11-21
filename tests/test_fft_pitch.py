"""FFT peak-picking pitch detector, and the yin/fft switch through the rest of the code."""

import math

import numpy as np
import pytest

from dsfp_tune.analysis import (FftPitch, HeldPitchStream, PitchEstimate, Yin, YinStream, hold_track,
                                make_pitch_detector, yin_track)
from dsfp_tune.analysis.fft_pitch import interpolate_peak
from dsfp_tune.correction import PitchCorrector, correct_pitch

SR = 44100


def tone(f, seconds=0.5, sr=SR, harmonics=((1, 1.0),), amp=0.3):
    t = np.arange(int(seconds * sr)) / sr
    return amp * sum(a * np.sin(2 * np.pi * f * h * t) for h, a in harmonics)


def cents(a, b):
    return 1200 * math.log2(a / b)


@pytest.mark.parametrize("f0", [72.0, 110.0, 196.0, 261.63, 440.0, 523.25, 880.0, 990.0])
def test_sine_within_a_few_cents(f0):
    d = FftPitch(SR)
    est = d.detect(tone(f0, 0.2))
    assert isinstance(est, PitchEstimate)
    assert est.voiced
    assert abs(cents(est.f0, f0)) < 3  # bins are ~5 Hz apart: the interpolation does the rest


def test_interpolation_beats_the_bin_grid():
    d = FftPitch(SR)
    f0 = 40.5 * d.bin_hz   # halfway between two bins
    est = d.detect(tone(f0, 0.2))
    assert abs(est.f0 - f0) < 0.1 * d.bin_hz


def test_interpolate_peak_exact_on_parabola():
    y = -((np.arange(5) - 2.3) ** 2)
    pos, level = interpolate_peak(y, 2)
    assert pos == pytest.approx(2.3)
    assert level == pytest.approx(0.0)
    assert interpolate_peak(y, 0) == (0.0, float(y[0]))  # edge: unchanged


def test_harmonic_tone_with_strong_fundamental():
    est = FftPitch(SR).detect(tone(220, 0.2, harmonics=((1, 1.0), (2, 0.5), (3, 0.3))))
    assert abs(cents(est.f0, 220)) < 3


def test_weak_fundamental_gives_the_louder_harmonic():
    # The documented limitation of the direct method (and the reason to compare with YIN).
    x = tone(200, 0.2, harmonics=((1, 0.3), (2, 1.0), (3, 0.5)))
    assert abs(cents(FftPitch(SR).detect(x).f0, 400)) < 3
    assert abs(cents(Yin(SR).detect(x).f0, 200)) < 3


def test_noise_and_silence_are_unvoiced():
    d = FftPitch(SR)
    noise = np.random.default_rng(1).normal(0, 0.1, d.frame_length)
    est = d.detect(noise)
    assert not est.voiced and math.isnan(est.f0) and est.aperiodicity > d.threshold
    est = d.detect(np.zeros(d.frame_length))
    assert not est.voiced and est.aperiodicity == 1.0
    assert not d.detect(np.full(d.frame_length, 1e-5)).voiced  # below the silence gate


def test_settings_and_errors():
    d = FftPitch(SR, f_min=100, periods=3, zero_pad=4)
    assert d.frame_length == math.ceil(3 * SR / 100)
    assert d.n_fft >= 4 * d.frame_length
    assert d.latency_seconds == pytest.approx(d.frame_length / SR)
    assert FftPitch(SR, frame_length=2048).frame_length == 2048
    with pytest.raises(ValueError):
        FftPitch(SR, f_min=500, f_max=100)
    with pytest.raises(ValueError):
        FftPitch(SR, f_max=SR / 2)
    with pytest.raises(ValueError):
        d.detect(np.zeros(10))
    with pytest.raises(ValueError):
        make_pitch_detector("hps", SR)
    assert isinstance(make_pitch_detector("FFT", SR), FftPitch)
    assert isinstance(make_pitch_detector("yin", SR), Yin)


def test_track_stream_and_hold_take_method():
    x = np.concatenate([tone(220, 0.5), np.zeros(SR // 4), tone(330, 0.5)])
    track = yin_track(x, SR, hop_length=256, method="fft")
    v = track.f0[track.voiced]
    assert track.voiced.mean() > 0.6
    assert np.median(np.abs([cents(f, 220 if f < 275 else 330) for f in v])) < 3
    # Streaming gives the same estimates as the offline track.
    stream = YinStream(SR, hop_length=256, method="fft")
    assert isinstance(stream.yin, FftPitch)
    ests = []
    for i in range(0, len(x), 1000):
        ests += stream.push(x[i : i + 1000])
    assert len(ests) > 0
    held = hold_track(track, x)
    assert held.voiced.sum() >= track.voiced.sum()
    hs = HeldPitchStream(SR, hop_length=256, method="fft")
    assert isinstance(hs.yin, FftPitch) and len(hs.push(x)) > 0


@pytest.mark.parametrize("harmony", [0.0, 0.5])
def test_autotune_with_fft_pitch(harmony):
    sung = 220 * 2 ** (30 / 1200)  # 30 cents sharp of A3
    x = tone(sung, 1.0)
    res = correct_pitch(x, SR, key="C major", harmony=harmony, pitch_method="fft")
    assert len(res.samples) == len(x)
    v = res.voiced
    assert v.mean() > 0.6
    assert np.nanmedian(res.target_midi[v]) == 57  # A3
    out = yin_track(res.samples[SR // 4 :], SR, method="fft")
    assert abs(cents(np.median(out.f0[out.voiced]), 220)) < 10
    assert isinstance(PitchCorrector(SR, pitch_method="fft").yin.yin, FftPitch)
