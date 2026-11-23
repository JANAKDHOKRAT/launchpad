"""Sampled instruments: play your own (licensed) sample libraries on the piano page.

Put each instrument in its own folder under `instruments/` (gitignored, see instruments/README.md):

* **SFZ**: a folder with one or more `.sfz` files and the samples they reference. Every `.sfz`
  file becomes one instrument. A basic subset of SFZ is read: <control> <global> <master> <group>
  <region>, #define / #include, sample, default_path, key / lokey / hikey / pitch_keycenter,
  lovel / hivel, transpose, tune, pitch_keytrack, volume, amplitude, offset, end, ampeg_release,
  note_offset / octave_offset. Release triggers, round robins (seq_position > 1, lorand > 0)
  and pedal / CC-switched regions are skipped.
* **Note-named files**: a folder of WAV / FLAC / AIFF / OGG files whose names contain the note,
  e.g. `C4.wav`, `F#3.flac`, `Piano.mf.Db4.aiff`, `A0v8.flac` (C4 = MIDI 60), or a MIDI
  number (`060.wav`). Velocity layers named pp/p/mp/mf/f/ff or v1..v16 are detected; one layer
  is used (the original Launchpad has no velocity), medium-loud by default.

An optional `instrument.json` in the folder overrides:
  {"name": "My Piano", "short": "Pno", "release": 0.4, "gain": 0.5, "velocity": 90,
   "layer": "mf", "transpose": 0, "max_seconds": 6, "credit": "Samples by ... (CC-BY 3.0)",
   "sfz": "only-this-file.sfz", "note_map": {"000": 21}}

Notes are rendered as stereo float32 buffers: the nearest sample is resampled to the
note's pitch and to the output sample rate in one step, then cached.
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional

import numpy as np
import soundfile as sf

from . import fx as _fx

PREFIX = "sample:"
AUDIO_EXTS = (".wav", ".flac", ".aif", ".aiff", ".ogg")
DYNAMICS = ("ppp", "pp", "p", "mp", "mf", "f", "ff", "fff")
TARGET_PEAK = 0.45           # the built-in synths peak here; sampled instruments are matched to it
DEFAULT_MAX_SECONDS = 6.0    # long piano tails cost memory (6 s stereo at 48 kHz = 2.3 MB per note)
DEFAULT_VELOCITY = 90
_NOTE = re.compile(r"^([A-Ga-g])(#|b|s|♯|♭)?(-?\d)$")
_NOTE_IN_NAME = re.compile(r"^([A-Ga-g])(#|b|s)?(-?\d)(?:v(\d+))?$")
_SEMI = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def parse_note(v) -> Optional[int]:
    """MIDI number from '60', 'c4', 'C#4', 'db4' (C4 = 60). None if not a note."""
    s = str(v).strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    m = _NOTE.match(s)
    if not m:
        return None
    acc = {"#": 1, "s": 1, "♯": 1, "b": -1, "♭": -1}.get(m.group(2) or "", 0)
    return 12 * (int(m.group(3)) + 1) + _SEMI[m.group(1).lower()] + acc


@dataclass
class Region:
    path: str
    lokey: int = 0
    hikey: int = 127
    center: int = 60
    lovel: int = 1
    hivel: int = 127
    transpose: int = 0
    tune: float = 0.0        # cents
    keytrack: float = 100.0  # cents per key
    volume: float = 0.0      # dB
    amplitude: float = 100.0  # %
    offset: int = 0
    end: int = 0             # 0 = whole file


@dataclass
class Instrument:
    id: str
    name: str
    short: str
    source: str              # the .sfz file or the folder
    regions: List[Region] = field(default_factory=list)
    release: float = 0.3
    gain: float = 1.0
    velocity: int = DEFAULT_VELOCITY
    max_seconds: float = DEFAULT_MAX_SECONDS
    credit: str = ""

    def region_for(self, midi: int) -> Region:
        vel = [r for r in self.regions if r.lovel <= self.velocity <= r.hivel] or self.regions
        for r in vel:  # first region that maps this key, like an SFZ player
            if r.lokey <= midi <= r.hikey:
                return r
        return min(vel, key=lambda r: abs(r.center - midi))  # outside the mapped range: nearest sample


INSTRUMENTS: Dict[str, Instrument] = {}
ERRORS: Dict[str, str] = {}  # folder / file -> why it could not be loaded


# ------------------------------------------------------------------ SFZ
def _sfz_text(path: str, root_dir: str, depth: int = 0) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        s = f.read()
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"(^|\s)//[^\n]*", r"\1", s)

    def include(m):
        if depth > 8:
            return ""
        inc = os.path.join(root_dir, m.group(1).replace("\\", "/"))
        return _sfz_text(inc, root_dir, depth + 1) if os.path.isfile(inc) else ""
    return re.sub(r'#include\s+"([^"]+)"', include, s)


def parse_sfz(path: str) -> tuple:
    """(regions, max ampeg_release) of an SFZ file."""
    root_dir = os.path.dirname(os.path.abspath(path))
    s = _sfz_text(path, root_dir)
    defines = dict(re.findall(r"#define\s+(\$\w+)\s+(\S+)", s))
    s = re.sub(r"#define\s+\$\w+\s+\S+", " ", s)
    for k in sorted(defines, key=len, reverse=True):
        s = s.replace(k, defines[k])

    tokens = list(re.finditer(r"<(\w+)>|\b([A-Za-z0-9_]+)=", s))
    control: dict = {}
    levels = {"global": {}, "master": {}, "group": {}}
    current: Optional[dict] = None
    raw_regions: List[dict] = []
    for i, m in enumerate(tokens):
        if m.group(1):
            h = m.group(1).lower()
            if h == "region":
                current = {}
                raw_regions.append(current)
                current["__inherit"] = {**levels["global"], **levels["master"], **levels["group"]}
            elif h in levels:
                for lv in list(levels)[list(levels).index(h):]:  # a new level resets the ones below it
                    levels[lv] = {}
                current = levels[h]
            elif h == "control":
                current = control
            else:  # <curve>, <effect>, <midi>, ...: ignore their opcodes
                current = None
            continue
        end = tokens[i + 1].start() if i + 1 < len(tokens) else len(s)
        val = s[m.end():end].strip()
        key = m.group(2).lower()
        if key not in ("sample", "default_path"):
            val = val.split()[0] if val.split() else ""
        if current is not None:
            current[key] = val

    note_offset = int(float(control.get("note_offset", 0) or 0)) + 12 * int(float(control.get("octave_offset", 0) or 0))
    default_path = control.get("default_path", "").replace("\\", "/")
    regions, release = [], 0.0

    def num(d, k, default, conv=float):
        try:
            return conv(float(d[k])) if k in d else default
        except ValueError:
            n = parse_note(d[k])
            return conv(n) if n is not None else default

    for raw in raw_regions:
        d = {**raw.pop("__inherit"), **raw}
        sample = d.get("sample", "").replace("\\", "/")
        if not sample or sample.startswith("*"):  # *sine, *noise: generators, not files
            continue
        if d.get("trigger", "attack").lower() not in ("attack", "first", "legato"):
            continue
        if num(d, "seq_position", 1) > 1 or num(d, "lorand", 0) > 0:
            continue
        if any(re.fullmatch(r"locc\d+", k) and num(d, k, 0) > 0 for k in d):  # e.g. pedal down only
            continue
        r = Region(path=os.path.normpath(os.path.join(root_dir, default_path, sample)))
        if "key" in d:
            r.lokey = r.hikey = r.center = num(d, "key", 60, int)
        r.lokey = num(d, "lokey", r.lokey, int)
        r.hikey = num(d, "hikey", r.hikey, int)
        r.center = num(d, "pitch_keycenter", r.center if "key" in d else 60, int)
        r.lokey, r.hikey, r.center = (r.lokey + note_offset, r.hikey + note_offset, r.center + note_offset)
        r.lovel, r.hivel = num(d, "lovel", 1, int), num(d, "hivel", 127, int)
        r.transpose = num(d, "transpose", 0, int)
        r.tune = num(d, "tune", 0.0)
        r.keytrack = num(d, "pitch_keytrack", 100.0)
        r.volume, r.amplitude = num(d, "volume", 0.0), num(d, "amplitude", 100.0)
        r.offset, r.end = num(d, "offset", 0, int), num(d, "end", 0, int)
        release = max(release, num(d, "ampeg_release", 0.0))
        regions.append(r)
    return regions, release


# ------------------------------------------------------------------ note-named folders
def _layer_rank(layer: str, all_layers) -> float:
    """Lower is better: prefer a medium-loud layer."""
    if layer in DYNAMICS:
        i, mf = DYNAMICS.index(layer), DYNAMICS.index("mf")
        return abs(i - mf) + (0.5 if i < mf else 0)  # on a tie prefer the louder layer
    m = re.fullmatch(r"(v|dyn)(\d+)", layer)
    if m:  # numbered layers (v1..v16, dyn1..dyn3): about 60% of the way up
        nums = [int(x[len(m.group(1)):]) for x in all_layers if re.fullmatch(m.group(1) + r"\d+", x)]
        return abs(int(m.group(2)) - max(1, round(max(nums) * 0.6)))
    return 0.0 if layer == "" else 50.0


def read_mapping_chart(folder: str) -> Dict[str, int]:
    """VSCO-style MappingChart.txt ("000=21" per line: file-name token -> MIDI note), if present."""
    path = os.path.join(folder, "MappingChart.txt")
    out: Dict[str, int] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k and re.fullmatch(r"\d+", v.strip()):
                    out[k.strip()] = int(v)
    return out


def scan_note_files(folder: str, layer: Optional[str] = None,
                    note_map: Optional[Dict[str, int]] = None) -> List[Region]:
    """Regions from audio files whose names carry the note (or a token listed in `note_map`)."""
    note_map = {str(k): int(v) for k, v in (note_map or read_mapping_chart(folder)).items()}
    found: Dict[int, Dict[str, str]] = {}  # midi -> layer -> path
    for path in sorted(glob.glob(os.path.join(folder, "**", "*"), recursive=True)):
        if os.path.splitext(path)[1].lower() not in AUDIO_EXTS:
            continue
        toks = re.split(r"[^A-Za-z0-9#]+", os.path.splitext(os.path.basename(path))[0])
        midi, lay = None, ""
        for t in toks:
            m = _NOTE_IN_NAME.match(t)
            if t in note_map:
                midi = note_map[t]
            elif m and not note_map:
                midi = parse_note(m.group(1) + (m.group(2) or "") + m.group(3))
                if m.group(4):
                    lay = "v" + m.group(4)
            elif t.lower() in DYNAMICS:
                lay = t.lower()
            elif re.fullmatch(r"(?i)dyn\d+", t):
                lay = t.lower()
        if midi is None and not note_map:  # a bare MIDI number, e.g. 060.wav / piano_60.wav
            nums = [int(t) for t in toks if re.fullmatch(r"\d{2,3}", t) and 0 <= int(t) <= 127]
            midi = nums[-1] if nums else None
        if midi is not None and 0 <= midi <= 127:
            found.setdefault(midi, {}).setdefault(lay, path)  # first file wins (round robins)
    if not found:
        return []
    all_layers = {l for lays in found.values() for l in lays}
    notes = []
    for midi in sorted(found):  # best layer per note: libraries often miss a layer on some notes
        lays = found[midi]
        pick = layer if layer in lays else min(lays, key=lambda l: (_layer_rank(l, all_layers), l))
        notes.append((midi, lays[pick]))
    regions = []
    for i, (midi, path) in enumerate(notes):  # each sample covers the keys halfway to its neighbours
        lo = 0 if i == 0 else (notes[i - 1][0] + midi) // 2 + 1
        hi = 127 if i == len(notes) - 1 else (midi + notes[i + 1][0]) // 2
        regions.append(Region(path=path, lokey=lo, hikey=hi, center=midi))
    return regions


# ------------------------------------------------------------------ discovery
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def scan(directory: str) -> Dict[str, Instrument]:
    """Find every instrument under `directory`; replaces INSTRUMENTS and clears the caches."""
    INSTRUMENTS.clear()
    ERRORS.clear()
    _render.cache_clear()
    _render_fx.cache_clear()
    if not os.path.isdir(directory):
        return INSTRUMENTS
    for entry in sorted(os.listdir(directory)):
        folder = os.path.join(directory, entry)
        if entry.startswith(".") or not os.path.isdir(folder):
            continue
        try:
            meta = {}
            if os.path.isfile(os.path.join(folder, "instrument.json")):
                with open(os.path.join(folder, "instrument.json")) as f:
                    meta = json.load(f)
            sfzs = sorted(glob.glob(os.path.join(folder, "**", "*.sfz"), recursive=True))
            included = set()  # fragments pulled in with #include are not instruments themselves
            for path in sfzs:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for inc in re.findall(r'#include\s+"([^"]+)"', f.read()):
                        included.add(os.path.normpath(os.path.join(os.path.dirname(path), inc.replace("\\", "/"))))
            sfzs = [x for x in sfzs if os.path.normpath(x) not in included]
            if meta.get("sfz"):
                sfzs = [os.path.join(folder, meta["sfz"])]
            if sfzs:
                for path in sfzs:
                    regions, rel = parse_sfz(path)
                    stem = os.path.splitext(os.path.basename(path))[0]
                    name = meta.get("name") or entry
                    if len(sfzs) > 1:
                        name = f"{name} – {stem}"
                    _add(meta, PREFIX + _slug(entry) + ("/" + _slug(stem) if len(sfzs) > 1 else ""),
                         name, path, regions, rel)
            else:
                regions = scan_note_files(folder, meta.get("layer"), meta.get("note_map"))
                _add(meta, PREFIX + _slug(entry), meta.get("name") or entry, folder, regions, 0.0)
        except Exception as e:  # a broken library must not stop the app
            ERRORS[entry] = f"{type(e).__name__}: {e}"
    for k, v in ERRORS.items():
        print(f"Instrument {k!r}: {v}")
    return INSTRUMENTS


def _add(meta: dict, iid: str, name: str, source: str, regions: List[Region], release: float) -> None:
    missing = sorted({os.path.basename(r.path) for r in regions if not os.path.isfile(r.path)})
    regions = [r for r in regions if os.path.isfile(r.path)]
    label = os.path.relpath(source, os.path.dirname(os.path.dirname(source))) if os.path.isfile(source) \
        else os.path.basename(source)
    if missing:
        ERRORS[label] = f"{len(missing)} sample file(s) not found, e.g. {missing[0]}"
    if not regions:
        ERRORS[label] = ERRORS.get(label, "no playable samples found")
        return
    if "transpose" in meta:
        for r in regions:
            r.transpose += int(meta["transpose"])
    velocity = int(meta.get("velocity", DEFAULT_VELOCITY))
    if "gain" in meta:
        gain = float(meta["gain"])
    else:  # auto: loudest attack of the samples actually used peaks like the synths
        peak = 0.0
        for r in {r.path: r for r in regions if r.lovel <= velocity <= r.hivel}.values():
            sr = sf.info(r.path).samplerate
            x, _ = sf.read(r.path, start=r.offset, frames=sr // 2, dtype="float32", always_2d=True)
            peak = max(peak, float(np.max(np.abs(x))) * 10 ** (r.volume / 20) * r.amplitude / 100 if len(x) else 0.0)
        gain = min(8.0, max(0.05, TARGET_PEAK / peak)) if peak > 0 else 1.0
    INSTRUMENTS[iid] = Instrument(
        id=iid, name=str(name), short=str(meta.get("short") or name)[:5].strip(), source=source, regions=regions,
        release=float(meta.get("release") or min(3.0, max(0.05, release or 0.3))),
        gain=gain, velocity=velocity,
        max_seconds=float(meta.get("max_seconds", DEFAULT_MAX_SECONDS)), credit=str(meta.get("credit", "")))


def is_sampled(patch: str) -> bool:
    return patch.startswith(PREFIX)


# ------------------------------------------------------------------ rendering
def render_note(patch: str, midi: float, sr: int, fx=None) -> np.ndarray:
    """Stereo float32 buffer of `midi` on sampled instrument `patch` (KeyError if unknown)."""
    if not fx or _fx.is_neutral(fx):
        return _render(patch, float(midi), sr)
    fx = _fx.clean(fx)
    return _render_fx(patch, float(midi) + _fx.semitones(fx), sr, _fx.key_of(fx))


@lru_cache(maxsize=48)
def _render_fx(patch: str, midi: float, sr: int, fxkey) -> np.ndarray:
    return _fx.apply(_render(patch, midi, sr), sr, dict(fxkey), pitch=False)


@lru_cache(maxsize=96)
def _render(patch: str, midi: float, sr: int) -> np.ndarray:
    inst = INSTRUMENTS[patch]
    r = inst.region_for(int(round(midi)))
    shift = (midi - r.center) * r.keytrack / 100 + r.transpose + r.tune / 100
    info = sf.info(r.path)
    ratio = 2 ** (shift / 12) * info.samplerate / sr   # source frames per output frame
    stop = r.end + 1 if r.end else info.frames
    n_out = max(2, min(int((stop - r.offset) / ratio), int(inst.max_seconds * sr)))
    need = min(stop, r.offset + int(np.ceil(n_out * ratio)) + 2)
    x, _ = sf.read(r.path, start=r.offset, stop=need, dtype="float32", always_2d=True)
    x = np.repeat(x, 2, axis=1) if x.shape[1] == 1 else x[:, :2]
    src = np.minimum(np.arange(n_out) * ratio, len(x) - 1)
    idx = np.arange(len(x))
    y = np.stack([np.interp(src, idx, x[:, c]) for c in range(2)], axis=1)
    y *= inst.gain * 10 ** (r.volume / 20) * r.amplitude / 100
    fade = min(len(y), int((0.08 if len(y) >= inst.max_seconds * sr - 1 else 0.005) * sr))
    if fade:
        y[-fade:] *= np.linspace(1, 0, fade)[:, None]  # truncated tails fade instead of clicking
    return np.ascontiguousarray(y, dtype=np.float32)
