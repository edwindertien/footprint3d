"""
footprint3d.py
==============
Pipeline: SVG contour → distance-field height map → OpenSCAD .scad + .dat → STL

Usage
-----
  python footprint3d.py input.svg --depth 8 --base 3 --width 80
  python footprint3d.py input.svg --depth 8 --base 3 --width 80 --stamp

OpenSCAD model structure (stamp mode)
--------------------------------------
  union() {
    // 1. Embossed surface clipped to paw outline
    intersection() {
      scale([px_x, px_y, 1])
        surface(file="..._heights.dat", center=true);
      linear_extrude(height=total_height)
        import("..._outline.svg");   // paw silhouette polygon
    }
    // 2. Solid paw-shaped base plate
    linear_extrude(height=base_thickness)
      import("..._outline.svg");
  }

The outline SVG is the dilated paw silhouette in mm units, centered at origin,
matching the surface() coordinate system exactly.

Dependencies
------------
  pip install svgpathtools svgwrite
  pip install numpy scipy scikit-image Pillow
"""

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
import numpy as np
from scipy.ndimage import distance_transform_edt, gaussian_filter, label, binary_fill_holes
from skimage import measure
from skimage.measure import approximate_polygon
import warnings
warnings.filterwarnings("ignore")


# ──────────────────────────────────────────────────────────────
# 1.  SVG → binary mask
# ──────────────────────────────────────────────────────────────

def get_svg_dimensions(svg_path):
    tree = ET.parse(svg_path)
    root = tree.getroot()
    w = root.get("width", "")
    h = root.get("height", "")
    try:
        return (float(re.sub(r'[^\d.]', '', w)),
                float(re.sub(r'[^\d.]', '', h)))
    except ValueError:
        pass
    vb = root.get("viewBox", "")
    if vb:
        parts = vb.split()
        return float(parts[2]), float(parts[3])
    return 300.0, 300.0


def parse_group_transform(t):
    dx, dy, sx, sy = 0.0, 0.0, 1.0, 1.0
    if not t:
        return dx, dy, sx, sy
    m = re.search(r'translate\(\s*([+-]?\d*\.?\d+)\s*,?\s*([+-]?\d*\.?\d+)?\s*\)', t)
    if m:
        dx = float(m.group(1))
        dy = float(m.group(2)) if m.group(2) else 0.0
    m = re.search(r'scale\(\s*([+-]?\d*\.?\d+)\s*,?\s*([+-]?\d*\.?\d+)?\s*\)', t)
    if m:
        sx = float(m.group(1))
        sy = float(m.group(2)) if m.group(2) else sx
    return dx, dy, sx, sy


def collect_paths_with_transforms(svg_path):
    tree = ET.parse(svg_path)
    root = tree.getroot()
    results = []

    def walk(elem, dx, dy, sx, sy):
        tag = elem.tag.split('}')[-1]
        t = elem.get('transform', '')
        if t:
            tdx, tdy, tsx, tsy = parse_group_transform(t)
            dx += tdx * sx;  dy += tdy * sy
            sx *= tsx;       sy *= tsy
        if tag == 'path':
            d = elem.get('d', '').strip()
            if d:
                results.append((d, dx, dy, sx, sy))
        for child in elem:
            walk(child, dx, dy, sx, sy)

    walk(root, 0.0, 0.0, 1.0, 1.0)
    return results


def split_path_into_subpaths(path):
    segs = list(path)
    if not segs:
        return []
    breaks = [0]
    for i in range(1, len(segs)):
        if abs(segs[i].start - segs[i-1].end) > 1.0:
            breaks.append(i)
    breaks.append(len(segs))
    return [segs[breaks[k]:breaks[k+1]] for k in range(len(breaks)-1)]


