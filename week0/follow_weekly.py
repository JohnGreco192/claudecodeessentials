"""
Weekly Follower Scan — discovers and follows relevant Moltbook users on a ramp-then-sustain schedule.

Follow schedule (week_number is the run count starting at 1):
  Week 1: 5  |  Week 2: 4  |  Week 3: 3  |  Week 4: 2  |  Week 5+: 1 forever

Candidate scoring combines:
  1. Keyword relevance — how much they post about NVDA/AI/finance/puts
  2. Upstash Vector similarity — semantic proximity to the bear thesis + prior good followers

State lives in MEMORY.md (## Follow Week, ## Following, ## Follow Log).
Detailed per-run records go to follow_log.json.
"""
import os
import re
import json
import time
import random
import requests
import httpx
from datetime import datetime, timezone, timedelta
from openai import OpenAI

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from follower_vectors import upsert_user, query_similar, fetch_user
    _VECTOR_AVAILABLE = True
except ImportError as _e:
    print(f"  [vector] import failed ({_e}) — vector scoring disabled")
    _VECTOR_AVAILABLE = False
    def upsert_user(*a, **kw): return False
    def query_similar(*a, **kw): return []
    def fetch_user(*a, **kw): return None

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"
MOLTBOOK_KEY = os.environ["MOLTBOOK_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
MODEL = "Meta-Llama-3.1-8B-Instruct"

_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_PATH = os.path.join(_DIR, "MEMORY.md")
LOG_PATH = os.path.join(_DIR, "follow_log.json")
SOUL_PATH = os.path.join(_DIR, "SOUL.md")

MAX_FOLLOWING_LOG = 200
MAX_FOLLOW_LOG_ENTRIES = 52

_UTC = timezone.utc

# Terms that signal a user posts about what we care about
_RELEVANCE_KEYWORDS = [
    "nvda", "nvidia", "puts", "short", "bear", "ai bubble", "capex",
    "multiple compression", "overvalued", "h100", "gpu", "blackwell",
    "semiconductor", "ai stocks", "data center", "tech bubble",
    "valuation", "forward p/e", "jensen", "inference", "training",
]

# Search terms to find interesting authors
_SEARCH_TERMS = [
    "nvidia bear", "nvda puts", "ai bubble", "capex cycle",
    "nvidia overvalued", "gpu demand", "ai stocks valuation",
    "nvidia short", "h100", "blackwell demand",
    "tech bubble", "nvda analysis",
]

_SUBMOLTS = {"general", "ai", "stocks", "finance", "markets", "tech"}


# ── Utilities ─────────────────────────────────────────────────────────────────

def _now_et() -> datetime:
    now_utc = datetime.now(_UTC)
    year = now_utc.year
    mar1 = datetime(year, 3, 1, tzinfo=_UTC)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=7)
    nov1 = datetime(year, 11, 1, tzinfo=_UTC)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7, hours=6)
    offset = timedelta(hours=-4) if dst_start <= now_utc < dst_end else timedelta(hours=-5)
    return now_utc.astimezone(timezone(offset))


def follows_this_week(week_number: int) -> int:
    """week_number is the run index (1 = first run). Floor at 1 from week 5 onward."""
    return max(1, 6 - week_number)


# ── Memory ────────────────────────────────────────────────────────────────────

def load_follow_state() -> dict:
    """Returns {week_number, last_follow_date, following_set}."""
    with open(MEMORY_PATH) as f:
        content = f.read()

    week_number = 0
    last_follow_date = None

    m = re.search(r"## Follow Week\n((?:- .+\n?)*)", content)
    if m:
        for line in m.group(1).strip().splitlines():
            if line.startswith("- week_number:"):
                try:
                    week_number = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("- last_follow_date:"):
                val = line.split(":", 1)[1].strip()
                if val and val.lower() != "none":
                    last_follow_date = val

    following: set[str] = set()
    fm = re.search(r"## Following\n((?:- .+\n?)*)", content)
    if fm:
        for line in fm.group(1).strip().splitlines():
            u = line.strip().lstrip("- ").strip()
            if u:
                following.add(u)

    return {"week_number": week_number, "last_follow_date": last_follow_date, "following": following}


def already_followed_this_week(last_follow_date: str | None) -> bool:
    if not last_follow_date:
        return False
    try:
        last = datetime.fromisoformat(last_follow_date)
        now = _now_et().replace(tzinfo=None)
        return (now - last).days < 7
    except Exception:
        return False


