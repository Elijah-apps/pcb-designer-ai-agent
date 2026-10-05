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

Run:  python3 app.py          (from the project root)
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
# This file lives at: <project_root>/app.py
# The pcbai package lives at: <project_root>/anna-app/executas/pcb-designer/pcbai/
_project_root = Path(__file__).resolve().parent          # pcb-designer-ai-agent/
_web_dir = _project_root / "web"                        # templates/static/env live here
_pcbai_root = _project_root / "anna-app" / "executas" / "pcb-designer"
sys.path.insert(0, str(_pcbai_root))
sys.path.insert(0, str(_project_root))
os.environ.setdefault("PYTHONPATH", str(_pcbai_root) + os.pathsep + str(_project_root))

# Load web/.env (and a project-root .env) so API keys in web/.env are actually read.
# MUST happen before pcbai.llm.provider is imported — it reads os.environ at import
# time in the dataclass defaults. Real env vars always win over .env values.
_ENV_LOADED = False
try:
    from dotenv import load_dotenv
    _ENV_LOADED = load_dotenv(_project_root / ".env") or load_dotenv(_web_dir / ".env")
except ImportError:
    # python-dotenv is optional — fall back to shell-exported env vars only.
    pass

app = Flask(__name__,
            template_folder=str(_web_dir / "templates"),
            static_folder=str(_web_dir / "static"))

# Directory for user-generated output
OUTPUT_DIR = _web_dir / "outputs"
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
    from pcbai.llm.context import ProviderContext, ProviderTracker, PipelineCheckpoint
    _HAS_CONTEXT = True
except Exception:
    _HAS_CONTEXT = False

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
        "env_file_loaded": _ENV_LOADED,
    })


@app.route("/api/providers")
def api_providers():
    """List available LLM providers and whether an API key is configured."""
    providers = []
    for name in ["poolside", "openrouter", "lmstudio", "ollama", "openai", "anthropic", "gemini"]:
        key = {
            "poolside": "OPENROUTER_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "lmstudio": "LMSTUDIO_URL",
            "ollama": "OLLAMA_URL",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
        }[name]
        configured = bool(os.getenv(key))
        providers.append({"name": name, "api_key_env": key, "configured": configured})

    return jsonify({
        "current": os.getenv("PCB_AI_LLM_PROVIDER", "poolside"),
        "available": providers,
    })


@app.route("/api/providers/metrics")
def api_provider_metrics():
    """Return provider usage metrics from the singleton tracker."""
    tracker = ProviderTracker() if _HAS_CONTEXT else None
    if tracker:
        return jsonify({
            "active_provider": tracker.active_provider,
            "recommended": tracker.get_recommended(),
            "providers": {
                name: {
                    "calls": s.calls,
                    "successes": s.successes,
                    "errors": s.errors,
                    "success_rate": round(s.success_rate, 4),
                    "avg_latency_ms": round(s.avg_latency_ms, 1),
                    "last_used": s.last_used,
                    "recent_errors": s.error_messages[-3:] if s.error_messages else [],
                }
                for name, s in tracker.stats.items()
            },
        })
    return jsonify({"error": "Provider tracking not available"})


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline helpers (with provider context + checkpointing)
# ─────────────────────────────────────────────────────────────────────────────

