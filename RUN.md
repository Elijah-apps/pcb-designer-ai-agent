# PCB Designer AI Agent — Run Commands Reference

All commands to run, test, and interact with the PCB Designer AI Agent.
The Flask web app lives at the **project root** as `app.py` (templates, static
assets and outputs stay in `web/`). Full command reference: [`RUN_WEBAPP.md`](RUN_WEBAPP.md).

> **Prerequisites:** Python 3.10+, Flask 3.1+, pypdf installed. KiCad/pcbnew optional (placeholder PCB generated if missing). No LLM API key = local catalog fallback.

---

## Quick Start

```bash
# 1. Terminal: Start the web dashboard
cd pcb-designer-ai-agent/
python3 run_web.py

# 2. Browser: Open http://localhost:5000
#    → Dashboard UI with BOM table, provider metrics, download links
```

That's it. The app auto-resolves `pcbai` imports from `anna-app/executas/pcb-designer/`.

---

## Starting the Web Server

| Method | Command | Description |
|--------|---------|-------------|
| Quick script | `python3 run_web.py` | From project root |
| Direct | `python3 app.py` | From project root |
| Makefile | `make web` | Shortcut (project root) |
| Debug mode | `python3 run_web.py --debug` | Flask debug mode |
| Custom port | `python3 run_web.py --port 8080` | Override port |
| Custom host | `python3 run_web.py --host 0.0.0.0` | Bind to all interfaces |

### Environment Variables for the Web Server

```bash
export PCB_AI_PORT=5000                # Flask port (default 5000)
export PCB_AI_HOST=0.0.0.0            # Flask bind host
export PCB_AI_LLM_PROVIDER=poolside     # Default provider for pipeline
export OPENROUTER_API_KEY=your-key      # For poolside/openrouter providers
export OPENAI_API_KEY=your-key          # For OpenAI provider
export ANTHROPIC_API_KEY=your-key       # For Anthropic provider
export GEMINI_API_KEY=your-key          # For Gemini provider
export LMSTUDIO_API_URL=http://localhost:1234/v1  # For local LMStudio
export OLLAMA_API_URL=http://localhost:11434      # For local Ollama
```

See `web/.env.example` for the full list.

---

## Web API Endpoints

### Dashboard
```bash
# Web UI
curl http://localhost:5000/

# Status / health check
curl http://localhost:5000/api/status
# → {"status":"ok","pcbnew_available":false,...,"output_dir":"web/outputs"}

# Available providers
curl http://localhost:5000/api/providers
# → {"current":"poolside","available":[{"name":"poolside","configured":false},...]}

# Provider usage metrics
curl http://localhost:5000/api/providers/metrics
# → {"active_provider":"poolside","recommended":"lmstudio","providers":{...}}
```

### BOM Generation
```bash
# Simple BOM (local catalog, no API key needed)
curl -X POST http://localhost:5000/api/bom \
  -H "Content-Type: application/json" \
  -d '{"description":"ESP32 board with USB-C and buck converter"}'
# → {"success":true,"component_count":9,"bom":[{"ref":"J1","mpn":"USB4085-GF-A",...}]}

# BOM with specific provider
curl -X POST http://localhost:5000/api/bom \
  -H "Content-Type: application/json" \
  -d '{"description":"USB to serial adapter","provider":"poolside"}'

# List local BOM catalog (reference)
curl http://localhost:5000/api/catalog
```

### Full Pipeline (BOM + Schematic + PCB + ZIP)
```bash
# Start async full pipeline
TASK=$(curl -s -X POST http://localhost:5000/api/design \
  -H "Content-Type: application/json" \
  -d '{"description":"STM32 sensor board with BME280 and LiPo charger"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")

# Poll for result (task_id = $TASK from above)
curl http://localhost:5000/api/task/$TASK
# → {"status":"done","result":{"bom":[...],"files":{"board.kicad_pcb":...,"bom.json":...,"schematic.kicad_sch":...}}}

# Pipeline with specific provider
curl -X POST http://localhost:5000/api/design \
  -H "Content-Type: application/json" \
  -d '{"description":"Robotics board","provider":"openai"}'
```

### Resume from Checkpoint (with different provider)
```bash
# Resume a completed task with a different provider
curl -X POST http://localhost:5000/api/resume/<task_id> \
  -H "Content-Type: application/json" \
  -d '{"provider":"lmstudio"}'
```

