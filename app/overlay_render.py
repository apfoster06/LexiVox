"""Draws the recording pill as an antialiased RGBA image (Pillow only, no window code).

The pill is drawn horizontally at 2x and scaled down, so edges, text and the soft
shadow are smooth; the vertical ("right") layout is the same drawing turned a quarter.
While recording it shows a live waveform that scrolls right to left, like a voice
memo; while transcribing or polishing, a travelling wave.
"""

from __future__ import annotations

import math
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SS = 2  # supersampling factor
PAD = 16  # room around the pill for its shadow, at 100% scaling
BG, FG, DIM = (0x12, 0x0A, 0x26), (0xF5, 0xF0, 0xFF), (0xB2, 0xA7, 0xD9)
VOLT = (0xCC, 0xFF, 0x00)
# state -> (dot colour, waveform start colour, waveform end colour, label)
STYLE = {
    "recording": ((0xFF, 0x6B, 0x00), (0xFF, 0x8A, 0x1F), (0xFF, 0x2D, 0xC8), ""),  # ember to fuchsia
    "transcribing": ((0x8B, 0x5C, 0xFF), (0x7A, 0x4D, 0xFF), (0xD9, 0x7B, 0xFF), "Transcribing"),
    "polishing": (VOLT, VOLT, (0x6B, 0xFF, 0xC2), "Polishing"),
}
PITCH, BAR = 5.0, 3.0  # waveform bar spacing and width, at 100%
BAR_SECONDS = 0.075  # time for the waveform to scroll by one bar
FONTS = ("seguisb.ttf", "segoeuib.ttf", "segoeui.ttf")


def _mix(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _font(px: int):
    for name in FONTS:
        try:
            return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), px)
        except OSError:
            continue
    return ImageFont.load_default(px)


def fmt_time(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


class PillRenderer:
    """One pill at a fixed size. `feed()` it microphone levels, then ask for `frame()`s."""

    def __init__(self, position: str, w: int, h: int, scale: float) -> None:
        self.vertical = position == "right"
        self.w, self.h, self.scale = w, h, scale
        self.long, self.short = max(w, h), min(w, h)
        self.pad = round(PAD * scale)
        self.size = (w + 2 * self.pad, h + 2 * self.pad)
        self.u = scale * SS  # pixels per layout unit on the 2x canvas
        self.units = self.long / scale  # pill length in layout units
        self.hist: deque[float] = deque([0.0] * 64, maxlen=64)
        self.env = 0.0  # smoothed loudness, 0..1
        self.frac = 0.0  # how far the waveform has scrolled towards the next bar
        self._font = _font(round(13 * self.u))
        self._bg = self._background()

    def _background(self) -> Image.Image:
        w, h, pad = self.w, self.h, self.pad
        shadow = Image.new("L", self.size, 0)
        off = round(3 * self.scale)
        ImageDraw.Draw(shadow).rounded_rectangle(
            (pad, pad + off, pad + w - 1, pad + h - 1 + off), radius=self.short / 2, fill=120)
        shadow = shadow.filter(ImageFilter.GaussianBlur(4.5 * self.scale))
        bg = Image.new("RGBA", self.size, (0, 0, 0, 0))
        bg.putalpha(shadow)
        big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        ImageDraw.Draw(big).rounded_rectangle(
            (0, 0, w * SS - 1, h * SS - 1), radius=self.short * SS / 2,
            fill=(*BG, 244), outline=(*_mix(BG, FG, 0.22), 255), width=max(1, round(self.u * 0.5)))
        bg.alpha_composite(big.resize((w, h), Image.LANCZOS), (pad, pad))
        return bg

    def reset(self) -> None:
        self.hist.extend([0.0] * self.hist.maxlen)
        self.env = self.frac = 0.0

    def feed(self, level: float, dt: float) -> None:
        """Advance the waveform by `dt` seconds with the current mic peak (0..1)."""
        loud = min(1.0, max(0.0, level) * 3.5) ** 0.6  # quiet speech still moves the bars
        self.env = loud if loud > self.env else self.env + (loud - self.env) * min(1.0, dt * 9)
        self.frac += dt / BAR_SECONDS
        while self.frac >= 1.0:
            self.frac -= 1.0
            self.hist.append(self.env)

    # ---- drawing (layout units; the canvas is horizontal) ---------------------------

    def _bars(self, d: ImageDraw.ImageDraw, x0: float, x1: float, c0: tuple, c1: tuple, value) -> None:
        u, cy = self.u, self.short / self.scale / 2
        i = -1
        while True:
            x = x1 - (i + self.frac) * PITCH
            i += 1
            if x > x1 + PITCH:
                continue
            if x < x0:
                break
            v = value(i - 1, x)
            fade = min(1.0, (x - x0) / 22) * min(1.0, max(0.0, x1 - x) / 7)
            colour = _mix(BG, _mix(c0, c1, (x - x0) / (x1 - x0)), fade * (0.5 + 0.5 * v))
            half = (3 + 17 * v) / 2
            d.rounded_rectangle(
                ((x - BAR / 2) * u, (cy - half) * u, (x + BAR / 2) * u, (cy + half) * u),
                radius=BAR / 2 * u, fill=colour)

    def frame(self, state: str, seconds: float = 0, mode: str = "dictate", t: float = 0) -> Image.Image:
        """The whole window image (pill plus shadow) at time `t` seconds."""
        dot, c0, c1, label = STYLE.get(state, STYLE["recording"])
        u, units = self.u, self.units
        cy = self.short / self.scale / 2
        im = Image.new("RGBA", (round(self.long * SS), round(self.short * SS)), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)

        # Status dot with a soft halo: it breathes, and swells with your voice.
        glow = self.env if state == "recording" else 0.5 + 0.5 * math.sin(t * 3.6)
        for radius, strength in ((7.5 + 4.5 * glow, 0.16), (6 + 2.5 * glow, 0.3), (4.5, 1.0)):
            d.ellipse(((cy - radius) * u, (cy - radius) * u, (cy + radius) * u, (cy + radius) * u),
                      fill=_mix(BG, dot, strength))

        right = units - 15
        if state == "recording":
            clock = fmt_time(seconds)
            d.text((right * u, cy * u), clock, font=self._font, fill=FG, anchor="rm")
            x1 = right - d.textlength(clock, font=self._font) / u - 9
            if mode == "polish":
                d.text((x1 * u, cy * u), "AI", font=self._font, fill=VOLT, anchor="rm")
                x1 -= d.textlength("AI", font=self._font) / u + 8
            hist = self.hist
            self._bars(d, 34, x1, c0, c1,
                       lambda i, x: self.env if i < 0 else hist[-1 - i] if i < len(hist) else 0.0)
        else:
            d.text((33 * u, cy * u), label, font=self._font, fill=FG, anchor="lm")
            x0 = 33 + d.textlength(label, font=self._font) / u + 8
            frac, self.frac = self.frac, 0.0  # the busy wave stays put; only its shape moves
            self._bars(d, x0, right + 3, c0, c1,
                       lambda i, x: 0.12 + 0.75 * (0.5 + 0.5 * math.sin(t * 5.5 + x * 0.16)) ** 2)
            self.frac = frac

        im = im.reduce(SS)
        if self.vertical:
            im = im.rotate(-90, expand=True)  # reads top to bottom, like a book spine
        out = self._bg.copy()
        out.alpha_composite(im, (self.pad, self.pad))
        return out