def svg_to_mask(svg_path, resolution=512, padding=60):
    """
    Rasterise SVG to binary mask.
    Returns (mask float32 H×W, aspect W/H of padded canvas).
    """
    try:
        from svgpathtools import parse_path as _pp
    except ImportError:
        sys.exit("pip install svgpathtools svgwrite")
    from PIL import Image, ImageDraw

    svgW, svgH = get_svg_dimensions(svg_path)
    if svgW >= svgH:
        cw, ch = resolution, max(1, int(resolution * svgH / svgW))
    else:
        ch, cw = resolution, max(1, int(resolution * svgW / svgH))

    out_w, out_h = cw + 2*padding, ch + 2*padding
    aspect = out_w / out_h
    sx_s, sy_s = cw / svgW, ch / svgH

    img  = Image.new("L", (out_w, out_h), 255)
    draw = ImageDraw.Draw(img)

    records = collect_paths_with_transforms(svg_path)
    n_sub, n_pts = 0, 0

    for d, tdx, tdy, tsx, tsy in records:
        try:
            path = _pp(d)
        except Exception as e:
            print(f"  [warn] path parse: {e}"); continue

        for sub in split_path_into_subpaths(path):
            if len(sub) < 2:
                continue
            n = max(64, len(sub) * 8)
            pts = []
            for i in range(n+1):
                tg = i/n * len(sub)
                si = min(int(tg), len(sub)-1)
                tl = min(1.0, tg - si)
                try:
                    pt = sub[si].point(tl)
                    pts.append(((pt.real*tsx + tdx)*sx_s + padding,
                                (pt.imag*tsy + tdy)*sy_s + padding))
                except Exception:
                    continue
            if len(pts) >= 3:
                draw.polygon(pts, fill=0)
                n_sub += 1;  n_pts += len(pts)

    print(f"     (svgpathtools: {len(records)} path(s), {n_sub} subpaths, {n_pts} pts)")
    print(f"     canvas {out_w}×{out_h} px  (content {cw}×{ch} + {padding}px padding)")

    arr  = np.array(img, dtype=np.float32) / 255.0
    return 1.0 - arr, aspect


# ──────────────────────────────────────────────────────────────
# 2.  Mask → height map
# ──────────────────────────────────────────────────────────────

def mask_to_heightmap(
    mask,
    blend_outside   = 0.5,
    smooth_sigma    = 1.5,
    pad_power       = 2.0,
    nail_power      = 0.4,
    size_threshold  = 0.25,
    stamp_mode      = False,
    stamp_level     = 0.15,
):
    """
    Build height map.  Returns (heightmap, blend_px) so the caller
    can use blend_px for the outline silhouette.
    """
    binary = (mask >= 0.5).astype(np.float32)
    if binary.sum() == 0:
        raise ValueError("No shape pixels found.")

    dist_in  = distance_transform_edt(binary)
    dist_out = distance_transform_edt(1.0 - binary)
    max_in   = dist_in.max() or 1.0
    blend_px = max(1.0, blend_outside * max_in)

    labeled, n_regions = label(binary)
    region_max_dist    = np.zeros(n_regions + 1)
    for i in range(1, n_regions + 1):
        region_max_dist[i] = dist_in[labeled == i].max()

    global_max     = region_max_dist[1:].max()
    threshold_dist = size_threshold * global_max

    print(f"     Regions: {n_regions}  "
          f"(nail r<{threshold_dist:.0f}px, largest r={global_max:.0f}px)")

    h_in = np.zeros_like(dist_in)

    for i in range(1, n_regions + 1):
        reg     = (labeled == i)
        r_max   = region_max_dist[i]
        is_nail = r_max < threshold_dist
        nd      = np.clip(dist_in / r_max, 0.0, 1.0)

        if is_nail:
            h_profile = nd ** nail_power
            kind, detail = "nail", f"p={nail_power}"
        else:
            h_profile = 1.0 - (1.0 - nd) ** pad_power
            kind = "pad "
            flat_pct = (h_profile[reg] > 0.95).sum() / reg.sum() * 100
            detail = f"p={pad_power}  flat-top={flat_pct:.1f}%"

        h_in = np.where(reg, 0.5 + 0.5 * h_profile, h_in)
        print(f"       region {i}: r={r_max:.0f}px  {kind}  {detail}")

    floor_h = stamp_level if stamp_mode else 0.0
    fade    = np.clip(dist_out / blend_px, 0.0, 1.0)
    h_out   = floor_h + (0.5 - floor_h) * (1.0 - 0.5*(1.0 - np.cos(np.pi * fade)))

    heightmap = np.where(binary > 0.5, h_in, h_out)

    if smooth_sigma > 0:
        heightmap = gaussian_filter(heightmap, sigma=smooth_sigma)

    return heightmap.astype(np.float32), blend_px


