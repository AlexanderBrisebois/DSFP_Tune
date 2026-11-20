# DSFP_Tune

Autotune (pitch correction) built on Fourier-transform analysis. A command-line
tool written in Python, with optional C++ kernels if we need more speed.

## Layout

```
DSFP_Tune/
├── tune.py              # the CLI entry point
├── dsfp_tune/           # library code used by tune.py
│   ├── io/              # audio loading (MP3/WAV/... -> NumPy)    [done]
│   ├── analysis/        # STFT, pitch (f0) detection              [next]
│   └── synthesis/       # pitch shifting / resynthesis            [later]
├── cpp/                 # optional C++ (pybind11) kernels for speed
├── tests/               # pytest suite
├── scripts/             # one-off helper scripts
├── notebooks/           # Jupyter exploration
├── data/audio/          # input audio (git-ignored)
├── data/output/         # processed audio (git-ignored)
├── pytest.ini
└── requirements.txt
```

## Setup

```bash
brew install ffmpeg                    # MP3 decoding backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## Usage

Run from the project root:

```bash
python tune.py inspect data/audio/song.mp3                 # print sample rate, duration, peak, rms
python tune.py inspect data/audio/song.mp3 --plot          # plot time vs. amplitude
python tune.py inspect song.mp3 --sr 22050 --save data/output/song.npz
```

`--save` writes an `.npz` with `time`, `amplitude` and `sample_rate` arrays.

## Loading audio in code

```python
from dsfp_tune.io import load_audio

sig = load_audio("data/audio/song.mp3")         # native sample rate, mono
t, x = sig.time_amplitude()                     # time (s), amplitude in [-1, 1]
sig = load_audio("song.mp3", sample_rate=22050, mono=False)   # resample, keep stereo
```

Decoding backends: `ffmpeg` (any format, can resample) and `soundfile`
(libsndfile >= 1.1; no external binary, no resampling). `backend="auto"` picks
for you.
