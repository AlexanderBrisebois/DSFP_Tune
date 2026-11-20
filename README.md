# DSFP_Tune

Vocal pitch-correction software (in the spirit of Auto-Tune), built on Fourier-transform analysis.

## How it works

For a monophonic vocal recording, the pipeline is:

```
audio ──► 1. pitch detection ──► 2. target note ──► 3. pitch shift ──► corrected audio
          (f0 per frame, YIN)    (snap to key)      (time-varying ratio,
                                                     formant-preserving)
```

1. **Pitch detection.** Estimate the fundamental frequency (f0) of each short frame (around 30 ms) with the
   **YIN** algorithm, and flag unpitched frames (breaths, "s"/"t" consonants, silence) so they are left alone.
2. **Target note.** Convert f0 to semitones (`midi = 69 + 12·log2(f0 / 440)`) and snap it to the nearest note
   in the chosen key. Hysteresis stops flicker between two notes, and a *retune speed* sets how quickly the pitch
   glides to the target: instant gives the robotic "T-Pain" effect, slower sounds natural and keeps vibrato.
3. **Pitch shift.** Shift each frame by `target_f0 / detected_f0` without changing duration, while
   preserving formants (otherwise the voice sounds like a chipmunk).

### Design decisions so far

- **YIN for pitch detection.** It is accurate on voice, cheap to compute, and works in real time (it uses only past
  samples). Picking the largest FFT peak was rejected: vocal harmonics are often louder than the fundamental, which
  causes octave errors, and FFT bins are too coarse (about 21 Hz apart at 44.1 kHz / 2048 points, which is roughly
  3 semitones near 110 Hz). pYIN (offline only) and CREPE (heavy, 64 ms window) were also considered.
- **The FFT is used inside YIN.** YIN compares a frame with delayed copies of itself. That comparison is computed
  as `d(τ) = E0 + E(τ) − 2·r(τ)`, where the cross-correlation `r(τ)` comes from `IFFT(conj(FFT(ref)) · FFT(frame))`.
  The result is identical to the textbook formula and about 24× faster. YIN frames are **not windowed** (no Hann);
  they are zero-padded so the FFT doesn't wrap around. This is separate from any windowed STFT the pitch shifter uses.
- **Offline first, then real time.** Develop and validate in Python/NumPy on audio files, then build a streaming
  version, and port the hot loops to C++ (see [cpp/README.md](cpp/README.md)) if needed.
- **One detector interface:** `detect(frame) -> (f0, aperiodicity, voiced)`, so detectors can be swapped or moved
  to C++ without touching the rest of the pipeline.

## Status

| Area | Status |
|---|---|
| Repo, packaging, folder layout | ✅ Done |
| Audio loading (MP3/WAV/FLAC, resample, downmix) — `dsfp_tune.io` | ✅ Done |
| Waveform inspection script — `scripts/inspect_audio.py` | ✅ Done |
| YIN pitch detector (FFT-based) — `dsfp_tune.analysis.yin` | ✅ Done, tested on synthetic signals |
| Test with real vocal recordings, plot the f0 track | 🔜 Next |
| Note mapping: key/scale snap, hysteresis, retune speed | ⏳ Planned |
| Pitch shifter (phase vocoder or PSOLA) with formant preservation — `dsfp_tune.synthesis` | ⏳ Planned |
| End-to-end offline tool: file in → corrected file out | ⏳ Planned |
| Real-time streaming (block-based, low latency) | ⏳ Planned |
| C++ kernels / plugin | ⏳ Later, if needed |

## Repository layout

```
src/dsfp_tune/
  io/audio_io.py      load audio → AudioSignal (float32 samples, sample rate, time axis)
  analysis/yin.py     YIN pitch detector (Yin, yin_track)
  synthesis/          pitch shifting (to do)
  cli.py              command-line entry points
scripts/              runnable helper scripts
tests/                pytest suite
notebooks/            experiments
data/audio/           input audio (not committed)
data/output/          generated audio (not committed)
cpp/                  future C++ extensions
```

## Setup

Requires Python ≥ 3.10.

```bash
pip install -e ".[dev]"        # or: pip install -r requirements.txt
python -m pytest               # run the tests
```

To decode MP3 or resample, install **ffmpeg** (`brew install ffmpeg` on macOS). The `soundfile` package also reads
MP3/WAV/FLAC but can't resample.

## Usage

Inspect a file:

```bash
python scripts/inspect_audio.py data/audio/song.mp3 --plot
```

Track pitch:

```python
from dsfp_tune import load_audio
from dsfp_tune.analysis import Yin, yin_track

sig = load_audio("data/audio/vocal.wav")

# Whole file (offline)
track = yin_track(sig.samples, sig.sample_rate, hop_length=256, f_min=70, f_max=1000)
track.times, track.f0, track.voiced          # f0 is NaN where unvoiced

# One frame at a time (how the real-time version will work)
yin = Yin(sig.sample_rate, f_min=70, f_max=1000)
est = yin.detect(sig.samples[:yin.frame_length])
est.f0, est.voiced, est.confidence
```

### YIN parameters

| Parameter | Default | Effect |
|---|---|---|
| `f_min` | 70 Hz | Lowest pitch searched. Sets the frame length and therefore the latency (28.6 ms at 44.1 kHz with the defaults). Raise it for higher voices to reduce latency. |
| `f_max` | 1000 Hz | Highest pitch searched. |
| `threshold` | 0.1 | Dip threshold. Lower is stricter (more frames marked unvoiced); higher accepts noisier frames. |
| `window` | longest period | Comparison window length. |
| `silence_db` | −60 dBFS | Frames quieter than this are marked unvoiced. `None` disables it. |

Algorithm reference: A. de Cheveigné & H. Kawahara, *"YIN, a fundamental frequency estimator for speech and music"*,
JASA 111(4), 2002. The module docstring and comments in [src/dsfp_tune/analysis/yin.py](src/dsfp_tune/analysis/yin.py)
walk through each step.