def record_follows(new_week: int, today: str, followed: list[dict]) -> None:
    """Write ## Follow Week, ## Following, ## Follow Log to MEMORY.md."""
    with open(MEMORY_PATH) as f:
        content = f.read()

    # -- Follow Week block
    week_block = f"## Follow Week\n- week_number: {new_week}\n- last_follow_date: {today}\n"
    if "## Follow Week" in content:
        content = re.sub(r"## Follow Week\n(?:- [^\n]+\n)*", week_block, content)
    else:
        content = content.rstrip() + f"\n\n{week_block}"

    # -- Following block (append new usernames, cap at MAX_FOLLOWING_LOG)
    usernames = [u["username"] for u in followed]
    if "## Following" not in content:
        content = content.rstrip() + "\n\n## Following\n"
    for u in usernames:
        content = re.sub(r"(## Following\n)", f"\\1- {u}\n", content, count=1)
    fm = re.search(r"## Following\n((?:- .+\n?)*)", content)
    if fm:
        lines = fm.group(1).strip().splitlines(keepends=True)
        if len(lines) > MAX_FOLLOWING_LOG:
            content = re.sub(
                r"## Following\n(?:- .+\n?)*",
                "## Following\n" + "".join(lines[:MAX_FOLLOWING_LOG]),
                content,
            )

    # -- Follow Log block (one compact line per run)
    names_str = ", ".join(u["username"] for u in followed)
    log_entry = f"- {today} | week:{new_week} | added:{len(followed)} | {names_str}\n"
    if "## Follow Log" not in content:
        content = content.rstrip() + f"\n\n## Follow Log\n{log_entry}"
    else:
        content = re.sub(r"(## Follow Log\n)", f"\\1{log_entry}", content, count=1)
        fl = re.search(r"## Follow Log\n((?:- .+\n?)*)", content)
        if fl:
            lines = fl.group(1).strip().splitlines(keepends=True)
            if len(lines) > MAX_FOLLOW_LOG_ENTRIES:
                content = re.sub(
                    r"## Follow Log\n(?:- .+\n?)*",
                    "## Follow Log\n" + "".join(lines[:MAX_FOLLOW_LOG_ENTRIES]),
                    content,
                )

    with open(MEMORY_PATH, "w") as f:
        f.write(content)


def append_json_log(week: int, today: str, followed: list[dict]) -> None:
    log = []
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH) as f:
                log = json.load(f)
        except Exception:
            pass
    log.append({"date": today, "week": week, "followed": followed})
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


# ── Moltbook ──────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {"Authorization": f"Bearer {MOLTBOOK_KEY}", "Content-Type": "application/json"}


def search_posts(term: str, limit: int = 10) -> list[dict]:
    try:
        r = requests.get(f"{MOLTBOOK_BASE}/search",
                         params={"q": term, "limit": limit},
                         headers=_headers(), timeout=8)
        if r.status_code == 200:
            return r.json().get("results", [])
        print(f"  [search] '{term}' → {r.status_code}")
    except Exception as e:
        print(f"  [search] '{term}' error: {e}")
    return []


def fetch_user_profile(username: str) -> dict:
    """GET /users/{username} — bio, follower count, post count, etc."""
    try:
        r = requests.get(f"{MOLTBOOK_BASE}/users/{username}", headers=_headers(), timeout=8)
        if r.status_code == 200:
            data = r.json()
            return data.get("user", data)
    except Exception:
        pass
    return {}


def fetch_user_posts(username: str, limit: int = 5) -> list[dict]:
    """GET /users/{username}/posts — recent posts for relevance scoring."""
    try:
        r = requests.get(f"{MOLTBOOK_BASE}/users/{username}/posts",
                         params={"limit": limit}, headers=_headers(), timeout=8)
        if r.status_code == 200:
            return r.json().get("posts", [])
    except Exception:
        pass
    return []


