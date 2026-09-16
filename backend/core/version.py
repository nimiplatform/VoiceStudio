"""VoiceStudio version from the canonical frontend manifest, including frozen builds."""
import json
from pathlib import Path

_FALLBACK_VERSION = "0.5.5"

def _fallback_version() -> str:
    for parent in Path(__file__).resolve().parents:
        manifest = parent / "frontend" / "package.json"
        if manifest.is_file():
            return json.loads(manifest.read_text(encoding="utf-8"))["version"]
    return _FALLBACK_VERSION

APP_VERSION = _fallback_version()
