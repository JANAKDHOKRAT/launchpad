# Sampled instruments

Put your own sample libraries here and they show up as sounds on every **Piano / synth** page
(next to the built-in synths, and in the side-button sound slots). Everything in this folder except
this README is ignored by git: **do not commit sample libraries** unless their licence explicitly
allows redistribution. Most commercial ("royalty-free") libraries do not.

After adding or changing a library, press **Rescan instruments** in the piano editor (or restart).

## Layouts

One folder per instrument:

```
instruments/
  my-grand/                 SFZ library: every .sfz file becomes one instrument
    my-grand.sfz
    samples/C4v8.flac ...
  upright/                  note-named files: no .sfz needed
    C1.wav  F#1.wav  C2.wav ...
    instrument.json         optional
```

**SFZ**: the common subset is supported: `<control> <global> <master> <group> <region>`,
`#define`, `#include`, `sample`, `default_path`, `key` / `lokey` / `hikey` / `pitch_keycenter`,
`lovel` / `hivel`, `transpose`, `tune`, `pitch_keytrack`, `volume`, `amplitude`, `offset`, `end`,
`ampeg_release`, `note_offset` / `octave_offset`. Release-trigger, round-robin and pedal regions are
skipped. Kontakt (`.nki`), EXS and plugin-only (VST/AU) instruments cannot be loaded.

**Note-named files** (WAV, FLAC, AIFF, OGG): the note is read from the file name, e.g. `C4.wav`,
`Db4.flac`, `F#3.aiff`, `Piano.mf.C4.aiff`, `A0v8.flac`, or a MIDI number such as `060.wav`.
If the files are numbered some other way, map name parts to notes with `"note_map": {"000": 21, ...}`
in `instrument.json`; a VSCO-style `MappingChart.txt` (`000=21` per line) is read automatically.
C4 is MIDI 60 (middle C); if your library calls middle C "C3", set `"transpose": 12`. Each sample
covers the keys halfway to its neighbours and is re-pitched for the notes in between.

The original Launchpad is not velocity sensitive, so one velocity layer is used: medium-loud by
default (`mf`, or about 60% of the way up `v1`..`vN` / `dyn1`..`dynN`), chosen per note.

## instrument.json (optional)

```json
{
  "name": "Upright Piano",
  "short": "Upr",
  "credit": "Upright Piano KW by ... (CC0)",
  "release": 0.4,
  "gain": 1.0,
  "velocity": 90,
  "layer": "mf",
  "transpose": 0,
  "max_seconds": 6,
  "sfz": "only-this-one.sfz"
}
```

| key | meaning | default |
|---|---|---|
| `name`, `short` | name in the GUI, 5-character label on the side buttons | folder name |
| `credit` | shown in the piano editor: put the attribution your licence requires here | |
| `release` | fade-out after letting go of a key, seconds | SFZ `ampeg_release`, else 0.3 |
| `gain` | output level multiplier | automatic: matched to the built-in synths |
| `velocity` | which SFZ velocity layer to play (1–127) | 90 |
| `layer` | note-named files: which layer, e.g. `mf`, `ff`, `v12`, `dyn3` | medium-loud |
| `note_map` | note-named files: `{"name part": MIDI note}` for numbered files | `MappingChart.txt` if present |
| `transpose` | semitones added to every sample's note | 0 |
| `max_seconds` | notes are cut (with a fade) after this long, to save memory | 6 |
| `sfz` | use only this `.sfz` file of the folder | all of them |

Memory: each rendered note is about 2.3 MB (6 s, stereo, 48 kHz); up to ~100 notes are cached.
