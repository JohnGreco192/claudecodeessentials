"""
Tests for follower_vectors — verifies imports, graceful degradation, logic helpers,
and integration shim correctness. Does NOT require live Upstash credentials.
Run: python3 test_vectors.py
"""
import os
import sys
import re

# Ensure Week 0/ is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Clear any real Upstash creds so all network calls return graceful defaults
os.environ.pop("UPSTASH_VECTOR_REST_URL", None)
os.environ.pop("UPSTASH_VECTOR_REST_TOKEN", None)

PASS = "✅"
FAIL = "❌"
results = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    msg = f"  {status}  {label}"
    if not condition and detail:
        msg += f"\n     → {detail}"
    print(msg)
    results.append(condition)


# ── 1. Import tests ────────────────────────────────────────────────────────────
print("\n── Imports ──")
try:
    from follower_vectors import (
        upsert_user, query_similar, fetch_user, update_engagement,
        upsert_argument, query_similar_arguments, extract_similar_argument_texts,
        upsert_rebuttal, query_similar_rebuttal, best_prior_rebuttal,
        upsert_research, query_relevant_research,
        ARGUMENT_SIMILARITY_THRESHOLD, REBUTTAL_SIMILARITY_THRESHOLD,
    )
    check("follower_vectors package imports cleanly", True)
except ImportError as e:
    check("follower_vectors package imports cleanly", False, str(e))
    sys.exit(1)

try:
    from follower_vectors.vector_store import _short_hash, _available, NS_ARGUMENTS, NS_REBUTTALS, NS_RESEARCH
    check("vector_store internals accessible", True)
except ImportError as e:
    check("vector_store internals accessible", False, str(e))


# ── 2. Graceful degradation (no credentials) ──────────────────────────────────
print("\n── Graceful degradation (no UPSTASH creds) ──")

check("_available() returns False without creds", not _available())

result = upsert_user("testuser", "test profile", {})
check("upsert_user returns False without creds", result is False)

result = query_similar("test query")
check("query_similar returns [] without creds", result == [])

result = fetch_user("testuser")
check("fetch_user returns None without creds", result is None)

result = upsert_argument("2026-05-01", "capex bubble compression thesis")
check("upsert_argument returns False without creds", result is False)

result = query_similar_arguments("nvda multiple compression")
check("query_similar_arguments returns [] without creds", result == [])

result = upsert_rebuttal("2026-05-01", "earnings beat proves growth", "sandbagging guidance is not a moat")
check("upsert_rebuttal returns False without creds", result is False)

result = query_similar_rebuttal("earnings beat proves growth")
check("query_similar_rebuttal returns [] without creds", result == [])

result = upsert_research("https://example.com/article", "AI bubble", "summary text", "2026-05-01")
check("upsert_research returns False without creds", result is False)

result = query_relevant_research("capex cycle multiple compression")
check("query_relevant_research returns [] without creds", result == [])


# ── 3. Logic helper tests ──────────────────────────────────────────────────────
print("\n── Logic helpers ──")

h1 = _short_hash("same text")
h2 = _short_hash("same text")
h3 = _short_hash("different text")
check("_short_hash is deterministic", h1 == h2)
check("_short_hash differs for different inputs", h1 != h3)
check("_short_hash is 10 chars", len(h1) == 10)

check("ARGUMENT_SIMILARITY_THRESHOLD is in (0,1)", 0 < ARGUMENT_SIMILARITY_THRESHOLD < 1)
check("REBUTTAL_SIMILARITY_THRESHOLD is in (0,1)", 0 < REBUTTAL_SIMILARITY_THRESHOLD < 1)


# ── 4. extract_similar_argument_texts filter ──────────────────────────────────
print("\n── extract_similar_argument_texts ──")

fake_results = [
    {"score": 0.85, "metadata": {"argument": "capex bubble thesis", "date": "2026-04-30"}},
    {"score": 0.60, "metadata": {"argument": "low confidence match", "date": "2026-04-29"}},
    {"score": 0.75, "metadata": {"argument": "above threshold match", "date": "2026-04-28"}},
    {"score": 0.80, "metadata": {}},  # no argument key
]
filtered = extract_similar_argument_texts(fake_results)
check("extracts texts above threshold", len(filtered) == 2, f"got: {filtered}")
check("filters out entries without argument key", "above threshold match" in filtered)
check("rejects below-threshold results", "low confidence match" not in filtered)


