#!/usr/bin/env python3
"""
Run the PCB Designer AI Agent web dashboard.

Usage:
    python3 run_web.py
    python3 run_web.py --port 8080
    python3 run_web.py --host 0.0.0.0

Opens at: http://localhost:5000

The Flask application lives at the project root: app.py
"""
import argparse
import os
import sys
from pathlib import Path

# Ensure pcbai is importable
_project_root = Path(__file__).resolve().parent
_pcbai_root = _project_root / "anna-app" / "executas" / "pcb-designer"
sys.path.insert(0, str(_pcbai_root))
sys.path.insert(0, str(_project_root))

# Load .env before pcbai is imported — it reads os.environ at import time.
try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
    load_dotenv(_project_root / "web" / ".env")
except ImportError:
    # python-dotenv is optional — shell-exported env vars still work.
    pass


def main():
    parser = argparse.ArgumentParser(description="PCB Designer AI Agent Web Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    args = parser.parse_args()

    # Import the Flask app (lives at the project root: app.py)
    from app import app

    print(f"PCB Designer AI Agent — Web Dashboard")
    print(f"  Project root: {_project_root}")
    print(f"  pcbai path:   {_pcbai_root}")
    print(f"  Running on:   http://{args.host}:{args.port}")
    print(f"  Provider:     {os.getenv('PCB_AI_LLM_PROVIDER', 'poolside')}")
    print(f"  GEMINI key:   {'configured' if os.getenv('GEMINI_API_KEY') else 'not set'}")
    print()

    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