# ──────────────────────────────────────────────────────────────
# 3.  Extract paw outline polygon → outline SVG in mm
# ──────────────────────────────────────────────────────────────

def extract_paw_outline(mask, blend_px, aspect,
                         physical_width_mm, simplify_tolerance=1.0):
    """
    Trace the paw silhouette and convert to mm coordinates that match
    surface(center=true) exactly — no SVG import, no Y-flip ambiguity.

    Coordinate mapping:
      surface(center=true) with N rows × M cols and pixel sizes px_y × px_x:
        col 0   → x = -W_mm/2,   col M → x = +W_mm/2
        row 0   → y = -H_mm/2,   row N → y = +H_mm/2
      So: x_mm = col * px_x - W_mm/2
          y_mm = row * px_y - H_mm/2

    Returns (pts_mm, physical_width_mm, physical_height_mm).
    """
    binary   = (mask >= 0.5)
    dist_out = distance_transform_edt(~binary)

    silhouette = binary_fill_holes(dist_out <= blend_px)
    smoothed   = gaussian_filter(silhouette.astype(float), sigma=2.0)
    contours   = measure.find_contours(smoothed, 0.5)

    if not contours:
        raise RuntimeError("Could not extract paw outline contour.")

    c        = max(contours, key=len)
    c_simple = approximate_polygon(c, tolerance=simplify_tolerance)
    print(f"     Outline: {len(c)} → {len(c_simple)} points "
          f"(tolerance={simplify_tolerance}px)")

    H_px, W_px = mask.shape
    physical_height_mm = physical_width_mm / aspect
    px_x = physical_width_mm  / W_px
    px_y = physical_height_mm / H_px

    pts_mm = [
        (col * px_x - physical_width_mm  / 2,
         row * px_y - physical_height_mm / 2)
        for row, col in c_simple
    ]
    return pts_mm, physical_width_mm, physical_height_mm


def write_outline_scad_polygon(pts_mm, output_path):
    """
    Write the paw outline as a standalone OpenSCAD file containing a 2D polygon().
    This is included via 'use<>' or the points are embedded directly in the main .scad.
    Kept here for reference / manual use.
    """
    pts_str = ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in pts_mm)
    scad = f"polygon(points=[{pts_str}]);\n"
    with open(output_path, "w") as f:
        f.write(scad)
    print(f"  Outline polygon: {output_path}")


# ──────────────────────────────────────────────────────────────
# 4.  Export: heightmap SVG (visualisation)
# ──────────────────────────────────────────────────────────────

def export_heightmap_svg(heightmap, output_path, n_levels=12):
    H, W   = heightmap.shape
    levels = np.linspace(0.0, 1.0, n_levels + 1)

    def band_color(t):
        r = int(np.clip(255*(2*t-1),        0, 255))
        g = int(np.clip(255*(1-abs(2*t-1)), 0, 255))
        b = int(np.clip(255*(1-2*t),        0, 255))
        return f"rgb({r},{g},{b})"

    polys = []
    for i in range(len(levels)-1):
        lo, hi = levels[i], levels[i+1]
        color  = band_color((lo+hi)/2)
        band   = ((heightmap>=lo)&(heightmap<hi)).astype(np.float32)
        for c in measure.find_contours(band, 0.5):
            pts = " ".join(f"{y:.2f},{x:.2f}" for x,y in c)
            polys.append(
                f'  <polygon points="{pts}" fill="{color}" stroke="none" opacity="0.9"/>')

    with open(output_path, "w") as f:
        f.write(
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n'
            f'  <rect width="{W}" height="{H}" fill="#111"/>\n'
            + "\n".join(polys)
            + "\n  <!-- blue=low  green=mid  red=peak -->\n</svg>")
    print(f"  Heightmap SVG  : {output_path}")


# ──────────────────────────────────────────────────────────────
# 5.  Export: PNG preview
# ──────────────────────────────────────────────────────────────

