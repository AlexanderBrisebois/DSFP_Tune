# DSFP_Tune

Vocal pitch correction (in the spirit of Auto-Tune) built on Fourier-transform analysis. A command-line
tool written in Python, with optional C++ kernels if we need more speed.

Contributors' agents record what they've done and are working on in [AGENTS/](AGENTS/). Check there before
starting something new.

## How it works

For a monophonic vocal recording, the pipeline is:

```
audio ──► 1. pitch detection ──► 2. target note ──► 3. pitch shift ──► corrected audio
          (f0 per frame, YIN)    (snap to key)      (time-varying ratio,
                                                     formant-preserving)
```

1. **Pitch detection.** Estimate the fundamental frequency (f0) of each short frame (around 30 ms) with the
   **YIN** algorithm, and flag unpitched frames (breaths, "s"/"t" consonants, silence) so they are left alone.
2. **Target note.** Convert f0 to a MIDI note number and snap it to the nearest note of the chosen key
   (`dsfp_tune/theory`). Hysteresis stops flicker between two notes, and a *retune speed* sets how quickly the
   pitch glides to the target: instant gives the robotic "T-Pain" effect, slower sounds natural and keeps vibrato.
3. **Pitch shift.** Shift each frame by `target_f0 / detected_f0` without changing duration, while preserving
   formants (otherwise the voice sounds like a chipmunk).

### Design decisions so far

- **YIN for pitch detection.** It is accurate on voice, cheap to compute, and works in real time (it uses only past
  samples). Picking the largest FFT peak was rejected: vocal harmonics are often louder than the fundamental, which
  causes octave errors, and FFT bins are too coarse (about 21 Hz apart at 44.1 kHz / 2048 points, which is roughly
  3 semitones near 110 Hz). pYIN (offline only) and CREPE (heavy, 64 ms window) were also considered.
- **The FFT is used inside YIN.** YIN compares a frame with delayed copies of itself. That comparison is computed
  as `d(τ) = E0 + E(τ) − 2·r(τ)`, where the cross-correlation `r(τ)` comes from `IFFT(conj(FFT(ref)) · FFT(frame))`.
  The result is identical to the textbook formula and about 24× faster. YIN frames are **not windowed** (no Hann);
  they are zero-padded so the FFT doesn't wrap around. This is separate from any windowed STFT the pitch shifter uses.
- **One detector interface:** `detect(frame) -> (f0, aperiodicity, voiced)`, so detectors can be swapped or moved
  to C++ without touching the rest of the pipeline.
- **The microphone is polled inside GUI windows.** Matplotlib's macOS window loop holds Python's GIL, which starves
  audio-callback threads (no audio arrives). The live window therefore reads the microphone from its animation timer
  (`MicrophoneStream(use_callback=False)`). Elsewhere, the callback-and-queue mode is used.
- **Pitch shifting with an FFT frequency shifter (phase vocoder).** This builds on the idea in
  `scripts/freq_shift.py`: move the spectral power at f to f × shift, then inverse-FFT. To work on real audio it runs
  frame by frame (STFT, 2048 points, 75% overlap), moves *every* harmonic by the same ratio, and tracks each bin's phase
  so frames join without clicks. At ratio 1 it passes audio through exactly, so unpitched sounds are untouched.
  Formants move with the pitch, which is fine for small corrections; formant preservation is a later improvement.
- **The target note is chosen from a ~120 ms running median of the pitch**, so vibrato that swings past the midpoint
  between two notes doesn't flip the target. Each frame's instantaneous pitch is what gets corrected, and a jump of
  more than ¾ of a semitone switches notes immediately.
- **Offline first, then real time.** Develop and validate in Python/NumPy on audio files, then build a streaming
  version, and port the hot loops to C++ (see [cpp/README.md](cpp/README.md)) if needed.

## Status

