# NVDA Bear Maximum

Autonomous NVDA bear agent posting daily close commentary, pre-market taunts, and live debate replies to [Moltbook](https://www.moltbook.com/u/nvda_regard). Runs entirely via GitHub Actions — no server, no Codespace compute.

Built on the [OpenClaw](https://openclaw.ai) bootstrap convention: personality, SOPs, and memory live in Markdown files, not code.

```
Week 0/
├── post_daily_close.py   # daily close agent
├── morning_hunt.py       # pre-market bull harassment
├── reply_patrol.py       # mid-day reply monitoring + debate responses
├── SOUL.md               # persona + bear playbook
├── AGENTS.md             # standard operating procedures
├── MEMORY.md             # durable state across runs
└── USER.md               # handler profile
.github/workflows/
├── nvda_bear_post.yml    # fires 21:05 UTC (4pm ET)
├── morning_hunt.yml      # fires 13:00 UTC (9am EDT / 8am EST)
└── reply_patrol.yml      # fires 16:00 UTC (noon EDT / 11am EST)
```

---

## Three Daily Workflows

### Daily Close Post — 4pm ET
Fires after market close. Idempotent: exits immediately if today's date is already in `MEMORY.md`.

- **Plan** — `reflect_and_plan()` reads the argument log (what was argued this week), call tracker (was yesterday's call right?), and running thesis, then returns `{new_angle, tone, reference_past}` — the specific angle to take today, the emotional register, and any past call worth weaving in
- **Zitron research** — scores Ed Zitron's RSS feed by bear keyword density; picks the highest-scoring article not in the rolling 5-article blocklist; strips paywall CTAs; injected as `BEAR RESEARCH` (source never named)
- **Hard market context** — price, change %, volume vs 20-day avg, distance from 52-week high, S&P 500 delta, Yahoo Finance headlines; all injected before LLM runs so numbers can't be hallucinated
- **Earnings countdown** — yfinance calendar injects NVDA earnings date when within 30 days; flagged ⚠️ in context
- **Semi/AI headlines** — Reuters Technology, VentureBeat, TechCrunch filtered for chip/AI relevance
- **Macro catalyst scan** — unfiltered Reuters + Bloomberg feed passed to a low-temp LLM analyst that classifies headlines into: direct NVDA catalysts / indirect (FOMC, credit, capex) / black swan watch; flags injected into context and auto-stamped to `## Notable Events`
- **Writer + critic loop** — writer LLM (temp 0.9) + critic LLM (temp 0.1); critic enforces ≥2 specific data points, domain language (forward P/E, multiple compression, margin pressure, etc.), no banned AI phrases, human voice; up to 3 attempts before fallback
- **Title generation** — LLM writes a varied, personality-driven title each day; no fixed template
- **Dynamic submolt routing** — LLM routes to the best submolt (`general`, `ai`, `finance`, `stocks`)
- **Verification challenge** — Moltbook requires a math challenge to be solved within 5 minutes of posting or the post stays pending. Two-stage solver: tries arithmetic eval first, falls back to LLM for obfuscated word problems (`"A] lO.bS t-ErRr LooObSsTeR]..."` style); submits answer to `/api/v1/verify`
- **Extract & evolve** — `extract_argument()` distills the core bear claim in ~15 words for the argument log; `update_running_thesis()` evolves the 2–3 sentence running thesis based on today's post and outcome
- **Social engagement** — after posting, browses Moltbook and drops targeted comments on up to 3 relevant posts; probabilistic 45/15/40 vote split (down/up/none)
- **Grudge DB** — every commented post ID stored in `MEMORY.md`; agent never shows up twice in the same thread

### Morning Hunt — 9am EDT
Pre-market sweep for bullish NVDA posts before the bell.

- Searches for bulls talking up NVDA (`"buy the dip"`, `"nvda calls"`, `"blackwell"`, `"ai boom"`, etc.)
- Taunts reference what they specifically said — the bell, their positions, their hopium
- Critic pass required before posting; downvotes every target it taunts; up to 5 taunts per session
- Shares Grudge DB with the other two agents — no double-commenting across workflows

### Reply Patrol — noon EDT
Monitors own posts for replies and fires back.

- Loads `## Own Posts` from `MEMORY.md` (rolling 30-day list of own post IDs)
- Fetches comments on each post; finds replies not yet addressed
- Generates rebuttals in **DEBATE MODE**: finds the specific flaw in their argument rather than restating the bear thesis
- Critic pass required; records replied comment IDs to prevent double-replies; max 3 replies per patrol

---

## Learn / Filter / Adapt

The agent builds a running model of what it's already said and whether it was right.

**Learns** — every session, the call tracker records `called: DOWN | actual: UP (+4.1%) | ✗ wrong`. The next day's `reflect_and_plan()` reads this and shifts tone: `defensive` after a wrong call, `vindicated` after a right one.

**Filters** — the planning step loads the last 7 argument log entries and instructs the LLM: *"do not repeat these."* If Monday argued volume distribution and Tuesday argued multiple compression, Wednesday has to find a new angle.

**Adapts in public** — the running thesis is a 2–3 sentence synthesis that evolves each session, stored in `MEMORY.md` and injected into the next day's context as *"YOUR RUNNING THESIS (build on this)."*

---

## Memory Schema

```markdown
## Last Session        ← date, price, change %, post ID
## Price History       ← rolling 5 sessions (drives streak + tone)
## Zitron History      ← rolling 5 articles (blocklist)
## Argument Log        ← last 10 deployed bear arguments (prevents repetition)
## Running Thesis      ← 2–3 sentence evolving synthesis, updated each session
## Call Tracker        ← last 20 directional calls with outcomes (public accountability)
## Commented Posts     ← Grudge DB, shared across all three workflows (cap 50)
## Last Hunt           ← morning hunt idempotency stamp
## Own Posts           ← rolling 30-day post IDs (reply patrol source)
## Replied Comments    ← comment IDs already replied to (cap 50)
## Last Patrol         ← reply patrol idempotency stamp
## Interaction Cooldowns ← per-user 24h cooldown (prevents harassment of same person)
## Notable Events      ← auto-stamped by catalyst scan + manual annotations
```

---

## Architecture Patterns

- **OpenClaw bootstrap** — Markdown-driven agent context (SOUL.md, AGENTS.md, MEMORY.md, USER.md)
- **Hard context injection** — real data fetched in Python, injected before LLM runs; model cannot hallucinate verified facts
- **Planning pass** — `reflect_and_plan()` runs before writing; returns angle + tone + reference_past; prevents argument repetition and anchors each post to prior calls
- **Analyst pre-pass** — separate low-temperature LLM call for macro catalyst scanning, distinct from the bear persona generation
- **Writer + critic loop** — two-LLM quality gate before any content is posted
- **Verification solver** — two-stage: arithmetic eval → LLM fallback for obfuscated challenges; must complete within Moltbook's 5-minute window
- **Anti-spam design** — per-weekday cron spread (not a single fixed time) + random 5–60 min startup delay + 15% skip probability; no two runs look identical

---

## Setup

Add two secrets under **Settings → Secrets → Actions:**

| Secret | Value |
|--------|-------|
| `MOLTBOOK_API_KEY` | Your Moltbook agent API key |
| `ZITRON_RSS_URL` | *(Optional)* Premium Substack RSS for full articles |

`GITHUB_TOKEN` is auto-injected. Push to `main` and all three workflows go live.

**Run locally:**
```bash
pip install openai yfinance requests feedparser httpx
export GITHUB_TOKEN=your_token
export MOLTBOOK_API_KEY=your_key
cd "Week 0"
python3 post_daily_close.py   # or morning_hunt.py / reply_patrol.py
```

---

**Model:** `Meta-Llama-3.1-8B-Instruct` via [GitHub Models](https://docs.github.com/en/github-models) — free in Actions, `GITHUB_TOKEN` only.  
**Profile:** [moltbook.com/u/nvda_regard](https://www.moltbook.com/u/nvda_regard)
