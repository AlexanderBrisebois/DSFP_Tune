#!/usr/bin/env python3
"""DSFP_Tune command-line tool.

    python tune.py inspect data/audio/song.mp3 --plot
    python tune.py inspect song.mp3 --sr 22050 --save data/output/song.npz
    python tune.py live                          # live pitch window from the microphone
    python tune.py record --seconds 10           # record, save WAV, plot pitch
    python tune.py pitch data/audio/take.wav     # plot pitch of a file
    python tune.py devices                       # list microphones
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from dsfp_tune.io import load_audio


def cmd_inspect(args: argparse.Namespace) -> None:
    sig = load_audio(args.path, sample_rate=args.sr, mono=not args.stereo, backend=args.backend)
    print(sig)
    print(f"peak={np.abs(sig.samples).max():.4f}  rms={np.sqrt(np.mean(sig.samples**2)):.4f}")

    if args.save:
        np.savez_compressed(args.save, time=sig.time, amplitude=sig.samples, sample_rate=sig.sample_rate)
        print(f"saved {args.save}")

    if args.plot:
        import matplotlib.pyplot as plt

        t, x = sig.time_amplitude()
        plt.figure(figsize=(12, 4))
        plt.plot(t, x, linewidth=0.5)
        plt.xlabel("time (s)")
        plt.ylabel("amplitude")
        plt.title(str(sig.source))
        plt.tight_layout()
        plt.show()


# ---------------------------------------------------------------------------
# Pitch: microphone (record / live) and files
# ---------------------------------------------------------------------------

MIC_PERMISSION_HINT = (
    "The recording is completely silent. On macOS this usually means the app running Python\n"
    "(Terminal / iTerm / Visual Studio Code) has no microphone access. Allow it in\n"
    "System Settings -> Privacy & Security -> Microphone, then restart that app."
)


def _device_arg(value: str | None) -> int | str | None:
    if value is None:
        return None
    return int(value) if value.isdigit() else value


def _describe(f0: float) -> str:
    from dsfp_tune.theory import nearest_note

    name, cents = nearest_note(f0)
    return f"{f0:7.1f} Hz  {name:<4}{cents:+4.0f} cents"


def _cents_bar(cents: float, half_width: int = 10) -> str:
    """A tuner needle: '[----|--*-]'."""
    pos = int(round(cents / 50.0 * half_width))
    pos = max(-half_width, min(half_width, pos))
    cells = ["-"] * (2 * half_width + 1)
    cells[half_width] = "|"
    cells[half_width + pos] = "*"
    return "[" + "".join(cells) + "]"


def _summarize_pitch(samples: np.ndarray, sr: int, f_min: float, f_max: float) -> None:
    from dsfp_tune.analysis import yin_track
    from dsfp_tune.theory import nearest_note

    track = yin_track(samples, sr, hop_length=256, f_min=f_min, f_max=f_max)
    v = track.voiced
    print(f"pitch: {100 * v.mean():.0f}% of frames voiced", end="")
    if v.any():
        f = track.f0[v]
        lo, hi = np.percentile(f, [5, 95])
        print(f", median {_describe(float(np.median(f))).strip()}, "
              f"range {nearest_note(lo)[0]}-{nearest_note(hi)[0]} ({lo:.0f}-{hi:.0f} Hz)")
    else:
        print(" (no pitch found)")


def _show_pitch_plot(samples, sr, f_min, f_max, title, save=None, show=True) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Plotting needs matplotlib: pip install matplotlib (or pass --no-plot)", file=sys.stderr)
        return

    from dsfp_tune.plotting import plot_pitch

    fig = plot_pitch(samples, sr, title=title, f_min=f_min, f_max=f_max)
    if save:
        fig.savefig(save, dpi=150)
        print(f"saved plot {save}")
    if show:
        print("showing plot (close the window to continue)")
        plt.show()
    plt.close(fig)


def cmd_record(args: argparse.Namespace) -> None:
    from dsfp_tune.io import save_wav
    from dsfp_tune.io.microphone import record

    print(f"Recording {args.seconds:g} s at {args.sr} Hz ... sing or speak now")
    sig = record(args.seconds, sample_rate=args.sr, device=_device_arg(args.device))
    peak = float(np.abs(sig.samples).max())
    rms = float(np.sqrt(np.mean(sig.samples.astype(np.float64) ** 2)))
    print(f"done: peak={peak:.3f}  rms={rms:.4f} ({20 * np.log10(max(rms, 1e-10)):.0f} dBFS)")

    if peak == 0.0:
        print("WARNING: " + MIC_PERMISSION_HINT, file=sys.stderr)
    elif peak >= 0.99:
        print("WARNING: clipping - move back from the mic or lower the input gain.", file=sys.stderr)
    elif rms < 0.003:
        print("WARNING: very quiet - move closer to the mic or raise the input gain.", file=sys.stderr)

    if peak > 0:
        _summarize_pitch(sig.samples, sig.sample_rate, args.f_min, args.f_max)

    out = None
    if not args.no_save:
        out = Path(args.out) if args.out else Path("data/audio") / f"mic_{datetime.now():%Y%m%d_%H%M%S}.wav"
        save_wav(out, sig)
        print(f"saved {out}")

    if not args.no_plot and peak > 0:
        _show_pitch_plot(sig.samples, sig.sample_rate, args.f_min, args.f_max,
                         title=str(out) if out else "microphone recording")


def cmd_live(args: argparse.Namespace) -> None:
    if not args.no_plot:
        from dsfp_tune.plotting import LivePitchPlot

        print("Live pitch window open - sing! Close the window to stop.")
        view = LivePitchPlot(args.sr, f_min=args.f_min, f_max=args.f_max)
        view.run(device=_device_arg(args.device))
        if not view.heard_anything:
            print(MIC_PERMISSION_HINT, file=sys.stderr)
        return

    # Text tuner in the terminal.
    from dsfp_tune.analysis import YinStream
    from dsfp_tune.io.microphone import MicrophoneStream
    from dsfp_tune.theory import nearest_note

    hop = 256
    stream = YinStream(args.sr, hop_length=hop, f_min=args.f_min, f_max=args.f_max)
    print(f"Live pitch (analysis frame {stream.yin.latency_seconds * 1000:.0f} ms). Ctrl+C to stop.")
    heard_anything = False
    last_print = 0.0
    latest = None
    mic = MicrophoneStream(sample_rate=args.sr, blocksize=hop, device=_device_arg(args.device))
    try:
        with mic:
            started = time.monotonic()
            for block in mic:
                heard_anything = heard_anything or bool(np.any(block))
                ests = stream.push(block)
                if ests:
                    latest = ests[-1]
                now = time.monotonic()
                if latest is None or now - last_print < 0.05:  # redraw at ~20 Hz
                    continue
                last_print = now
                if latest.voiced:
                    _, cents = nearest_note(latest.f0)
                    line = f"{_describe(latest.f0)}  {_cents_bar(cents)}  conf {latest.confidence:.2f}"
                else:
                    line = "      --  (no pitch)"
                print("\r" + line.ljust(70), end="", flush=True)
                if not heard_anything and now - started > 2.0:
                    print("\n" + MIC_PERMISSION_HINT, file=sys.stderr)
                    return
    except KeyboardInterrupt:
        pass
    finally:
        print()
    if mic.overflows:
        print(f"note: {mic.overflows} audio blocks were dropped (processing fell behind)")


def cmd_pitch(args: argparse.Namespace) -> None:
    sig = load_audio(args.path)
    print(sig)
    _summarize_pitch(sig.samples, sig.sample_rate, args.f_min, args.f_max)
    _show_pitch_plot(sig.samples, sig.sample_rate, args.f_min, args.f_max, title=str(args.path),
                     save=args.save, show=not args.no_show)


def cmd_devices(args: argparse.Namespace) -> None:
    from dsfp_tune.io.microphone import list_input_devices

    for d in list_input_devices():
        star = "*" if d["is_default"] else " "
        print(f"{star} {d['index']:>2}  {d['name']}  ({d['channels']} ch, {d['default_samplerate']:.0f} Hz)")


def _add_pitch_range(p: argparse.ArgumentParser) -> None:
    p.add_argument("--f-min", type=float, default=70.0, help="lowest pitch to detect, Hz (default 70)")
    p.add_argument("--f-max", type=float, default=1000.0, help="highest pitch to detect, Hz (default 1000)")


def _add_mic(p: argparse.ArgumentParser) -> None:
    p.add_argument("--sr", type=int, default=44100, help="sample rate (default 44100)")
    p.add_argument("--device", help="input device index or part of its name (see `tune.py devices`)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tune.py", description="Autotune using Fourier transforms.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="load an audio file and show time vs. amplitude")
    p.add_argument("path")
    p.add_argument("--sr", type=int, default=None, help="resample to this rate")
    p.add_argument("--stereo", action="store_true", help="keep all channels")
    p.add_argument("--backend", default="auto", choices=["auto", "ffmpeg", "soundfile"])
    p.add_argument("--plot", action="store_true", help="plot the waveform (needs matplotlib)")
    p.add_argument("--save", help="save time/amplitude arrays to this .npz file")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("live", help="show the detected pitch from the microphone live")
    _add_mic(p)
    _add_pitch_range(p)
    p.add_argument("--no-plot", action="store_true", help="text tuner in the terminal instead of a window")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("record", help="record from the microphone, save a WAV, and plot its pitch")
    _add_mic(p)
    _add_pitch_range(p)
    p.add_argument("--seconds", type=float, default=5.0, help="recording length (default 5)")
    p.add_argument("--out", help="output WAV path (default data/audio/mic_<timestamp>.wav)")
    p.add_argument("--no-save", action="store_true", help="don't write a WAV file")
    p.add_argument("--no-plot", action="store_true", help="don't open a plot window")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("pitch", help="detect pitch in an audio file and plot it")
    p.add_argument("path")
    _add_pitch_range(p)
    p.add_argument("--save", help="also save the figure (e.g. plot.png)")
    p.add_argument("--no-show", action="store_true", help="don't open a window (use with --save)")
    p.set_defaults(func=cmd_pitch)

    p = sub.add_parser("devices", help="list microphones / audio inputs")
    p.set_defaults(func=cmd_devices)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