| Area | Status |
|---|---|
| Project structure, CLI (`tune.py`) | ✅ Done |
| Audio loading (MP3/WAV/FLAC, resample, downmix) and WAV writing — `dsfp_tune/io` | ✅ Done |
| Notes and keys database (12-TET, 12 tonics × 15 scales) — `dsfp_tune/theory` | ✅ Done |
| YIN pitch detector (FFT-based), offline and streaming — `dsfp_tune/analysis/yin.py` | ✅ Done, tested on synthetic signals |
| Microphone input: record, stream, list devices — `dsfp_tune/io/microphone.py` | ✅ Done |
| Pitch plots: live window, recording/file analysis — `dsfp_tune/plotting.py` | ✅ Done |
| Target note: nearest note or in-key note, hysteresis, retune speed — `dsfp_tune/correction.py` | ✅ Done |
| FFT frequency shifter (phase vocoder), streaming — `dsfp_tune/synthesis/freq_shift.py` | ✅ Done (formants not preserved yet) |
| Autotune end to end: mic or file in → corrected audio, before/after plot — `tune.py autotune` | ✅ Done, tested on synthetic voices |
| Real-time autotune through headphones — `tune.py autotune --live` | ✅ Works; ~120 ms total latency on the built-in mic |
| Test on real vocal recordings; tune YIN and correction settings | 🔜 Next |
| Formant preservation, phase locking (less "phasey" sound), lower live latency | ⏳ Planned |
| C++ kernels / plugin | ⏳ Later, if needed |

## Layout

```
DSFP_Tune/
├── tune.py              # the CLI entry point
├── dsfp_tune/           # library code used by tune.py
│   ├── io/              # audio loading/saving, microphone input       [done]
│   ├── theory/          # notes <-> frequencies, keys/scales           [done]
│   ├── analysis/        # pitch (f0) detection: YIN                    [done]
│   ├── synthesis/       # FFT frequency shifter (pitch shift)          [done]
│   ├── correction.py    # autotune: target note + retune + shifting    [done]
│   └── plotting.py      # pitch plots (static, live, before/after)     [done]
├── AGENTS/              # per-contributor agent status files (coordination)
├── cpp/                 # optional C++ (pybind11) kernels for speed
├── tests/               # pytest suite
├── scripts/             # one-off helper scripts
├── notebooks/           # Jupyter exploration
├── data/theory/         # generated notes.csv and keys.json
├── data/audio/          # input audio and microphone recordings (git-ignored)
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

Microphone input uses **sounddevice** (PortAudio is bundled on macOS and Windows; on Linux install
`libportaudio2`). On macOS, the app running Python (Terminal, iTerm or VS Code) needs microphone permission:
System Settings → Privacy & Security → Microphone. Without it, recordings are silent and the tool says so.

## Usage

Run from the project root:

```bash
python tune.py inspect data/audio/song.mp3                 # print sample rate, duration, peak, rms
python tune.py inspect data/audio/song.mp3 --plot          # plot time vs. amplitude
python tune.py inspect song.mp3 --sr 22050 --save data/output/song.npz

python tune.py live                                        # live pitch window from the microphone (close to stop)
python tune.py live --no-plot                              # text tuner in the terminal instead (Ctrl+C to stop)
python tune.py record                                      # record 5 s → data/audio/mic_<timestamp>.wav, then plot it
python tune.py record --seconds 10 --out data/audio/scale.wav
python tune.py pitch data/audio/scale.wav                  # plot the pitch of any audio file
python tune.py pitch take.wav --save take.png --no-show    # export the plot instead of showing it
python tune.py devices                                     # list microphones; use --device <index or name>

python tune.py autotune                                    # record 5 s, correct, play original + corrected, plot
python tune.py autotune data/audio/take.wav                # correct a file → data/output/take_tuned.wav
python tune.py autotune --key "A minor" --retune-ms 0      # only A-minor notes, instant (robotic) correction
python tune.py autotune --retune-ms 150                    # gentle correction that keeps vibrato
python tune.py autotune --live                             # real time: mic → corrected voice → headphones (Ctrl+C)
```

`autotune --live` refuses to run when the output is the built-in speakers, because the microphone would pick up the
corrected voice and feed back. Plug in headphones or choose one with `--output-device`; `--allow-speakers` overrides
the check. The default key is chromatic (any semitone). The default `--retune-ms` is 25.

`inspect --save` writes an `.npz` with `time`, `amplitude` and `sample_rate` arrays.

The pitch plots:

- **Live window:** your pitch scrolls over the last 8 s on a one-line-per-semitone grid that follows your voice.
  The readout shows the note, cents sharp or flat, and Hz. It is green within ±10 cents, amber within ±25, red beyond.
- **Recording / file plot**, three panels:
  1. the waveform;
  2. a spectrogram with the YIN f0 line on top. The line should sit on the lowest bright harmonic; if it jumps to
     a higher one, that's an octave error;
  3. cents off the note being held, with a green ±10 cent band. The held note is found with a running median, so
     vibrato shows as a smooth wave.

The **autotune plot** shows the sung pitch (grey), the target notes (orange, dashed) and the corrected output (blue,
re-measured with YIN on the output audio) on a note grid where in-key notes are darker. Below it is cents off target,
before and after.

## Loading audio in code

```python
from dsfp_tune.io import load_audio, save_wav

