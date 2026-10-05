# 3D Product Design + Injection Mold Design — Full Implementation Plan

**Status:** Proposed
**Target repo:** `pcb-designer-ai-agent`
**Author context:** extends the existing NL → PCB pipeline into a full NL → (PCB + 3D product + injection mold) platform
**Environment verified on:** Pop!_OS 22.04 / Ubuntu base, Python 3.10.12, no CAD tooling installed yet (see §4.4)

---

## 0. TL;DR — Decision Summary

| Question | Answer |
|---|---|
| What are we building? | A `mechai` sub-agent: NL → parametric **3D product** (enclosure, bracket, knob, actuator…) → DFM validation → **injection mold** (cavity/core, ejectors, gate, cooling, mold base assembly, drawings, cost). |
| Does it replace `pcbai`? | **No.** It is a sibling step-graph that *consumes* `pcbai` output (KiCad STEP export of a real PCB) to co-design an enclosure around it. Same LLM layer, same provider/checkpoint plumbing. |
| Core CAD engine | **[build123d](https://github.com/gumyr/build123d)** (Apache-2.0) on **[OCP / OpenCASCADE](https://github.com/CadQuery/OCP)** (LGPL-2.1 + exception). CadQuery as fallback / secondary API. |
| Fast boolean + mesh engine | **[manifold3d](https://github.com/elalish/manifold)** (Apache-2.0) via trimesh engine, plus **[trimesh](https://github.com/mikedh/trimesh)** (MIT). |
| Is there a GitHub library to "patch"? | Yes — but **do not fork**. Depend on wheels and send PRs upstream. See §4 for the exact repo list, what to vendor vs. depend on, and the patch policy. The only thing worth forking is *nothing*; what is worth **copying** is our own DFM rule engine + design IR schema (they are ours, not upstream). |
| Web tool integration | New tabs + `/api/mech/*` endpoints in [`app.py`](app.py), a three.js GLB viewer, reusing the existing async task registry, `ProviderContext` and `PipelineCheckpoint`. |
| Total effort | ~14–16 weeks for 1 senior engineer + 1 mechanical engineer part-time; ~5 days to a demoable MVP. |
| Hard blocker | None. Biggest risk is **LLM-authored geometry being invalid** — solved by a declarative feature DSL, not by trusting generated Python. |

---

## 1. Scope

### 1.1 In scope — 3D Product Design
- Natural-language → structured mechanical spec (`ProductSpec`)
- Parametric, feature-based solid generation (draft, shell, wall, ribs, bosses, draft angle, fillets, chamfers, pockets, holes, patterns, pockets/gasket grooves)
- **PCB-aware enclosure generation** driven by a real KiCad board outline, component bounding boxes, connector positions and mounting holes
- STEP AP214 / STL / 3MF / DXF export
- Interactive 3D preview in the web dashboard
- DFM report (moldability rules) with per-feature violations

### 1.2 In scope — Injection Mold Design
- Mold architecture selection (2-plate / 3-plate / insert / family)
- Shrinkage compensation per resin
- Parting-line placement (auto + manual override), core/cavity split
- Core & cavity geometry generation
- Gate placement + sizing (edge / drop / runner / valve)
- Ejector system (pin, blade, stripper) with layout rules
- Cooling circuits: routing geometry + first-order thermal network + ΔT report
- Mold base assembly (plates, guide pillars/bushes, return pins, sprue bushing, locator ring)
- Output package: mold assembly STEP, cavity/core STL, plate drawings, drilling/tapping table, coolant schematic, DFM checklist, cost estimate, cycle time
- Hook for Moldflow / OpenFOAM (optional, out-of-band)

### 1.3 Explicitly out of scope (v1)
- Metal die casting / CNC-only part quoting
- Full CAE: FEA, CFD, true mold-flow with shear/heat-transfer coupling
- GD&T-complete 2D drawing generation with balloon datums (v2 aspirational)
- Organic / freeform sculpting (only via optional concept mesh path)
- Training any model

---

## 2. Current System Anatomy (what we extend)

| Layer | File | Role |
|---|---|---|
| Web entry | [`run_web.py`](run_web.py) | Bootstraps `sys.path`, loads `.env`, starts Flask |
| Flask API | [`app.py`](app.py) | `/api/status`, `/api/bom`, `/api/design`, `/api/task/<id>`, `/api/resume/<id>`, `/api/footprint`, `/api/download/<f>` |
| Task registry | [`_tasks`](app.py:63) | In-memory `task_id → status/result/log` dict + lock, polled by the UI |
| Web UI | [`index.html`](web/templates/index.html), [`app.js`](web/static/app.js), [`style.css`](web/static/style.css) | Vanilla JS dashboard, 2.5 s polling loop |
| LLM layer | [`provider.py`](anna-app/executas/pcb-designer/pcbai/llm/provider.py), [`context.py`](anna-app/executas/pcb-designer/pcbai/llm/context.py) | Multi-provider + `ProviderContext`, `ProviderTracker`, `PipelineCheckpoint` |
| Pipeline steps | [`pcbai/steps/`](anna-app/executas/pcb-designer/pcbai/steps) | `requirements_parser`, `bom_generator`, footprint generators, `kicad_pcb_writer`, `design_compiler` |
| CLI | [`cli.py`](anna-app/executas/pcb-designer/pcbai/pipeline/cli.py) | Click group: `design`, `bom`, `footprint`, `extract_package`, `synthesize` |
| Compiler | [`compile_design()`](anna-app/executas/pcb-designer/pcbai/steps/design_compiler.py:1) | NL → BOM → `.kicad_sch` → `.kicad_pcb` → Gerbers → ZIP |
| Plugin (Anna OS) | [`plugin.py`](anna-app/executas/pcb-designer/plugin.py), [`executa.json`](anna-app/executas/pcb-designer/executa.json) | JSON-RPC tool surface; `requires_local_agent` (KiCad 8), PyInstaller spec |
| Packaging | [`pyproject.toml`](anna-app/executas/pcb-designer/pyproject.toml), [`uv.lock`](anna-app/executas/pcb-designer/uv.lock), [`Makefile`](Makefile) | Deps, entry point `pcbai`, `make web/test/cli-design` |
| Eval harness | [`reef_harness/`](reef_harness) | LLM eval loop — reuse pattern for mechanical prompt evals |

### 2.1 Key reuse decisions
1. **Reuse `ProviderContext` verbatim.** Mechanical steps call `get_provider()` exactly like [`parse_requirements()`](anna-app/executas/pcb-designer/pcbai/steps/requirements_parser.py:25) does. Zero new LLM plumbing.
2. **Reuse `PipelineCheckpoint`.** Mechanical stages (`spec`, `part`, `cavity`, `core`, `mold`) become checkpoint keys alongside `requirements` and `bom`. `POST /api/resume/<id>` then resumes mechanical runs for free.
3. **Reuse the task registry + `/api/download/<file>` + `web/outputs/<task_id>/`** layout. No new storage design.
4. **Reuse the Click CLI** by adding a `mech` sub-group under the same `pcbai` entry point so PyInstaller/Anna packaging stays one binary.
5. **New, separate Flask blueprint** (`mech_bp`) mounted in [`app.py`](app.py) so PCB endpoints are untouched.

---

## 3. Target Architecture

### 3.1 Package layout

```
pcb-designer-ai-agent/
├── app.py                              # + register_mech_blueprint(app)
├── web/
│   ├── templates/
│   │   ├── index.html                  # existing PCB dashboard
│   │   └── mech.html                   # NEW: product + mold workbench
│   ├── static/
│   │   ├── app.js  style.css           # existing
│   │   ├── mech.js                     # NEW: API client + polling + viewer glue
│   │   ├── mech.css                    # NEW: workbench layout
│   │   └── vendor/three/               # vendored three.js (MIT) + GLTF/DRACO loaders
│   └── outputs/<task_id>/              # artifacts land here (existing convention)
├── anna-app/executas/pcb-designer/
│   ├── plugin.py                       # + mechanical RPC handlers
│   ├── executa.json                    # + new `tools[]` entries
│   └── pcbai/
│       ├── llm/                        # REUSED as-is
│       ├── core/config.py              # + Settings fields for mech backends
│       ├── pipeline/cli.py             # + `mech` click group
│       ├── steps/                      # existing PCB steps (unchanged)
│       └── mech/                       # ★ NEW PACKAGE
│           ├── __init__.py
│           ├── ir.py                   # ProductSpec / MoldSpec pydantic models (the IR)
│           ├── llm_spec.py             # NL → ProductSpec (LLM + schema repair + fallback)
│           ├── materials.py            # resin library: shrinkage, draft, wall, cooling
│           ├── standards.py            # ISO 14580 holes, threads, mold-base catalog
│           ├── kernel.py               # FeatureOp dispatcher + build123d execution
│           ├── ops/                    # one module per feature op (solid ops)
│           │   ├── base.py  solid.py  shell.py  draft.py  ribs.py
│           │   ├── bosses.py  holes.py  patterns.py  pockets.py
│           │   ├── fasteners.py  gaskets.py  text.py  loft_sweep.py
│           ├── enclosure.py            # PCB-aware enclosure generator
│           ├── pcb_intake.py           # KiCad .kicad_pcb → board envelope/connectors
│           ├── dfm/
│           │   ├── rules.py            # rule catalog (data-driven)
│           │   ├── moldability.py      # runs rules over IR + solid
│           │   └── report.py           # markdown/HTML/PDF report
│           ├── mold/
│           │   ├── splitter.py         # parting line, core/cavity
│           │   ├── shrink.py           # shrinkage scaling
│           │   ├── gate.py             # gate selection + sizing
│           │   ├── ejectors.py         # ejector layout
│           │   ├── cooling.py          # circuit routing + thermal network
│           │   ├── mold_base.py        # plate stack-up + guides + bushings
│           │   └── assembly.py         # full mold assembly + BOM
│           ├── export/
│           │   ├── step.py  stl.py  glb.py  dxf.py  drawings.py  pdf.py
│           ├── sandbox.py              # hardened subprocess executor
│           └── compiler.py             # end-to-end mech compiler (mirror of design_compiler.py)
├── tests/
│   ├── test_mech_ir.py  test_mech_ops.py  test_mold_split.py
│   ├── test_cooling.py   test_dfm_rules.py test_mech_exports.py
│   └── goldens/                          # reference parts + golden metrics
└── reef_harness/harness/mech_evals.py   # NEW: prompt-level eval suite
```

### 3.2 The Design IR (contract between LLM and geometry kernel)

This is the single most important design decision. **The LLM never writes Python.** It emits a validated JSON IR; our code compiles the IR. This makes output deterministic, checkable, replayable and safe.

```jsonc
// product_spec.json  — schema: mechai.product/v1
{
  "schema": "mechai.product/v1",
  "name": "sensor-node-enclosure",
  "kind": "enclosure",                 // enclosure|bracket|knob|actuator|lid|panel|heatsink|gear|clip
  "function": "IP65 wall-mount enclosure for the BME280 + ESP32 board produced by the PCB pipeline",
  "material": { "resin": "ABS", "grade": "ABS-8020", "shrink_rate": 0.018, "min_wall": 1.0, "nominal_wall": 2.0 },
  "process": "injection_mold",
  "envelope": { "length": 92.0, "width": 62.0, "height": 26.0 },
  "pcb": {
    "source_task_id": "a1b2c3d4e5f6",   // links to web/outputs/<id>/board.kicad_pcb
    "clearance_xy": 1.0,                 // per-side air gap around board outline
    "clearance_z_top": 4.0,              // headroom over tallest component
    "standoff_holes": "auto",            // auto | explicit | none
    "connector_faces": "auto"            // auto-detect USB/JST on board edge
  },
  "features": [
    { "op": "shell",       "wall": "nominal", "open_face": "+z", "draft": 1.5 },
    { "op": "split_line",  "plane": "z=0", "style": "step", "offset": 0.002 },
    { "op": "pocket",      "select": "connector_usb", "clearance": 0.4 },
    { "op": "hole_pattern","select": "standoffs", "dia": 2.5, "csk_dia": 4.2, "csk_depth": 1.2 },
    { "op": "gasket_groove","profile": "d_6mm", "depth": 1.4 },
    { "op": "bosses",      "count": 4, "od": 5.0, "id": 2.4, "height": 3.0, "root_fillet": 1.0 },
    { "op": "fillet",      "select": "all_internal", "radius": 1.0 },
    { "op": "draft_apply", "angle": 1.5, "exclusions": ["connector_pockets"] },
    { "op": "text",        "content": "SN-001", "face": "+z", "depth": 0.35 }
  ],
  "constraints": {
    "max_wall": 3.0, "min_wall": 1.0, "rib_thickness_ratio": 0.6,
    "no_sharp_internal_corners": true, "parting_strategy": "auto",
    "cosmetic_faces": ["+z"]
  },
  "finish": { "texture": "VDI-24", "color": "RAL 7016" }
}
```

```jsonc
// mold_spec.json — schema: mechai.mold/v1
{
  "schema": "mechai.mold/v1",
  "part": "product_spec.json",
  "shrinkage": { "linear": 0.018, "material": "ABS", "source": "catalog" },
  "cavities": 1,
  "mold_type": "3_plate",                 // 2_plate | 3_plate | insert | family
  "parting_line": { "strategy": "auto", "plane": "z=0" },
  "gate":  { "type": "edge_gate", "face": "+y", "position": [0, 62, 13], "dia": 3.5, "vestige": "cosmetic_ok" },
  "ejectors": [
    { "type": "pin", "position": [12, 8, 26], "dia": 4.0, "length": 40 },
    { "type": "stripper", "over": "open_face" }
  ],
  "cooling": {
    "circuits": 2,
    "channel_dia": 8.0,
    "channel_center_spacing": 24.0,
    "wall_offset": 18.0,
    "inlet_temp_c": 30, "target_mold_temp_c": 55,
    "coolant": "water"
  },
  "mold_base": { "series": "HASCO", "size": "K10/90x56x110" },
  "inserts": [ { "name": "cavity_insert_A", "type": "A", "material": "1.2344", "hardness_hrc": 48 } ],
  "mold_number": 1
}
```

Both are **pydantic v2** models ([`pydantic`](https://docs.pydantic.dev) already a dependency in [`pyproject.toml`](anna-app/executas/pcb-designer/pyproject.toml)) with a JSON Schema published at `/api/mech/schema` so the LLM is given the schema and the result is validated + auto-repaired.

### 3.3 Runtime flow

```
NL prompt
   │
   ├─► pcbai (existing) ──► board.kicad_pcb ──► kicad-cli pcb export step ──► board.step
   │                                                    │
   │                                                    ▼
   │                                          pcb_intake.py
   │                             (outline polyline, component AABBs + max Z,
   │                              connector edge map, mounting holes)
   ▼                                                    │
mechai/llm_spec.py  ◄── pydantic schema + few-shot ───────┘
   │  ProductSpec  (validated, repaired, checkpoint "spec")
   ▼
mechai/kernel.py  ── feature ops ──► part solid (B-Rep)
   │                                   │
   ├─► export/ (STEP/STL/GLB/DXF/PDF)  │
   │                                   ▼
   ├─► dfm/moldability.py ──► DFM report (violations, wall map, undercut map)
   │                                   │
   ▼                                   ▼
mold/splitter.py ──► core / cavity ──► mold/assembly.py ──► mold package + cost
                                        (gate, ejectors, cooling, mold base)
```

---

## 4. Library Answer — "is there an additional GitHub library to patch?"

### 4.1 Recommendation matrix

| Capability | Library (GitHub) | Licence | Role | Integration |
|---|---|---|---|---|
| **B-Rep solid modelling** | [gumyr/build123d](https://github.com/gumyr/build123d) | Apache-2.0 | **Primary kernel.** Modern API, algebra mode, good fillet/chamfer/split, STEP/STL/GLB export | pip dep |
| B-Rep solid modelling (alt) | [CadQuery/cadquery](https://github.com/CadQuery/cadquery) | Apache-2.0 | Secondary API, huge ecosystem + docs, HLR/SVG exporters | pip dep |
| **Geometry kernel** | [CadQuery/OCP](https://github.com/CadQuery/OCP) → OpenCASCADE 7.x | LGPL-2.1 (+exception) | The actual B-Rep engine under both of the above | pip dep (`cadquery-ocp`) |
| Geometry kernel (alt) | [tpaviot/pythonocc-core](https://github.com/tpaviot/pythonocc-core) | LGPL-2.1 | conda-only, lower priority | conda env (escape hatch) |
| **Fast mesh booleans / robustness** | [elalish/manifold](https://github.com/elalish/manifold) (+ `manifold3d` wheels) | Apache-2.0 | Boolean split for core/cavity, undercut probing, fast web previews | pip dep |
| **Mesh IO + inspection** | [mikedh/trimesh](https://github.com/mikedh/trimesh) | MIT | STL/OBJ/GLB/PLY/3MF IO, watertight & manifold checks, ray casting | pip dep |
| Mesh IO | [nschloe/meshio](https://github.com/nschloe/meshio), [mmeshman/numpy-stl](https://github.com/mmeshman/numpy-stl) | MIT / MIT | Additional format IO | pip dep |
| **Fast triangulation of B-Rep** | [jmplonka/ocp_tessellate](https://github.com/jmplonka/ocp_tessellate) | Apache-2.0 | 10–100× faster STL/GLB meshing than naive OCP tessellation — **critical for UX** | pip dep |
| **3MF / STEP validation** | [3MFConsortium/lib3mf](https://github.com/3MFConsortium/lib3mf) | BSD-3 | 3MF package read/write for print/quote workflows | optional |
| **Hidden-line drawings / SVG** | build123d + OCP `HLRBRep_Algo` | same as above | Auto 2D ortho views → SVG → PDF drawing sheets | built-in |
| **Mesh repair / remesh** | [pymeshlab/pymeshlab](https://github.com/pymeshlab/pymeshlab) | GPL-3.0 | Optional post-process for messy imported meshes | **subprocess / optional extra** |
| **FE / meshing** | [gmsh-dev/gmsh](https://github.com/gmsh-dev/gmsh) | GPL-2.0 | Meshing for cooling-channel thermal proxy; also user meshes | **subprocess / optional extra** |
| **Assembly + import** | [FreeCAD/FreeCAD](https://github.com/FreeCAD/FreeCAD) (`freecadcmd`) | LGPL-2.1+ | Import customer STEP, run macros headless, assemblies | **subprocess / optional extra** |
| **PCB → 3D bridge** | [KiCad/kicad-source-mirror](https://gitlab.com/kicad/code/kicad) | GPL-3.0 | `kicad-cli pcb export step` gives the board + 3D models | **subprocess** (already a dep of this repo) |
| Component 3D bodies for KiCad | [KiCad/StepUp](https://github.com/KiCad/StepUp) | GPL-3.0 | STEP export/import of component models | optional |
| Fastener/thread geometry | [shaise/FreeCAD_FastenersWB](https://github.com/shaise/FreeCAD_FastenersWB) | LGPL | ISO 14580 / ISO 4762 library | optional; **otherwise** ship our own ISO tables (data, not code) |
| **Thermal/fluid props** | [ibell/CoolProp](https://github.com/ibell/CoolProp) | MIT | Coolant ρ, cp, μ, k for cooling network | optional pip dep |
| **Cooling network graph solve** | [networkx](https://github.com/networkx/networkx) + [scipy](https://github.com/scipy/scipy) | BSD-3 | Steady-state ΔT along circuit, iterative balance | pip dep |
| Numeric base | numpy, pydantic, click, flask (existing) | BSD/MIT | already in [`pyproject.toml`](anna-app/executas/pcb-designer/pyproject.toml) | existing |
| **Browser 3D viewer** | [mrdoob/three.js](https://github.com/mrdoob/three.js) | MIT | GLB/STEP-less preview, section clipping, explode, measure | vendored into `web/static/vendor/` |
| Browser STEP preview (nice-to-have) | occt-import-js (WASM) | MIT | Load `.step` directly in browser instead of waiting for GLB | optional |
| Text/image → concept mesh | [stabilityai/TripoSR](https://github.com/stabilityai/TripoSR) | MIT | **Concept only** — not mold-ready; must be remeshed + minimum-thickness pass | optional, offline job |

### 4.2 Licence hygiene (important — this repo is dual-licensed with a commercial gate)

- **This repo** is dual-licensed ([`LICENSE`](LICENSE)): non-commercial OSS, commercial requires written authorisation. Keep that posture.
- **Link dynamically** (pip) to Apache-2.0 / MIT / BSD / LGPL-with-exception. That is clean.
- **GPL components (KiCad, gmsh, pymeshlab)** must stay **subprocess-only** (we invoke `kicad-cli`, `gmsh`, `pymeshlab` as separate processes and read files). Never import them into our process graph and never vendor their binaries into the Anna OS distribution.
- **Omit `TripoSR`/any AI 3D generator with non-commercial clauses** (e.g. Hunyuan3D, TRELLIS) from anything commercial.
- Add a `THIRD_PARTY.md` generated by `pip-licenses` in CI so the licence posture is auditable per release.

### 4.3 Patch policy — **do not fork**

| Situation | Action |
|---|---|
| Bug in build123d / manifold / trimesh | Open upstream issue + PR. Bump pinned version. |
| Need for a local workaround lasting >2 weeks | Add a thin **adapter** in [`mech/kernel.py`](anna-app/executas/pcb-designer/pcbai/mech/kernel.py) with a `try/except` and a pinned comment + upstream link. |
| Genuinely need to modify upstream | `git subtree` a mirror into `third_party/<name>/` **only** for pure-Python libs (build123d, manifold python bindings, trimesh), never for compiled OCCT. |
| We author | DFM rule catalog, IR schema, feature ops, mold logic — these live here, not upstream. |

### 4.4 Install

```bash
# Core (kernel + mesh + solver)
cd anna-app/executas/pcb-designer
uv pip install "build123d>=0.6" "cadquery>=2.5" "cadquery-ocp>=7.7" \
              "manifold3d>=2.3" "trimesh>=4.4" "meshio>=5.3" "numpy-stl>=1.11" \
              "ocp_tessellate>=0.5" "networkx>=3.2" "scipy>=1.11"
# or add to [project.optional-dependencies] mech = [...] in pyproject.toml

# Optional extras (subprocess)
sudo apt install -y freecad gmsh          # or AppImage / conda for newer FreeCAD
uv pip install pymeshlab CoolProp
```

Pin `cadquery-ocp` to the exact minor that matches `build123d`'s tested OCCT version. This is the single most common breakage point — record it in `uv.lock` and add a smoke test that constructs a box + fillet + STEP round-trip.

---

## 5. Engine Design

### 5.1 Product-design kernel

**Principle: a feature op is a pure function `(Part, OpArgs, Context) → Part`.** Ops compose in a tree; the kernel replays the tree and caches by `(spec_hash, op_index)`.

Required ops for v1 (all are small, deterministic and unit-testable):

| Op | Notes |
|---|---|
| `shell` | `offset`/`thickness` via OCP `BRepOffsetAPI_MakeThickSolid`, open-face aware |
| `draft_apply` | Per-face draft using face normal + direction mask; implemented with `BRepOffsetAPI_DraftAngle`, exclusions by face selector |
| `fillet` / `chamfer` | Edge selectors (`all_internal`, `by_radius`, `by_predicate`) — **failure must be non-fatal**, degrade to skip + report |
| `holes` / `hole_pattern` | Countersink/counterbore, ISO clearance/pilot/tap from [`standards.py`](anna-app/executas/pcb-designer/pcbai/mech/standards.py) |
| `bosses` | OD/ID/height + base fillet (root radius is the single most common DFM error — automate it) |
| `ribs` | Thickness = ratio × wall, thickness relief at base, draft |
| `pockets` | Selectors over PCB connector map, screen cutouts, keyholes |
| `gasket_groove` | Standard groove profiles (EMI, IP65 O-ring) |
| `split_line` | Records parting geometry as a face-predicate mask, used later by the mold splitter |
| `text` | Emboss/deboss via OCP text → wire → prism |
| `pattern` / `mirror` | Polar + rectangular linear patterns |
| `loft` / `sweep` | For clips, hinges, ducts (v2) |

**Failure philosophy:** an op that cannot be applied returns the input part plus a `Violation(op, reason)`. The pipeline never aborts on geometry — it collects violations, feeds them into the DFM report, and asks the LLM for one repair round-trip (bounded at 2 iterations). This is what keeps a 95 % success rate instead of a 40 % one.

### 5.2 PCB ↔ mechanical co-design (the differentiator)

This is where the existing repo earns its keep. Instead of a generic "box enclosure", the enclosure is generated from the **real board**:

1. `kicad-cli pcb export step --force -o board.step board.kicad_pcb` (KiCad 8, already a dependency).
2. `pcb_intake.py` parses the `.kicad_pcb` (S-expression, we already write this format in [`kicad_pcb_writer.py`](anna-app/executas/pcb-designer/pcbai/steps/kicad_pcb_writer.py)) for:
   - Edge.Cuts outline → ordered polyline (the enclosure inner pocket)
   - Footprint courtyards → XY bounding boxes
   - `(model ...)` refs → Z height per component (fallback: package-height table)
   - MountingHole footprints → standoff positions
   - Footprints with `SMD`/edge pads within 3 mm of the outline → **connector cut-outs**
   - Tallest component Z → top clearance
3. Feature ops consume that map: inner pocket from the outline + clearance, standoff bosses at mounts, cut-outs for connectors, venting channels for high-power parts, ribs under heavy components.
4. The same board STEP is rendered **inside** the enclosure in the web viewer → instant visual verification of fit.

**Why this matters commercially:** an AI enclosure generator that ignores the actual board is a toy. One that says *"your JST-PH connector at X=78.2, Y=4.1 pokes out 1.4 mm — I cut a 9.4 × 5.0 window"* is a product.

### 5.3 DFM / moldability rule engine

Rules are **data** (a list of dicts with a callable id), not code branches. Each rule returns `Violation(feature, severity, measured, limit, message, fix_hint)`.

Core catalog (v1):

| Group | Rule | Typical limit |
|---|---|---|
| Wall | Min wall thickness | ≥ resin `min_wall` (ABS 1.0, PC 1.0, PP 1.0, PA6 0.8, POM 1.0 mm) |
| Wall | Max wall thickness | ≤ resin `max_wall` (ABS 3.5, PC 3.0, PP 3.5, PA6 3.0) |
| Wall | Wall thickness uniformity | ±25 % across the part |
| Rib | Rib thickness | 0.5–0.7 × wall (0.6 × W nominal) |
| Rib | Rib root fillet | R ≥ 0.25 × W |
| Boss | Boss OD | 1.5–2.0 × hole Ø |
| Boss | Boss-to-boss / boss-to-wall | ≥ 1.5 mm |
| Corner | External radius | R ≥ 0.5 × W |
| Corner | Internal radius | R ≥ 1.0 × W |
| Draft | Draft angle on vertical faces | ≥ 0.5° min, 1.5° rec. with texture, 2–3° cosmetic |
| Draft | Undercut detection | ray-cast per face normal vs. draw direction |
| Ejection | Ejection surface area | ≤ 8–10 cm² per ejector pin Ø4 |
| Ejection | Ejector pin Ø | 3–6 mm, pitch ≥ 8–12 mm |
| Ejection | Sink-mark risk | flags large flat unsupported faces |
| Parting | Parting line on cosmetic face | violation if intersecting `cosmetic_faces` |
| Parting | Undercut across parting plane | violation (unless side action declared) |
| Gate | Gate onto wall | distance from base 0.3–0.8 × W |
| Gate | Gate near thick section | within 1.5 × local max wall |
| Gate | Gate vestige | acceptable / not acceptable |
| Thermal | Shot weight vs. cavity count | n × V_part ≤ 30 % of mold cavity volume |
| Thermal | Wall thickness at gate | ≤ 1.2 × nominal |
| Tolerance | Draft of holes/pins | ≥ 0.5° |
| Tolerance | Step at parting line | 0.001–0.005 mm |

Undercut detection: sample each face's outward normal, cast a ray along −draw direction, test hits. Pure trimesh/manifold, ~10 ms/10 k faces, cheap enough to always run.

### 5.4 Injection mold engine

**Step M1 — Architecture selection.** Score the part: bounding box, depth/width ratio, cosmetic faces, draft-ability, cavity count economics, insert needs. Output `2_plate | 3_plate | insert_mold | family_mold` with a reason string.

**Step M2 — Shrinkage.** Scale the *part solid* by `1 + shrink_rate` (material catalog, user-overridable). Do **not** scale the PCB clearance — scaling the cavity would break the fit; instead compensate clearance by `shrink_rate × distance` so the fit survives moulding.

**Step M3 — Parting line + split.** Default: the plane that (a) is orthogonal to the draw direction of the largest face set, (b) minimises total silhouette area (=> cheapest steel), (c) does not intersect cosmetic faces. Candidates evaluated: X/Y/Z mid-planes, user planes, and **step parting**. Then:
```
core   = intersect(part_scaled, halfspace_A)
cavity = cut(part_scaled, halfspace_A)
```
with `manifold3d` for speed and OCP for exactness; assert `volume(core) + volume(cavity) == volume(part)` within 1e-6 relative.

**Step M4 — Gate.** Heuristic scorer over candidate positions (edge gate per side, drop gate onto bosses, direct gate for near-2D parts). Score = flow length uniformity (max/mean), weld-line risk, vestige visibility, gate-to-thick-section distance. Output gate position, type, diameter, cold-slug well, sprue bushing position.

**Step M5 — Ejectors.** Voronoi-ish distribution over the core face with constraints: ≥ 5 mm from cosmetic-face edges, avoid hollow bosses (place on boss tops + ring of pins), pin Ø by local area (P = A_part / n), stripper plate for flat panels, blade ejectors for shells. Report predicted stress concentration.

**Step M6 — Cooling circuits.** Route as parallel circuits on a serpentine path (boustrophedon) around the cavity, respecting:
- channel Ø 6–10 mm (default 8)
- centre-to-centre spacing ≥ 3 × D (default 24)
- channel centre to cavity wall ≥ 2.5 × D (default 18)
- channel centre to parting line ≥ 0.5 × D
- 6–8 mm from ejector pin holes and gate bushings
Then a **first-order thermal network**: path resistance `R = L/(k·A)`, coolant temperature rise `ΔT = Q / (ṁ·cp)` where `Q = m_cavity·c_p·(T_fill − T_mold)`, flow from `ṁ = ρ·A·v` at `v = 0.5–3.0 m/s`; properties from [`CoolProp`](https://github.com/ibell/CoolProp) with a water table fallback. Solve for inlet/outlet ΔT and cycle-time impact. **Flag if ΔT > 5 °C → "rebalance circuits".**

**Step M7 — Mold base.** Plate stack-up against a catalog (HASCO / DME / FUTABA) with guide pillar Ø/position, return-pin layout, sprue bushing, locator ring, support columns, and **dimensional validation**: guide centres must be symmetric to the cavity layout, plates must be ≥ part envelope + 30 mm each way, plate thickness from bending estimate.

**Step M8 — Assembly + outputs.** Mold assembly STEP with colours per component, exploded view in the viewer, cavity/core STL, per-plate 2D drawings (SVG→PDF) with dimensions, drilling + tapping table (CSV), coolant schematic (SVG), mold BOM with steel grades (P20/H13/1.2344) and HRC, DFM checklist, cost estimate.

**Costing model (parametric, transparent):**
```
mold_cost ≈ steel_mass × €/kg
          + CNC_hours × €/hour            # rough from surface area × depth
          + cooling_hours × rate
          + assembly_hours × rate
          + inserts + hot runner + texturing
```
plus `cycle_time_s` (fill + hold + cool-dominated) and `shots_per_hour`, `clamp_tonnes ≈ A_proj[cm²] × P[MPa] × 0.098`, `annual_output @ n_schines`. Show the formula and the inputs so the number is auditable, not magic.

### 5.5 Sandbox / safety

LLM output is JSON, but the IR can still request ops we didn't anticipate, and users will eventually want raw CadQuery. Build [`sandbox.py`](anna-app/executas/pcb-designer/pcbai/mech/sandbox.py) **now**, not later:

- Execute in a subprocess (`python -I -S -c ...`) with: `resource.RLIMIT_CPU` (60 s), `RLIMIT_AS` (4 GB), `RLIMIT_NOFILE`, `RLIMIT_NPROC`, a scratch `cwd`, and **no network** (unshare/iptables not portable → at minimum, no proxy env + no sockets in the runner module namespace).
- Hard-kill on timeout, emit structured error back into the DFM report.
- Never pass user strings into `eval`/template rendering — validated IR only.

---

## 6. Web Tool Integration

### 6.1 UI

New template [`mech.html`](web/templates/mech.html) at `/mech`, with two workspaces:

**A. Product Design**
- Prompt box + structured form (kind, resin, envelope, draft, wall, process)
- Live **three.js** viewport: GLB with orbit/zoom, section-plane slider, exploded slider, part/PCB/connector colour toggle, measurement
- Feature tree panel (op list with enable/disable + param edit → **regenerate without re-calling the LLM**)
- DFM report panel grouped by severity (error / warn / info), each row clickable → highlights the offending face in the viewer
- Download row: STEP · STL · GLB · 3MF · DXF · PDF drawing

**B. Injection Mold**
- Mold type + cavities + material selectors
- Split-view viewport: core (one colour) / cavity (another) / parting line (highlighted) / gate (green) / ejectors (blue) / cooling circuits (cyan, semi-transparent)
- Ejector pin table (editable positions, re-solves)
- Cooling schematic (SVG) with ΔT per circuit
- Mold BOM table + cost + cycle time + clamp force
- Downloads: mold STEP (assembly), cavity STL, core STL, plate drawings PDF, drilling CSV, DFM checklist PDF

### 6.2 API surface (additive; nothing existing changes)

```
GET    /api/mech/backends            → {ocp, build123d, manifold, trimesh, kicad_cli, freecad, gmsh} + versions
GET    /api/mech/schema              → JSON Schema for ProductSpec + MoldSpec
POST   /api/mech/spec                → {description, kind?, resin?, process?}  → task_id  (NL → ProductSpec)
POST   /api/mech/design              → {description | spec, material?, pcb_task_id?} → task_id  (full product pipeline)
POST   /api/mech/mold                → {product_task_id | spec, mold opts}      → task_id  (full mold pipeline)
POST   /api/mech/dfm                 → {spec}                                 → report (sync, fast)
POST   /api/mech/regenerate          → {spec, ops:[...]}                      → new task_id (no LLM; op-level edits)
GET    /api/mech/task/<task_id>      → {status, stage, progress, result, dfm, mold, files, log}
GET    /api/mech/preview/<task_id>   → model.glb | core.glb | cavity.glb | assembly.glb
GET    /api/mech/download/<task_id>/<file>  → any artifact (reuse OUTPUT_DIR convention)
POST   /api/mech/resume/<task_id>    → reuse PipelineCheckpoint
```

Implementation notes:
- Mount a Blueprint in [`app.py`](app.py): `from pcbai.mech.web import mech_bp; app.register_blueprint(mech_bp)`.
- Reuse `_tasks` + `_tasks_lock` + the `_log()` callback pattern already in [`api_design()`](app.py:324) — extend the task dict with a `"stage"` and `"progress_pct"` field; the frontend already polls, so extend [`app.js`](web/static/app.js)'s progress rendering rather than inventing SSE (SSE is a v2 nice-to-have).
- Add `mech` info to [`api_status()`](app.py:114) so the dashboard badge shows CAD backend availability next to `pcbnew`.
- CAD work is CPU-bound and long (5–60 s). Use a dedicated bounded `ThreadPoolExecutor(max_workers=N_CPU-1)` rather than the current unbounded daemon threads so a user cannot fork-bomb the box. OCCT releases the GIL for most heavy ops, so threads work.
- Cache by `sha256(spec_json)` under `web/outputs/_cache/` — repeated tweaks of the same part then return in <1 s.

---

## 7. Anna OS Plugin Integration

[`executa.json`](anna-app/executas/pcb-designer/executa.json) gains tools:

```jsonc
{ "name": "design_product_3d",
  "description": "Natural language → parametric 3D product (enclosure, bracket, knob...) with STEP/STL/GLB export and DFM report.",
  "parameters": [ {"name":"description","type":"string","required":true},
                  {"name":"material","type":"string","required":false},
                  {"name":"process","type":"string","required":false,"enum":["injection_mold","3d_print","cnc"]} ] },

{ "name": "design_injection_mold",
  "description": "Injection mold design from a product spec: parting line, core/cavity, gate, ejectors, cooling circuits, mold base, drawings, cost.",
  "parameters": [ {"name":"product_spec_json","type":"string","required":true},
                  {"name":"cavities","type":"integer","required":false},
                  {"name":"mold_type","type":"string","required":false} ] },

{ "name": "dfm_check",  "parameters": [ {"name":"spec_json","type":"string","required":true} ] },
{ "name": "export_geometry", "parameters": [ {"name":"task_or_spec","type":"string","required":true},
                                             {"name":"format","type":"string","required":true} ] },
{ "name": "mech_pipeline", "description":"NL → 3D product → DFM → mold, one call.",
  "parameters": [ {"name":"description","type":"string","required":true} ] }
```

- Add handlers in [`plugin.py`](anna-app/executas/pcb-designer/plugin.py) following the existing JSON-RPC pattern.
- Bump `local_agent_dependencies` with the CAD stack.
- **PyInstaller warning:** OCCT makes the binary large (expect ~400–900 MB vs today's tens of MB). Mitigations: (a) lazy-import OCP inside a worker process, (b) build Linux binaries with `--exclude-module` sweeps and strip debug symbols, (c) consider shipping the Python env instead of a frozen binary (uv-based local profile), (d) split the mechanical tools into a **second Anna executa** `mech-designer` if the store has size limits. Decide in Phase 5 — track as a risk.

---

## 8. Testing & Validation Strategy

| Layer | What | How |
|---|---|---|
| Unit | Each feature op | Cube+op → assert volume/face-count/position within tolerance |
| Property | Kernel robustness | Random valid specs → build must never raise; must always return a part + violations |
| Invariant | Split correctness | `vol(core)+vol(cavity) ≈ vol(part)`; `part ∩ core = core` |
| Invariant | Mesh health | trimesh: `is_watertight`, `is_winding_consistent`, no self-intersection (manifold) |
| Golden | 12–15 reference parts | Bracket, clip, knob, USB enclosure, snap-fit lid, gear-ish boss cluster, heat sink, panel. Store spec + STEP + expected volume/fill-count. Diffs on change |
| DFM rules | Each rule | Synthetic parts that must trip exactly the expected rules (true-positive + true-negative) |
| E2E | Web | `/api/mech/design` → GLB download → render in CI with headless-gl or just byte-validate GLB |
| Eval | LLM quality | Extend [`reef_harness`](reef_harness): prompt set of 40 mechanical requests, score spec validity %, DFM violations %, human 1–5 "usable?" — same approach as the existing footprint eval |
| Regression | Perf | Each golden under 30 s on CI; OCP import < 5 s |

CI: GitHub Actions matrix `ubuntu-22.04` + `macos-14`; cache `~/.cache/uv` and the OCCT wheel (large). Golden STEP comparisons via a tolerance-based mesh comparison (Chamfer distance via trimesh proximity), not byte equality.

---

## 9. Phased Roadmap

### Phase 0 — Foundation (Week 1)
- Add `mech` extra to [`pyproject.toml`](anna-app/executas/pcb-designer/pyproject.toml); pin OCCT; get a box+fillet+STEP round-trip green in CI
- `mech/__init__.py`, `materials.py`, `standards.py`, `ir.py` (pydantic models + JSON Schema)
- `/api/mech/backends`, `/api/mech/schema`, mech blueprint skeleton
- **AC:** `GET /api/mech/backends` reports versions; a build123d box round-trips to STEP and back.

### Phase 1 — Parametric product core (Weeks 2–4)
- Kernel + ops: `solid, shell, draft_apply, fillet, chamfer, holes, hole_pattern, bosses, ribs, pockets, split_line`
- `llm_spec.py` with schema-constrained generation + repair loop
- `pcb_intake.py` + `kicad-cli pcb export step` integration
- `enclosure.py` (auto inner pocket, standoffs, connector cut-outs)
- `/api/mech/spec`, `/api/mech/design`, `/api/mech/task/<id>`, GLB preview
- `mech.html` + `mech.js` + three.js viewer (viewport, section, explode)
- **AC:** "IP65 enclosure for the board from task X" yields a valid STEP whose inner pocket matches the board outline within 0.2 mm and whose connector windows clear every edge pad.

### Phase 2 — DFM engine (Weeks 5–6)
- Rule catalog + undercut detection + wall-thickness map + report renderer (Markdown/HTML/PDF)
- LLM repair loop (max 2 rounds) driven by violations
- `/api/mech/dfm`, DFM panel in UI with face highlighting
- Goldens for ops + rules
- **AC:** 10/10 golden parts build with 0 *error*-severity DFM violations; rule unit tests all green.

### Phase 3 — Injection mold core (Weeks 7–10)
- `splitter`, `shrink`, `gate`, `ejectors` + cavity-count economics
- Split-view viewer, parting-line overlay
- `/api/mech/mold` (v1: single-cavity, edge/drop gate, pin ejectors)
- **AC:** core/cavity volume invariant holds to 1e-6; a human mold engineer signs off split + gate + ejector placement on 3 reference parts.

### Phase 4 — Cooling, mold base, drawings, costing (Weeks 11–12)
- `cooling` (routing + thermal network), `mold_base` (HASCO/DME catalog), `assembly`
- Drawings (HLR→SVG→PDF), drilling/tapping CSV, coolant schematic
- Cost model + cycle time + clamp force
- **AC:** complete mold package downloadable; cooling ΔT per circuit reported and within 5 °C; mold BOM priced.

### Phase 5 — Multi-cavity, inserts, packaging, hardening (Weeks 13–16)
- Multi-cavity balancing, insert molds, family molds, side actions, hot-runner hook
- CLI `pcbai mech` group
- Anna OS executa updates + PyInstaller size strategy
- `reef_harness` mech evals; prompt-tuning from eval results
- Docs: `RUN_MECH.md`, README section, `THIRD_PARTY.md`, example gallery
- **AC:** full NL → mold ZIP; plugin smoke tests pass; eval suite reports spec-validity > 90 % on the 40-prompt set.

### MVP slice (5 working days, do this first)
1. Day 1: `mech/ir.py` + `materials.py`; build123d smoke test; CI green
2. Day 2: `kernel.py` with `solid/shell/holes/fillet` + STEP+GLB export
3. Day 3: `llm_spec.py` (single-box kind only) + `/api/mech/design` returning GLB
4. Day 4: `mech.html` with three.js viewport showing the GLB
5. Day 5: `mold/splitter.py` — part → core + cavity, split-view in the same viewer
   → Demo: *"make me an enclosure for my board"* → GLB → split core/cavity. This proves the whole architecture end-to-end.

---

## 10. Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | OCCT/OCP version mismatch between `build123d`, `cadquery`, `trimesh` | High | High | Pin exact versions; single smoke test in CI; isolate imports in `kernel.py` |
| R2 | LLM emits an invalid/unsolvable spec | High | High | pydantic schema + repair loop + non-fatal ops + rule-driven auto-fix (§5.1) |
| R3 | Boolean ops fail on tangencies (shell at zero-thickness, fillet at coincident edges) | High | Medium | 1e-4 mm geometric tolerances everywhere; op-level try/except returning violations; manifold3d as a robust fallback path |
| R4 | Build times exceed the 2.5 s polling UX | Medium | Medium | Progress percentages per op; result caching by spec hash; reduce polling interval while running; plan SSE |
| R5 | PyInstaller binary blows past size limits for Anna OS | Medium | High | Lazy OCP import; separate `mech-designer` executa; or ship a uv-based local profile |
| R6 | Copyleft contamination (gmsh/pymeshlab/FreeCAD) | Low | High | Subprocess-only, no imports, no vendored binaries; `THIRD_PARTY.md` audit in CI |
| R7 | Drawings/dimensions are wrong enough to damage trust | Medium | High | Never auto-release a "production" drawing: mark generated outputs **CONCEPT — NOT FOR MANUFACTURE** until a human reviews; require human sign-off flag on the DFM report |
| R8 | Cooling estimates mislead (first-order ≠ real mold flow) | Medium | Medium | Label explicitly as first-order estimate; provide the Moldflow import path (STL + gate/study setup) as the "verify" step |
| R9 | Scope creep into general-purpose CAD | High | High | Enforce the op allow-list and the kind enum; say "not supported" loudly instead of half-doing it |
| R10 | Performance/CPU exhaustion on user machines | Medium | Medium | Bounded thread pool; per-task RLIMIT_CPU; concurrency cap per session |

---

## 11. Effort Summary

| Phase | Duration | Primary skill |
|---|---|---|
| 0 Foundation | 1 week | CAD/Python |
| 1 Product core | 3 weeks | CAD + LLM prompting |
| 2 DFM | 2 weeks | Injection molding knowledge |
| 3 Mold core | 4 weeks | Injection molding knowledge |
| 4 Cooling/base/drawings | 2 weeks | Mould engineering + thermal |
| 5 Advanced + packaging | 4 weeks | CAD + packaging |
| **Total** | **16 weeks** | |

Critical path is **mold engineering knowledge**, not CAD. If one person: expect 18–20 weeks. If a mechanical engineer pairs with a software engineer: 12–14 weeks. The MVP slice is 5 days regardless of staffing.

---

## 12. Success Metrics

| Metric | Target (end of Phase 5) |
|---|---|
| Spec validity from NL (eval set of 40) | ≥ 90 % schema-valid after repair |
| Goldens building without error-severity DFM violations | 12 / 15 |
| Prompt → STEP latency (enclosure, no PCB) | < 20 s |
| Prompt → full mold package latency | < 90 s |
| Human "would I send this to a toolmaker?" on 10 samples | ≥ 5/10 concept, 0/10 production (by design) |
| Web dashboard session → first 3D model | < 60 s |
| New-part cold start (install → first result) | < 15 min |

---

## 13. Open Decisions to Lock Before Phase 1

1. **Kernel:** build123d only, or CadQuery + build123d both? → *Recommend build123d only in `kernel.py`, CadQuery available only inside `sandbox.py` for user scripts.*
2. **Distribution:** single Anna executa vs. new `mech-designer` executa? → *Decide in Phase 5 based on measured binary size; provision for the split now by keeping `mech/` self-contained.*
3. **Renderer:** browser-only three.js vs. server-side VTK renders? → *Recommend browser-only; add VTK/PyVista only if users need thumbnails in emails.*
4. **Product scope:** enclosure-only first, or general parts? → *Recommend enclosure + bracket + knob for v1; the op allow-list keeps the door open.*
5. **Mold scope:** single-cavity first, or multi-cavity from day one? → *Recommend single-cavity; multi-cavity economics is Phase 5.*
6. **Licensing posture for the mechanical engine** — same dual-license as the PCB agent, or permissive for the CAD layer? → *Legal call; default to the existing dual-license.*
7. **Do we sell a "mold quote"?** If yes, costing must be calibrated against 3 real vendor quotes before it is shown to users.

---

## 14. Appendices

### 14.1 Materials catalog (starter data for [`materials.py`](anna-app/executas/pcb-designer/pcbai/mech/materials.py))

| Resin | Shrink (linear) | Min wall | Nominal | Max wall | Mold temp °C | Notes |
|---|---|---|---|---|---|---|
| ABS | 1.5–2.0 % | 1.0 | 2.0 | 3.5 | 40–80 | general purpose, good gloss |
| ABS/PC | 1.5–2.0 % | 1.2 | 2.2 | 4.0 | 50–90 | impact/tough |
| PC | 0.5–0.7 % | 1.0 | 2.0 | 3.0 | 60–100 | transparent ok, hygroscopic |
| PC/ABS | 1.5–2.0 % | 1.2 | 2.2 | 3.8 | 50–90 | |
| PP | 1.5–2.5 % | 1.0 | 2.0 | 3.5 | 30–70 | highest shrinkage, warps |
| PP-GF30 | 0.8–1.2 % | 1.0 | 2.0 | 3.5 | 40–80 | stiffer, less warp |
| PA6 / PA66 | 0.8–1.5 % | 0.8 | 2.0 | 3.0 | 50–90 | moisture-sensitive |
| POM | 1.8–2.2 % | 1.0 | 2.0 | 3.5 | 40–70 | precision, low friction |
| PS / HIPS | 1.4–1.8 % | 1.0 | 2.0 | 3.5 | 30–60 | cosmetic only |
| PET / PETG | 0.2–0.5 % | 1.0 | 2.0 | 3.5 | 40–80 | |
| TPU | 1.5–3.5 % | 0.8 | 2.0 | 3.0 | 40–70 | shrink varies a lot with hardness |
| TPE (SBS) | 1.5–3.0 % | 0.8 | 2.0 | 3.0 | 40–70 | |

*Values are typical ranges for validation, not design authority — every value is user-overridable and the report prints the source.*

### 14.2 Cooling defaults

| Parameter | Default | Range |
|---|---|---|
| Channel diameter | 8 mm | 6–10 |
| Circuit spacing (c/c) | 24 mm | ≥ 3 × D |
| Channel to cavity wall | 18 mm | ≥ 2.5 × D |
| Channel to parting line | 5 mm | ≥ 0.5 × D |
| Inlet water temp | 30 °C | 20–40 |
| Target mold temp | 55 °C | resin-specific |
| Flow velocity | 1.5 m/s | 0.5–3.0 |
| Max ΔT per circuit | 5 °C | — |
| Circuits (auto) | ceil(cavity perimeter / spacing) | — |

### 14.3 Mold base catalog (starter data for [`standards.py`](anna-app/executas/pcb-designer/pcbai/mech/standards.py))

Fields: `series, mold_number, cavity_plate_w/h, core_plate_w/h, plate_thicknesses{top,cavity,core,ejector,backing}, guide_pillar_dia/positions, return_pin_dia/positions, clamping_style, max_cavity_size, price_band`.
Seed with the top ~20 HASCO K-series and DME AX-series sizes; allow `custom` entry.

### 14.4 Glossary
B-Rep · parting line · draft angle · undercut · sink mark · weld line · vestige · cold slug well · stripper plate · blade ejector · sprue bushing · return pin · guide pillar · shot weight · clamp tonnage · cycle time · first-order cooling network.

---

*End of plan. Next action: confirm the §13 decisions, then start Phase 0 with the OCCT pin + the build123d smoke test.*