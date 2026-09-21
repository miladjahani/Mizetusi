#!/usr/bin/env python3
"""Render the NEXUS app icons used by the PWA from the brand mark.

The mark is the one artwork the whole panel is themed around — a bright lime
shield outline inside a deep-green ring on a near-black tile — so it is drawn
here from signed distance fields (clean anti-aliased edges, no image library)
and written out as 8-bit RGBA PNGs by :func:`write_png`. Re-run this after
changing the brand:

    python3 scripts/make_icons.py

Everything it writes lives in ``static/icons`` and is referenced by
``PWA_ICONS`` in ``app/main.py``.
"""
import math
import os
import struct
import zlib

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'icons')

# Brand palette (kept in sync with :root in static/app.css).
ACCENT = (0xc9, 0xf2, 0x4c)       # bright lime — the shield
ACCENT_DEEP = (0xa6, 0xd8, 0x33)  # bottom of the shield gradient
TILE_TOP = (0x11, 0x18, 0x0a)     # near-black green tile
TILE_BOTTOM = (0x06, 0x0a, 0x05)
RING_OUTER = (0x4a, 0x59, 0x28)   # olive ring, outer edge
RING_INNER = (0x2f, 0x3b, 0x19)   # olive ring, inner edge
WELL = (0x0a, 0x0f, 0x07)         # the darker disc inside the ring


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


def shield_outline(steps=9):
    """The shield as a closed polygon in unit space (-1..1 on both axes).

    A pointed top (the peak of the coat-of-arms silhouette), two straight sides
    and a rounded bottom tip: the exact reading of the brand mark, and the shape
    of the ``i-shield`` glyph the panel uses in its own UI.
    """
    right = [(0.0, 0.96), (0.66, 0.70), (0.66, -0.06)]
    # The lower half is an arc so the taper reads as a rounded tip, not a wedge.
    for i in range(1, steps + 1):
        t = i / steps
        right.append((0.66 * math.cos(t * math.pi / 2) ** 0.72,
                      -0.06 - 0.90 * math.sin(t * math.pi / 2) ** 0.85))
    # The left half is the mirror of everything between the apex and the tip.
    return right + [(-x, y) for (x, y) in reversed(right[1:-1])]


def polygon_distance(px, py, polygon):
    """Signed distance to a closed polygon (negative inside)."""
    best = float('inf')
    inside = False
    count = len(polygon)
    for i in range(count):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % count]
        best = min(best, sd_segment(px, py, ax, ay, bx, by))
        if (ay > py) != (by > py):
            crossing = ax + (py - ay) / (by - ay) * (bx - ax)
            if px < crossing:
                inside = not inside
    return -best if inside else best


