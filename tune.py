#!/usr/bin/env python3
"""DSFP_Tune command-line tool.

    python tune.py inspect data/audio/song.mp3 --plot
    python tune.py inspect song.mp3 --sr 22050 --save data/output/song.npz
    python tune.py live                          # live pitch window from the microphone
    python tune.py record --seconds 10           # record, save WAV, plot pitch
    python tune.py pitch data/audio/take.wav     # plot pitch of a file
    python tune.py devices                       # list microphones
    python tune.py web                           # dashboard in the browser (file or mic)
    python tune.py web --public                  # same, plus a public trycloudflare.com link
    python tune.py autotune                      # record, pitch-correct, play back, plot
    python tune.py autotune take.wav --key "A minor" --retune-ms 0
    python tune.py autotune --live               # real-time correction (headphones!)
    python tune.py autotune take.wav --key C --harmony 0.5   # add the key's triad on each note
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


def _record_from_mic(seconds: float, sr: int, device):
    """Record, print level info and warnings. Returns ``(AudioSignal, peak)``."""
    from dsfp_tune.io.microphone import record

    print(f"Recording {seconds:g} s at {sr} Hz ... sing or speak now")
    sig = record(seconds, sample_rate=sr, device=_device_arg(device))
    peak = float(np.abs(sig.samples).max())
    rms = float(np.sqrt(np.mean(sig.samples.astype(np.float64) ** 2)))
    print(f"done: peak={peak:.3f}  rms={rms:.4f} ({20 * np.log10(max(rms, 1e-10)):.0f} dBFS)")

    if peak == 0.0:
        print("WARNING: " + MIC_PERMISSION_HINT, file=sys.stderr)
    elif peak >= 0.99:
        print("WARNING: clipping - move back from the mic or lower the input gain.", file=sys.stderr)
    elif rms < 0.003:
        print("WARNING: very quiet - move closer to the mic or raise the input gain.", file=sys.stderr)
    return sig, peak


def _recording_path(out: str | None) -> Path:
    return Path(out) if out else Path("data/audio") / f"mic_{datetime.now():%Y%m%d_%H%M%S}.wav"


def cmd_record(args: argparse.Namespace) -> None:
    from dsfp_tune.io import save_wav

    sig, peak = _record_from_mic(args.seconds, args.sr, args.device)
    if peak > 0:
        _summarize_pitch(sig.samples, sig.sample_rate, args.f_min, args.f_max)

    out = None
    if not args.no_save:
        out = _recording_path(args.out)
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


# ---------------------------------------------------------------------------
# Autotune: pitch detection + target note + frequency shifting
# ---------------------------------------------------------------------------


def _parse_key_arg(text: str | None):
    if not text:
        return None
    from dsfp_tune.theory import parse_key

    try:
        return parse_key(text)
    except ValueError as exc:
        raise SystemExit(f"tune.py autotune: bad --key {text!r}: {exc}")


def _correction_settings(args: argparse.Namespace, default_fft: int) -> dict:
    key = _parse_key_arg(args.key)
    if args.harmony < 0:
        raise SystemExit("tune.py autotune: --harmony must be >= 0")
    if args.harmony > 0:
        if key is None:
            raise SystemExit('tune.py autotune: --harmony needs --key (e.g. --key "C major")')
        if len(key.intervals) != 7:
            raise SystemExit(f"tune.py autotune: --harmony needs a 7-note key; {key.name} has {len(key.intervals)} notes")
    return dict(
        key=key,
        harmony=args.harmony,
        retune_ms=args.retune_ms,
        f_min=args.f_min,
        f_max=args.f_max,
        n_fft=args.fft_size or default_fft,
    )


def cmd_autotune(args: argparse.Namespace) -> None:
    if args.live:
        _autotune_live(args)
        return

    from dsfp_tune.correction import correct_pitch
    from dsfp_tune.io import save_wav
    from dsfp_tune.theory import midi_to_name

    settings = _correction_settings(args, default_fft=2048)  # validate --key before recording
    if args.path:
        sig = load_audio(args.path)
        stem = Path(args.path).stem
        print(sig)
    else:
        sig, peak = _record_from_mic(args.seconds, args.sr, args.device)
        if peak == 0.0:
            return
        rec_path = None if args.no_save else _recording_path(None)
        if rec_path:
            save_wav(rec_path, sig)
            print(f"saved recording {rec_path}")
        stem = rec_path.stem if rec_path else "mic"

    key = settings["key"]
    print(f"correcting: key {key.name if key else 'chromatic (any semitone)'}, retune {args.retune_ms:g} ms"
          + (f", chord harmony {args.harmony:g}" if args.harmony else "") + " ...")
    result = correct_pitch(sig.samples, sig.sample_rate, **settings)

    shifted = result.correction[result.voiced] * 100
    if shifted.size:
        targets = [midi_to_name(int(m)) for m in result.target_midi[~np.isnan(result.target_midi)]]
        notes = list(dict.fromkeys(targets))  # unique, in order sung
        print(f"voiced {100 * result.voiced.mean():.0f}% · mean shift {np.mean(np.abs(shifted)):.0f} cents "
              f"(max {np.max(np.abs(shifted)):.0f}) · notes: {' '.join(notes[:16])}{' ...' if len(notes) > 16 else ''}")
    else:
        print("no pitched audio found - nothing to correct")

    if args.harmony:
        peak = float(np.max(np.abs(result.samples), initial=0.0))
        if peak > 0.99:  # lead + chord voices can exceed full scale: scale down instead of clipping
            result.samples *= 0.99 / peak
            print(f"lowered output level by {20 * np.log10(peak / 0.99):.1f} dB to avoid clipping")

    out = Path(args.out) if args.out else Path("data/output") / f"{stem}_tuned.wav"
    save_wav(out, result.samples, result.sample_rate)
    print(f"saved corrected audio {out}")

    if not args.no_play:
        from dsfp_tune.io.microphone import play

        print("playing original ...")
        play(sig.samples, sig.sample_rate, device=_device_arg(args.output_device))
        print("playing corrected ...")
        play(result.samples, result.sample_rate, device=_device_arg(args.output_device))

    if not args.no_plot:
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            print("Plotting needs matplotlib: pip install matplotlib (or pass --no-plot)", file=sys.stderr)
            return
        from dsfp_tune.plotting import plot_correction

        fig = plot_correction(sig.samples, result, title=str(out), f_min=args.f_min, f_max=args.f_max)
        print("showing plot (close the window to continue)")
        plt.show()
        plt.close(fig)


def _autotune_live(args: argparse.Namespace) -> None:
    from dsfp_tune.correction import PitchCorrector
    from dsfp_tune.io.microphone import DuplexStream, default_output_name
    from dsfp_tune.theory import midi_to_name, nearest_note

    out_dev = _device_arg(args.output_device)
    out_name = default_output_name(out_dev)
    if "speaker" in out_name.lower() and not args.allow_speakers:
        raise SystemExit(
            f"Output device is '{out_name}'. Plug in headphones (or choose one with --output-device): "
            "through speakers the microphone hears the corrected voice and it feeds back.\n"
            "Pass --allow-speakers to run anyway, with the volume low."
        )

    settings = _correction_settings(args, default_fft=1024)  # 1024: lower latency for live use
    pc = PitchCorrector(args.sr, **settings)
    duplex = DuplexStream(pc.process, args.sr, blocksize=256, input_device=_device_arg(args.device),
                          output_device=out_dev)
    key = settings["key"]
    with duplex:
        total_ms = (pc.latency / args.sr + duplex.total_latency) * 1000
        print(f"Live autotune -> {out_name} | key {key.name if key else 'chromatic'} | "
              f"retune {args.retune_ms:g} ms{f' | harmony {args.harmony:g}' if args.harmony else ''} | latency ~{total_ms:.0f} ms. Ctrl+C to stop.")
        try:
            while duplex.active:
                time.sleep(0.05)
                f = pc.latest
                if f is not None and f.voiced and not np.isnan(f.target_midi):
                    name, cents = nearest_note(f.f0)
                    line = (f"sung {name:<4}{cents:+4.0f}c ({f.f0:6.1f} Hz)  ->  {midi_to_name(int(f.target_midi)):<4}"
                            f" shift {100 * f.correction:+4.0f}c")
                    if f.chord:
                        line += "  chord " + " ".join(midi_to_name(m) for m in f.chord)
                else:
                    line = "      --  (no pitch: passing audio through)"
                print("\r" + line.ljust(78), end="", flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            print()
    if duplex.error:
        print(f"audio processing stopped with an error: {duplex.error!r}", file=sys.stderr)
    if duplex.xruns:
        print(f"note: {duplex.xruns} audio dropouts (buffer over/underruns)")


def cmd_devices(args: argparse.Namespace) -> None:
    from dsfp_tune.io.microphone import list_input_devices

    for d in list_input_devices():
        star = "*" if d["is_default"] else " "
        print(f"{star} {d['index']:>2}  {d['name']}  ({d['channels']} ch, {d['default_samplerate']:.0f} Hz)")


def cmd_web(args: argparse.Namespace) -> None:
    from dsfp_tune.web.server import serve

    serve(args.host, args.port, open_browser=not args.no_browser, quiet=not args.verbose, public=args.public)


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

    p = sub.add_parser("web", help="open the dashboard in the browser (audio file or microphone)")
    p.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    p.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1, this computer only)")
    p.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    p.add_argument("--verbose", action="store_true", help="log every request")
    p.add_argument("--public", action="store_true",
                   help="also share it through a Cloudflare quick tunnel (needs cloudflared; new link each run)")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser(
        "autotune",
        help="pitch-correct the microphone (record or --live) or an audio file",
        description="Detect pitch (YIN), snap it to the nearest note (optionally in a key), and shift it "
        "there with the FFT frequency shifter. Without a file: record from the microphone, correct, play "
        "back and plot. With --live: correct in real time to your headphones.",
    )
    p.add_argument("path", nargs="?", help="audio file to correct (default: record from the microphone)")
    p.add_argument("--key", help='restrict to a key, e.g. "A minor", "F# major", "Bb dorian" (default: any semitone)')
    p.add_argument("--retune-ms", type=float, default=25.0,
                   help="how fast pitch glides to the note: 0 = instant/robotic, 100+ = natural (default 25)")
    p.add_argument("--harmony", type=float, default=0.0, metavar="LEVEL",
                   help="add the key's triad on each snapped note (C major + A -> A C E): level of the third "
                   "and fifth voices relative to the lead, e.g. 0.5 (default 0 = off; needs --key)")
    p.add_argument("--live", action="store_true", help="real-time: microphone -> corrected audio -> headphones")
    p.add_argument("--seconds", type=float, default=5.0, help="recording length (default 5)")
    p.add_argument("--out", help="corrected WAV path (default data/output/<name>_tuned.wav)")
    p.add_argument("--no-save", action="store_true", help="don't save the raw microphone recording")
    p.add_argument("--no-play", action="store_true", help="don't play original and corrected audio")
    p.add_argument("--no-plot", action="store_true", help="don't open the before/after plot")
    _add_mic(p)
    p.add_argument("--output-device", help="output device index or name (see `python -m sounddevice`)")
    p.add_argument("--allow-speakers", action="store_true", help="allow --live through speakers (feedback risk)")
    p.add_argument("--fft-size", type=int, help="shifter frame size (default 2048; 1024 with --live)")
    _add_pitch_range(p)
    p.set_defaults(func=cmd_autotune)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
