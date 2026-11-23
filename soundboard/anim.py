"""Key-press LED animations for instrument pages (piano).

An animation is a *shape* (which pads around the pressed key take part) combined with a
*motion* (how it plays out over time). Each press starts a Burst; every frame the app asks
`frame()` for the pads to overlay on top of the normal instrument colours.

Shapes ("style"):
    off       no animation (default)
    burst     every pad within `radius` (round)
    cross     horizontal + vertical lines through the key
    diagonal  the two diagonals through the key (an X)
    star      cross + diagonals (8 directions)
    square    every pad within `radius` (square)
    row       the key's row only
    column    the key's column only
    sparkle   random pads within `radius`, twinkling

Motions:
    wave      spreads outward from the key; pads fade behind the front
    ring      only the moving front is lit (a ripple)
    flash     the whole shape lights at once and fades out
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

STYLES = {
    "off": "Off",
    "burst": "Burst (radius)",
    "cross": "Cross (+)",
    "diagonal": "Diagonals (×)",
    "star": "Star (+ and ×)",
    "square": "Square",
    "row": "Row",
    "column": "Column",
    "sparkle": "Sparkle",
}
MOTIONS = {"wave": "Wave", "ring": "Ripple", "flash": "Flash"}
LIMITS = {"radius": (1, 8), "duration": (0.1, 2.0)}

DEFAULT_ANIM = {
    "style": "off",        # see STYLES. Off by default.
    "motion": "wave",      # see MOTIONS
    "radius": 3,           # pads, 1 .. 8
    "duration": 0.4,       # seconds, 0.1 .. 2.0
    "color": [3, 2],       # [red 0-3, green 0-3]
    "hide_keys": False,    # True: note pads stay dark when idle, only presses + animations light up
}


def clean(raw: dict) -> dict:
    """Merge `raw` over the defaults, dropping anything invalid."""
    out = dict(DEFAULT_ANIM)
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("style") in STYLES:
        out["style"] = raw["style"]
    if raw.get("motion") in MOTIONS:
        out["motion"] = raw["motion"]
    for k, (lo, hi) in LIMITS.items():
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = max(lo, min(hi, int(v) if k == "radius" else round(float(v), 3)))
    if isinstance(raw.get("hide_keys"), bool):
        out["hide_keys"] = raw["hide_keys"]
    c = raw.get("color")
    if isinstance(c, (list, tuple)) and len(c) >= 2:
        try:
            out["color"] = [max(0, min(3, int(x))) for x in c[:2]]
        except (TypeError, ValueError):
            pass
    return out


def _shape_distance(style: str, dx: int, dy: int) -> float:
    """Distance of (dx, dy) from the key within `style`, or -1 if the pad is not part of it."""
    ax, ay = abs(dx), abs(dy)
    if style in ("burst", "sparkle"):
        return math.hypot(dx, dy)
    if style == "cross":
        return ax + ay if dx == 0 or dy == 0 else -1
    if style == "diagonal":
        return ax if ax == ay else -1
    if style == "star":
        return max(ax, ay) if dx == 0 or dy == 0 or ax == ay else -1
    if style == "square":
        return max(ax, ay)
    if style == "row":
        return ax if dy == 0 else -1
    if style == "column":
        return ay if dx == 0 else -1
    return -1


def _hash01(*vals: int) -> float:
    """Cheap deterministic pseudo-random number in [0, 1)."""
    h = 2166136261
    for v in vals:
        h = ((h ^ (v & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF
    return h / 2 ** 32


@dataclass
class Burst:
    page: int
    x: int
    y: int
    cfg: dict
    t0: float = field(default_factory=time.monotonic)

    def done(self, now: float) -> bool:
        return now - self.t0 >= self.cfg["duration"]

    def intensity(self, px: int, py: int, now: float) -> float:
        """0 .. 1 brightness of pad (px, py) for this burst at time `now`."""
        dx, dy = px - self.x, py - self.y
        if dx == 0 and dy == 0:
            return 0.0  # the key itself shows its own "pressed" colour
        cfg = self.cfg
        style, radius = cfg["style"], cfg["radius"]
        d = _shape_distance(style, dx, dy)
        if d < 0 or d > radius + 0.5:
            return 0.0
        p = min(1.0, (now - self.t0) / cfg["duration"])
        if style == "sparkle":
            seed = int(self.t0 * 1000)
            if _hash01(seed, px, py, int(p * 8)) > 0.4:  # re-rolls 8 times per burst: twinkle
                return 0.0
        motion = cfg["motion"]
        if motion == "flash":
            return 1.0 - p
        front = p * (radius + 1)
        if motion == "ring":
            return max(0.0, 1.0 - abs(d - front) / 0.9)
        # wave: lit once the front has passed, fading behind it
        if d > front:
            return 0.0
        tail = max(1.0, radius * 0.6)
        return max(0.0, 1.0 - (front - d) / tail)


def frame(bursts: Iterable[Burst], page: int, now: float) -> Dict[Tuple[int, int], Tuple[int, int]]:
    """{(x, y): (red, green)} of grid pads lit by the bursts of `page` at time `now`."""
    best: Dict[Tuple[int, int], Tuple[float, List[int]]] = {}
    for b in bursts:
        if b.page != page:
            continue
        r = b.cfg["radius"]
        for py in range(max(0, b.y - r), min(8, b.y + r + 1)):
            for px in range(max(0, b.x - r), min(8, b.x + r + 1)):
                i = b.intensity(px, py, now)
                if i > 0 and i > best.get((px, py), (0.0, None))[0]:
                    best[(px, py)] = (i, b.cfg["color"])
    out = {}
    for pos, (i, (red, green)) in best.items():
        # The Launchpad only has 4 levels per colour: scale and round, keep the hue while visible.
        rr, gg = int(red * i + 0.5), int(green * i + 0.5)
        if rr or gg:
            out[pos] = (rr, gg)
    return out
