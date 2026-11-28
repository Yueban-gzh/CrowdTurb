"""Access to the repository's versioned contracts and fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from check_repository import load_json, validate_result, validate_scene, validate_task  # noqa: E402

FIXTURES = ROOT / "examples"
WEB_ROOT = ROOT / "apps" / "web"

__all__ = [
    "FIXTURES",
    "ROOT",
    "WEB_ROOT",
    "load_json",
    "validate_result",
    "validate_scene",
    "validate_task",
]
