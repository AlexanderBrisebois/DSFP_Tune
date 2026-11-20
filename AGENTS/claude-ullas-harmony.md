# Agent: Claude (working for Ullas): chord harmony on top of snapping

**Status:** done, waiting for Ullas's feedback
**Last updated:** 2026-11-20 15:35 PT

## Done
| What | Files | Commit |
|---|---|---|
| `diatonic_triad(key, root_midi)`: triad built on a note from the key's own notes (C major + A -> A C E = A minor; + B -> B D F). 7-note scales only | `dsfp_tune/theory/keys.py`, `dsfp_tune/theory/__init__.py` | not committed yet (Ullas commits) |
| `PitchCorrector(..., harmony=0.5)`: the snapped note stays the root; two extra `FrequencyShifter`s fed the same input add the third and fifth, tuned to those notes, at level `harmony` relative to the lead. Voices fade in/out over 15 ms (muted when nothing is snapped). `CorrectionFrame.chord` records the triad. `harmony=0` is bit-identical to before | `dsfp_tune/correction.py` | not committed yet |
| CLI `python tune.py autotune [file] --key "C major" --harmony 0.5` (also with `--live`; needs `--key`). Offline output is scaled down if lead + chord would clip | `tune.py` | not committed yet |
| Dashboard setting `SnapSettings.harmony` (0..1, default 0), saved/loaded with the other snap settings. The page doesn't show a control for it yet | `dsfp_tune/web/settings.py`, `tests/test_web.py` (endpoint now returns `harmony`) | not committed yet |
| Tests | `tests/test_harmony.py` | not committed yet |
| Pulled Alex's `03f239a` (dashboard autotuned playback), fast-forward, no conflicts | | |
| **Dashboard hookup:** "Chord harmony" slider (0-100 %) in the Snapping panel, enabled only for "Nearest note in key" with a 7-note scale; hint shows an example chord from the key. Saved with the snap settings; `/api/autotune` takes `harmony=` (falls back to the saved value) and `autotune_samples()` passes it to `correct_pitch` (ignored in chromatic mode / non-7-note scales; output scaled down if it would clip). The recording hint shows "chords N%". Verified in headless Chromium with a fake mic: request carries `harmony=0.5`, no console errors | `dsfp_tune/web/server.py`, `dsfp_tune/web/static/{index.html,app.js,styles.css}` | not committed yet |

| **Chord dropouts fix** (Ullas: chords not always added): chords were gated on YIN `voiced`, which flickers on real singing. Now a chord is held through pitch dropouts for up to `chord_hold_ms` (500) while the input stays within 12 dB of the note's level; it still ends when the singer stops. Chord voices keep the last detected correction while held | `dsfp_tune/correction.py`, `tests/test_harmony.py` | not committed yet |

## Notes for other agents
- **Shifter quirk (Alex):** after `FrequencyShifter` runs at ratio exactly 1 (exact pass-through, resyncs phases), a later shift by ~3 semitones comes out ~4 dB quieter, and some fixed ratios (e.g. 3.7 semitones) lose a lot of level, because there's no phase locking between the bins of a peak. The chord voices avoid ratio 1 (they keep the last chord's intervals while muted). Phase locking in the shifter would make the chord voices more even.
- File mode still has no autotuned playback (see Alex's note), so chords are heard on microphone takes only.
- Live latency is unchanged (voices use the same frame size); CPU is ~3x the lead-only shifter.
