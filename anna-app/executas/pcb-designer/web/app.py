#!/usr/bin/env python3
"""
Flask web API + dashboard for the PCB Designer AI Agent.

Provides a browser-based interface to the pcbai pipeline:
  POST /api/design       — full pipeline (prompt → BOM → schematic → PCB → ZIP)
  POST /api/bom          — generate BOM from description
  POST /api/footprint     — generate a single KiCad footprint
  GET  /api/status       — health check + environment diagnostics
  GET  /api/providers    — list available LLM providers
  GET  /api/download/<f> — download a generated artifact
  GET  /                — dashboard UI

Run:  cd web && python3 app.py
"""
from __future__ import annotations

import os
import sys
import json
import base64
import traceback
import tempfile
import threading
import time
from pathlib import Path

from flask import (
    Flask, request, jsonify, render_template,
    send_from_directory, send_file, abort, Response
)

# Ensure the pcbai package is importable regardless of CWD
_project_root = Path(__file__).resolve().parents[1]  # …/pcb-designer/
sys.path.insert(0, str(_project_root))
os.environ.setdefault("PYTHONPATH", str(_project_root))

app = Flask(__name__,
            template_folder="templates",
            static_folder="static")

# Directory for user-generated output (cleaned on restart)
OUTPUT_DIR = _project_root / "web" / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# In-memory task registry (task_id → {"status", "result", "error", "log"})
_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()

# ── Import pcbai modules lazily (they may need PYTHONPATH) ──────────────────
try:
    from pcbai.steps.design_compiler import compile_design
    _HAS_DESIGN_COMPILER = True
except Exception:
    _HAS_DESIGN_COMPILER = False

try:
    from pcbai.steps.requirements_parser import parse_requirements
    from pcbai.steps.bom_generator import generate_bom, _LOCAL_CATALOG
    _HAS_BOM = True
except Exception:
    _HAS_BOM = False

try:
    from pcbai.llm.provider import get_provider
    _HAS_PROVIDER = True
except Exception:
    _HAS_PROVIDER = False

try:
    import pcbnew  # noqa: F401
    _HAS_PCBNEW = True
except Exception:
    _HAS_PCBNEW = False


def _task_id() -> str:
    import hashlib
    return hashlib.md5(f"{time.time()}{threading.get_ident()}".encode()).hexdigest()[:12]


def _set_provider(provider_name: str | None) -> None:
    """Set PCB_AI_LLM_PROVIDER env var for this request's subprocess calls."""
    if provider_name:
        os.environ["PCB_AI_LLM_PROVIDER"] = provider_name.lower()


# ─────────────────────────────────────────────────────────────────────────────
# API — Status / diagnostics
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    """Health check + environment diagnostics."""
    return jsonify({
        "status": "ok",
        "version": "1.2.0",
        "pcbnew_available": _HAS_PCBNEW,
        "design_compiler": _HAS_DESIGN_COMPILER,
        "bom_generator": _HAS_BOM,
        "llm_provider_factory": _HAS_PROVIDER,
        "default_provider": os.getenv("PCB_AI_LLM_PROVIDER", "poolside"),
        "output_dir": str(OUTPUT_DIR),
    })


@app.route("/api/providers")
def api_providers():
    """List available LLM providers and whether an API key is configured."""
    providers = []
    for name in ["poolside", "lmstudio", "ollama", "openai", "openrouter", "anthropic", "gemini"]:
        key = {
            "poolside": "OPENROUTER_API_KEY",
            "lmstudio": "LMSTUDIO_URL",
            "ollama": "OLLAMA_URL",
            "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
        }[name]
        configured = bool(os.getenv(key))
        providers.append({"name": name, "api_key_env": key, "configured": configured})

    return jsonify({
        "current": os.getenv("PCB_AI_LLM_PROVIDER", "poolside"),
        "available": providers,
    })


