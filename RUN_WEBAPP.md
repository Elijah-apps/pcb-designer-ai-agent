# Flask Webapp — All Run Commands

Every command needed to install, run, test and operate the Flask web dashboard.

The Flask application now lives at the **project root** as [`app.py`](app.py).
Templates, static assets, env template and outputs stay in [`web/`](web/).

> **Prerequisites:** Python 3.10+. Flask is **not** listed in
> [`pyproject.toml`](anna-app/executas/pcb-designer/pyproject.toml), so install it
> explicitly. KiCad 8 / `pcbnew` is optional (a placeholder PCB is generated if missing).
> No LLM API key = local catalog fallback.

---

## Layout

```
pcb-designer-ai-agent/
├── app.py                     # Flask app + API  (moved up from web/app.py)
├── run_web.py                 # Launcher with --host / --port / --debug
├── web/
│   ├── templates/index.html   # Dashboard UI
│   ├── static/app.js          # Frontend logic
│   ├── static/style.css       # Dashboard styling
│   ├── .env.example           # Environment variable reference
│   └── outputs/               # Generated artifacts (auto-created)
└── anna-app/executas/pcb-designer/pcbai/   # Core pipeline modules
```

---

## 1. Install

### Option A — virtualenv (recommended)

```bash
cd /home/elijah/Documents/GitHub/pcb-designer-ai-agent

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install flask requests pypdf
```

### Option B — the project package (adds `pcbai` core deps)

```bash
cd /home/elijah/Documents/GitHub/pcb-designer-ai-agent

pip install -e "anna-app/executas/pcb-designer[llm]"
pip install flask
```

### Option C — system-wide

```bash
sudo pip install flask requests pypdf
```

### Optional extras

```bash
pip install "anna-app/executas/pcb-designer[vision]"   # datasheet OCR
pip install "anna-app/executas/pcb-designer[eda]"      # SKiDL
pip install pytest                                     # run the test suite
```

Verify the install:

```bash
python3 -c "import flask; print('Flask', flask.__version__)"
```

---

## 2. Install KiCad 8 (`pcbnew`) — optional

