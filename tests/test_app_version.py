"""Every distributed VoiceStudio entry reports the canonical frontend version."""
import json
from pathlib import Path
import re
from core.version import APP_VERSION, _fallback_version

ROOT = Path(__file__).resolve().parents[1]

def canonical():
    return json.loads((ROOT / 'frontend/package.json').read_text(encoding='utf-8'))['version']

def test_app_version_is_semver():
    assert re.fullmatch(r'\d+\.\d+\.\d+', APP_VERSION)

def test_app_version_matches_frontend_manifest():
    assert APP_VERSION == canonical()

def test_all_version_files_in_lockstep():
    for name in ['package.json', 'electron/package.json']:
        assert json.loads((ROOT / name).read_text(encoding='utf-8'))['version'] == canonical()
    for name in ['pyproject.toml', 'backend/business/pyproject.toml', 'frontend/src-tauri/Cargo.toml']:
        assert re.search(r'(?m)^version\s*=\s*"([^"]+)"', (ROOT / name).read_text(encoding='utf-8')).group(1) == canonical()
    assert re.search(r'(?m)^_FALLBACK_VERSION\s*=\s*"([^"]+)"', (ROOT / 'backend/core/version.py').read_text(encoding='utf-8')).group(1) == canonical()

def test_tauri_version_derives_from_package_json():
    assert json.loads((ROOT / 'frontend/src-tauri/tauri.conf.json').read_text(encoding='utf-8'))['version'] == '../package.json'

def test_fallback_version_resolves_to_frontend():
    assert _fallback_version() == canonical()

def test_frozen_build_collects_the_canonical_manifest():
    assert "root / 'frontend/package.json'" in (ROOT / 'backend/nimi-business.spec').read_text(encoding='utf-8')
