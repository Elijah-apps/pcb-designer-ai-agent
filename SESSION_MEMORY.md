# PCB Designer AI Agent - Session Memory

**Date:** October 2, 2026
**Project Status:** v1.2.0 — Poolside LLM provider, graceful pcbnew handling, webapp fixes

## Summary of Fixes

### Anna Review Fixes (v1.1.0 — already complete, preserved)
1. **Fixed hardcoded board** — `design_compiler.py` rewritten to be prompt-driven
2. **Fixed BOM/footprint mismatch** — `_footprint_str()` derives footprint from BOM package field
3. **About section honesty** — Local Agent only, KiCad 8 required, no LPKF claims
4. **Silent pcbnew failure** — pre-flight check + clear error message

### New Work (v1.2.0)

#### Poolside LLM Provider
- Added `PoolsideProvider` class in `pcbai/llm/provider.py` — uses OpenRouter API with poolside models (`poolside/laguna-s-2.1:free`, `poolside/laguna-2-7b`)
- Set as default provider (`PCB_AI_LLM_PROVIDER` defaults to `"poolside"`) in both `provider.py` and `config.py`
- LMStudio still available via `PCB_AI_LLM_PROVIDER=lmstudio`
- Cleaned up verbose `print("DEBUG: ...")` statements in `GeminiProvider`

#### Graceful pcbnew Handling (no more crashes)
- `compile_design()` now returns **partial results** (BOM + schematic + placeholder PCB) when pcbnew is unavailable — includes `pcb_error` field
- CLI `design` command shows friendly ⚠ message instead of traceback
- Plugin `_tool_full_pipeline` passes `pcb_error` back to webapp, returns `success: True` with `pipeline_steps_completed: 3` (instead of crashing)

#### Local BOM Catalog (no LLM required)
- `_LOCAL_CATALOG` with 22 keyword→component entries (ESP32, STM32, USB-C, LDO, BME280, SSD1306, MCP73831, etc.)
- `_add_supporting_components()` — auto adds decoupling caps, pull-ups, CC resistors
- `_LOCAL_PACKAGES` table — parametric footprint data for all common packages
- `requirements_parser.py` keyword fallback (keyword matching when LLM unavailable)

#### Passive Component Handling
- `_ref_prefix(pkg, category)` — 0805 capacitor → `C`, 0805 resistor → `R`, LED → `D`
- `_footprint_str(pkg, category)` — disambiguates SMD passives and LEDs by category

#### Protocol Integrity
- No `print()` to stdout — all debug output via `log()` (stderr)
- `_REAL_STDOUT` saved reference ensures JSON-RPC messages always reach the transport
- `contextlib.redirect_stdout(sys.stderr)` wraps tool execution in `handle()`

#### Webapp Fixes (`anna-app/bundle/app.js`)
- Fixed `result.bom.bom.forEach()` → `result.bom.forEach()` (BOM is a flat array, not nested)
- Added SOT-23-5 pad generation support to both PCB and footprint generators

### Version
- `executa.json` and plugin MANIFEST: **v1.2.0**

## Files Changed
- `anna-app/executas/pcb-designer/plugin.py` — stdout redirect, _REAL_STDOUT, graceful pipeline
- `anna-app/executas/pcb-designer/pcbai/llm/provider.py` — PoolsideProvider, default change, debug cleanup
- `anna-app/executas/pcb-designer/pcbai/steps/design_compiler.py` — graceful pcbnew, local packages, ref prefix
- `anna-app/executas/pcb-designer/pcbai/steps/bom_generator.py` — local catalog, supporting components
- `anna-app/executas/pcb-designer/pcbai/core/config.py` — default provider → poolside
- `anna-app/executas/pcb-designer/pcbai/pipeline/cli.py` — duplicate import removed, graceful pcbnew
- `anna-app/executas/pcb-designer/pcbai/steps/datasheet_package_extractor.py` — duplicate/unused imports removed
- `anna-app/executas/pcb-designer/test_harness.py` — graceful pcbnew handling in smoke tests
- `anna-app/bundle/app.js` — fixed BOM structure access, added SOT-23-5 pads
- New files: `pcbai/core/__init__.py`, `pcbai/llm/__init__.py`, `pcbai/pipeline/__init__.py`, `pcbai/steps/__init__.py`, `tests/test_bom_generator_local.py`

