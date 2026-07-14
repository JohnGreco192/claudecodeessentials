import json
import os
from pathlib import Path
from typing import Any


MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"


def _candidate_paths() -> list[Path]:
    home = Path.home()
    return [
        Path(os.environ.get("MOLTBOOK_CREDENTIALS_FILE", "")) if os.environ.get("MOLTBOOK_CREDENTIALS_FILE") else None,
        home / ".config" / "moltbook" / "credentials.json",
        home / ".moltbook" / "credentials.json",
    ]


def _read_credentials_file(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    try:
        with path.open() as fh:
            data = json.load(fh)
    except Exception:
        return None

    if isinstance(data, dict):
        for key in ("api_key", "token", "access_token", "moltbook_api_key", "MOLTBOOK_API_KEY"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if isinstance(data.get("credentials"), dict):
            for key in ("api_key", "token", "access_token"):
                value = data["credentials"].get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    elif isinstance(data, str) and data.strip():
        return data.strip()
    return None


def resolve_moltbook_api_key() -> str:
    env_candidates = [
        os.environ.get("MOLTBOOK_API_KEY"),
        os.environ.get("MOLTBOOK_TOKEN"),
        os.environ.get("MOLTBOOK_ACCESS_TOKEN"),
    ]
    for value in env_candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()

    for path in _candidate_paths():
        if path is None:
            continue
        value = _read_credentials_file(path)
        if value:
            return value
    return ""


def build_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = resolve_moltbook_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def has_moltbook_auth() -> bool:
    return bool(resolve_moltbook_api_key())


def maybe_warn_missing_auth(label: str = "Moltbook") -> None:
    if not has_moltbook_auth():
        print(f"  [{label.lower()}] no API key found; requests will be skipped until MOLTBOOK_API_KEY is configured")


def request_json(method: str, url: str, **kwargs: Any) -> tuple[int, Any, dict[str, str]]:
    if not has_moltbook_auth() and os.environ.get("MOLTBOOK_DRY_RUN", "").lower() not in {"1", "true", "yes"}:
        return 401, {"error": "missing_moltbook_api_key"}, build_headers()

    if os.environ.get("MOLTBOOK_DRY_RUN", "").lower() in {"1", "true", "yes"}:
        return 200, {"success": True, "dry_run": True, "message": "dry-run stub"}, build_headers()

    import requests

    response = requests.request(method=method, url=url, headers=build_headers(), **kwargs)
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}
    return response.status_code, data, build_headers()
