"""Command-line helpers."""

from __future__ import annotations

import argparse

import numpy as np

from dsfp_tune.io import load_audio


def inspect_main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Load an audio file and print/plot time vs. amplitude.")
    p.add_argument("path")
    p.add_argument("--sr", type=int, default=None, help="resample to this rate")
    p.add_argument("--stereo", action="store_true", help="keep all channels")
    p.add_argument("--backend", default="auto", choices=["auto", "ffmpeg", "soundfile"])
    p.add_argument("--plot", action="store_true", help="plot waveform (needs matplotlib)")
    p.add_argument("--save", help="save time/amplitude arrays to this .npz file")
    args = p.parse_args(argv)

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


if __name__ == "__main__":
    inspect_main()
