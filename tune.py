#!/usr/bin/env python3
"""DSFP_Tune command-line tool.

    python tune.py inspect data/audio/song.mp3 --plot
    python tune.py inspect song.mp3 --sr 22050 --save data/output/song.npz
"""

from __future__ import annotations

import argparse

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

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