def _pipeline_with_context(ctx: "ProviderContext", description: str,
                           tmpdir: str, ckpt: "PipelineCheckpoint | None",
                           log_fn) -> dict:
    """Run pipeline using a ProviderContext for provider management + fallback."""
    import pcbai.steps.requirements_parser as rp
    import pcbai.steps.bom_generator as bg
    from pcbai.steps.design_compiler import _ref_prefix, _footprint_str, _build_schematic_from_bom

    # Step 1: Parse requirements
    log_fn(f"Parsing requirements with {ctx.name}...")
    if ckpt and ckpt.exists("requirements"):
        req = ckpt.load("requirements")
        log_fn("Resumed from checkpoint: requirements")
    else:
        try:
            req = rp.parse_requirements(description)
        except Exception as e:
            log_fn(f"LLM parse failed ({e}), using keyword fallback")
            req = rp.parse_requirements(description)
        if ckpt:
            ckpt.save("requirements", req)

    keywords = req.get("keywords", [])
    log_fn(f"Keywords: {keywords}")

    # Step 2: Generate BOM (uses env var set by ProviderContext, falls back to local catalog)
    log_fn("Generating BOM...")
    try:
        bom = bg.generate_bom(req)
    except Exception as e:
        log_fn(f"BOM generation raised ({e}) — retrying with local catalog")
        bom = []
    if not bom:
        log_fn("BOM empty — using local catalog fallback")
        bom = bg.generate_bom(req)

    # Enrich
    counters = {}
    for entry in bom:
        pkg = entry.get("package", "0805")
        cat = entry.get("category", "")
        prefix = _ref_prefix(pkg, cat)
        counters[prefix] = counters.get(prefix, 0) + 1
        entry["ref"] = f"{prefix}{counters[prefix]}"
        entry["footprint"] = _footprint_str(pkg, cat)
        entry.setdefault("description", entry.get("mpn", ""))
    if ckpt:
        ckpt.save("bom", bom)
    log_fn(f"BOM: {len(bom)} components")

    # Step 3: Schematic (no LLM needed)
    sch_path = os.path.join(tmpdir, "schematic.kicad_sch")
    _build_schematic_from_bom(bom, sch_path, description)
    log_fn(f"Schematic → {sch_path}")

    # Step 4: PCB (best-effort — placeholder if pcbnew missing)
    pcb_path = os.path.join(tmpdir, "board.kicad_pcb")
    from pcbai.steps.design_compiler import _build_pcb_from_bom
    pcb_ok = _build_pcb_from_bom(bom, pcb_path, description)

    pcb_error = None
    if not pcb_ok:
        pcb_error = "pcbnew (KiCad 8) not installed — BOM + schematic only"
        log_fn(f"⚠ {pcb_error}")

    # Step 5: Project + ZIP
    pro_path = os.path.join(tmpdir, "project.kicad_pro")
    with open(pro_path, "w") as f:
        f.write('{"board": {"design_settings": {}}}')

    import zipfile
    zip_path = os.path.join(tmpdir, "pcb_project.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in [os.path.join(tmpdir, "bom.json"), sch_path, pcb_path, pro_path]:
            if os.path.exists(fp):
                zf.write(fp, os.path.basename(fp))

    return {
        "bom": bom,
        "sch": sch_path,
        "pcb": pcb_path,
        "gerbers": os.path.join(tmpdir, "gerbers"),
        "zip": zip_path,
        "pcb_error": pcb_error,
        "provider_used": ctx.name,
    }


def _pipeline_direct(description: str, tmpdir: str,
                     ckpt: "PipelineCheckpoint | None", log_fn) -> dict:
    """Simple pipeline without ProviderContext (direct compile_design call)."""
    if ckpt:
        ckpt.save("started", {"description": description, "started_at": time.time()})
    result = compile_design(description, tmpdir)
    if ckpt:
        ckpt.save("done", result)
    return result


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
            # Determine provider strategy
            provider_name = data.get("provider") or os.getenv("PCB_AI_LLM_PROVIDER", "poolside")
            fallback_chain = data.get("fallback", ["openrouter", "lmstudio"])

            # Set up checkpoint for pipeline state tracking
            ckpt_dir = OUTPUT_DIR / task_id / "checkpoints"
            ckpt = PipelineCheckpoint(str(ckpt_dir)) if _HAS_CONTEXT else None

            def _log(msg):
                with _tasks_lock:
                    _tasks[task_id]["log"].append(msg)
                print(f"[pipeline:{task_id}] {msg}")

            try:
                _log(f"Provider: {provider_name}, fallback chain: {fallback_chain}")

                # Use ProviderContext to manage provider switching + tracking
                if _HAS_CONTEXT:
                    ctx = ProviderContext(
                        provider_name=provider_name,
                        model=data.get("model"),
                        fallback_chain=fallback_chain,
                    )
                    ctx.__enter__()
                    try:
                        result = _pipeline_with_context(ctx, description, tmpdir, ckpt, _log)
                    finally:
                        ctx.__exit__(None, None, None)
                else:
                    _set_provider(provider_name)
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
                    _tasks[task_id]["provider"] = ctx.name if _HAS_CONTEXT else provider_name

            except Exception as e:
                _log(f"Error: {e}")
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
        "provider_used": task.get("provider"),
        "log": task.get("log", []),
    })