The dashboard reports `pcbnew_available` in [`/api/status`](#5-health-checks). Without
KiCad, the pipeline still emits the BOM and schematic but writes a **placeholder** board.

`pcbnew` is **not** on PyPI, so `pip install pcbnew` will fail. It must come from a
system package that Python 3 can import.

> Tested on Pop!_OS 22.04 LTS (`ID_LIKE="ubuntu debian"`), Python 3.10.12.
> Ubuntu's default repos only ship **KiCad 6**, but this codebase uses the
> KiCad 7/8 API (`pcbnew.BOARD()`, `pcbnew.NETINFO_ITEM(board, name, net_code)`,
> `ZONE_FILLER`, `LIB_ID`), so the KiCad 8 PPA is required.

### Install (needs sudo)

```bash
sudo add-apt-repository -y ppa:kicad/kicad-8.0-releases
sudo apt update
sudo apt install -y kicad
```

If `add-apt-repository` is missing:

```bash
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:kicad/kicad-8.0-releases
sudo apt update && sudo apt install -y kicad
```

### Verify the binding imports into the system Python

```bash
python3 -c "import pcbnew; print('pcbnew', pcbnew.GetBuildVersion())"
```

Expected output is a version starting with `8.` — e.g. `(8.0.x)`.

Confirm the dashboard picks it up:

```bash
python3 run_web.py &
curl -s http://localhost:5000/api/status | python3 -m json.tool
# "pcbnew_available": true
```

### If `import pcbnew` still fails after installing

The bindings may have landed in a directory your Python does not scan. Point at them:

```bash
sudo find / -name "pcbnew.py" -o -name "_pcbnew*.so" 2>/dev/null | head

export PYTHONPATH=/usr/lib/python3/dist-packages:$PYTHONPATH
python3 -c "import pcbnew; print('pcbnew', pcbnew.GetBuildVersion())"
```

Make it permanent in the venv by adding a `.pth` file:

```bash
echo "/usr/lib/python3/dist-packages" >> .venv/lib/python3.10/site-packages/kicad.pth
```

### Launch the GUI (optional)

```bash
kicad              # full suite
pcbnew             # PCB editor alone
```

### Why not Flatpak?

```bash
flatpak install flathub org.kicad.kicad
```

Flatpak KiCad works for the **GUI**, but its Python bindings live inside the
sandbox and are invisible to system `python3`. The Flask app would keep reporting
`pcbnew_available: false`. Only the system install makes `import pcbnew` work.

---

## 3. Run the Webapp

All commands are run from the **project root**.

### Quick start

```bash
cd /home/elijah/Documents/GitHub/pcb-designer-ai-agent
python3 run_web.py
```

Open <http://localhost:5000>.

### All the ways to start it

| Method | Command | Notes |
|--------|---------|-------|
| Launcher script | `python3 run_web.py` | From the project root |
| Direct | `python3 app.py` | From the project root |
| Custom port | `python3 run_web.py --port 8080` | `--port` is honoured by both |
| Custom host | `python3 run_web.py --host 127.0.0.1` | Localhost only |
| Debug mode | `python3 run_web.py --debug` | Flask debug, no reloader |
| Makefile | `make web` | Equivalent to `python3 run_web.py` |
| Makefile debug | `make web-debug` | Equivalent to `python3 run_web.py --debug` |

Both entry points accept the same flags:

```bash
python3 run_web.py --host 0.0.0.0 --port 8080 --debug
python3 app.py     --host 0.0.0.0 --port 8080 --debug
```

### Bind to the LAN / containers

```bash
python3 run_web.py --host 0.0.0.0 --port 5000
# then reach it from another machine at http://<your-ip>:5000
```

### Run in the background

```bash
nohup python3 run_web.py --port 5000 > /tmp/pcbai-web.log 2>&1 &

# stop it again
pkill -f "run_web.py"
```

### Run under a process manager / WSGI

```bash
pip install gunicorn
gunicorn --bind 0.0.0.0:5000 "app:app"
```

`app:app` works because [`app.py`](app.py) exposes a module-level `app` object.

---

## 4. Environment Variables

Copy the template and edit it, or export the variables in your shell:

```bash
cd /home/elijah/Documents/GitHub/pcb-designer-ai-agent
cp web/.env.example .env
```

The app does not auto-load `.env`; export the values you need:

```bash
export $(grep -v '^#' web/.env.example | grep -v '^$' | xargs)
```

Or set them individually:

```bash
export PCB_AI_PORT=5000
export PCB_AI_HOST=0.0.0.0
export PCB_AI_LLM_PROVIDER=poolside

export OPENROUTER_API_KEY=your-key    # poolside / openrouter
export OPENAI_API_KEY=your-key
export ANTHROPIC_API_KEY=your-key
export GEMINI_API_KEY=your-key

export LMSTUDIO_API_URL=http://localhost:1234/v1   # local LMStudio
export OLLAMA_API_URL=http://localhost:11434       # local Ollama
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `PCB_AI_LLM_PROVIDER` | `poolside` | Default provider for pipeline calls |
| `PCB_AI_MODEL` | — | Generic model override |
| `PCB_AI_PORT` | `5000` | Port when no `--port` flag is given |
| `PCB_AI_HOST` | `0.0.0.0` | Host when no `--host` flag is given |
| `OPENROUTER_API_KEY` | — | Required for poolside / openrouter |
| `OPENAI_API_KEY` | — | Required for the OpenAI provider |
| `ANTHROPIC_API_KEY` | — | Required for the Anthropic provider |
| `GEMINI_API_KEY` | — | Required for the Gemini provider |
| `LMSTUDIO_API_URL` | `http://localhost:1234/v1` | Local LMStudio endpoint |
| `OLLAMA_API_URL` | `http://localhost:11434` | Local Ollama endpoint |

> Command-line flags (`--host`, `--port`) take precedence over env vars.

---

## 5. Health Checks

```bash
# Is it up?
curl http://localhost:5000/api/status

# Which providers have keys configured?
curl http://localhost:5000/api/providers

# Per-provider usage stats
curl http://localhost:5000/api/providers/metrics

# Local BOM catalog
curl http://localhost:5000/api/catalog
```

`/api/status` returns the resolved paths, so it also confirms the move:

```json
{
  "status": "ok",
  "version": "1.2.0",
  "pcbnew_available": false,
  "design_compiler": true,
  "bom_generator": true,
  "llm_provider_factory": true,
  "default_provider": "poolside",
  "output_dir": "/home/elijah/Documents/GitHub/pcb-designer-ai-agent/web/outputs"
}
```

---

## 6. API Usage

### Generate a BOM

```bash
curl -X POST http://localhost:5000/api/bom \
  -H "Content-Type: application/json" \
  -d '{"description": "ESP32 board with USB-C and a buck converter", "provider": "poolside"}'
```

### Run the full pipeline (async)

```bash
curl -X POST http://localhost:5000/api/design \
  -H "Content-Type: application/json" \
  -d '{"description": "STM32 sensor node with BME280 and USB-C", "provider": "poolside"}'

# → {"task_id": "a1b2c3d4e5f6", "status": "running", ...}
```

### Poll the task

```bash
curl http://localhost:5000/api/task/<task_id>

# poll in a loop until status is "done" or "error"
TASK_ID=a1b2c3d4e5f6
until [ "$(curl -s http://localhost:5000/api/task/$TASK_ID | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')" != "running" ]; do
  echo "still running..."; sleep 3
done
curl -s http://localhost:5000/api/task/$TASK_ID
```

### Resume from a checkpoint with a different provider

```bash
curl -X POST http://localhost:5000/api/resume/<task_id> \
  -H "Content-Type: application/json" \
  -d '{"provider": "lmstudio"}'
```

### Generate a single footprint

```bash
# SMD resistor/capacitor
curl -X POST http://localhost:5000/api/footprint \
  -H "Content-Type: application/json" \
  -d '{"type": "smd_rc", "name": "R_0805", "body_l": 2.0, "body_w": 1.25, "pad_l": 1.15, "pad_w": 1.4}'

# SOIC
curl -X POST http://localhost:5000/api/footprint \
  -H "Content-Type: application/json" \
  -d '{"type": "soic", "name": "SOIC-8", "pins": 8, "pitch": 1.27, "body_l": 4.9, "body_w": 3.9, "pad_l": 1.5, "pad_w": 0.6}'

# QFN
curl -X POST http://localhost:5000/api/footprint \
  -H "Content-Type: application/json" \
  -d '{"type": "qfn", "name": "QFN-32", "pins": 32, "pitch": 0.5, "body_l": 5.0, "body_w": 5.0, "pad_l": 0.85, "pad_w": 0.25}'

# QFP
curl -X POST http://localhost:5000/api/footprint \
  -H "Content-Type: application/json" \
  -d '{"type": "qfp", "name": "QFP-64", "pins": 64, "pitch": 0.5, "body_l": 10.0, "body_w": 10.0, "pad_l": 1.5, "pad_w": 0.3}'
```

### Download a generated artifact

```bash
curl -OJ http://localhost:5000/api/download/bom.json
curl -OJ http://localhost:5000/api/download/schematic.kicad_sch
curl -OJ http://localhost:5000/api/download/board.kicad_pcb
```

Artifacts land in [`web/outputs/<task_id>/`](web/outputs).

---

## 7. Endpoint Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Dashboard UI |
| `GET` | `/api/status` | Health check + environment diagnostics |
| `GET` | `/api/providers` | Available providers + key status |
| `GET` | `/api/providers/metrics` | Per-provider usage metrics |
| `GET` | `/api/catalog` | Local BOM catalog |
| `POST` | `/api/bom` | BOM from a description |
| `POST` | `/api/design` | Full pipeline (async, returns `task_id`) |
| `GET` | `/api/task/<task_id>` | Poll task status |
| `POST` | `/api/resume/<task_id>` | Resume from checkpoint |
| `POST` | `/api/footprint` | Generate one `.kicad_mod` |
| `GET` | `/api/download/<file>` | Download a generated artifact |

---

## 8. Tests

```bash
# Unit tests
make test

# Plugin smoke tests
make smoke

# Explicit pytest invocation
cd anna-app/executas/pcb-designer && PYTHONPATH=. pytest ../../../../tests/ -v
```

---

## 9. CLI (non-web) equivalents

```bash
# Full pipeline to a build directory
make cli-design

# BOM only
make cli-bom

# Direct invocation
cd anna-app/executas/pcb-designer
PYTHONPATH=. python3 -m pcbai.pipeline.cli design "STM32 sensor board with BME280" --out build
PYTHONPATH=. python3 -m pcbai.pipeline.cli bom "ESP32 board with USB-C and buck converter" --out build
```

---

## 10. Cleanup

```bash
make clean

# or by hand
rm -rf web/outputs/
rm -rf anna-app/executas/pcb-designer/build/
find . -name "__pycache__" -type d -exec rm -rf {} +
```

---

## 11. Troubleshooting

### `ModuleNotFoundError: No module named 'flask'`

```bash
pip install flask
```

### `ModuleNotFoundError: No module named 'pcbai'`

Run from the project root. [`app.py`](app.py) adds
`anna-app/executas/pcb-designer` to `sys.path` itself, so no `PYTHONPATH` is needed:

```bash
cd /home/elijah/Documents/GitHub/pcb-designer-ai-agent
python3 app.py
```

### `Address already in use` / port 5000 busy

```bash
python3 run_web.py --port 8080

# or free the port
lsof -ti:5000 | xargs kill -9
```

### `TemplateNotFound: index.html`

The app must be started from the project root, or via `run_web.py`, so the
`web/templates` and `web/static` paths resolve. Confirm with:

```bash
curl http://localhost:5000/api/status
```

### `pcbnew_available: false`

Expected without KiCad installed. The pipeline falls back to a placeholder PCB
and still produces the BOM and schematic.

Ubuntu's default repos only ship **KiCad 6**, but this code needs the KiCad 7/8
API. Install **KiCad 8** via its PPA:

```bash
sudo add-apt-repository -y ppa:kicad/kicad-8.0-releases
sudo apt update && sudo apt install -y kicad
python3 -c "import pcbnew; print('pcbnew OK', pcbnew.GetBuildVersion())"
```

Full detail, including why Flatpak will not work: see
[Install KiCad 8](#2-install-kicad-8-pcbnew--optional).

### Provider errors

Check that a key is configured and the provider is reachable:

```bash
curl http://localhost:5000/api/providers
curl http://localhost:5000/api/providers/metrics
```

Switch provider per request:

```bash
curl -X POST http://localhost:5000/api/design \
  -H "Content-Type: application/json" \
  -d '{"description": "Buck converter board", "provider": "lmstudio", "fallback": ["ollama"]}'