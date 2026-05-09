# footprint3d — SVG contour → 3-D STL pipeline

Converts a 2-D SVG footprint (paw, foot, any filled shape) into a printable
3-D model via a distance-field height map and OpenSCAD. Produces either a
flat embossed tile or a paw-shaped stamp ready for pressing into clay or wax.

```
your_footprint.svg
      │
      ▼  footprint3d.py  (rasterise SVG → binary mask)
  binary mask
      │
      ▼  Euclidean Distance Transform  (scipy)
  height map  (per-region adaptive profile)
      │
      ├──▶  _heightmap.svg   coloured iso-contour visualisation
      ├──▶  _heightmap.png   greyscale preview
      ├──▶  .scad            OpenSCAD script (polygon embedded)
      └──▶  _heights.dat     height grid loaded by the .scad
```

---

## Contents

- [Setup](#setup)
- [Python environment quirks](#python-environment-quirks)
- [Quick start](#quick-start)
- [Parameters](#parameters)
- [How the height map works](#how-the-height-map-works)
- [SVG input requirements and limits](#svg-input-requirements-and-limits)
- [Output files](#output-files)
- [Test shapes](#test-shapes)
- [Scientific alternatives](#scientific-alternatives)

---

## Setup

### 1. Install OpenSCAD

Download from https://openscad.org/downloads.html and install normally.
Version 2021.01 or newer. Used only for the final render step.

### 2. Install Python 3.11+

If you don't have Python 3.11 or newer:

```bash
brew install python        # macOS with Homebrew
```

Check your version:
```bash
python3 --version
```

### 3. Create a virtual environment

**Always use a venv for this project** — see [Python environment quirks](#python-environment-quirks)
for why this matters.

```bash
cd ~/Desktop/paw           # or wherever your project lives
python3 -m venv venv
source venv/bin/activate   # macOS / Linux
# venv\Scripts\activate    # Windows
```

Your prompt will now show `(venv)` at the start. Every time you open a new
terminal to use the script, activate the venv first.

### 4. Install Python dependencies

```bash
pip install svgpathtools svgwrite
pip install numpy scipy scikit-image Pillow
```

That's it — no system libraries required, no Homebrew extras.

### 5. Verify

```bash
python -c "from svgpathtools import svg2paths2; import numpy, scipy, skimage, PIL; print('all ok')"
```

---

## Python environment quirks

### Why not just `pip install` globally?

On macOS, running `pip install` without a venv installs packages into whichever
Python environment owns the `pip` command — which is often not the one you think.

In this project we hit two specific problems:

**Problem 1 — PlatformIO's Python**
The first `pip install` attempt landed inside PlatformIO's internal virtualenv
(`/Users/.../platformio/penv/`). This environment is locked down for PlatformIO's
own use. When it tried to install `cairosvg`, it needed to compile `pycairo`
from source, which requires the Cairo C library (`libcairo`) installed at the
OS level. Without it, the build failed with a long Meson error.

**Problem 2 — `svglib` pulling in `rlpycairo`**
The next attempt used `svglib` as an SVG renderer. `svglib` lists `rlpycairo`
as a dependency, which lists `pycairo` as its dependency — same Cairo C library
problem. Installing with `--no-deps` avoided the chain, but then `reportlab`
(svglib's renderer) still tried to use `rlPyCairo` at runtime and crashed.

**The final solution**
We dropped both `cairosvg` and `svglib` entirely. The script now uses
`svgpathtools` to parse SVG paths directly and `Pillow` to rasterise them.
Both are pure Python with no C library dependencies. A clean venv ensures no
interference from system or tool-managed Python environments.

**Rule of thumb**: if you see errors mentioning `cairo`, `pycairo`, `Meson`,
`pkg-config`, or `libcairo.so`, you have a system library dependency problem.
The solution is always to avoid the package that needs it, not to fight the
C library installation.

### Why not `brew install cairo` and be done with it?

You could, but then you're coupling your Python environment to a system library
that future Homebrew updates can break. The pure-Python approach is more robust
and easier to share with others.

### Reactivating the venv

Each new terminal session needs the venv activated:

```bash
cd ~/Desktop/paw
source venv/bin/activate
```

You can add this to your shell profile or create a small shell script if you
use the tool frequently.

---

## Quick start

```bash
# Activate venv
cd ~/Desktop/paw
source venv/bin/activate

# Basic emboss (rectangular base, paw sits on a flat tile)
python footprint3d.py paw.svg --depth 8 --base 2 --width 80 --out output/

# Stamp mode (paw-shaped base, ready to press into clay or wax)
python footprint3d.py paw.svg --depth 8 --base 3 --width 80 --stamp --out output/

# Open in OpenSCAD, press F6 to render, F7 to export STL
# Or render from command line:
openscad -o output/paw_stamp.stl output/paw_stamp.scad
```

---

## Parameters

### Core geometry

| Flag | Default | Description |
|------|---------|-------------|
| `--depth` | `8.0` | Total emboss height in mm — how far the tallest peak rises above the base |
| `--base` | `2.0` | Base plate thickness in mm |
| `--width` | `80.0` | Physical width of the printed model in mm. Height is derived automatically from the SVG aspect ratio |
| `--res` | `512` | Rasterisation resolution in pixels (longest edge of the SVG content). Higher = more detail, slower |
| `--downsample` | `4` | Decimation factor applied to the height grid before writing the `.dat` file. `4` means 1 sample per 4×4 px block. Lower = finer grid, larger file, slower OpenSCAD render |

### Height map profiles

The inside of each shape is mapped from `t=0` at the contour edge to `t=1`
at the centre, then converted to a height using one of two profiles:

| Flag | Default | Description |
|------|---------|-------------|
| `--pad-power` | `2.0` | Exponent for large pads. Profile: `1-(1-t)^p`. Rises steeply from the contour and flattens toward the centre — a pillbox / stadium shape. `p=1` is linear, `p=2` gives steep sides with a broad flat top, `p=3` gives near-vertical walls |
| `--nail-power` | `0.4` | Exponent for small shapes (nails/claws). Profile: `t^p`. Values below 1 give a fast initial rise and a sharp pointed tip |
| `--size-threshold` | `0.25` | Shapes whose inscribed circle radius is less than this fraction of the largest shape's radius are classified as nails and get `--nail-power`. Increase if pads are being misclassified as nails |

```
Profile 1-(1-t)^2  (pad, steep sides)    Profile t^0.4  (nail, pointed tip)
height                                    height
  1 ┤      ████████                         1 ┤  ▄█████████
    │    ██        ██                         │ █          █
    │  █              █                       │█            █
  0 ┤██                ██                   0 ┤              ██
    └──────────────────                       └──────────────────
    edge              centre                  edge            centre
```

### Bleed and smoothing

| Flag | Default | Description |
|------|---------|-------------|
| `--blend` | `0.5` | Controls how wide the outward slope is beyond each shape's contour, as a fraction of the largest shape's inscribed radius. In stamp mode this also determines how far out the paw silhouette extends. `0.3` = tight, `0.5` = medium, `0.8` = wide |
| `--smooth` | `1.5` | Gaussian blur sigma in pixels applied after the height map is computed. Softens the step at each shape's contour edge. Keep small (1–3); large values soften the profile too much |
| `--padding` | `60` | Blank pixel border added around the SVG content before computing distances. Prevents the outward bleed from being clipped at the canvas edge. Increase if you see the shape touching the edge of the model |
| `--simplify` | `1.0` | Ramer-Douglas-Peucker tolerance for simplifying the paw outline polygon that is embedded in the `.scad`. Higher = fewer polygon points, slightly less accurate outline |

### Stamp mode

| Flag | Default | Description |
|------|---------|-------------|
| `--stamp` | off | Enable stamp mode. The paw silhouette polygon is used to clip the height map surface and form the base. Outside the silhouette there is no material. The result is a paw-shaped object rather than a rectangular tile |
| `--stamp-level` | `0.15` | Height of the flat platform between the pads, as a fraction of `--depth`. `0.15` = platform sits at `base + 0.15 × depth` mm. This is the surface that contacts the clay when you press the stamp |

### Visualisation

| Flag | Default | Description |
|------|---------|-------------|
| `--levels` | `12` | Number of iso-contour colour bands in the `_heightmap.svg`. More bands = finer topographic detail in the preview |
| `--out` | same folder as SVG | Output directory for all generated files |

---

## How the height map works

Each pixel in the rasterised SVG mask is assigned a distance value by the
Euclidean Distance Transform (EDT): its distance to the nearest contour edge.
This creates a "distance landscape" where every iso-distance line is a smaller
eroded copy of the original shape — so a kidney-shaped pad stays kidney-shaped
all the way up to the peak.

The distance is normalised per region (0 at the contour, 1 at the centre) and
passed through a profile function to produce a height:

```
  height
    │
  1 ┤              ████████   ← flat top (pad profile, p=2)
    │          ████        ████
    │       ███                ███
0.5 ┤───████                        ████───  ← SVG contour line
    │  ██                                ██
    │ █                                    █
  0 ┤█                                      █
    └─────────────────────────────────────────
    far outside    contour    centre    contour    far outside
```

The outward bleed (from contour toward 0) uses a cosine curve regardless
of the inward profile, giving a smooth organic fade between shapes.

In stamp mode, the outward bleed fades to `stamp_level` (not 0), creating
a flat platform between the pads at a consistent pressing height.

---

## SVG input requirements and limits

### What works

- Filled `<path>` elements with M, L, C, S, Q, A commands (lines, bezier curves, arcs)
- Compound paths: multiple M...Z subpaths inside one `<path>` element
- `<g>` group transforms: `translate()` and `scale()` are correctly applied
- Black fill on white or transparent background
- Inkscape "Trace Bitmap" output (Plain SVG format)
- Multiple separate shapes as separate `<path>` elements

### What will fail or give wrong results

| Issue | Symptom | Fix |
|-------|---------|-----|
| Primitive elements (`<circle>`, `<ellipse>`, `<rect>`, `<polygon>`) | Shape not found, empty mask | In Inkscape: select all → **Path → Object to Path** → save as Plain SVG |
| Stroke-only paths (no fill) | Empty mask | Add a black fill to all shapes |
| Group transforms using `rotate()`, `matrix()`, `skewX/Y()` | Shape misaligned or distorted | Flatten transforms in Inkscape: select all → **Object → Flatten Transforms** |
| `<use>`, `<symbol>`, `<defs>` references | Shapes missing | Unlink all clones/symbols in Inkscape before export |
| Very fine details smaller than ~2px at `--res 512` | Detail lost | Use `--res 1024` or higher |
| Semi-transparent fills | Shape partially missing | Set all fills to fully opaque black (opacity: 1) |
| SVG with `viewBox` but no `width`/`height` attributes | Wrong physical size | Add `width` and `height` to the `<svg>` root element |

### Inkscape preparation checklist

1. Open your SVG in Inkscape
2. Select all (`Ctrl+A`)
3. **Path → Object to Path** (converts circles, rects etc. to paths)
4. **Object → Flatten Transforms** (if you have rotated groups)
5. Ensure all shapes have black fill and no stroke (or stroke is irrelevant)
6. **File → Save a Copy → Plain SVG** (not Inkscape SVG, which has extra metadata)

### Why SVG import in OpenSCAD was abandoned

We initially tried to export the paw outline as a separate SVG file and use
OpenSCAD's `import()` to bring it in. This caused persistent centering problems
due to three compounding ambiguities in how OpenSCAD 2021 handles SVG:

- **Y-axis flip**: SVG Y goes downward, OpenSCAD Y goes upward. OpenSCAD is
  supposed to flip this automatically but the behaviour changed between versions.
- **ViewBox origin**: OpenSCAD places the viewBox *bottom-left corner* at its
  own origin (0,0), not the viewBox centre — so a centered viewBox still lands
  offset.
- **Units**: SVG dimensions in `mm`, `px`, or unitless are interpreted
  inconsistently between OpenSCAD versions.

The solution: the paw outline polygon is computed in Python in the same
coordinate system as `surface(center=true)` and embedded directly as a
`polygon()` inside the `.scad` file. No external SVG file, no coordinate
system guessing.

---

## Output files

```
output/
  paw_heightmap.svg     ← topographic preview (open in browser)
                           blue = low, green = mid, red = peak
  paw_heightmap.png     ← greyscale preview (white = high)
  paw.scad              ← OpenSCAD script — open this in OpenSCAD
  paw_heights.dat       ← height grid, loaded by the .scad

  paw_stamp.scad        ← stamp mode version (paw-shaped base)
  paw_stamp_heights.dat ← height grid for stamp mode
```

Keep the `.scad` and `_heights.dat` in the **same directory**.
The `.scad` contains the paw outline polygon directly — no other files needed.

### Rendering the STL

In OpenSCAD GUI:
1. Open `paw_stamp.scad`
2. Press **F6** (full render — may take 30–60 seconds)
3. Press **F7** or **File → Export → Export as STL**

From the command line:
```bash
openscad -o paw_stamp.stl output/paw_stamp.scad
```

---

## Test shapes

`make_test_shapes.py` generates four synthetic SVG shapes for testing:

```bash
python make_test_shapes.py
# writes to test_shapes/
```

| File | Description | Good for testing |
|------|-------------|-----------------|
| `shape_circle_3toes.svg` | Circle body + 3 circular toes | Basic pipeline check |
| `shape_ellipse_5toes.svg` | Ellipse body + 5 oval toes + heel | Aspect ratio, multiple regions |
| `shape_heel_block.svg` | Rounded rectangle | Profile symmetry |
| `shape_paw_4toes.svg` | 4-toe paw with organic ellipses | Nail/pad classification |

```bash
python footprint3d.py test_shapes/shape_paw_4toes.svg \
  --depth 8 --base 3 --width 80 --stamp --out output/
```

---

## Scientific alternatives

This script uses the morphological distance-field approach — practical for
SVG-to-print with no measurement hardware. For higher accuracy:

**Pressure plate / pedobarograph**
Measure actual contact pressure on a grid sensor. Normalise pressure → height
and feed directly to the `surface()` call, skipping the distance transform.
Biomechanically accurate. Requires hardware.

**Photogrammetry**
Take 40–50 photos around the object. Process with Meshroom (free, GPU) or
COLMAP to get a full 3-D mesh. No SVG pipeline needed at all.

**Statistical shape model (PCA)**
Fit a 2-D silhouette to a PCA model trained on thousands of 3-D scans
(e.g. CAESAR anthropometric dataset). Used in medical orthotics CAD.
Requires a large scan database and ICP registration.

**Depth-from-shading**
Single photo taken under raking light encodes surface normals in the shading.
Horn's algorithm (implementable in numpy) can recover approximate depth.
Photo-only, no hardware needed, but requires careful lighting setup.