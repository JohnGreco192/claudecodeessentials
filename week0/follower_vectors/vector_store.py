"""
Upstash Vector store — multi-namespace semantic memory.

Namespaces:
  (default)  → follower personality profiles
  arguments  → deployed bear arguments (semantic dedup across unlimited history)
  rebuttals  → bull argument → rebuttal pairs (combat memory)
  research   → bear research articles (thematic archive)

All functions degrade gracefully when UPSTASH_VECTOR_REST_URL / _TOKEN are unset.
Index must be created with an auto-embedding model (text-embedding-3-small recommended).
"""
import os
import hashlib
import requests

_URL = os.environ.get("UPSTASH_VECTOR_REST_URL", "")
_TOKEN = os.environ.get("UPSTASH_VECTOR_REST_TOKEN", "")
_TIMEOUT = 10

NS_ARGUMENTS = "arguments"
NS_REBUTTALS = "rebuttals"
NS_RESEARCH  = "research"

ARGUMENT_SIMILARITY_THRESHOLD = 0.72
REBUTTAL_SIMILARITY_THRESHOLD = 0.72


def _h() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"}


def _ep(endpoint: str, namespace: str = "") -> str:
    return f"{_URL}/{namespace}/{endpoint}" if namespace else f"{_URL}/{endpoint}"


def _available() -> bool:
    return bool(_URL and _TOKEN)


def _short_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:10]


def _upsert(vid: str, text: str, metadata: dict, namespace: str = "") -> bool:
    if not _available():
        return False
    try:
        r = requests.post(
            _ep("upsert-data", namespace),
            headers=_h(),
            json=[{"id": vid, "data": text, "metadata": metadata}],
            timeout=_TIMEOUT,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  [vector:{namespace or 'default'}] upsert failed ({vid}): {e}")
        return False


def _query(text: str, top_k: int, namespace: str = "") -> list[dict]:
    if not _available():
        return []
    try:
        r = requests.post(
            _ep("query-data", namespace),
            headers=_h(),
            json={"data": text, "topK": top_k, "includeMetadata": True},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception as e:
        print(f"  [vector:{namespace or 'default'}] query failed: {e}")
    return []


# ── Followers (default namespace) ─────────────────────────────────────────────

def upsert_user(username: str, profile_text: str, metadata: dict) -> bool:
    return _upsert(f"user:{username}", profile_text, metadata)


def query_similar(query_text: str, top_k: int = 20, exclude: set | None = None) -> list[dict]:
    results = _query(query_text, top_k=top_k + (len(exclude) if exclude else 0) + 2)
    if exclude:
        results = [x for x in results if x.get("id", "").removeprefix("user:") not in exclude]
    return results[:top_k]


def fetch_user(username: str) -> dict | None:
    if not _available():
        return None
    try:
        r = requests.post(
            _ep("fetch"),
            headers=_h(),
            json={"ids": [f"user:{username}"], "includeMetadata": True},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            items = r.json().get("result", [])
            return items[0] if items else None
    except Exception:
        pass
    return None


def update_engagement(username: str, delta: float) -> None:
    rec = fetch_user(username)
    if not rec or not _available():
        return
    meta = dict(rec.get("metadata") or {})
    meta["engagement"] = round(float(meta.get("engagement", 0)) + delta, 3)
    try:
        requests.post(
            _ep("update"), headers=_h(),
            json={"id": f"user:{username}", "metadata": meta},
            timeout=_TIMEOUT,
        )
    except Exception:
        pass


# ── Arguments — semantic deduplication ────────────────────────────────────────

def upsert_argument(date: str, argument: str, metadata: dict | None = None) -> bool:
    """Store a deployed bear argument. The argument text is both the embedding input
    and stored in metadata for retrieval on query."""
    vid = f"arg:{date}:{_short_hash(argument)}"
    meta = {"date": date, "argument": argument, **(metadata or {})}
    return _upsert(vid, argument, meta, NS_ARGUMENTS)


def query_similar_arguments(context_text: str, top_k: int = 5) -> list[dict]:
    """Find past deployed arguments semantically similar to the given context.
    Each result has score + metadata['argument']."""
    return _query(context_text, top_k=top_k, namespace=NS_ARGUMENTS)


def extract_similar_argument_texts(results: list[dict]) -> list[str]:
    """Pull argument strings from query results above threshold."""
    return [
        r["metadata"]["argument"]
        for r in results
        if r.get("score", 0) >= ARGUMENT_SIMILARITY_THRESHOLD
        and r.get("metadata", {}).get("argument")
    ]


# ── Rebuttals — bull argument combat memory ────────────────────────────────────

def upsert_rebuttal(date: str, bull_argument: str, our_rebuttal: str,
                    metadata: dict | None = None) -> bool:
    """Embed the bull argument; store our rebuttal as metadata.
    Works for both reply_patrol replies and morning_hunt challenges."""
    vid = f"rebuttal:{date}:{_short_hash(bull_argument)}"
    meta = {
        "date": date,
        "bull_argument": bull_argument[:400],
        "rebuttal": our_rebuttal[:400],
        **(metadata or {}),
    }
    return _upsert(vid, bull_argument, meta, NS_REBUTTALS)


def query_similar_rebuttal(bull_argument: str, top_k: int = 3) -> list[dict]:
    """Find past exchanges where we faced a semantically similar bull argument."""
    return _query(bull_argument, top_k=top_k, namespace=NS_REBUTTALS)


def best_prior_rebuttal(results: list[dict]) -> dict | None:
    """Return the highest-scoring result above threshold, or None."""
    above = [r for r in results if r.get("score", 0) >= REBUTTAL_SIMILARITY_THRESHOLD]
    return max(above, key=lambda x: x["score"]) if above else None


# ── Research archive — bear research thematic memory ──────────────────────────

def upsert_research(url: str, title: str, summary: str, date: str,
                    metadata: dict | None = None) -> bool:
    """Store a bear research article. ID is URL-based so re-fetching the same
    article is idempotent."""
    vid = f"research:{_short_hash(url)}"
    text = f"{title}\n\n{summary[:800]}"
    meta = {"url": url, "title": title, "date": date, **(metadata or {})}
    return _upsert(vid, text, meta, NS_RESEARCH)


def query_relevant_research(angle: str, top_k: int = 2,
                             exclude_urls: set | None = None) -> list[dict]:
    """Find past research most semantically relevant to today's angle.
    Excludes recently used URLs so we don't surface today's already-injected article."""
    fetch_k = top_k + (len(exclude_urls) if exclude_urls else 0) + 2
    results = _query(angle, top_k=fetch_k, namespace=NS_RESEARCH)
    if exclude_urls:
        results = [r for r in results if r.get("metadata", {}).get("url") not in exclude_urls]
    return results[:top_k]