sig = load_audio("data/audio/song.mp3")         # native sample rate, mono
t, x = sig.time_amplitude()                     # time (s), amplitude in [-1, 1]
sig = load_audio("song.mp3", sample_rate=22050, mono=False)   # resample, keep stereo
save_wav("data/output/copy.wav", sig)           # 16-bit PCM
```

Decoding backends: `ffmpeg` (any format, can resample) and `soundfile` (libsndfile >= 1.1; no external binary,
no resampling). Integer-PCM WAV files also load with Python's built-in `wave` module, so no extra package is needed
for them. `backend="auto"` picks for you.

## Pitch detection in code

```python
from dsfp_tune.analysis import Yin, YinStream, yin_track
from dsfp_tune.theory import nearest_note

# Whole file (offline)
track = yin_track(sig.samples, sig.sample_rate, hop_length=256, f_min=70, f_max=1000)
track.times, track.f0, track.voiced          # f0 is NaN where unvoiced

# One frame at a time
yin = Yin(sig.sample_rate, f_min=70, f_max=1000)
est = yin.detect(sig.samples[:yin.frame_length])
est.f0, est.voiced, est.confidence
nearest_note(est.f0)                         # ('A3', +12.3): note and cents off

# Streaming: feed blocks of any size, get an estimate every hop_length samples
stream = YinStream(44100, hop_length=256)
for est in stream.push(block): ...
```

| YIN parameter | Default | Effect |
|---|---|---|
| `f_min` | 70 Hz | Lowest pitch searched. Sets the frame length and therefore the latency (28.6 ms at 44.1 kHz with the defaults). Raise it for higher voices to reduce latency. |
| `f_max` | 1000 Hz | Highest pitch searched. |
| `threshold` | 0.1 | Dip threshold. Lower is stricter (more frames marked unvoiced); higher accepts noisier frames. |
| `window` | longest period | Comparison window length. |
| `silence_db` | −60 dBFS | Frames quieter than this are marked unvoiced. `None` disables it. |

Algorithm reference: A. de Cheveigné & H. Kawahara, *"YIN, a fundamental frequency estimator for speech and music"*,
JASA 111(4), 2002. The module docstring in [dsfp_tune/analysis/yin.py](dsfp_tune/analysis/yin.py) walks through each step.

## Pitch correction in code

```python
from dsfp_tune.correction import PitchCorrector, correct_pitch
from dsfp_tune.synthesis import freq_shift

y = freq_shift(x, 2 ** (1 / 12))                       # whole signal up one semitone

result = correct_pitch(x, 44100, key="A minor", retune_ms=25)   # offline, no delay
result.samples, result.times, result.f0, result.target_midi, result.correction

pc = PitchCorrector(44100, key="A minor", n_fft=1024)  # streaming (any block size)
out_block = pc.process(in_block)                       # delayed by pc.latency samples
pc.latest                                              # CorrectionFrame: f0, target_midi, correction, ratio
```

## Notes and keys database

`dsfp_tune/theory` holds the source of truth (12-TET, A4 = 440 Hz by default);
`scripts/build_note_db.py [--a4 432]` regenerates the files in `data/theory/`:

- `notes.csv`: MIDI 0..127 with sharp/flat name, pitch class, octave, frequency (Hz)
- `keys.json`: 12 tonics x 15 scales (major, minor, harmonic/melodic minor,
  the 7 modes, pentatonics, blues, chromatic) with intervals, pitch classes,
  spelled note names and every in-key MIDI note

```python
from dsfp_tune.theory import parse_key, midi_to_freq, nearest_note
k = parse_key("A minor")
k.note_names          # ('A', 'B', 'C', 'D', 'E', 'F', 'G')
k.notes_in_range(57, 69)   # in-key notes A3..A4 with frequencies
nearest_note(445.0)   # ('A4', 19.56): nearest note and cents off
```
