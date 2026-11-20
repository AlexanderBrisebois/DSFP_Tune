#!/usr/bin/env python3
"""Record from the microphone, or watch the detected pitch live.

    python scripts/record_audio.py                    # record 5 s -> data/audio/mic_<time>.wav
    python scripts/record_audio.py --seconds 10 --out data/audio/scale.wav
    python scripts/record_audio.py --live             # live tuner readout, Ctrl+C to stop
    python scripts/record_audio.py --list-devices
"""
from dsfp_tune.cli import record_main

if __name__ == "__main__":
    record_main()