### Footprint Generation
```bash
# Generate a single KiCad footprint (.kicad_mod)
curl -X POST http://localhost:5000/api/footprint \
  -H "Content-Type: application/json" \
  -d '{"type":"qfn","name":"QFN-32","pins":32,"pitch":0.5,"body_l":5.0,"body_w":5.0,
       "pad_l":0.8,"pad_w":0.3,"ep_l":3.0,"ep_w":3.0}'
# → {"success":true,"path":".../QFN-32.kicad_mod","content":"(module ...)"}

# Other types: smd_rc, soic, qfp
curl -X POST http://localhost:5000/api/footprint \
  -H "Content-Type: application/json" \
  -d '{"type":"soic","name":"SOIC-8","pins":8,"pitch":1.27,"body_l":5.0,"body_w":4.0,
       "pad_l":0.8,"pad_w":0.4}'
```

### File Download
```bash
# Download a generated artifact
curl -OJ http://localhost:5000/api/download/bom.json
curl -OJ http://localhost:5000/api/download/schematic.kicad_sch
curl -OJ http://localhost:5000/api/download/board.kicad_pcb
curl -OJ http://localhost:5000/api/download/pcb_project.zip
```

---

## CLI Commands (direct, no web server)

```bash
cd anna-app/executas/pcb-designer/

# BOM only (local catalog, no API key needed)
PYTHONPATH=. python3 -m pcbai.pipeline.cli bom "ESP32 board with USB-C and buck converter" --out build

# Full pipeline (BOM + schematic + placeholder PCB)
PYTHONPATH=. python3 -m pcbai.pipeline.cli design "STM32 sensor board with BME280" --out build

# Generate a QFN footprint
PYTHONPATH=. python3 -m pcbai.pipeline.cli footprint --type qfn --name QFN-32-5x5 \
  --pins 32 --pitch 0.5 --body-l 5.0 --body-w 5.0 --pad-l 0.8 --pad-w 0.3 --out build

# Generate a SOIC footprint
PYTHONPATH=. python3 -m pcbai.pipeline.cli footprint --type soic --name SOIC-8 \
  --pins 8 --pitch 1.27 --body-l 5.0 --body-w 4.0 --pad-l 0.8 --pad-w 0.4 --out build

# List available footprints
PYTHONPATH=. python3 -m pcbai.pipeline.cli footprint --list
```

---

## Testing

```bash
# Unit tests (20 tests)
cd anna-app/executas/pcb-designer/
PYTHONPATH=. python3 -m pytest ../../../../tests/ -v

# Or from project root
PYTHONPATH=anna-app/executas/pcb-designer:. python3 -m pytest tests/ -v

# Smoke tests (plugin JSON-RPC, 10 tests)
cd anna-app/executas/pcb-designer/
PYTHONPATH=. python3 test_harness.py --smoke

# Quick smoke (just check plugin protocol)
PYTHONPATH=. python3 test_harness.py
```

---

## Makefile Shortcuts

```bash
make help         # List all commands
make web          # Start web dashboard
make web-debug    # Start in debug mode
make test         # Run unit tests
make smoke        # Run plugin smoke tests
make cli-design   # Full pipeline via CLI
make cli-bom      # BOM only via CLI
make clean        # Remove generated outputs
```

---

## Output Structure

```
web/outputs/
├── <task_id>/                     # One dir per pipeline run
│   ├── bom.json                   # BOM as JSON array
│   ├── schematic.kicad_sch        # KiCad schematic
│   ├── board.kicad_pcb            # KiCad PCB layout (or placeholder)
│   ├── project.kicad_pro          # KiCad project file
│   ├── pcb_project.zip            # All files bundled
│   └── checkpoints/               # Pipeline checkpoints
│       ├── step_requirements.json # Parsed requirements
│       └── step_bom.json          # Generated BOM
└── footprints/                    # Generated .kicad_mod files
    └── QFN-32.kicad_mod
```

Download any file via `GET /api/download/<filename>` — the server searches all task dirs.

---

## Provider Switching (at runtime)

The web UI has a provider dropdown. Programmatically:

```python
# Python — ProviderContext with failover
from pcbai.llm.context import ProviderContext

with ProviderContext("poolside", fallback=["lmstudio", "openai"]) as ctx:
    result = ctx.complete("Design a buck converter")

# Python — ProviderTracker metrics
from pcbai.llm.context import ProviderTracker
tracker = ProviderTracker()
print(tracker.get_metrics())
print(tracker.get_recommended())  # e.g. "lmstudio" if openrouter has errors

# Python — Resume from checkpoint
from pcbai.llm.context import PipelineCheckpoint
cp = PipelineCheckpoint("web/outputs/<task_id>/checkpoints")
cp.save("bom", bom_data)       # Save a step
cp.exists("bom")               # Check if step was saved
cp.list_steps()                # List completed steps
resumed_bom = cp.load("bom")   # Load saved step
```

Available providers: `poolside` (default via OpenRouter), `openrouter`, `lmstudio`, `ollama`, `openai`, `anthropic`, `gemini`.