# ─────────────────────────────────────────────────────────────────────────────
# API — BOM generation
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/bom", methods=["POST"])
def api_bom():
    """Generate a BOM from a natural-language description."""
    if not _HAS_BOM:
        return jsonify({"error": "bom_generator module not available"}), 500

    data = request.get_json(silent=True) or {}
    description = data.get("description", "")
    provider = data.get("provider")
    _set_provider(provider)

    try:
        requirements = parse_requirements(description)
        bom = generate_bom(requirements)
        # Enrich with ref designators and footprints
        from pcbai.steps.design_compiler import _ref_prefix, _footprint_str
        counters: dict[str, int] = {}
        for entry in bom:
            pkg = entry.get("package", "0805")
            cat = entry.get("category", "")
            prefix = _ref_prefix(pkg, cat)
            counters[prefix] = counters.get(prefix, 0) + 1
            entry["ref"] = f"{prefix}{counters[prefix]}"
            entry["footprint"] = _footprint_str(pkg, cat)

        return jsonify({
            "success": True,
            "description": description,
            "keywords": requirements.get("keywords", []),
            "bom": bom,
            "component_count": len(bom),
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


# ─────────────────────────────────────────────────────────────────────────────
# API — Full pipeline (async)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/design", methods=["POST"])
def api_design():
    """Run the full pipeline asynchronously and return a task_id for polling."""
    data = request.get_json(silent=True) or {}
    description = data.get("description", "")
    provider = data.get("provider")
    _set_provider(provider)

    task_id = _task_id()
    with _tasks_lock:
        _tasks[task_id] = {
            "status": "running",
            "description": description,
            "result": None,
            "error": None,
            "log": [],
        }

    def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                result = compile_design(description, tmpdir)
                out_dir = OUTPUT_DIR / task_id
                out_dir.mkdir(parents=True, exist_ok=True)

                file_map = {}
                if result.get("bom"):
                    bom_json = json.dumps(result["bom"], indent=2)
                    fpath = out_dir / "bom.json"
                    fpath.write_text(bom_json)
                    file_map["bom.json"] = str(fpath)

                for fkey, fname in [("sch", "schematic.kicad_sch"),
                                     ("pcb", "board.kicad_pcb")]:
                    src = result.get(fkey)
                    if src and os.path.exists(src):
                        dst = out_dir / fname
                        dst.write_text(Path(src).read_text())
                        file_map[fname] = str(dst)

                result["files"] = file_map
                result["output_dir"] = str(out_dir)

                with _tasks_lock:
                    _tasks[task_id]["status"] = "done"
                    _tasks[task_id]["result"] = result

            except Exception as e:
                with _tasks_lock:
                    _tasks[task_id]["status"] = "error"
                    _tasks[task_id]["error"] = str(e)
                    _tasks[task_id]["log"] = traceback.format_exc().splitlines()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"task_id": task_id, "status": "running",
                     "message": "Pipeline started. Poll /api/task/<task_id> for status."})


@app.route("/api/task/<task_id>")
def api_task(task_id: str):
    """Get the status of an async task."""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    return jsonify({
        "task_id": task_id,
        "status": task["status"],
        "description": task.get("description"),
        "result": task.get("result"),
        "error": task.get("error"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# API — Footprint generation
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/footprint", methods=["POST"])
def api_footprint():
    """Generate a single KiCad footprint (.kicad_mod)."""
    data = request.get_json(silent=True) or {}
    ftype = data.get("type", "smd_rc")
    name = data.get("name", "custom_fp")
    outdir = data.get("outdir", str(OUTPUT_DIR / "footprints"))

    try:
        if ftype == "smd_rc":
            from pcbai.steps.footprint_generator import SmdRcParams, write_kicad_mod_smd_rc
            params = SmdRcParams(
                name=name,
                body_l=data["body_l"], body_w=data["body_w"],
                pad_l=data["pad_l"], pad_w=data["pad_w"],
                gap=data.get("gap", 0.0),
            )
            path = write_kicad_mod_smd_rc(outdir, params)
        elif ftype == "soic":
            from pcbai.steps.footprint_generator import SoicParams, write_kicad_mod_soic
            params = SoicParams(
                name=name,
                pins=data["pins"], pitch=data["pitch"],
                body_l=data["body_l"], body_w=data["body_w"],
                pad_l=data["pad_l"], pad_w=data["pad_w"],
                row_offset=data.get("row_offset", 0.0),
            )
            path = write_kicad_mod_soic(outdir, params)
        elif ftype == "qfn":
            from pcbai.steps.footprint_qfn_qfp import QfnParams, generate_qfn, KiCadModuleWriter
            params = QfnParams(
                name=name,
                pins=data["pins"], pitch=data["pitch"],
                body_l=data["body_l"], body_w=data["body_w"],
                pad_l=data["pad_l"], pad_w=data["pad_w"],
                ep_l=data.get("ep_l"), ep_w=data.get("ep_w"),
            )
            path = KiCadModuleWriter(outdir).write(name, generate_qfn(params))
        elif ftype == "qfp":
            from pcbai.steps.footprint_qfn_qfp import QfpParams, generate_qfp, KiCadModuleWriter
            params = QfpParams(
                name=name,
                pins=data["pins"], pitch=data["pitch"],
                body_l=data["body_l"], body_w=data["body_w"],
                pad_l=data["pad_l"], pad_w=data["pad_w"],
                gullwing_ext=data.get("gullwing_ext", 0.0),
            )
            path = KiCadModuleWriter(outdir).write(name, generate_qfp(params))
        else:
            return jsonify({"error": f"Unknown footprint type: {ftype}"}), 400

        return jsonify({
            "success": True,
            "footprint_type": ftype,
            "path": path,
            "content": Path(path).read_text(),
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


# ─────────────────────────────────────────────────────────────────────────────
# File download
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/download/<path:filename>")
def api_download(filename: str):
    """Download a generated file by filename (searches all output dirs)."""
    # Search in all task output directories
    for task_dir in OUTPUT_DIR.iterdir():
        if task_dir.is_dir():
            fpath = task_dir / filename
            if fpath.exists():
                return send_file(str(fpath), as_attachment=True)
    abort(404)


@app.route("/api/catalog")
def api_catalog():
    """Return the local BOM catalog for the frontend to show available components."""
    try:
        return jsonify(_LOCAL_CATALOG)
    except Exception:
        return jsonify({})


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template("index.html",
                           default_provider=os.getenv("PCB_AI_LLM_PROVIDER", "poolside"))


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PCB_AI_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
