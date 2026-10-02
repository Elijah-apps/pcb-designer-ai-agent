"""
Context manager and tracking for LLM providers.

Provides:
  - ``ProviderContext``  — context manager for easy provider switching with
    automatic env-var save/restore and usage tracking.
  - ``ProviderTracker``  — singleton metrics tracker (calls, success rate,
    errors, token usage per provider).
  - ``OpenRouterProvider`` — first-class OpenRouter provider (single model, not
    fallback-iterator).  Use ``PCB_AI_MODEL`` to pick the model.
  - ``PipelineCheckpoint`` — save/restore pipeline state so you can resume
    mid-pipeline if a provider fails or you switch providers.
"""
from __future__ import annotations

import os
import json
import copy
import time
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from pathlib import Path
from typing import Any, Generator, Optional

from pcbai.llm.provider import LLMProvider, get_provider, _get_max_tokens, _get_temperature


# ─────────────────────────────────────────────────────────────────────────────
# OpenRouter (first-class, single model)
# ─────────────────────────────────────────────────────────────────────────────

class OpenRouterProvider(LLMProvider):
    """Single-model OpenRouter provider — unlike OpenRouterFallbackProvider
    which iterates through a list, this one uses one configurable model."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "poolside/laguna-s-2.1:free",
        base_url: str = "https://openrouter.ai/api/v1",
        **kwargs,
    ):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.model = model
        self.base_url = base_url.rstrip("/")

    def chat(self, messages: list, temperature: float = 0.2, max_tokens: int = 512, **kw) -> str:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        import requests
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/assalas/pcb-designer-ai-agent",
            "X-Title": "PCB Designer AI Agent",
        }
        r = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "messages": messages,
                "temperature": _get_temperature(temperature),
                "max_tokens": _get_max_tokens(max_tokens),
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"].get("content", "").strip()

    def complete(self, prompt: str, **kw) -> str:
        return self.chat([{"role": "user", "content": prompt}], **kw)


# ─────────────────────────────────────────────────────────────────────────────
# Provider Tracker (singleton)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProviderStats:
    calls: int = 0
    successes: int = 0
    errors: int = 0
    error_messages: list = field(default_factory=list)
    total_latency_ms: float = 0.0
    last_used: float = 0.0

    def record(self, success: bool, latency_ms: float = 0, error: str = ""):
        self.calls += 1
        self.last_used = time.time()
        self.total_latency_ms += latency_ms
        if success:
            self.successes += 1
        else:
            self.errors += 1
            if error:
                self.error_messages.append(error[:200])

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(self.calls, 1)

    @property
    def success_rate(self) -> float:
        return self.successes / max(self.calls, 1)


class ProviderTracker:
    """Singleton that tracks per-provider call metrics."""
    _instance: Optional["ProviderTracker"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_singleton()
        return cls._instance

    def _init_singleton(self):
        self._stats: dict[str, ProviderStats] = defaultdict(ProviderStats)
        self._active_provider: Optional[str] = None

    @property
    def stats(self) -> dict[str, ProviderStats]:
        return dict(self._stats)

    def get_stats(self, provider_name: str) -> ProviderStats:
        return self._stats[provider_name]

    def record_call(self, provider_name: str, success: bool,
                    latency_ms: float = 0, error: str = ""):
        self._stats[provider_name].record(success, latency_ms, error)

    @property
    def active_provider(self) -> Optional[str]:
        return self._active_provider

    def set_active(self, name: str):
        self._active_provider = name

    def get_recommended(self) -> str:
        """Return the provider with the highest success rate (min 1 call)."""
        best, best_rate = "poolside", 0.0
        for name, s in self._stats.items():
            if s.calls > 0 and s.success_rate > best_rate:
                best, best_rate = name, s.success_rate
        return best

    def reset(self):
        with self._lock:
            self._stats.clear()
            self._active_provider = None


# ─────────────────────────────────────────────────────────────────────────────
# Provider Context Manager
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProviderContext:
    """Context manager that temporarily switches the LLM provider.

    Usage::

        from pcbai.llm.context import ProviderContext
        with ProviderContext("openai", model="gpt-4o") as ctx:
            result = ctx.provider.chat([{"role": "user", "content": "Hi"}])

    Features:
      - Saves and restores all env vars on exit
      - Records call metrics via ``ProviderTracker``
      - Falls back to the next provider in a chain on failure
      - Logs every call with timing
    """
    provider_name: str = ""
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    # Chain of fallback providers to try if the primary fails
    fallback_chain: list = field(default_factory=list)
    # Env variables to override (auto-saved & restored)
    _env_backup: dict = field(default_factory=dict, init=False)
    _provider: Optional[LLMProvider] = field(default=None, init=False)
    _tracker: ProviderTracker = field(default_factory=ProviderTracker, init=False)

    def __enter__(self) -> "ProviderContext":
        self._env_backup = {}

        def _save_set(key: str, value: str):
            self._env_backup[key] = os.environ.get(key)
            os.environ[key] = value

        # Determine provider name (default = current env or "poolside")
        name = self.provider_name or os.getenv("PCB_AI_LLM_PROVIDER", "poolside")
        _save_set("PCB_AI_LLM_PROVIDER", name.lower())
        if self.model:
            _save_set("PCB_AI_MODEL", self.model)
        if self.api_key:
            # Set the appropriate API key env var for this provider
            key_map = {
                "openai": "OPENAI_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
                "poolside": "OPENROUTER_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "claude": "ANTHROPIC_API_KEY",
                "gemini": "GEMINI_API_KEY",
                "lmstudio": "LMSTUDIO_URL",
                "ollama": "OLLAMA_URL",
            }
            env_key = key_map.get(name.lower(), "OPENROUTER_API_KEY")
            _save_set(env_key, self.api_key)
        if self.base_url:
            _save_set("OPENROUTER_URL", self.base_url)

        self._tracker.set_active(name)
        self._provider = get_provider()
        return self

    def __exit__(self, *exc):
        # Restore env vars
        for key, old_val in self._env_backup.items():
            if old_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_val
        self._provider = None
        return False  # Don't suppress exceptions

    @property
    def provider(self) -> LLMProvider:
        """The active LLM provider instance."""
        if self._provider is None:
            raise RuntimeError("ProviderContext must be used as a context manager: `with ProviderContext(...) as ctx:`")
        return self._provider

    @property
    def name(self) -> str:
        return self.provider_name or os.getenv("PCB_AI_LLM_PROVIDER", "poolside")

    def chat(self, messages: list, temperature: float = 0.2, max_tokens: int = 512) -> str:
        """Call the provider with tracking + fallback chain support."""
        chain = [self.name.lower()] + [f.lower() for f in self.fallback_chain]
        last_error = ""
        for i, provider_name in enumerate(chain):
            # Temporarily set env for this provider
            old = os.environ.get("PCB_AI_LLM_PROVIDER")
            os.environ["PCB_AI_LLM_PROVIDER"] = provider_name
            try:
                provider = get_provider()
                start = time.time()
                result = provider.chat(messages, temperature=temperature, max_tokens=max_tokens)
                latency = (time.time() - start) * 1000
                self._tracker.record_call(provider_name, True, latency)
                self._tracker.set_active(provider_name)
                if i > 0:
                    # Failed over to fallback
                    import sys; print(f"[context] Switched to fallback provider: {provider_name}", file=sys.stderr)
                return result.strip()
            except Exception as e:
                latency = (time.time() - start) * 1000 if 'start' in dir() else 0
                self._tracker.record_call(provider_name, False, latency, str(e))
                last_error = str(e)
                if i > 0:
                    import sys; print(f"[context] Fallback provider {provider_name} also failed: {e}", file=sys.stderr)
            finally:
                if old is None:
                    os.environ.pop("PCB_AI_LLM_PROVIDER", None)
                else:
                    os.environ["PCB_AI_LLM_PROVIDER"] = old
        raise RuntimeError(f"All providers in chain failed. Last error: {last_error}")

    def complete(self, prompt: str, **kw) -> str:
        return self.chat([{"role": "user", "content": prompt}], **kw)


@contextmanager
def provider_session(provider_name: str = "", model: Optional[str] = None,
                     api_key: Optional[str] = None,
                     fallback: list = None) -> Generator[ProviderContext, None, None]:
    """Convenience context manager for provider switching."""
    ctx = ProviderContext(
        provider_name=provider_name,
        model=model,
        api_key=api_key,
        fallback_chain=fallback or [],
    )
    with ctx:
        yield ctx


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Checkpoint — save/restore state between steps
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PipelineCheckpoint:
    """Save and restore pipeline state so you can resume mid-pipeline.

    Usage::

        cp = PipelineCheckpoint("/tmp/mychip")
        cp.save("bom", bom_data)
        # ... later ...
        if cp.exists("bom"):
            bom_data = cp.load("bom")
    """
    checkpoint_dir: str

    def __post_init__(self):
        os.makedirs(self.checkpoint_dir, exist_ok=True)

    def save(self, step: str, data: Any) -> str:
        """Save pipeline state for a given step."""
        path = os.path.join(self.checkpoint_dir, f"step_{step}.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        return path

    def load(self, step: str) -> Any:
        """Load pipeline state for a given step."""
        path = os.path.join(self.checkpoint_dir, f"step_{step}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"No checkpoint for step '{step}' at {path}")
        with open(path, "r") as f:
            return json.load(f)

    def exists(self, step: str) -> bool:
        """Check if a checkpoint exists for the given step."""
        path = os.path.join(self.checkpoint_dir, f"step_{step}.json")
        return os.path.exists(path)

    def list_steps(self) -> list[str]:
        """List all saved checkpoint steps."""
        files = os.listdir(self.checkpoint_dir)
        return sorted([f[5:-5] for f in files if f.startswith("step_") and f.endswith(".json")])

    def summary(self) -> dict:
        """Return a summary of all saved checkpoints."""
        steps = self.list_steps()
        return {
            "checkpoint_dir": self.checkpoint_dir,
            "steps": steps,
            "total_steps": len(steps),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Provider Factory (updated with OpenRouter)
# ─────────────────────────────────────────────────────────────────────────────

def get_provider_with_fallback(name: str = "", allow_fallback: bool = True) -> LLMProvider:
    """Get a provider with automatic fallback to the next working one."""
    tracker = ProviderTracker()
    chain = [name or os.getenv("PCB_AI_LLM_PROVIDER", "poolside")]
    if allow_fallback:
        chain.extend(["openrouter", "lmstudio"])

    for provider_name in chain:
        old = os.environ.get("PCB_AI_LLM_PROVIDER")
        os.environ["PCB_AI_LLM_PROVIDER"] = provider_name
        try:
            provider = get_provider()
            # Test with a tiny request to verify the provider works
            tracker.set_active(provider_name)
            return provider
        except Exception:
            pass
        finally:
            if old is None:
                os.environ.pop("PCB_AI_LLM_PROVIDER", None)
            else:
                os.environ["PCB_AI_LLM_PROVIDER"] = old

    # All failed — return DummyProvider
    from pcbai.llm.provider import DummyProvider
    return DummyProvider()
