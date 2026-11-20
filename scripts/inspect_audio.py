#!/usr/bin/env python3
"""Load an audio file and show time vs. amplitude.

    python scripts/inspect_audio.py data/audio/song.mp3 --plot
"""
from dsfp_tune.cli import inspect_main

if __name__ == "__main__":
    inspect_main()
