# DSFP Tune plugin (AU / VST3 / Standalone)

The pitch correction from the Python package, as an audio plugin for GarageBand, Logic and other DAWs.
GarageBand loads **Audio Units (AU)** only. The same build also produces a **VST3** (Ableton, Reaper, FL Studio,
Cubase, ...) and a **Standalone** app for trying it with a microphone.

```
cpp/
  CMakeLists.txt
  src/dsp/      C++17 port of the Python DSP, no dependencies, real-time safe
    Fft.*               radix-2 FFT with numpy-compatible rfft/irfft
    Yin.*               dsfp_tune/analysis/yin.py         (Yin, YinStream)
    FrequencyShifter.*  dsfp_tune/synthesis/freq_shift.py (phase vocoder)
    PitchCorrector.*    dsfp_tune/correction.py           (NoteSnapper, PitchCorrector)
  src/plugin/   JUCE wrapper: parameters, processing, editor UI
  src/tools/
    dsfp_offline.cpp    runs the C++ DSP on files (used by tests/test_cpp_port.py)
    plugin_host.cpp     loads the *built* AU/VST3 and renders audio through it (tests)
```

## Build

Needs Apple's command-line tools (`xcode-select --install`), CMake ≥ 3.22 and Ninja (`brew install cmake ninja`);
full Xcode is not required. JUCE 8.0.4 is used from `cpp/build/JUCE` if present, otherwise CMake downloads it.

```bash
cmake -S cpp -B cpp/build/cmake -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build cpp/build/cmake --target DSFPTune_AU DSFPTune_VST3 DSFPTune_Standalone
```

The build copies the plugins into your user plugin folders:

- `~/Library/Audio/Plug-Ins/Components/DSFP Tune.component` (AU: GarageBand, Logic)
- `~/Library/Audio/Plug-Ins/VST3/DSFP Tune.vst3`

The Standalone app is at `cpp/build/cmake/DSFPTune_artefacts/Release/Standalone/DSFP Tune.app`.

Check the AU with Apple's validator (GarageBand only loads AUs that pass):

```bash
auval -v aufx Dstn Dsfp
```

## Use in GarageBand

1. Quit and reopen GarageBand after building, so it rescans plugins.
2. Select the vocal track, open **Smart Controls** (B) → **Plug-ins** → click an empty slot →
   **Audio Units → DSFP → DSFP Tune**.
3. Pick **Snap to** (nearest note or nearest note in key), the key and scale, and adjust the knobs.

If it doesn't appear: run the `auval` command above, then restart the Mac's AU registry with
`killall -9 AudioComponentRegistrar` and reopen GarageBand.

**Latency:** the plugin reports its delay (1024 samples ≈ 23 ms at 44.1/48 kHz) so GarageBand lines up playback.
While *recording* with monitoring on you'll hear that delay plus the audio buffer; for tracking, monitor dry and
add the plugin afterwards.

## Controls

Same settings as the dashboard's Correction card and the `tune.py autotune` options:

| Control | Python argument | Default |
|---|---|---|
| Snap to (nearest note / nearest note in key), Key, Scale | `key` | nearest note |
| Attack | `retune_ms` | 25 ms |
| Release | `release_ms` | 25 ms |
| Amount | `amount` | 100 % |
| Stickiness | `hysteresis` | 15 cents |
| Note window | `note_window_ms` | 120 ms |
| Note jump | `note_jump` | 75 cents |
| Hold | `hold_ms` | 60 ms |
| Max correction | `max_correction` | 200 cents |

Stereo tracks are summed to mono for pitch detection and correction, and the result goes to both channels.
Not in the plugin yet: chord harmony (`--harmony`) and the pitch hold (`pitch_hold_ms`, which bridges short
YIN dropouts inside a note).

## Tests

`tests/test_cpp_port.py` (run with `pytest`) compares the C++ DSP against the Python reference on the same audio:
YIN pitch tracks, the shifter, and the whole corrector (per-frame targets and corrections plus the output). They
agree to floating-point rounding (pitch ~1e-16 relative; audio differs only by float32 rounding). It also runs the
built AU and VST3 through `dsfp_plugin_host` when they exist. The tests are skipped if the C++ hasn't been built.

## License note

JUCE 8 is dual-licensed (AGPLv3 or a commercial licence; see juce.com). Fine for this project; check the terms
before distributing the plugin commercially. Sharing the plugin with other Macs needs signing and notarisation
with an Apple Developer ID.
