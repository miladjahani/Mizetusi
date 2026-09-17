#!/usr/bin/env python3
"""Render the NEXUS app icons used by the PWA.

No third-party dependency: the artwork is drawn with signed distance fields
(which gives clean anti-aliased edges for free) and written out as 8-bit RGBA
PNGs by :func:`write_png`. Re-run this after changing the brand:

    python3 scripts/make_icons.py
"""
import math
import os
import struct
import zlib

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'icons')

# Brand palette (matches --accent / --accent-2 in static/app.css).
ACCENT = (0x5a, 0xd1, 0xff)
ACCENT_2 = (0x8b, 0x7b, 0xff)
BG_TOP = (0x0d, 0x15, 0x27)
BG_BOTTOM = (0x04, 0x07, 0x0d)


def write_png(path, size, pixels):
    """pixels: bytearray of size*size*4 (RGBA rows, top to bottom)."""
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)  # filter type 0
        raw += pixels[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    header = struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)
    blob = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(bytes(raw), 9)) + chunk(b'IEND', b''))
    with open(path, 'wb') as handle:
        handle.write(blob)
    return len(blob)


def sd_round_rect(px, py, half, radius):
    qx = abs(px) - (half - radius)
    qy = abs(py) - (half - radius)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    inside = min(max(qx, qy), 0.0)
    return outside + inside - radius


def sd_segment(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    length = vx * vx + vy * vy
    t = 0.0 if length == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / length))
    return math.hypot(wx - t * vx, wy - t * vy)


def coverage(distance):
    """1px analytic feather: solid inside, empty outside."""
    return min(1.0, max(0.0, 0.5 - distance))


def mix(a, b, t):
    t = min(1.0, max(0.0, t))
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def render(size, glyph_scale=1.0, margin=0.0, radius_ratio=0.235):
    """Return the RGBA bytes for one square icon."""
    pixels = bytearray(size * size * 4)
    half = size / 2.0 * (1.0 - margin)
    corner = half * radius_ratio * (1.0 - margin)
    stroke = size * 0.086 * glyph_scale
    diagonal_stroke = size * 0.076 * glyph_scale
    centre = size / 2.0

    # Glyph geometry: an "N" built from two verticals and one diagonal.
    gx = size * 0.205 * glyph_scale
    gy = size * 0.235 * glyph_scale
    left = (centre - gx, centre + gy, centre - gx, centre - gy)
    right = (centre + gx, centre + gy, centre + gx, centre - gy)
    diagonal = (centre - gx, centre - gy, centre + gx, centre + gy)

    for y in range(size):
        py = y + 0.5
        for x in range(size):
            px = x + 0.5
            bg = coverage(sd_round_rect(px - centre, py - centre, half, corner))
            if bg <= 0.0:
                continue
            vertical = mix(BG_TOP, BG_BOTTOM, py / size)
            # Soft cyan glow behind the mark, so the icon does not read as flat.
            glow = math.hypot(px - size * 0.32, py - size * 0.24) / (size * 0.78)
            vertical = mix(vertical, ACCENT, 0.24 * max(0.0, 1.0 - glow) ** 2)

            d_left = sd_segment(px, py, *left) - stroke / 2
            d_right = sd_segment(px, py, *right) - stroke / 2
            d_diag = sd_segment(px, py, *diagonal) - diagonal_stroke / 2
            glyph = coverage(min(d_left, d_right, d_diag))
            if glyph > 0:
                tint = (px / size) * 0.42 + (1 - py / size) * 0.58
                vertical = mix(vertical, mix(ACCENT, ACCENT_2, tint), glyph)

            # Hairline inner border that reads as an icon frame.
            edge = abs(sd_round_rect(px - centre, py - centre, half, corner))
            if edge < size * 0.008:
                vertical = mix(vertical, (0xff, 0xff, 0xff), 0.10 * (1 - edge / (size * 0.008)))

            offset = (y * size + x) * 4
            pixels[offset] = int(vertical[0])
            pixels[offset + 1] = int(vertical[1])
            pixels[offset + 2] = int(vertical[2])
            pixels[offset + 3] = int(round(bg * 255))
    return pixels


SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="NEXUS">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0d1527"/><stop offset="1" stop-color="#04070d"/>
    </linearGradient>
    <linearGradient id="n" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#5ad1ff"/><stop offset="1" stop-color="#8b7bff"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.32" cy="0.24" r="0.78">
      <stop offset="0" stop-color="#5ad1ff" stop-opacity="0.34"/>
      <stop offset="1" stop-color="#5ad1ff" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="512" height="512" rx="120" fill="url(#bg)"/>
  <rect width="512" height="512" rx="120" fill="url(#glow)"/>
  <g stroke="url(#n)" stroke-width="44" stroke-linecap="round" fill="none">
    <path d="M157 391V121"/><path d="M355 391V121"/><path d="M157 121l198 270" stroke-width="39"/>
  </g>
  <rect x="2.5" y="2.5" width="507" height="507" rx="118" fill="none" stroke="#ffffff" stroke-opacity="0.10" stroke-width="5"/>
</svg>
'''


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    jobs = [
        ('icon-192.png', 192, 1.0, 0.0),
        ('icon-512.png', 512, 1.0, 0.0),
        # Maskable icons must keep the mark inside the 80% safe zone.
        ('icon-maskable-512.png', 512, 0.78, 0.0),
        ('apple-touch-icon.png', 180, 1.0, 0.0),
        ('favicon-32.png', 32, 1.06, 0.0),
    ]
    for name, size, scale, margin in jobs:
        path = os.path.join(OUT_DIR, name)
        written = write_png(path, size, render(size, scale, margin))
        print(f'{name:26} {size}x{size}  {written / 1024:.1f} KiB')
    with open(os.path.join(OUT_DIR, 'nexus.svg'), 'w', encoding='utf-8') as handle:
        handle.write(SVG)
    print('nexus.svg                  vector logo')


if __name__ == '__main__':
    main()