def follow_user(username: str, user_id: str | None = None) -> bool:
    """Follow a user. Tries multiple endpoint patterns — logs which one works."""
    # Build attempt list: try all plausible patterns for this API
    # POST/PUT by username, by user_id (if known), and body-based fallbacks
    attempts: list[tuple[str, str, dict]] = [
        ("POST", f"{MOLTBOOK_BASE}/users/{username}/follow", {}),
        ("PUT",  f"{MOLTBOOK_BASE}/users/{username}/follow", {}),
        ("POST", f"{MOLTBOOK_BASE}/users/{username}/followers", {}),
    ]
    if user_id:
        attempts += [
            ("POST", f"{MOLTBOOK_BASE}/users/{user_id}/follow", {}),
            ("PUT",  f"{MOLTBOOK_BASE}/users/{user_id}/follow", {}),
        ]
    attempts += [
        ("POST", f"{MOLTBOOK_BASE}/follow", {"username": username}),
        ("POST", f"{MOLTBOOK_BASE}/follows", {"username": username}),
        ("POST", f"{MOLTBOOK_BASE}/follow", {"target_username": username}),
    ]
    if user_id:
        attempts.append(("POST", f"{MOLTBOOK_BASE}/follow", {"user_id": user_id}))

    for method, url, body in attempts:
        try:
            fn = requests.post if method == "POST" else requests.put
            r = fn(url, headers=_headers(), json=body, timeout=10)
            data = r.json() if r.content else {}
            if r.status_code in (200, 201) or data.get("success") or data.get("followed"):
                print(f"  [follow] {username} → ✓ via {method} {url.removeprefix(MOLTBOOK_BASE)}")
                return True
            if r.status_code == 404:
                continue
            # Non-404 failure — log and keep trying other patterns
            print(f"  [follow] {username} → {method} {url.removeprefix(MOLTBOOK_BASE)} {r.status_code}: {str(data)[:120]}")
        except Exception as e:
            print(f"  [follow] {username} error ({method}): {e}")
    return False


# ── LLM (used for profile summarisation only) ─────────────────────────────────

def _llm() -> OpenAI:
    return OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=GITHUB_TOKEN,
        http_client=httpx.Client(
            proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"),
        ),
    )


def summarise_profile(username: str, bio: str, post_titles: list[str]) -> str:
    """Condense a user's profile + posts into 2-3 sentences for vector embedding."""
    posts_str = " | ".join(post_titles[:5]) if post_titles else "(no recent posts)"
    prompt = (
        f"User: @{username}\nBio: {bio or '(none)'}\nRecent posts: {posts_str}\n\n"
        "Summarise this user's apparent focus areas and posting style in 2-3 sentences. "
        "Focus on: investment style (bull/bear/neutral), topics (NVDA, AI, macro, etc.), "
        "analytical depth, and how they might engage with a bear thesis. Be factual and concise."
    )
    try:
        resp = _llm().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return f"{username}: {bio or ''} | posts: {posts_str}"


# ── Scoring ────────────────────────────────────────────────────────────────────

def keyword_score(text: str) -> float:
    """Count bear-thesis keyword hits in lowercased text. Max ~10."""
    lower = text.lower()
    return sum(1.0 for kw in _RELEVANCE_KEYWORDS if kw in lower)


def build_profile_text(username: str, profile: dict, posts: list[dict]) -> str:
    bio = profile.get("bio") or profile.get("description") or ""
    titles = [p.get("title") or p.get("content", "")[:100] for p in posts]
    return summarise_profile(username, bio, titles)


def score_candidate(username: str, profile: dict, posts: list[dict],
                    vector_similar: dict) -> float:
    """Composite relevance score. Higher = more desirable follow."""
    bio = profile.get("bio") or profile.get("description") or ""
    post_text = " ".join(
        (p.get("title") or "") + " " + str(p.get("content") or "")[:200]
        for p in posts
    )
    kw = keyword_score(bio + " " + post_text)

    # Upstash Vector similarity bonus (0–3 points)
    vec_bonus = vector_similar.get(username, 0.0) * 3.0

    # Follower/engagement signal — prefer users with some presence but not mega-accounts
    follower_count = profile.get("follower_count") or profile.get("followers_count") or 0
    if 10 <= follower_count <= 2000:
        presence_bonus = 1.0
    elif follower_count > 0:
        presence_bonus = 0.3
    else:
        presence_bonus = 0.0

    return round(kw + vec_bonus + presence_bonus, 3)


# ── Candidate Discovery ────────────────────────────────────────────────────────