def export_png_preview(heightmap, output_path):
    try:
        from PIL import Image
        Image.fromarray((np.clip(heightmap,0,1)*255).astype(np.uint8),"L").save(output_path)
        print(f"  Heightmap PNG  : {output_path}")
    except Exception as e:
        print(f"  [skip] PNG: {e}")


# ──────────────────────────────────────────────────────────────
# 6.  Export: OpenSCAD .scad + _heights.dat
# ──────────────────────────────────────────────────────────────

def export_openscad(heightmap, output_path, svg_aspect,
                    emboss_depth_mm, base_thickness_mm,
                    physical_width_mm, downsample,
                    pts_mm, stamp_mode, stamp_level):
    """
    Write .scad + _heights.dat.

    The paw outline is embedded directly as a polygon() inside the .scad —
    no SVG import, no coordinate system ambiguity.
    Points in pts_mm are already in surface(center=true) space:
      x = -W/2 .. +W/2,  y = -H/2 .. +H/2
    """
    hm         = heightmap[::downsample, ::downsample]
    rows, cols = hm.shape
    phys_h     = physical_width_mm / svg_aspect
    px_x       = physical_width_mm / cols
    px_y       = phys_h / rows

    z_map    = base_thickness_mm + hm * emboss_depth_mm
    dat_path = output_path.replace(".scad", "_heights.dat")
    dat_base = os.path.basename(dat_path)

    with open(dat_path, "w") as f:
        for row in z_map:
            f.write(" ".join(f"{v:.4f}" for v in row) + "\n")

    total_h  = base_thickness_mm + emboss_depth_mm + 1.0
    pts_str  = ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in pts_mm)
    poly_mod = f"polygon(points=[{pts_str}]);"

    scad = f"""\
// ── Footprint 3-D model {"(STAMP)" if stamp_mode else ""} ──────────────────────────────
// Physical size : {physical_width_mm:.1f} mm W  x  {phys_h:.1f} mm H
// Emboss depth  : {emboss_depth_mm} mm     Base: {base_thickness_mm} mm
// Grid          : {cols} x {rows} px  ({px_x:.3f} x {px_y:.3f} mm/px)
//
// Only one external file needed (same folder):
//   {dat_base}
//
// Render:  openscad -o model.stl {os.path.basename(output_path)}
// ─────────────────────────────────────────────────────────────

// Paw silhouette polygon (centered at origin, mm units)
module paw_outline() {{
  {poly_mod}
}}

union() {{

  // ── Embossed surface, clipped to paw silhouette ──────────
  intersection() {{
    scale([{px_x:.6f}, {px_y:.6f}, 1])
      surface(file = "{dat_base}", center = true, convexity = 10);
    linear_extrude(height = {total_h:.3f}, center = false)
      paw_outline();
  }}

  // ── Solid paw-shaped base plate ({base_thickness_mm} mm) ─
  linear_extrude(height = {base_thickness_mm:.3f}, center = false)
    paw_outline();

}}
"""
    with open(output_path, "w") as f:
        f.write(scad)

    print(f"  OpenSCAD script: {output_path}")
    print(f"  Height grid    : {dat_path}")
    print(f"  Physical size  : {physical_width_mm:.1f} x {phys_h:.1f} mm")
    print(f"  Grid           : {cols} x {rows} px  ({px_x:.3f} x {px_y:.3f} mm/px)")
    print(f"  Polygon points : {len(pts_mm)} (embedded in .scad, no separate outline file)")


