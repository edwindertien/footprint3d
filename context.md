# context.md — technical hand-off for footprint3d

Audience: whoever continues this project (including future-you). For usage and parameters see
[README.md](README.md); this file records **what was decided, why, what was verified, and what is open**.

Facts are marked where it matters: **[verified]** = checked against the files in this repo,
**[unverified]** = reasoned but not measured.

---

## 1. Status in one paragraph

`footprint3d.py` turns an SVG of filled shapes into a distance-field height map and writes an
OpenSCAD script (`.scad` + `_heights.dat`) that builds a paw-shaped relief stamp. The full chain
works on `input/paw.svg` and has been **printed** (see [`images/footprint.jpg`](images/footprint.jpg)).
Re-running `python footprint3d.py input/paw.svg --depth 8 --base 2 --width 80 --out <dir>`
reproduces `output/paw.scad`, `output/paw_heights.dat` and `output/paw_heightmap.png`
byte-for-byte **[verified]**. Open items are in [§9](#9-known-issues-open-questions-and-next-steps).

## 2. Origin and goal

Initial request: a tool that takes a well-traced SVG contour of a footprint, builds a "height map"
from layered inward offsets (and outward bleed) with the original contour as the average level,
and produces an STL via OpenSCAD, driven by a Python script, with simple test shapes.

The concrete target is the wolf-paw stamp in
[`input/wolfspootafdrukstempel1.pdf`](input/wolfspootafdrukstempel1.pdf): a paw of 7.59 × 10.75 cm,
**with a handle**, pressed about 3 cm deep into sand, with a photo of a real print for reference.
Pillows should look like paw cushions whose outline is the SVG contour; claws should be sharper.

## 3. Environment (known good)

| Item | Value |
|------|-------|
| OS | macOS on Apple Silicon (arm64) |
| Python | 3.11 in a project venv (`python3 -m venv venv`); script also runs on 3.12 **[verified]** |
| Packages | numpy 2.4.4, scipy 1.17.1, scikit-image 0.26.0, Pillow 12.2.0, svgpathtools 1.7.2, svgwrite |
| OpenSCAD | 2021.01 (behaviour of `surface()` and SVG import differs in other versions) |
| Not needed | cairo, cairosvg, svglib, reportlab, pkg-config |

Why a venv and why no Cairo is documented in the README ("Python environment quirks"). In short:
`cairosvg` → `pycairo` needs the native Cairo library; `svglib` → `rlpycairo` → `pycairo` hits the
same wall; the default `pip` on the author's machine belonged to PlatformIO's private Python.
**Do not reintroduce a Cairo-based renderer.**

## 4. Repository map

| Path | Role | Status |
|------|------|--------|
| `footprint3d.py` | The pipeline | Source of truth |
| `make_test_shapes.py` | Writes 4 synthetic SVGs to `test_shapes/` | Source |
| `input/paw.svg` | Main input: one `<path>` with 9 subpaths (pad, 4 toes, 4 claws) inside a `<g transform="translate(0.50018625,-118.92158)">` | Input |
| `input/paw.png` | Raster twin of the SVG | Reference |
| `input/wolfspootafdrukstempel1.pdf` | Design brief (size, depth, handle, reference photo) | Reference |
| `output/paw.scad`, `paw_heights.dat`, `paw_heightmap.png/.svg` | Generated, reproducible | Generated |
| `output/paw.stl` | Exported from a **hand-edited** `paw.scad` (see §7.3) | Generated + manual |
| `output/paw_outline.svg` | Written by an abandoned earlier approach (centred viewBox `-40 -56.682 80 113.363`); nothing reads it | **Stale — delete** |
| `images/*` | `footprint.jpg` (print), `3dmodel.png` (render), `openscad.png` (screenshot) | Documentation |
| `test_shapes/` | `toes.svg` (= `shape_circle_3toes.svg`, renamed **[verified]** identical), `shape_ellipse_5toes.svg`, `shape_heel_block.svg`, `shape_paw_4toes.svg` | Test data |

## 5. Pipeline, step by step

Functions in `footprint3d.py`, in call order from `main()`:

1. **`svg_to_mask`** — rasterise the SVG to a mask.
   - `get_svg_dimensions` reads `width`/`height` (units stripped), falling back to `viewBox`.
   - `collect_paths_with_transforms` walks the XML tree and accumulates `translate()`/`scale()`
     from every ancestor (`dx += tdx*sx; sx *= tsx`). Other transform types are ignored.
   - `svgpathtools.parse_path` parses each `d`; `split_path_into_subpaths` cuts the single returned
     `Path` wherever one segment's start is more than 1.0 user unit from the previous segment's end.
   - Each subpath is sampled (`max(64, 8 × segments)` points) and drawn filled with
     `PIL.ImageDraw.polygon` onto a white canvas; mask = 1 − image/255. Drawing is **not**
     anti-aliased, so the mask is effectively binary.
   - Content is scaled so its longest edge is `--res` px, then **`--padding` px of blank border is
     added on all sides**. The aspect ratio used downstream is that of the *padded* canvas.
2. **`mask_to_heightmap`** — build the height map in [0, 1].
   - EDT inside (`dist_in`) and outside (`dist_out`) a binary mask (threshold 0.5).
   - `scipy.ndimage.label` splits the mask into connected regions; each region's own maximum
     inside-distance `r_max` is its inscribed radius. A region is a **nail** if
     `r_max < size_threshold × largest r_max`, otherwise a **pad**.
   - Per region: `t = clip(dist_in / r_max, 0, 1)`; pad profile `1-(1-t)^p`, nail profile `t^q`;
     inside height = `0.5 + 0.5 × profile` (contour = 0.5, peak = 1.0).
   - Outside: `fade = clip(dist_out / blend_px, 0, 1)` with `blend_px = blend × (largest r_max)`;
     `h_out = floor + (0.5 − floor) × (1 − 0.5(1 − cos πfade))` — a cosine ease from 0.5 down to
     `floor`, where `floor = stamp_level` with `--stamp`, else 0. Inside and outside both equal 0.5 at
     the contour, so the surface is continuous.
   - Gaussian blur with `--smooth` sigma over the whole map.
3. **`extract_paw_outline`** — the stamp silhouette.
   `dist_out <= blend_px` → `binary_fill_holes` → Gaussian σ = 2 → `find_contours` at 0.5 → longest
   contour → `approximate_polygon(tolerance = --simplify)` (2055 → 81 points for the paw) →
   mm coordinates (§7.1).
4. **`export_heightmap_svg` / `export_png_preview`** — visual checks only; not used by OpenSCAD.
5. **`export_openscad`** — writes `_heights.dat` and the `.scad`.
   - Height grid: `hm[::downsample, ::downsample]` (plain striding, **no filtering**), then
     `z = base + hm × depth` in mm. For `paw.svg` the grid is 112 × 158 cells at ≈ 0.714 × 0.717 mm.
   - `.scad`: `intersection()` of `scale([px_x, px_y, 1]) surface(center = true)` with the outline
     extruded to `base + depth + 1`, united with the outline extruded to `base`.

## 6. Decisions and why

| # | Decision | Reason | Rejected alternatives |
|---|----------|--------|-----------------------|
| D1 | Rasterise with `svgpathtools` + `Pillow` | Pure Python; handles béziers; no native library | `cairosvg`, `svglib`/`reportlab` (Cairo dependency chain, see §3) |
| D2 | Draw each subpath separately | `paw.svg` packs 9 shapes in one path; drawing it as one polygon created bogus triangles between shapes | One polygon per `<path>` element |
| D3 | Apply `<g>` transforms by hand | `svgpathtools` ignores them; `paw.svg`'s `translate(…,-118.9)` pushed shapes off-canvas | — |
| D4 | Pad canvas with blank border | Without it the outward bleed was clipped at the canvas edge (edge heights up to 0.27) | — |
| D5 | Classify regions as pad/nail by inscribed radius | `paw.svg` has a clean gap: claws r ≈ 11–16 px, pads r ≈ 38–76 px | Hand-labelling shapes |
| D6 | Inside profile `1-(1-t)^p` on the EDT value (see §6.1) | Iso-height lines are eroded copies of the contour, so pads keep their shape; steep at the edge, flat on top | See §6.1 |
| D7 | Outline = dilated silhouette, embedded as `polygon()` | Makes the base paw-shaped (not a rectangle) and avoids SVG-import ambiguity | `import()` of an outline SVG (§6.2); mesh export from Python (not done, see §9) |
| D8 | `--stamp` only changes the bleed floor | The outline is paw-shaped in both modes; stamp mode adds a flat pressing platform between pads | Cutting the grid to zero outside the silhouette (OpenSCAD `surface()` cannot represent a hole) |

### 6.1 History of the inside profile (for anyone tempted to change it)

1. **Cosine on EDT** — slope largest at the contour, smallest at the centre: a "tent" held down by
   the contour. Opposite of a cushion.
2. **`t^p`, p > 1** — slow start, then steep: still tent-like, with a thin peak.
3. **Medial-axis ridge.** The EDT is equal along the skeleton of an elongated shape, so any
   monotonic profile of it produces a ridge, not a dome. Pre-smoothing the EDT (`--dome-smooth`,
   σ = fraction of radius) rounded the ridge but at high values created artefacts where the
   inside/outside transition met. **Removed.**
4. **Hemisphere around the inscribed-circle centre** (`sqrt(nd(2−nd))`) — no ridge, but each pad
   looked like a circular dome dropped on its centre and no longer followed the SVG contour. **Removed.**
5. **`1-(1-t)^p` on the per-region normalised EDT** — adopted. It is the "contour inward in
   steps that grow larger toward the top" idea: each level is the eroded contour. The ridge becomes
   a flat top (not a spike) because the profile is flat near t = 1.

### 6.2 Why OpenSCAD SVG import was abandoned

Three ambiguities in OpenSCAD 2021.01: Y direction (SVG down vs OpenSCAD up), viewBox origin
(bottom-left of the viewBox lands at the origin, so a centred viewBox is offset), and unit handling
(`mm`/`px`/unitless). Wrapping `import()` in `translate()` did not make it reliable. Embedding
`polygon()` computed in the same coordinates as `surface()` did, and removed a file dependency.

## 7. Coordinates, units and orientation

### 7.1 Mapping (outline polygon ↔ `surface(center = true)`)

With `W_px × H_px` the padded canvas and `W_mm = --width`, `H_mm = W_mm / aspect`:

```
x_mm = col × (W_mm / W_px) − W_mm / 2
y_mm = row × (H_mm / H_px) − H_mm / 2        # no Y flip
```

**[verified]** For OpenSCAD 2021.01, `surface()` places `.dat` row 0 at the **−Y** side: reading
`paw_heights.dat` as-is correlates 0.986 with the top surface reconstructed from `output/paw.stl`;
with rows reversed the correlation is 0.002. That matches the polygon mapping above.
**[unverified]** A systematic registration offset of up to about half a grid cell (≈ 0.4 mm) probably
exists, because the outline is mapped from canvas edges while `surface()` positions cell centres.
It is not visible in the print.

### 7.2 Orientation of the stamp

Image row 0 (the claws) → −Y, so the model's top view is a **vertical mirror of the input SVG**
**[verified]**. For a stamp pressed into clay that is the correct way round: turning the stamp over
mirrors it again, so the impression reads like the original paw. If you ever want the model itself
to read like the SVG (e.g. a display plaque), mirror it in Y in the `.scad` (or flip the rows when
writing the `.dat` and the outline together). Whether the mirrored orientation was intended or lucky
is not recorded.

### 7.3 Size and the hand-edited base

- `--width` is the **padded canvas** width. With defaults and `--width 80`: canvas 80 × 113.4 mm,
  paw contour ≈ 58.5 × 91.8 mm, stamp outline ≈ 71.6 × 105.1 mm (polygon extents x −35.9…35.7,
  y −52.6…52.5) **[verified]**. The brief asks for 75.9 × 107.5 mm; there is no flag for
  "paw contour width" yet.
- Vertical layout: peaks at `base + depth`, contour at `base + depth/2`. **[unverified]** OpenSCAD's
  `surface()` should be a closed solid down to z = 0, so the base plate overlaps it.
- `output/paw.stl` was exported from a **hand-edited** `paw.scad`
  (`translate([0,0,-4]) linear_extrude(height = 5)` for the base, as in
  [`images/openscad.png`](images/openscad.png)); the STL's z-range is −4.0…10.0 **[verified]**. The
  generator writes `linear_extrude(height = base)` and the `.scad` shipped in `output/` is the
  generated one. The intent of the extra 4 mm is not recorded.

## 8. How things were verified

- **Reproducibility:** regenerated `output/paw.*` from `input/paw.svg`; `.scad`, `.dat` and the
  height-map PNG are byte-identical to the shipped files.
- **Orientation:** correlation of `paw_heights.dat` vs. the STL top surface, both row orders (§7.1).
- **Test shapes:** all four run to completion. `toes.svg` and `shape_paw_4toes.svg` yield one region,
  `shape_ellipse_5toes.svg` five, `shape_heel_block.svg` one.
- **Physical:** one print exists ([`images/footprint.jpg`](images/footprint.jpg)); it shows the relief
  and the claw notches described below. No clay/sand impression has been recorded.
- **Not verified:** behaviour in OpenSCAD versions other than 2021.01; anything involving the
  handle; non-paw inputs from Inkscape.

## 9. Known issues, open questions and next steps

Ordered roughly by value.

1. **Claw pits.** In [`images/3dmodel.png`](images/3dmodel.png) and the print, each claw has a dark
   pit/step near its base. Cause not diagnosed. Candidates **[unverified]**: the steep `t^0.4`
   profile meeting a region that is narrow in one direction; coarse stride-4 decimation sampling a
   thin feature (cells are ~0.7 mm, claws only a few cells wide); the 1 px un-anti-aliased mask.
   Cheap experiments: `--downsample 2`; `--nail-power 0.7`; replace striding by block-averaging.
2. **Size control.** Add a flag for the paw contour width (or total outline width) in mm so the
   10.75 cm brief can be met without trial and error.
3. **Handle and depth.** The brief says "met handvat" and ~3 cm print depth; the model has 8 mm
   relief and no handle. Decide the intended depth (sand vs clay) and design a handle/lug.
4. **Mirroring.** Decide whether to document the mirrored orientation (§7.2) or add a `--mirror`
   option that flips both `.dat` and polygon consistently.
5. **Touching shapes merge** into one region (`scipy.ndimage.label`), so overlapping toes get one
   normalisation and no pad/nail split (`toes.svg`, `shape_paw_4toes.svg`). Fix by separating shapes
   in the SVG, or by labelling per SVG subpath instead of per connected component.
6. **Direct mesh export.** Writing the STL from Python would remove the `.dat`/OpenSCAD
   dependency, allow smooth outline edges and a proper handle, at the cost of writing mesh code
   (the OpenSCAD route was chosen for speed of iteration).
7. **SVG coverage.** Add `<circle>/<ellipse>/<rect>/<polygon>` and `rotate()/matrix()` support so
   the Inkscape "Object to Path / Flatten Transforms" step is no longer needed.
8. **Housekeeping.**
   - Delete `output/paw_outline.svg` (stale).
   - The module docstring at the top of `footprint3d.py` still describes the old
     `import("..._outline.svg")` structure — update it.
   - `write_outline_scad_polygon()` in `footprint3d.py` is unused.
   - `padding` is in pixels: if you change `--res`, scale `--padding` with it.
   - macOS `.DS_Store` files sit in `images/`, `output/` and `test_shapes/`; add them to `.gitignore`.

## 10. How to resume

```bash
cd <project>
source venv/bin/activate                     # or create it: see README → Setup
python footprint3d.py input/paw.svg --depth 8 --base 2 --width 80 --out output/
openscad -o output/paw.stl output/paw.scad   # or F6/F7 in the GUI
```

Sanity checks after any change to the height-map code:

1. Run the command above and compare `output/paw_heightmap.png` with the committed one.
2. Open `paw.scad` and check the outline follows the relief (no clipped pads).
3. Check the model's `.stl` z-range: min = `0` (or your base override), max = `base + depth`.

Interaction notes learned the hard way:

- A one-line summary of every failed Python setup is in the README; skip straight to the venv recipe.
- OpenSCAD's `surface()` can only make a full rectangle. Anything shaped (outline, handle) has to be
  done with `intersection()`/`union()` around it.
- Keep the `.scad` and `_heights.dat` in the same folder; the `.dat` path is relative.
