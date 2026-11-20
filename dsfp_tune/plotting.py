"""Pitch plots (matplotlib): a static analysis figure and a live, scrolling microphone view.

* :func:`plot_pitch` — waveform, spectrogram with the YIN f0 track on top, and how many
  cents each frame is from the nearest note.
* :class:`LivePitchPlot` — a window that scrolls the detected pitch from the microphone
  in real time on a semitone grid.
"""

from __future__ import annotations

import math
import warnings
from collections import deque

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FixedFormatter, FixedLocator, FuncFormatter, NullFormatter, NullLocator

from dsfp_tune.analysis import PitchTrack, YinStream, yin_track
from dsfp_tune.theory import freq_to_midi, midi_to_freq, midi_to_name, nearest_note

__all__ = ["LivePitchPlot", "cents_from_held_note", "note_ticks", "plot_correction", "plot_pitch", "spectrogram"]

F0_COLOR = "#2ec4ff"
_NATURALS = {0, 2, 4, 5, 7, 9, 11}  # C D E F G A B (pitch classes)


def note_ticks(lo_midi: float, hi_midi: float) -> tuple[list[int], list[int]]:
    """Choose which notes to label on a pitch axis spanning ``lo_midi..hi_midi``.

    Returns ``(labelled, all_semitones)`` as MIDI numbers. The wider the range, the fewer
    notes are labelled: every semitone, then natural notes, then C-E-G, then only C.
    """
    notes = list(range(math.ceil(lo_midi), math.floor(hi_midi) + 1))
    span = hi_midi - lo_midi
    if span <= 14:
        keep = set(range(12))
    elif span <= 26:
        keep = _NATURALS
    elif span <= 48:
        keep = {0, 4, 7}
    else:
        keep = {0}
    return [n for n in notes if n % 12 in keep], notes


