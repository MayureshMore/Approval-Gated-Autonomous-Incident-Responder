"""
TrueFoundry AI Gateway provider — the primary runtime (task A2).

The gateway is OpenAI-compatible, so this is a thin configuration of
OpenAIProvider rather than a second implementation: same loop, same approval
gate, same sandbox. What the gateway adds is what the harness track cares about —
every model call is routed, authenticated, rate-limited and **traced** by
TrueFoundry, and switching the underlying model is a config change, not a code
change.

Setup:
    export TRUEFOUNDRY_API_KEY=...              # Personal Access Token
    export TRUEFOUNDRY_BASE_URL=https://pacific.truefoundry.cloud/api/llm
    export TRUEFOUNDRY_MODEL=openai-main/gpt-4o
    python run_agent.py --provider truefoundry

Model IDs are `{provider-account}/{model}` — e.g. `openai-main/gpt-4o`. The
account prefix is whatever the gateway is configured with, so list what your
tenant actually exposes before the demo:

    python -m agent.providers.truefoundry          # prints available models
"""
import json
import os
from typing import Optional

from .openai_provider import OpenAIProvider

DEFAULT_BASE_URL = "https://pacific.truefoundry.cloud/api/llm"
DEFAULT_MODEL = "openai-main/gpt-4o"


def base_url() -> str:
    return os.environ.get("TRUEFOUNDRY_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def api_key() -> str:
    key = os.environ.get("TRUEFOUNDRY_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "TRUEFOUNDRY_API_KEY is not set. Create a Personal Access Token in the "
            "TrueFoundry dashboard and export it.")
    return key


class TrueFoundryProvider(OpenAIProvider):
    name = "truefoundry"
    default_model = DEFAULT_MODEL

    def __init__(self, model: Optional[str] = None, run_id: Optional[str] = None):
        super().__init__(
            model=model or os.environ.get("TRUEFOUNDRY_MODEL") or DEFAULT_MODEL,
            api_key=api_key(),
            base_url=base_url(),
            # Tags every call in the gateway's observability view, so a run on the
            # dashboard can be traced back to its LLM spend and latency.
            extra_headers={"X-TFY-METADATA": json.dumps({
                "application": "ripcord-incident-responder",
                "environment": "hackathon",
                **({"run_id": run_id} if run_id else {}),
            })},
        )


def list_models() -> list[str]:
    """What this tenant actually exposes. Run before the demo — a wrong model id
    is a 404 at the worst possible moment."""
    import requests
    resp = requests.get(f"{base_url()}/models",
                        headers={"Authorization": f"Bearer {api_key()}"}, timeout=20)
    resp.raise_for_status()
    body = resp.json()
    items = body.get("data", body if isinstance(body, list) else [])
    return sorted(m.get("id", str(m)) if isinstance(m, dict) else str(m) for m in items)


def preflight() -> dict:
    """Can we reach the gateway and is the configured model actually there?"""
    status = {"base_url": base_url(), "model": os.environ.get("TRUEFOUNDRY_MODEL", DEFAULT_MODEL),
              "key_present": bool(os.environ.get("TRUEFOUNDRY_API_KEY")), "ready": False}
    if not status["key_present"]:
        status["error"] = "TRUEFOUNDRY_API_KEY is not set"
        return status
    try:
        models = list_models()
    except Exception as exc:
        status["error"] = f"gateway unreachable or key rejected: {exc}"
        return status
    status["models_available"] = len(models)
    status["sample_models"] = models[:10]
    if status["model"] not in models:
        status["error"] = (f"model {status['model']!r} is not exposed by this gateway; "
                           f"pick one of: {models[:5]}")
        return status
    status["ready"] = True
    return status


if __name__ == "__main__":
    print(json.dumps(preflight(), indent=2))