def render(size, glyph_scale=1.0, margin=0.0, radius_ratio=0.235):
    """Return the RGBA bytes for one square icon."""
    pixels = bytearray(size * size * 4)
    centre = size / 2.0
    half = centre * (1.0 - margin)
    corner = half * radius_ratio * (1.0 - margin)
    ring_outer = size * 0.415 * glyph_scale
    ring_width = size * 0.070 * glyph_scale
    ring_inner = ring_outer - ring_width
    stroke = size * 0.068 * glyph_scale
    shield_scale = size * 0.238 * glyph_scale
    polygon = shield_outline()
    glow_radius = size * 0.34

    for y in range(size):
        py = y + 0.5
        for x in range(size):
            px = x + 0.5
            tile = coverage(sd_round_rect(px - centre, py - centre, half, corner))
            if tile <= 0.0:
                continue
            dx, dy = px - centre, py - centre
            radial = math.hypot(dx, dy)

            colour = mix(TILE_TOP, TILE_BOTTOM, py / size)
            # A green bloom in the corner keeps the tile from reading as flat black.
            bloom = math.hypot(px - size * 0.24, py - size * 0.20) / (size * 0.85)
            colour = mix(colour, ACCENT, 0.10 * max(0.0, 1.0 - bloom) ** 2)

            # The ring, then the darker well it encloses.
            if radial < ring_outer:
                edge = radial - ring_inner
                if edge > 0:
                    ring = mix(RING_INNER, RING_OUTER, min(1.0, edge / ring_width))
                    colour = ring
                else:
                    colour = WELL
                    # Lime glow around the shield, only inside the well.
                    glow = math.hypot(dx, dy) / glow_radius
                    colour = mix(colour, ACCENT, 0.16 * max(0.0, 1.0 - glow) ** 2)

            if radial < ring_outer + ring_width:
                # Hairline specular on the ring's outer lip.
                lip = abs(radial - ring_outer)
                if lip < size * 0.006:
                    colour = mix(colour, ACCENT, 0.30 * (1 - lip / (size * 0.006)))

            # The shield itself: an outline of even width, brightest at the top.
            sx, sy = dx / shield_scale, dy / shield_scale
            if abs(sx) < 1.2 and -1.2 < sy < 1.2:
                # A stroke is centred on the boundary: solid within half the
                # width, feathered over the last pixel on both sides.
                signed = polygon_distance(sx, sy, polygon) * shield_scale
                glyph = min(1.0, max(0.0, stroke / 2 + 0.5 - abs(signed)))
                if glyph > 0:
                    tint = (1 - (sy + 1) / 2) * 0.85
                    colour = mix(colour, mix(ACCENT, ACCENT_DEEP, tint), glyph)

            offset = (y * size + x) * 4
            pixels[offset] = int(colour[0])
            pixels[offset + 1] = int(colour[1])
            pixels[offset + 2] = int(colour[2])
            pixels[offset + 3] = int(round(tile * 255))
    return pixels


def svg_mark():
    """The vector logo, generated from the same geometry as the PNGs."""
    polygon = shield_outline()
    path = 'M' + ' L'.join(
        f'{256 + x * 128:.1f} {256 - y * 128:.1f}' for (x, y) in polygon) + ' Z'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="NEXUS">
  <defs>
    <linearGradient id="tile" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#11180a"/><stop offset="1" stop-color="#060a05"/>
    </linearGradient>
    <linearGradient id="ring" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#4a5928"/><stop offset="1" stop-color="#2f3b19"/>
    </linearGradient>
    <linearGradient id="lime" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#d9f96b"/><stop offset="1" stop-color="#a6d833"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="#c9f24c" stop-opacity="0.20"/>
      <stop offset="1" stop-color="#c9f24c" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="bloom" cx="0.24" cy="0.20" r="0.85">
      <stop offset="0" stop-color="#c9f24c" stop-opacity="0.16"/>
      <stop offset="1" stop-color="#c9f24c" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="512" height="512" rx="120" fill="url(#tile)"/>
  <rect width="512" height="512" rx="120" fill="url(#bloom)"/>
  <circle cx="256" cy="256" r="212" fill="none" stroke="url(#ring)" stroke-width="36"/>
  <circle cx="256" cy="256" r="194" fill="#0a0f07"/>
  <circle cx="256" cy="256" r="194" fill="url(#glow)"/>
  <path d="{path}" fill="none" stroke="url(#lime)" stroke-width="35"
        stroke-linejoin="round" stroke-linecap="round"/>
  <rect x="2.5" y="2.5" width="507" height="507" rx="118" fill="none"
        stroke="#c9f24c" stroke-opacity="0.14" stroke-width="5"/>
</svg>
'''


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    jobs = [
        ('icon-192.png', 192, 1.0, 0.0),
        ('icon-512.png', 512, 1.0, 0.0),
        # Maskable icons must keep the mark inside the 80% safe zone.
        ('icon-maskable-512.png', 512, 0.80, 0.0),
        ('apple-touch-icon.png', 180, 1.0, 0.0),
        ('favicon-32.png', 32, 1.05, 0.0),
    ]
    for name, size, scale, margin in jobs:
        path = os.path.join(OUT_DIR, name)
        written = write_png(path, size, render(size, scale, margin))
        print(f'{name:26} {size}x{size}  {written / 1024:.1f} KiB')
    with open(os.path.join(OUT_DIR, 'nexus.svg'), 'w', encoding='utf-8') as handle:
        handle.write(svg_mark())
    print('nexus.svg                  vector logo')


if __name__ == '__main__':
    main()