# ── 5. best_prior_rebuttal selection ──────────────────────────────────────────
print("\n── best_prior_rebuttal ──")

fake_rebuttals = [
    {"score": 0.78, "metadata": {"rebuttal": "sandbagging guidance, not a moat"}},
    {"score": 0.91, "metadata": {"rebuttal": "4 customers = 61% revenue concentration"}},
    {"score": 0.55, "metadata": {"rebuttal": "below threshold"}},
]
best = best_prior_rebuttal(fake_rebuttals)
check("best_prior_rebuttal returns highest above threshold", best is not None)
check("returns highest-scoring result", best["score"] == 0.91)

check("best_prior_rebuttal returns None when all below threshold",
      best_prior_rebuttal([{"score": 0.5, "metadata": {}}]) is None)
check("best_prior_rebuttal returns None for empty list",
      best_prior_rebuttal([]) is None)


# ── 6. Namespace URL construction ─────────────────────────────────────────────
print("\n── Namespace URL construction ──")

from follower_vectors.vector_store import _ep
os.environ["UPSTASH_VECTOR_REST_URL"] = "https://test.upstash.io"
os.environ["UPSTASH_VECTOR_REST_TOKEN"] = "test-token"

from importlib import reload
import follower_vectors.vector_store as vs
reload(vs)

check("default namespace URL has no prefix", vs._ep("upsert-data") == "https://test.upstash.io/upsert-data")
check("named namespace URL includes prefix", vs._ep("upsert-data", "arguments") == "https://test.upstash.io/arguments/upsert-data")
check("research namespace correct", vs._ep("query-data", "research") == "https://test.upstash.io/research/query-data")

# Restore
os.environ.pop("UPSTASH_VECTOR_REST_URL", None)
os.environ.pop("UPSTASH_VECTOR_REST_TOKEN", None)


# ── 7. MEMORY.md argument log seed simulation ─────────────────────────────────
print("\n── MEMORY.md argument log parsing ──")

MEMORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MEMORY.md")
with open(MEMORY_PATH) as f:
    content = f.read()

argument_log = []
al = re.search(r"## Argument Log\n((?:- .+\n?)*)", content)
if al:
    for line in al.group(1).strip().splitlines():
        m = re.match(r"- (\d{4}-\d{2}-\d{2}) \| (.+)", line.strip())
        if m:
            argument_log.append({"date": m.group(1), "argument": m.group(2).strip()})

check("MEMORY.md argument log parses with dates", len(argument_log) > 0,
      f"found {len(argument_log)} entries")
if argument_log:
    check("argument entries have date + text",
          all("date" in e and "argument" in e for e in argument_log))
    # IDs are deterministic — same (date, argument) always produces the same ID.
    # Same-date duplicates upsert-overwrite in the store, which is fine.
    ids = [f"arg:{e['date']}:{_short_hash(e['argument'])}" for e in argument_log]
    all_have_prefix = all(id_.startswith("arg:") for id_ in ids)
    check("argument vector IDs all have correct prefix", all_have_prefix)


# ── 8. Script import safety (no side effects) ─────────────────────────────────
print("\n── Script import safety ──")

import ast
for fname in ["post_daily_close.py", "reply_patrol.py", "morning_hunt.py", "follow_weekly.py"]:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
    try:
        with open(path) as f:
            ast.parse(f.read())
        check(f"{fname} parses without syntax errors", True)
    except SyntaxError as e:
        check(f"{fname} parses without syntax errors", False, str(e))


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*40}")
passed = sum(results)
total = len(results)
print(f"  {passed}/{total} checks passed")
if passed < total:
    print(f"  {FAIL} {total - passed} check(s) failed")
    sys.exit(1)
else:
    print(f"  {PASS} All checks passed")
