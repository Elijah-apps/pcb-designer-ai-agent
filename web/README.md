# PCB Designer AI Agent — Web Dashboard

A Flask-based web frontend for the PCB Designer AI Agent, providing a browser UI
for hardware design from natural-language prompts.

## Quick Start

```bash
# From the project root (pcb-designer-ai-agent/)
python3 run_web.py
# → opens at http://localhost:5000
```

Or directly:
```bash
cd web
PYTHONPATH=../anna-app/executas/pcb-designer:. python3 app.py
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET /` | Dashboard | Web UI |
| `GET /api/status` | Health check | Environment diagnostics |
| `GET /api/providers` | Provider list | Available LLM providers + key status |
| `GET /api/providers/metrics` | Usage stats | Per-provider metrics |
| `POST /api/bom` | BOM generation | `{"description": "...", "provider": "poolside"}` |
| `POST /api/design` | Full pipeline | Async — returns `task_id` for polling |
| `POST /api/resume/<task_id>` | Resume pipeline | Continue from checkpoint with a different provider |
| `GET /api/task/<task_id>` | Task status | Poll for pipeline completion |
| `POST /api/footprint` | Single footprint | Generate .kicad_mod |
| `GET /api/download/<file>` | Artifact download | Download any generated file |
| `GET /api/catalog` | Component catalog | Local BOM catalog reference |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PCB_AI_LLM_PROVIDER` | `poolside` | Default LLM provider |
| `PCB_AI_PORT` | `5000` | Flask server port |
| `OPENROUTER_API_KEY` | — | Required for poolside/openrouter providers |
| `OPENAI_API_KEY` | — | Required for OpenAI provider |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic provider |
| `GEMINI_API_KEY` | — | Required for Gemini provider |

## Provider Switching

Switch providers at runtime via the dashboard dropdown, or programmatically:

```python
from pcbai.llm.context import ProviderContext

# Simple switch
with ProviderContext("openai", model="gpt-4o") as ctx:
    response = ctx.provider.chat([{"role": "user", "content": "Analyze this schematic"}])

# Fallback chain — auto-tries next provider on failure
with ProviderContext("poolside", fallback=["lmstudio", "openai"]) as ctx:
    result = ctx.complete("Design a buck converter")
```

## Pipeline Checkpointing

Each pipeline step is saved to disk and can be resumed:

```python
from pcbai.llm.context import PipelineCheckpoint

cp = PipelineCheckpoint("web/outputs/<task_id>/checkpoints")
cp.save("bom", bom_data)
cp.exists("bom")      # True
cp.list_steps()       # ["requirements", "bom"]
cp.load("bom")        # Resume from BOM
```

Resume a task with a different provider via the API:
```bash
curl -X POST http://localhost:5000/api/resume/<task_id> \
  -H "Content-Type: application/json" \
  -d '{"provider": "lmstudio"}'
```

## Provider Metrics

Track usage via the metrics endpoint:
```bash
curl http://localhost:5000/api/providers/metrics
```

Returns per-provider: call count, success rate, average latency, recent errors.

## File Structure

```
pcb-designer-ai-agent/
├── run_web.py                        # Quick-run script
├── web/
│   ├── app.py                        # Flask application + API
│   ├── templates/index.html          # Dashboard UI
│   ├── static/style.css              # Dashboard styling
│   ├── static/app.js                 # Frontend logic
│   └── outputs/                      # Generated artifacts (auto-created)
├── anna-app/executas/pcb-designer/
│   ├── plugin.py                     # Anna plugin (JSON-RPC)
│   ├── pcbai/                        # Core modules (compiles designs)
│   │   ├── llm/                      # Providers + context manager
│   │   ├── steps/                    # Design pipeline steps
│   │   ├── pipeline/                 # CLI entry point
│   │   └── core/                     # Config + logger
│   └── test_harness.py               # Plugin smoke tests
└── tests/                            # Unit tests
```
