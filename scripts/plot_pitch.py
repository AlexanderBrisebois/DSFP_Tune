#!/usr/bin/env python3
"""Detect pitch in an audio file and show waveform, spectrogram + f0, and cents off pitch.

    python scripts/plot_pitch.py data/audio/mic_20261120_141500.wav
    python scripts/plot_pitch.py take.wav --save take_pitch.png --no-show
"""
from dsfp_tune.cli import pitch_main

if __name__ == "__main__":
    pitch_main()