## How to Run

### Unit tests
```bash
cd anna-app/executas/pcb-designer
PYTHONPATH=. pytest tests/ -v
```

### Smoke tests (plugin JSON-RPC)
```bash
PYTHONPATH=. python3 test_harness.py --smoke
```

### CLI — BOM only
```bash
PYTHONPATH=. python3 -m pcbai.pipeline.cli bom "ESP32 board with USB-C and LDO" --out build
```

### CLI — Full pipeline (BOM + schematic + placeholder PCB)
```bash
PYTHONPATH=. python3 -m pcbai.pipeline.cli design "ESP32 WiFi board" --out build
```

### CLI — Generate a footprint
```bash
PYTHONPATH=. python3 -m pcbai.pipeline.cli footprint --type qfn --name QFN-32-5x5 \
  --pins 32 --pitch 0.5 --body-l 5.0 --body-w 5.0 --pad-l 0.8 --pad-w 0.3 --out build
```

### With LLM (poolside/Ollama/etc.)
```bash
export PCB_AI_LLM_PROVIDER=poolside   # or lmstudio, openai, ollama, openrouter, anthropic, gemini
export OPENROUTER_API_KEY=your-key    # for poolside via OpenRouter
PYTHONPATH=. python3 -m pcbai.pipeline.cli design "STM32 sensor board" --out build
```

### Web Dashboard (v1.2.0 — moved to project root)
```bash
cd pcb-designer-ai-agent/       # project root
python3 run_web.py              # → http://localhost:5000
```

The Flask app is now at `pcb-designer-ai-agent/web/` (moved from `anna-app/executas/pcb-designer/web/`).
Path resolution: `parents[1]` → project root, then `anna-app/executas/pcb-designer/` for pcbai imports.
All endpoints tested: `GET /`, `GET /api/status`, `GET /api/providers`, `POST /api/bom`, `POST /api/design`, `POST /api/resume/<id>`, `GET /api/task/<id>`.

## Provider Context Manager (v1.2.0)

### ProviderContext — easy switching + failover
```python
from pcbai.llm.context import ProviderContext

# Switch providers for a single call
with ProviderContext("openai", model="gpt-4o") as ctx:
    r = ctx.provider.chat([{"role": "user", "content": "Analyze this schematic"}])

# Fallback chain — auto-tries next on failure
with ProviderContext("poolside", fallback=["lmstudio", "openai"]) as ctx:
    result = ctx.complete("Design a buck converter")
```

### ProviderTracker — per-provider metrics
```python
from pcbai.llm.context import provider_session, provider_tracker

# provider_session decorator tracks call count, success rate, latency
@provider_session("openai")
def my_call(ctx):
    return ctx.provider.complete("Hello")

# Get metrics
metrics = provider_tracker.get_metrics()
recommended = provider_tracker.get_recommended_provider()
```

### PipelineCheckpoint — save/resome pipeline steps
```python
from pcbai.llm.context import PipelineCheckpoint

cp = PipelineCheckpoint("web/outputs/<task_id>/checkpoints")
cp.save("bom", bom_data)
cp.exists("bom")
cp.list_steps()   # ["requirements", "bom"]
data = cp.load("bom")  # Resume from saved state
```

### API: Resume with different provider
```bash
curl -X POST http://localhost:5000/api/resume/<task_id> \
  -H "Content-Type: application/json" \
  -d '{"provider": "lmstudio"}'
```

## Available Providers (7 total)
`poolside` (default), `openrouter`, `openai`, `anthropic`, `gemini`, `lmstudio`, `ollama`

Poolside uses OpenRouter under the hood (`poolside/laguna-s-2.1:free`). Falls back to local catalog when `OPENROUTER_API_KEY` not set.
