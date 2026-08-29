"""
Minimal .env loader — no new dependency, and no secrets in argv or shell history.

Existing environment variables always win, so `TRUEFOUNDRY_API_KEY=... python
run_agent.py` still overrides the file.
"""
import os
from typing import Optional

SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def load_dotenv(path: Optional[str] = None) -> list[str]:
    """Load KEY=VALUE lines. Returns the names loaded — never the values."""
    path = path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return []

    loaded = []
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if not key or not value or key in os.environ:
                continue
            os.environ[key] = value
            loaded.append(key)
    return loaded


def mask(value: Optional[str]) -> str:
    """Render a secret safely for logs and status output."""
    if not value:
        return "(not set)"
    return f"{value[:4]}…{value[-4:]} ({len(value)} chars)" if len(value) > 12 else "(set)"


def describe(name: str) -> str:
    value = os.environ.get(name)
    return mask(value) if any(h in name.upper() for h in SECRET_HINTS) else (value or "(not set)")
