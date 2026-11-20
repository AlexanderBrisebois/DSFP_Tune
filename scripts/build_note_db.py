#!/usr/bin/env python3
"""Write the notes/keys database files from dsfp_tune.theory.

    python scripts/build_note_db.py              # A4 = 440 Hz
    python scripts/build_note_db.py --a4 432

Outputs:
    data/theory/notes.csv  - every MIDI note 0..127: name, pitch class, octave, frequency
    data/theory/keys.json  - every tonic x scale: intervals, pitch classes, note
                             names, and every in-key MIDI note 0..127 (look up
                             frequencies in notes.csv)
"""

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dsfp_tune.theory import SCALES, all_keys, note_table  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--a4", type=float, default=440.0, help="reference pitch for A4 in Hz")
    p.add_argument("--out", type=Path, default=ROOT / "data" / "theory")
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    notes = note_table(a4=args.a4)
    with open(args.out / "notes.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(notes[0]))
        w.writeheader()
        w.writerows(notes)

    keys = {
        k.name: {
            "tonic": k.note_names[0],
            "tonic_pitch_class": k.tonic,
            "scale": k.scale,
            "intervals": list(k.intervals),
            "pitch_classes": list(k.pitch_classes),
            "note_names": list(k.note_names),
            "midi_notes": [n["midi"] for n in k.notes_in_range(0, 127)],
        }
        for k in all_keys(tuple(SCALES))
    }
    with open(args.out / "keys.json", "w") as f:
        json.dump({"a4_hz": args.a4, "tuning": "12-TET", "keys": keys}, f, indent=1)

    print(f"wrote {len(notes)} notes and {len(keys)} keys to {args.out} (A4 = {args.a4} Hz)")


if __name__ == "__main__":
    main()
