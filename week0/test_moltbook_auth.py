import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for candidate in (str(ROOT), str(ROOT.parent)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from moltbook_client import resolve_moltbook_api_key, build_headers


def test_env_key_takes_priority(monkeypatch):
    monkeypatch.setenv("MOLTBOOK_API_KEY", "env-key")
    monkeypatch.delenv("MOLTBOOK_TOKEN", raising=False)
    assert resolve_moltbook_api_key() == "env-key"


def test_credentials_file_is_supported(tmp_path, monkeypatch):
    cred_path = tmp_path / "credentials.json"
    cred_path.write_text(json.dumps({"api_key": "file-key"}))
    monkeypatch.setenv("MOLTBOOK_CREDENTIALS_FILE", str(cred_path))
    monkeypatch.delenv("MOLTBOOK_API_KEY", raising=False)
    assert resolve_moltbook_api_key() == "file-key"


def test_headers_include_authorization_when_key_present(monkeypatch):
    monkeypatch.setenv("MOLTBOOK_API_KEY", "abc123")
    headers = build_headers()
    assert headers["Authorization"] == "Bearer abc123"
