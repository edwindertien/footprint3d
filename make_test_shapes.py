"""
make_test_shapes.py
-------------------
Generates synthetic SVG footprint shapes for testing the pipeline.
Each shape is a clean, filled SVG polygon/path ready for the heightmap generator.
"""

import math
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "test_shapes")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def svg_wrap(width, height, paths_str, viewbox=None):
    vb = viewbox or f"0 0 {width} {height}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="{vb}">
  <g fill="black" stroke="none">
{paths_str}
  </g>
</svg>"""


def circle_path(cx, cy, r, n=64):
    """Approximate a circle as a closed SVG path."""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in pts) + " Z"
    return f'    <path d="{d}"/>'


def ellipse_path(cx, cy, rx, ry, angle_deg=0, n=80):
    """Rotated ellipse as a closed SVG path."""
    a = math.radians(angle_deg)
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x0 = rx * math.cos(t)
        y0 = ry * math.sin(t)
        x = cx + x0 * math.cos(a) - y0 * math.sin(a)
        y = cy + x0 * math.sin(a) + y0 * math.cos(a)
        pts.append((x, y))
    d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in pts) + " Z"
    return f'    <path d="{d}"/>'


def rounded_rect_path(x, y, w, h, r, n=16):
    """Rounded rectangle via arc approximation."""
    # Use SVG arc commands for proper rounded corners
    d = (
        f"M {x+r},{y} "
        f"L {x+w-r},{y} "
        f"A {r},{r} 0 0,1 {x+w},{y+r} "
        f"L {x+w},{y+h-r} "
        f"A {r},{r} 0 0,1 {x+w-r},{y+h} "
        f"L {x+r},{y+h} "
        f"A {r},{r} 0 0,1 {x},{y+h-r} "
        f"L {x},{y+r} "
        f"A {r},{r} 0 0,1 {x+r},{y} Z"
    )
    return f'    <path d="{d}"/>'


# ─────────────────────────────────────────────────────────────
# Shape 1 – Simple test: circle body + 3 circular toes
# ─────────────────────────────────────────────────────────────
def make_circle_three_toes(filename="shape_circle_3toes.svg"):
    W, H = 300, 380
    cx, cy = 150, 220  # body centre

    paths = []
    # Main pad (circle)
    paths.append(circle_path(cx, cy, 90))

    # Three toes arranged in a 140° arc above the body
    toe_r = 28
    toe_dist = 115          # distance from body centre to toe centre
    toe_angles_deg = [-55, 0, 55]   # relative to straight up (−90°)
    for ang in toe_angles_deg:
        rad = math.radians(-90 + ang)
        tx = cx + toe_dist * math.cos(rad)
        ty = cy + toe_dist * math.sin(rad)
        paths.append(circle_path(tx, ty, toe_r))

    svg = svg_wrap(W, H, "\n".join(paths))
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w") as f:
        f.write(svg)
    print(f"  Wrote: {path}")
    return path


# ─────────────────────────────────────────────────────────────
# Shape 2 – More realistic: elongated ellipse body + 5 oval toes
# ─────────────────────────────────────────────────────────────
def make_ellipse_five_toes(filename="shape_ellipse_5toes.svg"):
    W, H = 320, 500
    cx, cy = 160, 290

    paths = []
    # Main pad – tall ellipse, slightly tilted
    paths.append(ellipse_path(cx, cy, 75, 120, angle_deg=5))

    # Heel bump
    paths.append(ellipse_path(cx, cy + 85, 50, 35))

    # Five toes, fan arrangement
    toe_configs = [
        # (offset_x, offset_y, rx, ry, angle)
        (-95, -115, 18, 24, -20),
        (-52, -140, 20, 26,  -8),
        (  0, -148, 22, 28,   0),
        ( 52, -140, 20, 26,   8),
        ( 90, -118, 17, 23,  18),
    ]
    for dx, dy, rx, ry, ang in toe_configs:
        paths.append(ellipse_path(cx + dx, cy + dy, rx, ry, angle_deg=ang))

    svg = svg_wrap(W, H, "\n".join(paths))
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w") as f:
        f.write(svg)
    print(f"  Wrote: {path}")
    return path


# ─────────────────────────────────────────────────────────────
# Shape 3 – Minimal: single rounded rectangle (e.g. a heel block)
#            Good for validating height-map symmetry
# ─────────────────────────────────────────────────────────────
def make_heel_block(filename="shape_heel_block.svg"):
    W, H = 240, 200
    paths = [rounded_rect_path(40, 30, 160, 140, r=50)]
    svg = svg_wrap(W, H, "\n".join(paths))
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w") as f:
        f.write(svg)
    print(f"  Wrote: {path}")
    return path


# ─────────────────────────────────────────────────────────────
# Shape 4 – Full stylised paw (4 toes + large central pad)
# ─────────────────────────────────────────────────────────────
def make_paw_four_toes(filename="shape_paw_4toes.svg"):
    W, H = 300, 340
    cx, cy = 150, 210

    paths = []
    # Central pad – big rounded oval
    paths.append(ellipse_path(cx, cy, 80, 90))

    # Four toes, arc above
    toe_r = 30
    toe_dist = 110
    for ang in [-55, -18, 18, 55]:
        rad = math.radians(-90 + ang)
        tx = cx + toe_dist * math.cos(rad)
        ty = cy + toe_dist * math.sin(rad)
        paths.append(ellipse_path(tx, ty, toe_r, 34, angle_deg=ang * 0.4))

    svg = svg_wrap(W, H, "\n".join(paths))
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w") as f:
        f.write(svg)
    print(f"  Wrote: {path}")
    return path


if __name__ == "__main__":
    print("Generating test SVG shapes …")
    make_circle_three_toes()
    make_ellipse_five_toes()
    make_heel_block()
    make_paw_four_toes()
    print("Done. SVGs written to:", OUTPUT_DIR)