def cents_from_held_note(f0: np.ndarray, frame_rate: float, smooth_seconds: float = 0.3) -> np.ndarray:
    """Cents between each frame's pitch and the note being *held* around it.

    The held note is the nearest semitone to a running median of the pitch over
    ``smooth_seconds``, so vibrato shows up as a smooth wave around 0 instead of jumping
    between +50 and -50 whenever it crosses the midpoint between two notes. ``nan`` where
    unvoiced.
    """
    midi = freq_to_midi(np.asarray(f0, dtype=np.float64))  # nan stays nan
    half = max(1, int(round(smooth_seconds * frame_rate / 2)))
    padded = np.pad(midi, half, constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN windows in unvoiced gaps
        centre = np.nanmedian(windows, axis=1)
    return 100.0 * (midi - np.round(centre))


def spectrogram(samples: np.ndarray, sr: int, n_fft: int = 4096, hop: int = 512, f_max: float = 4000.0):
    """Magnitude spectrogram in dB. Returns ``(times, freqs, S_db)`` with ``S_db`` shaped (freqs, times)."""
    x = np.asarray(samples, dtype=np.float64)
    x = np.pad(x, (n_fft // 2, n_fft // 2))
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    frames = np.lib.stride_tricks.sliding_window_view(x, n_fft)[::hop]
    spec = np.abs(np.fft.rfft(frames * np.hanning(n_fft), axis=1)).T
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    keep = (freqs > 0) & (freqs <= f_max)
    s_db = 20 * np.log10(spec[keep] + 1e-10)
    times = np.arange(frames.shape[0]) * hop / sr
    return times, freqs[keep], s_db


def _pitch_axis_limits(track: PitchTrack, f_min: float, f_max: float, sr: int) -> tuple[float, float]:
    f = track.f0[track.voiced]
    if f.size == 0:
        return f_min, min(f_max * 2, sr / 2)
    lo, hi = np.percentile(f, [2, 98])
    # Show a little below the lowest note and a few harmonics above the highest.
    return max(30.0, lo / 1.5), min(sr / 2, hi * 4)


def plot_pitch(
    samples: np.ndarray,
    sample_rate: int,
    track: PitchTrack | None = None,
    title: str | None = None,
    f_min: float = 70.0,
    f_max: float = 1000.0,
):
    """Figure with three panels sharing the time axis:

    1. waveform;
    2. spectrogram (log frequency, note names on the left, Hz on the right) with the
       YIN f0 track drawn on top: it should sit on the lowest bright harmonic;
    3. cents away from the nearest equal-tempered note for each voiced frame.

    Returns the matplotlib ``Figure`` (call ``plt.show()`` to display it).
    """
    samples = np.asarray(samples, dtype=np.float64)
    if track is None:
        track = yin_track(samples, sample_rate, hop_length=256, f_min=f_min, f_max=f_max)

    fig, (ax_w, ax_s, ax_c) = plt.subplots(
        3, 1, figsize=(12, 8), sharex=True, gridspec_kw=dict(height_ratios=[1, 3.2, 1.3])
    )
    duration = len(samples) / sample_rate

    # 1. Waveform (min/max envelope when long, so drawing stays fast).
    t = np.arange(len(samples)) / sample_rate
    if len(samples) > 200_000:
        chunk = len(samples) // 4000
        n = len(samples) // chunk * chunk
        blocks = samples[:n].reshape(-1, chunk)
        tc = t[:n].reshape(-1, chunk)[:, 0]
        ax_w.fill_between(tc, blocks.min(axis=1), blocks.max(axis=1), color="0.35", linewidth=0)
    else:
        ax_w.plot(t, samples, color="0.35", linewidth=0.5)
    ax_w.set_ylabel("amplitude")
    ax_w.set_ylim(-1.05, 1.05)
    ax_w.set_title(title or "Pitch analysis")

    # 2. Spectrogram + f0.
    lo_hz, hi_hz = _pitch_axis_limits(track, f_min, f_max, sample_rate)
    st, sf, s_db = spectrogram(samples, sample_rate, f_max=hi_hz * 1.05)
    vmax = s_db.max()
    ax_s.pcolormesh(st, sf, s_db, shading="auto", cmap="magma", vmin=vmax - 80, vmax=vmax, rasterized=True)
    ax_s.plot(track.times, track.f0, color=F0_COLOR, linewidth=2.0, label="YIN f0")
    ax_s.set_yscale("log")
    ax_s.set_ylim(lo_hz, hi_hz)
    labelled, all_notes = note_ticks(float(freq_to_midi(lo_hz)), float(freq_to_midi(hi_hz)))
    ax_s.yaxis.set_major_locator(FixedLocator(midi_to_freq(labelled)))
    ax_s.yaxis.set_major_formatter(FixedFormatter([midi_to_name(n) for n in labelled]))
    ax_s.yaxis.set_minor_locator(FixedLocator(midi_to_freq(all_notes)))
    ax_s.yaxis.set_minor_formatter(NullFormatter())
    ax_s.grid(True, axis="y", which="major", color="white", alpha=0.25, linewidth=0.6)
    ax_s.set_ylabel("note")
    hz_axis = ax_s.secondary_yaxis("right")
    hz_axis.set_ylabel("Hz")
    hz_ticks = [h for h in (30, 50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000) if lo_hz <= h <= hi_hz]
    hz_axis.yaxis.set_major_locator(FixedLocator(hz_ticks))
    hz_axis.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    hz_axis.yaxis.set_minor_locator(NullLocator())
    ax_s.legend(loc="upper right", framealpha=0.8)

    # 3. Cents off the note being held (green band = within 10 cents).
    v = track.voiced
    cents = cents_from_held_note(track.f0, sample_rate / track.hop_length)
    ax_c.axhspan(-10, 10, color="#3fb950", alpha=0.15, linewidth=0)
    ax_c.axhline(0, color="0.5", linewidth=0.8)
    ax_c.plot(track.times, cents, color=F0_COLOR, linewidth=1.2)
    lim = 55.0 if not v.any() else float(np.clip(np.nanmax(np.abs(cents)) + 5, 55, 105))
    ax_c.set_ylim(-lim, lim)
    ax_c.set_yticks([-100, -50, -25, 0, 25, 50, 100] if lim > 55 else [-50, -25, 0, 25, 50])
    ax_c.set_ylabel("cents off note")
    ax_c.set_xlabel("time (s)")
    ax_c.set_xlim(0, max(duration, 1e-3))

    if v.any():
        name, c = nearest_note(float(np.median(track.f0[v])))
        summary = (f"{100 * v.mean():.0f}% voiced · median {name} {c:+.0f}¢ · "
                   f"mean |offset| {np.nanmean(np.abs(cents)):.0f}¢")
        ax_w.set_title(f"{title or 'Pitch analysis'}\n{summary}", fontsize=10)
    fig.tight_layout()
    return fig


def plot_correction(original: np.ndarray, result, title: str | None = None, f_min: float = 70.0, f_max: float = 1000.0):
    """Before/after figure for :func:`dsfp_tune.correction.correct_pitch`.

    Top: pitch on a semitone grid. Grey = what was sung, orange = target notes, blue = the
    corrected output (measured again with YIN on the output audio). In-key notes have
    darker grid lines. Bottom: cents away from the target note, before and after.

    ``result`` is a :class:`~dsfp_tune.correction.CorrectionResult`.
    """
    sr = result.sample_rate
    out_track = yin_track(result.samples, sr, hop_length=256, f_min=f_min, f_max=f_max)
    in_midi = np.where(result.voiced, freq_to_midi(result.f0), np.nan)
    out_midi = np.where(out_track.voiced, freq_to_midi(out_track.f0), np.nan)
    target = result.target_midi

    # Target at the output-track times (nearest pitch estimate).
    if len(result.times):
        idx = np.clip(np.searchsorted(result.times, out_track.times), 0, len(result.times) - 1)
        target_at_out = target[idx]
    else:
        target_at_out = np.full(len(out_track.times), np.nan)
    cents_in = 100 * (in_midi - target)
    cents_out = 100 * (out_midi - target_at_out)

    fig, (ax_p, ax_c) = plt.subplots(2, 1, figsize=(12, 7.5), sharex=True, gridspec_kw=dict(height_ratios=[3, 1.3]))

    voiced_vals = np.concatenate([in_midi[~np.isnan(in_midi)], target[~np.isnan(target)]])
    if voiced_vals.size:
        lo, hi = np.percentile(voiced_vals, [1, 99])
        centre, span = (lo + hi) / 2, max(8.0, hi - lo + 3.0)
        lo, hi = centre - span / 2, centre + span / 2
    else:
        lo, hi = 50.0, 62.0
    ax_p.set_ylim(lo, hi)
    labelled, all_notes = note_ticks(lo, hi)
    ax_p.set_yticks(labelled, [midi_to_name(n) for n in labelled])
    key_pcs = set(result.key.pitch_classes) if result.key is not None else set(range(12))
    for n in all_notes:
        in_key = n % 12 in key_pcs
        ax_p.axhline(n, color="0.55" if in_key else "0.88", linewidth=0.9 if in_key else 0.6, zorder=0)

    ax_p.plot(result.times, in_midi, color="0.55", linewidth=1.6, label="sung (input)")
    ax_p.step(result.times, target, where="mid", color="#f0883e", linewidth=1.4, linestyle="--", label="target note")
    ax_p.plot(out_track.times, out_midi, color=F0_COLOR, linewidth=2.0, label="corrected (output)")
    ax_p.set_ylabel("note")
    ax_p.legend(loc="upper right", framealpha=0.9)

    ax_c.axhspan(-10, 10, color="#3fb950", alpha=0.15, linewidth=0)
    ax_c.axhline(0, color="0.5", linewidth=0.8)
    ax_c.plot(result.times, cents_in, color="0.55", linewidth=1.2, label="before")
    ax_c.plot(out_track.times, cents_out, color=F0_COLOR, linewidth=1.4, label="after")
    ax_c.set_ylim(-60, 60)
    ax_c.set_yticks([-50, -25, 0, 25, 50])
    ax_c.set_ylabel("cents off target")
    ax_c.set_xlabel("time (s)")
    ax_c.set_xlim(0, max(len(result.samples) / sr, 1e-3))
    ax_c.legend(loc="upper right", framealpha=0.9, ncol=2)

    key_name = result.key.name if result.key is not None else "chromatic"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        before, after = np.nanmean(np.abs(cents_in)), np.nanmean(np.abs(cents_out))
    summary = f"key: {key_name}"
    if np.isfinite(before) and np.isfinite(after):
        summary += f" · mean |off target|: {before:.0f}¢ → {after:.0f}¢"
    ax_p.set_title(f"{title or 'Pitch correction'}\n{summary}", fontsize=10)
    fig.tight_layout()
    return fig


class LivePitchPlot:
    """Scrolling, real-time pitch view fed by :class:`~dsfp_tune.io.microphone.MicrophoneStream`.

    The y-axis is in semitones (one grid line per note) and follows your voice; the big
    readout shows the current note and how many cents sharp/flat you are.

    Use :meth:`run` for the microphone; :meth:`push` and :meth:`redraw` are separate so
    the view can be driven (and tested) with any audio.
    """

    def __init__(
        self,
        sample_rate: int = 44100,
        hop_length: int = 256,
        window_seconds: float = 8.0,
        f_min: float = 70.0,
        f_max: float = 1000.0,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.hop_length = int(hop_length)
        self.window_seconds = float(window_seconds)
        self.stream = YinStream(sample_rate, hop_length=hop_length, f_min=f_min, f_max=f_max)
        self._midi: deque[float] = deque(maxlen=int(window_seconds * sample_rate / hop_length) + 1)
        self.latest = None
        self.samples_seen = 0
        self.heard_anything = False
        self.fig = None
        self._mic = None

    # -- data ---------------------------------------------------------------

    def push(self, block: np.ndarray) -> None:
        """Feed microphone samples."""
        block = np.asarray(block)
        self.samples_seen += len(block)
        self.heard_anything = self.heard_anything or bool(np.any(block))
        for est in self.stream.push(block):
            self._midi.append(float(freq_to_midi(est.f0)) if est.voiced else math.nan)
            self.latest = est

    def data(self) -> tuple[np.ndarray, np.ndarray]:
        """``(seconds_ago, midi)`` for the visible window; ``seconds_ago`` ends at 0 (now)."""
        midi = np.array(self._midi)
        t = (np.arange(len(midi)) - (len(midi) - 1)) * self.hop_length / self.sample_rate
        return t, midi

    # -- drawing ------------------------------------------------------------

    def build_figure(self):
        self.fig, self.ax = plt.subplots(figsize=(11, 6))
        self.fig.canvas.manager.set_window_title("DSFP_Tune — live pitch")
        self.ax.set_xlim(-self.window_seconds, 0)
        self.ax.set_xlabel("seconds ago")
        self.ax.set_ylabel("note")
        (self.line,) = self.ax.plot([], [], color=F0_COLOR, linewidth=2.2)
        (self.dot,) = self.ax.plot([], [], "o", color=F0_COLOR, markersize=8)
        self.readout = self.ax.text(
            0.015, 0.97, "sing!", transform=self.ax.transAxes, va="top", fontsize=26,
            fontweight="bold", family="monospace", color="0.5",
        )
        self.status = self.ax.text(
            0.985, 0.03, "", transform=self.ax.transAxes, ha="right", va="bottom", fontsize=9, color="0.45"
        )
        self._set_pitch_range(57 - 6, 57 + 6)  # around A3 until we hear something
        self.fig.tight_layout()
        return self.fig

    def _set_pitch_range(self, lo: float, hi: float) -> None:
        self._ylim = (lo, hi)
        self.ax.set_ylim(lo, hi)
        labelled, all_notes = note_ticks(lo, hi)
        self.ax.set_yticks(labelled, [midi_to_name(n) for n in labelled])
        self.ax.set_yticks(all_notes, minor=True)
        self.ax.grid(True, axis="y", which="minor", color="0.85", linewidth=0.6)
        self.ax.grid(True, axis="y", which="major", color="0.7", linewidth=0.9)
        self.ax.set_axisbelow(True)

    def _follow_voice(self, midi: np.ndarray) -> None:
        """Re-centre the y-axis when the voice leaves the view or the view is far too wide."""
        recent = midi[-int(3 * self.sample_rate / self.hop_length):]
        recent = recent[~np.isnan(recent)]
        if recent.size == 0:
            return
        lo_d, hi_d = float(recent.min()), float(recent.max())
        need = max(12.0, hi_d - lo_d + 4.0)
        lo, hi = self._ylim
        outside = lo_d < lo + 0.5 or hi_d > hi - 0.5
        too_wide = (hi - lo) > 2 * need
        if outside or too_wide:
            centre = 0.5 * (lo_d + hi_d)
            self._set_pitch_range(round(centre - need / 2), round(centre + need / 2))

    def redraw(self) -> None:
        t, midi = self.data()
        self.line.set_data(t, midi)
        self._follow_voice(midi)

        est = self.latest
        if est is not None and est.voiced:
            name, cents = nearest_note(est.f0)
            self.dot.set_data([0.0], [freq_to_midi(est.f0)])
            colour = "#2da44e" if abs(cents) <= 10 else ("#d29922" if abs(cents) <= 25 else "#cf222e")
            self.readout.set_text(f"{name:<3} {cents:+3.0f}¢  {est.f0:6.1f} Hz")
            self.readout.set_color(colour)
        else:
            self.dot.set_data([], [])
            self.readout.set_text("—")
            self.readout.set_color("0.6")

        if not self.heard_anything and self.samples_seen > 2 * self.sample_rate:
            self.status.set_text(
                "No audio from the microphone. On macOS, allow microphone access for this app in "
                "System Settings → Privacy & Security → Microphone, then restart it."
            )
            self.status.set_color("#cf222e")
        else:
            dropped = f" · {self._mic.overflows} blocks dropped" if self._mic and self._mic.overflows else ""
            self.status.set_text(f"frame {self.stream.yin.latency_seconds * 1000:.0f} ms · close window to stop{dropped}")

    # -- microphone loop ---------------------------------------------------------

    def _tick(self, _frame):
        blocks = self._mic.read_available()
        if blocks.size:
            self.push(blocks)
        self.redraw()
        return ()

    def run(self, device: int | str | None = None, blocksize: int = 256) -> None:
        """Open the microphone and the window; returns when the window is closed."""
        from matplotlib.animation import FuncAnimation

        from dsfp_tune.io.microphone import MicrophoneStream

        # Polling mode: GUI event loops (notably matplotlib's macOS backend) hold the GIL and
        # starve PortAudio callback threads, so read the microphone from the animation timer.
        self._mic = MicrophoneStream(self.sample_rate, blocksize=blocksize, device=device, use_callback=False)
        self.build_figure()
        with self._mic:
            # Keep a reference to the animation, or it is garbage-collected and stops.
            self._anim = FuncAnimation(self.fig, self._tick, interval=30, cache_frame_data=False)
            plt.show()