@app.route("/api/resume/<task_id>", methods=["POST"])
def api_resume(task_id: str):
    """Resume a pipeline from its last checkpoint with a different provider."""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404

    ckpt_dir = OUTPUT_DIR / task_id / "checkpoints"
    if not ckpt_dir.exists():
        return jsonify({"error": "no checkpoint data for this task"}), 404

    data = request.get_json(silent=True) or {}
    provider_name = data.get("provider") or os.getenv("PCB_AI_LLM_PROVIDER", "poolside")

    ckpt = PipelineCheckpoint(str(ckpt_dir))
    steps = ckpt.list_steps()

    def _log(msg):
        with _tasks_lock:
            task["log"].append(msg)

    def _resume_run():
        with _tasks_lock:
            task["status"] = "running"

        try:
            # Resume from the last completed checkpoint
            if "bom" in steps:
                bom = ckpt.load("bom")
                _log(f"Resumed from BOM checkpoint ({len(bom)} components)")
                # Continue: generate schematic + PCB from the saved BOM
                from pcbai.steps.design_compiler import _build_schematic_from_bom, _build_pcb_from_bom
                tmpdir = tempfile.mkdtemp()
                sch_path = os.path.join(tmpdir, "schematic.kicad_sch")
                _build_schematic_from_bom(bom, sch_path, task.get("description", ""))
                pcb_path = os.path.join(tmpdir, "board.kicad_pcb")
                pcb_ok = _build_pcb_from_bom(bom, pcb_path, task.get("description", ""))

                pcb_error = None
                if not pcb_ok:
                    pcb_error = "pcbnew not installed"

                result = {
                    "bom": bom,
                    "sch": sch_path,
                    "pcb": pcb_path,
                    "pcb_error": pcb_error,
                    "provider_used": provider_name,
                }
                with _tasks_lock:
                    task["status"] = "done"
                    task["result"] = result
                    task["provider"] = provider_name

            elif "requirements" in steps:
                _log("Requirements checkpoint exists but no BOM — re-running BOM generation")
                # Fall back to full design restart with new provider
                with _tasks_lock:
                    task["status"] = "running"
                result = compile_design(task.get("description", ""), tempfile.mkdtemp())
                result["provider_used"] = provider_name
                with _tasks_lock:
                    task["status"] = "done"
                    task["result"] = result
                    task["provider"] = provider_name
            else:
                _log("No resumable checkpoints found — restart recommended")
                with _tasks_lock:
                    task["status"] = "error"
                    task["error"] = "No checkpoint to resume from"

        except Exception as e:
            _log(f"Resume error: {e}")
            with _tasks_lock:
                task["status"] = "error"
                task["error"] = str(e)

    threading.Thread(target=_resume_run, daemon=True).start()
    return jsonify({"task_id": task_id, "status": "running",
                     "message": "Resuming from checkpoint with provider: " + provider_name})


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
    import argparse

    parser = argparse.ArgumentParser(description="PCB Designer AI Agent — Web Dashboard")
    parser.add_argument("--host", default=os.getenv("PCB_AI_HOST", "0.0.0.0"),
                        help="Bind host (default: $PCB_AI_HOST or 0.0.0.0)")
    parser.add_argument("--port", type=int,
                        default=int(os.getenv("PCB_AI_PORT", "5000")),
                        help="Port (default: $PCB_AI_PORT or 5000)")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    args = parser.parse_args()

    print("PCB Designer AI Agent — Web Dashboard")
    print(f"  Project root: {_project_root}")
    print(f"  pcbai path:   {_pcbai_root}")
    print(f"  Templates:    {app.template_folder}")
    print(f"  Static:       {app.static_folder}")
    print(f"  Outputs:      {OUTPUT_DIR}")
    print(f"  Provider:     {os.getenv('PCB_AI_LLM_PROVIDER', 'poolside')}")
    print(f"\n  Running on:   http://localhost:{args.port}\n")

    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)