# ──────────────────────────────────────────────────────────────
# 7.  Main CLI
# ──────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="SVG footprint → contour-following height map → paw-shaped OpenSCAD STL",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("svg")
    p.add_argument("--depth",          type=float, default=8.0,
                   help="Emboss height mm (above base)")
    p.add_argument("--base",           type=float, default=3.0,
                   help="Base plate thickness mm")
    p.add_argument("--width",          type=float, default=80.0,
                   help="Physical width mm")
    p.add_argument("--padding",        type=int,   default=60,
                   help="Blank border px around SVG content")
    p.add_argument("--res",            type=int,   default=512,
                   help="Rasterisation resolution px (longest edge)")
    p.add_argument("--smooth",         type=float, default=1.5,
                   help="Seam-smoothing sigma px")
    p.add_argument("--blend",          type=float, default=0.5,
                   help="Outward bleed + paw outline size (fraction of max inner radius)")
    p.add_argument("--pad-power",      type=float, default=2.0,
                   help="Pillbox exponent for pads: 1=ramp, 2=steep+flat, 3=walls. "
                        "Profile: 1-(1-t)^p")
    p.add_argument("--nail-power",     type=float, default=0.4,
                   help="Peak exponent for nails. Profile: t^p")
    p.add_argument("--size-threshold", type=float, default=0.25,
                   help="Shapes with radius < this × largest → nail profile")
    p.add_argument("--stamp",          action="store_true",
                   help="Stamp mode: platform at stamp-level between shapes")
    p.add_argument("--stamp-level",    type=float, default=0.15,
                   help="Stamp platform height as fraction of emboss depth")
    p.add_argument("--simplify",       type=float, default=1.0,
                   help="Outline polygon simplification tolerance in px")
    p.add_argument("--levels",         type=int,   default=12,
                   help="Iso-contour bands in heightmap SVG")
    p.add_argument("--downsample",     type=int,   default=4,
                   help="Grid decimation for .dat file")
    p.add_argument("--out",            default=None)
    args = p.parse_args()

    if not os.path.isfile(args.svg):
        sys.exit(f"Error: file not found: {args.svg}")

    base_name = os.path.splitext(os.path.basename(args.svg))[0]
    if args.stamp:
        base_name += "_stamp"
    out_dir = args.out or os.path.dirname(os.path.abspath(args.svg))
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n── footprint3d ───────────────────────────────────")
    print(f"  Input   : {args.svg}")
    print(f"  Emboss  : {args.depth} mm    Base: {args.base} mm")
    print(f"  Width   : {args.width} mm   Blend: {args.blend}")
    print(f"  Pads    : 1-(1-t)^{args.pad_power}   Nails: t^{args.nail_power}")
    if args.stamp:
        print(f"  Mode    : STAMP  platform={args.stamp_level:.0%}")
    print()

    print("1/4  Rasterising SVG …")
    mask, aspect = svg_to_mask(args.svg, resolution=args.res, padding=args.padding)
    H, W = mask.shape
    print(f"     {W}×{H} px  aspect={aspect:.3f}  filled={mask.mean()*100:.1f}%")

    print("2/4  Computing height map …")
    hm, blend_px = mask_to_heightmap(
        mask,
        blend_outside  = args.blend,
        smooth_sigma   = args.smooth,
        pad_power      = args.pad_power,
        nail_power     = args.nail_power,
        size_threshold = args.size_threshold,
        stamp_mode     = args.stamp,
        stamp_level    = args.stamp_level,
    )
    inside = mask >= 0.5
    print(f"     range [{hm.min():.3f}…{hm.max():.3f}]  "
          f"inside={hm[inside].mean():.3f}  outside={hm[~inside].mean():.3f}")

    print("3/4  Extracting paw outline + exporting SVGs …")
    pts_mm, pw, ph = extract_paw_outline(
        mask, blend_px, aspect, args.width, args.simplify)
    export_heightmap_svg(hm, os.path.join(out_dir, f"{base_name}_heightmap.svg"), args.levels)
    export_png_preview(hm,   os.path.join(out_dir, f"{base_name}_heightmap.png"))

    print("4/4  Writing OpenSCAD files …")
    scad_path = os.path.join(out_dir, f"{base_name}.scad")
    export_openscad(
        hm, scad_path, aspect,
        emboss_depth_mm    = args.depth,
        base_thickness_mm  = args.base,
        physical_width_mm  = args.width,
        downsample         = args.downsample,
        pts_mm             = pts_mm,
        stamp_mode         = args.stamp,
        stamp_level        = args.stamp_level,
    )

    print(f"\n── Done ──────────────────────────────────────────")
    print(f"\n  Keep these 2 files in the same folder:")
    print(f"    {base_name}.scad")
    print(f"    {base_name}_heights.dat")
    print(f"\n  Open {base_name}.scad in OpenSCAD → F6 → F7 (export STL)\n")


if __name__ == "__main__":
    main()