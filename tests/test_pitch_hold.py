"""Pitch hold after YIN: carry-over through detection gaps, octave-glitch rejection, median."""

import numpy as np
import pytest

from dsfp_tune.analysis import HeldPitchStream, PitchEstimate, PitchHold, hold_track, yin_track
from tests.test_correction import SR, held

HOP = 256


def feed(hold, f0s, rms=0.1):
    """Run PitchHold over a list of f0 values (None = YIN found no pitch)."""
    out = []
    for f in f0s:
        est = PitchEstimate(np.nan, 0.5, False) if f is None else PitchEstimate(f, 0.05, True)
        out.append(hold.update(est, rms))
    return out


def test_carries_pitch_over_a_gap_then_updates():
    h = PitchHold(SR, HOP, hold_ms=300, median=1)
    out = feed(h, [220.0] * 5 + [None] * 10 + [247.0] * 3)
    assert all(e.voiced for e in out)
    assert [e.f0 for e in out[5:15]] == [220.0] * 10 and all(e.held for e in out[5:15])
    assert out[-1].f0 == 247.0 and not out[-1].held


def test_gap_longer_than_hold_or_quiet_input_releases():
    frames = round(0.3 * SR / HOP)
    h = PitchHold(SR, HOP, hold_ms=300, median=1)
    out = feed(h, [220.0] * 5 + [None] * (frames + 5))
    assert all(e.voiced for e in out[5 : 5 + frames]) and not any(e.voiced for e in out[5 + frames :])
    h = PitchHold(SR, HOP, hold_ms=300, median=1)
    feed(h, [220.0] * 5, rms=0.1)
    assert not h.update(PitchEstimate(np.nan, 1.0, False), rms=0.01).voiced   # 20 dB down: note ended


def test_octave_glitch_is_ignored_but_a_real_leap_is_kept():
    h = PitchHold(SR, HOP, octave_ms=80, median=1)
    out = feed(h, [220.0] * 5 + [440.0] * 2 + [220.0] * 3)
    assert [e.f0 for e in out] == [220.0] * 10
    h = PitchHold(SR, HOP, octave_ms=80, median=1)
    out = feed(h, [220.0] * 5 + [440.0] * 30)
    assert out[-1].f0 == 440.0


def test_median_removes_single_frame_spikes():
    h = PitchHold(SR, HOP, median=3)
    out = feed(h, [220.0] * 5 + [240.0] + [220.0] * 3)
    assert max(e.f0 for e in out[5:]) == pytest.approx(220.0)


def test_bridges_a_noisy_gap_in_audio_but_not_silence():
    rng = np.random.default_rng(3)
    note = held(57, seconds=0.5)
    noise = np.std(note) * rng.standard_normal(int(0.15 * SR))
    for gap, expect in ((noise, True), (np.zeros_like(noise), False)):
        x = np.concatenate([note, gap, note])
        raw = yin_track(x, SR, hop_length=HOP)
        tr = hold_track(raw, x)
        mid = (tr.times > 0.53) & (tr.times < 0.62)
        assert not raw.voiced[mid].any()
        assert tr.voiced[mid].all() == expect
        if expect:
            np.testing.assert_allclose(tr.f0[mid], 220.0, rtol=0.003)
            assert tr.held[mid].all()


def test_streaming_bridges_the_gap_too():
    rng = np.random.default_rng(4)
    note = held(57, seconds=0.5)
    x = np.concatenate([note, np.std(note) * rng.standard_normal(int(0.1 * SR)), note])
    s = HeldPitchStream(SR, hop_length=HOP)
    stream, pos = [], 0
    while pos < len(x):  # random block sizes
        b = int(rng.integers(50, 900))
        stream += s.push(x[pos : pos + b])
        pos += b
    t = (np.arange(1, len(stream) + 1) * HOP - s.yin.frame_length / 2) / SR   # frame centres
    mid = (t > 0.53) & (t < 0.57)
    gap = [e for e, m in zip(stream, mid) if m]
    assert gap and all(e.voiced and e.held for e in gap)
    np.testing.assert_allclose([e.f0 for e in gap], 220.0, rtol=0.003)


def test_rejects_bad_settings():
    with pytest.raises(ValueError):
        PitchHold(SR, HOP, hold_ms=-1)
    with pytest.raises(ValueError):
        PitchHold(SR, HOP, median=0)