def discover_candidates(already_following: set[str]) -> list[dict]:
    """
    Search Moltbook for relevant posts, extract unique authors,
    fetch their profiles and recent posts, return scored candidates.
    """
    seen_authors: set[str] = set(already_following)
    raw_candidates: list[dict] = []

    for term in random.sample(_SEARCH_TERMS, min(6, len(_SEARCH_TERMS))):
        results = search_posts(term, limit=10)
        for r in results:
            author = (r.get("author") or {}).get("name") or (r.get("author") or {}).get("username")
            if not author or author in seen_authors:
                continue
            submolt = (r.get("submolt") or {}).get("name", "general")
            if submolt not in _SUBMOLTS:
                continue
            seen_authors.add(author)
            raw_candidates.append({"username": author, "source_post": r})
        if len(raw_candidates) >= 60:
            break
        time.sleep(0.5)

    print(f"  discovered {len(raw_candidates)} unique candidate authors")
    return raw_candidates


def enrich_and_score(candidates: list[dict], already_following: set[str]) -> list[dict]:
    """Fetch profiles, score, return sorted list."""
    # Query Upstash for users similar to the bear thesis persona
    bear_query = (
        "NVDA puts, multiple compression, AI capex bubble, overvalued tech stocks, "
        "short thesis, GPU demand, forward P/E, margin compression, capex cycle bear"
    )
    vector_results = query_similar(bear_query, top_k=40, exclude=already_following)
    similar_map: dict[str, float] = {
        r.get("id", "").removeprefix("user:"): r.get("score", 0.0)
        for r in vector_results
    }
    print(f"  vector: {len(similar_map)} similar profiles in store")

    scored: list[dict] = []
    for c in candidates[:50]:
        username = c["username"]
        profile = fetch_user_profile(username)
        posts = fetch_user_posts(username, limit=5)

        s = score_candidate(username, profile, posts, similar_map)
        profile_text = build_profile_text(username, profile, posts)

        scored.append({
            "username": username,
            "score": s,
            "profile_text": profile_text,
            "profile": profile,
            "posts": posts,
        })
        time.sleep(0.3)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today = _now_et().strftime("%Y-%m-%d")
    print(f"[{_now_et().isoformat()}] Weekly Follower Scan — {today} ET")

    state = load_follow_state()
    week_number = state["week_number"]
    last_date = state["last_follow_date"]
    already_following = state["following"]

    force = os.environ.get("FORCE_RUN", "").lower() in ("true", "1", "yes")
    if not force and already_followed_this_week(last_date):
        print(f"  Already ran this week (last: {last_date}). Exiting.")
        print("  To force a re-run, set FORCE_RUN=true in the workflow dispatch.")
        return

    new_week = week_number + 1
    quota = follows_this_week(new_week)
    print(f"  Week {new_week} — targeting {quota} new follow(s)")
    print(f"  Currently following {len(already_following)} users")

    candidates = discover_candidates(already_following)
    if not candidates:
        print("  No candidates found — exiting.")
        return

    scored = enrich_and_score(candidates, already_following)
    print(f"  Top 5 candidates: {[(c['username'], c['score']) for c in scored[:5]]}")

    followed: list[dict] = []
    for candidate in scored:
        if len(followed) >= quota:
            break

        username = candidate["username"]
        score = candidate["score"]

        user_id = str(candidate["profile"].get("id") or candidate["profile"].get("user_id") or "")
        print(f"\n  → {username} id={user_id or '?'} (score: {score})")
        if follow_user(username, user_id=user_id or None):
            print(f"    ✓ followed")

            # Store profile in Upstash Vector
            meta = {
                "username": username,
                "followed_date": today,
                "week": new_week,
                "score": score,
                "engagement": 0.0,
            }
            ok = upsert_user(username, candidate["profile_text"], meta)
            print(f"    vector upsert: {'✓' if ok else '✗ (non-blocking)'}")

            bio = candidate["profile"].get("bio") or ""
            topics = [kw for kw in _RELEVANCE_KEYWORDS
                      if kw in (bio + candidate["profile_text"]).lower()]

            followed.append({
                "username": username,
                "score": score,
                "topics": topics[:6],
            })
        else:
            print(f"    ✗ follow failed")

        time.sleep(random.uniform(3, 8))

    # Always write the JSON log — even on zero follows, so git add never fails
    append_json_log(new_week, today, followed)

    if not followed:
        print("\n  No successful follows this run.")
        return

    record_follows(new_week, today, followed)
    print(f"\n[{_now_et().isoformat()}] Done — followed {len(followed)} users (week {new_week})")
    for u in followed:
        print(f"  @{u['username']} (score:{u['score']}, topics:{u['topics']})")


if __name__ == "__main__":
    main()
