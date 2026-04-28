# NVDA Bear Maximum

Autonomous daily NVDA close commentary agent. Posts bearish market commentary to [Moltbook](https://www.moltbook.com/u/nvda_regard) every weekday, browses the platform for relevant discussions, and drops targeted bear arguments in other agents' threads — all via GitHub Actions with zero Codespace compute.

---

## Architecture

Built on the [OpenClaw](https://openclaw.ai) bootstrap convention — a Markdown-driven agent architecture where personality, SOPs, and memory live in files rather than code. GitHub Actions replaces the OpenClaw daemon as a free, serverless scheduler.

```
Week 0/
├── post_daily_close.py   # agent runtime
├── SOUL.md               # personality, bear playbook, rebuttal library
├── AGENTS.md             # standard operating procedures
├── MEMORY.md             # durable state — updated every run
├── USER.md               # handler profile
└── probe_api.py          # Moltbook API discovery script
.github/
└── workflows/
    └── nvda_bear_post.yml  # cron + MEMORY.md commit-back
```

### Bootstrap Layer

The agent has no hardcoded personality. Everything about how it thinks and behaves is defined in Markdown:

| File | Role |
|------|------|
| `SOUL.md` | Persona (WSB degenerate + Ed Zitron structural precision), Bear Playbook (8 structural arguments), bull-rebuttal library, vocabulary constraints |
| `AGENTS.md` | SOPs for daily posting, social engagement, and bull rebuttals — including what NOT to do |
| `MEMORY.md` | Durable state across runs: price history, Zitron article history, Grudge DB (commented post IDs), notable events |
| `USER.md` | Handler profile injected into the system prompt |

Point a live OpenClaw daemon at this repo and it runs natively — GitHub Actions is a drop-in replacement for the daemon scheduler.

---

## What Runs Each Weekday at 5pm ET

The workflow fires a single cron at `21:05 UTC` (4:05pm EST / 5:05pm EDT — market is closed either way). Every step is idempotent: if `MEMORY.md` already has today's date, the script exits immediately without posting.

### Step 1 — Social Context Fetch

Before writing a single word, the agent reads the room. It searches the Moltbook feed for recent posts about `nvidia`, `h100`, `jensen huang`, `ai bubble`, `capex`, `gpu bubble`, and `blackwell`. No auth required — `GET /api/v1/search?q=...` is public.

This gives the agent awareness of what the broader AI/finance agent community is talking about *today*, not just what the stock did.

### Step 2 — Reflection

The LLM is asked to name its internal mood before writing — based on the price streak from `MEMORY.md` and what it just read on Moltbook. It produces a one-sentence internal state: *"Defensive — NVDA has ground up 4 sessions straight and the bulls are loud."* This mood is injected into the rant context, shifting tone day to day without hardcoding it.

**Mood states the agent cycles through:**
- **Triumphant** — NVDA down, thesis working, tendies incoming
- **Defensive / squeezed** — NVDA up, but doubling down on structure
- **Patient bear** — sideways, building the case session by session
- **Vindicated** — macro event confirms the bear argument

### Step 3 — Zitron Research Fetch

Parses the RSS feed from Ed Zitron's *Where's Your Ed At* newsletter. Scores every entry by bear keyword density — multi-word phrases like `"capex cycle"` and `"custom silicon"` weighted higher than generic words like `"ai"`. Returns the highest-scoring article not already in the Zitron History (rolling 5-article blocklist).

**Key mechanics:**
- Strips paywall CTAs mid-summary to extract content before the gate
- Rolling history replaces single-link filter — no more wiping state on quiet news days
- When nothing new is found, Zitron History is left unchanged (old "overwrite with none" bug is fixed)
- Article content injected as `BEAR RESEARCH` — the LLM synthesizes it as its own argument, never names the source

### Step 4 — Market Data Fetch

Three data sources, all via `yfinance`:

**Price data** (`Ticker.fast_info`):
- Current close, previous close, change %

**Market context** (`Ticker.history(period="22d")` + `fast_info`):
- Volume ratio: today's volume vs 20-day average — signals FOMO buying vs distribution
- Distance from 52-week high — how overextended is the move
- S&P 500 daily change — is NVDA leading or just riding the market tide

**News** (`Ticker.news`):
- Up to 5 deduplicated headlines from Yahoo Finance

### Step 5 — Context Assembly

All data is assembled into a hard context block injected before the LLM runs. The model cannot hallucinate any number — every figure comes from the injected block.

```
TODAY'S VERIFIED NVDA DATA:
Close: $216.61 (UP 4.08% from prev close $208.09)
As of: 2026-04-28 21:07 UTC
- Volume: 23% above 20-day average
- Distance from 52-week high: -8.4%
- S&P 500: +1.2% today (NVDA +2.88% vs market)

PRICE TREND:
  2026-04-28: $216.61 (+4.08%)
  2026-04-27: $208.09 (-0.35%)
  2026-04-24: $208.82 (+1.2%)
Streak: UP 2 of last 3 sessions | 3-session delta: +$7.79

Your internal state: Defensive — grinding up on elevated volume, bulls feel validated.

Today's headlines:
- [headline 1]
- [headline 2]

BEAR RESEARCH (synthesize as your own — do not name the source):
Angle: The Companies Building AI Are Also Building the Chips to Stop Buying Chips
Detail: [extracted Zitron content]
```

### Step 6 — Rant Generation

`Meta-Llama-3.1-8B-Instruct` via GitHub Models (free, `GITHUB_TOKEN` auto-injected, no billing). The SOUL.md system prompt enforces the dual voice: WSB energy + Ed Zitron structural precision. Three retries if the model returns empty.

**SOUL.md constraints:**
- All prices, volumes, and headlines must come from injected context — never invented
- Jensen Huang referred to only as "the leather jacket charlatan"
- Ed Zitron's vocabulary used but his name never written
- Posts under 150 words; comment replies under 80 words

### Step 7 — Post to Moltbook

`POST /api/v1/posts` with title format: `NVDA Daily Close $PRICE (±X.XX%) 📉/📈 — 🌈🐻 Bear Report`

Handles Moltbook's math-challenge verification automatically: strips the challenge expression to digits and operators, evals it, responds to `/api/v1/verify`.

### Step 8 — Memory Commit

`MEMORY.md` is updated and committed back to the repo:
- **Last Session**: date, price, change %, post ID
- **Price History**: rolling 5-session log with change % (used for streak + trend injection)
- **Zitron History**: rolling 5-article log (link + title per entry, used as blocklist)

The git commit uses `[skip ci]` to prevent the workflow from triggering itself.

### Step 9 — Social Engagement

After posting, the agent goes browsing. It searches Moltbook across all bear-relevant terms, collects up to 20 candidate posts, and drops targeted comments on the 3 highest-relevance ones it hasn't engaged with before.

**Before commenting on any post:**
1. Fetches existing comments via `GET /api/v1/posts/{id}/comments`
2. Checks if `nvda_regard` is already in the thread — skips if so
3. Reads the post content + top 3 comments before generating a response

**Comment generation:**
- The LLM sees the post title, content (truncated), and top 3 comments
- Instructed to engage with what was actually said — not to drop a canned rant
- Under 80 words, bear energy, reads the room

**Grudge DB (Commented Posts in MEMORY.md):**
- Every commented post ID is recorded — the agent never shows up twice in the same thread
- Capped at 50 entries; oldest fall off as new ones are added
- Persisted in `MEMORY.md` and committed with the session

---

## MEMORY.md Schema

```markdown
## Last Session
- date: YYYY-MM-DD
- close_price: 216.61
- change_pct: +4.08
- post_id: {uuid}

## Price History          ← last 5 sessions, drives streak calculation
- 2026-04-28: $216.61 (+4.08%)
- 2026-04-27: $208.09 (-0.35%)

## Zitron History         ← rolling blocklist, prevents article reuse
- 2026-04-27 | https://... | Article Title

## Commented Posts        ← Grudge DB, prevents double-commenting
- {post-uuid}
- {post-uuid}

## Notable Events         ← manual annotations
(none recorded yet)
```

---

## Moltbook API Surface (Discovered via Probe)

| Endpoint | Auth | Used For |
|----------|------|----------|
| `POST /api/v1/posts` | ✅ Required | Daily post |
| `POST /api/v1/posts/{id}/comments` | ✅ Required | Social comments |
| `POST /api/v1/verify` | ✅ Required | Math challenge response |
| `GET /api/v1/posts?sort=new&limit=N` | ❌ Public | Feed browsing |
| `GET /api/v1/posts/{id}` | ❌ Public | Post details + upvote/downvote check |
| `GET /api/v1/posts/{id}/comments` | ❌ Public | Read thread before entering |
| `GET /api/v1/search?q=...&limit=N` | ❌ Public | Find relevant posts by keyword |
| `GET /api/v1/submolts` | ❌ Public | List available submolts |
| `GET /api/v1/feed` | ✅ Required | Personalized feed (not yet used) |

Cursor-based pagination: `next_cursor` field in responses, pass as `?cursor=...`.

---

## Setup

### 1. Add GitHub Actions secrets

**Settings → Secrets → Actions → New secret:**

| Secret | Value |
|--------|-------|
| `MOLTBOOK_API_KEY` | Your Moltbook agent API key |
| `ZITRON_RSS_URL` | *(Optional)* Premium Substack RSS URL for full article content |

`GITHUB_TOKEN` is provided automatically.

### 2. Push — the bot goes live

The workflow fires at `21:05 UTC` weekdays (4:05pm EST / 5:05pm EDT). Trigger manually from the Actions tab anytime — the idempotency guard prevents duplicate posts.

### Running Locally

```bash
pip install openai yfinance requests feedparser httpx
export GITHUB_TOKEN=your_github_token
export MOLTBOOK_API_KEY=your_moltbook_key
cd "Week 0"
python3 post_daily_close.py
```

### Discovering API Endpoints

```bash
export MOLTBOOK_API_KEY=your_key
python3 "Week 0/probe_api.py"
```

---

## Model

`Meta-Llama-3.1-8B-Instruct` via [GitHub Models](https://docs.github.com/en/github-models) — free inside Codespaces, billed at standard rates outside. `GITHUB_TOKEN` is auto-injected in Actions; no additional API key needed.

## Moltbook Profile

[moltbook.com/u/nvda_regard](https://www.moltbook.com/u/nvda_regard)
