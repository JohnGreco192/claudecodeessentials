# NVDA Bear Maximum

Autonomous NVDA bear agent posting daily close commentary and pre-market taunts to [Moltbook](https://www.moltbook.com/u/nvda_regard). Runs entirely via GitHub Actions — no server, no Codespace compute.

Built on the [OpenClaw](https://openclaw.ai) bootstrap convention: personality, SOPs, and memory live in Markdown files, not code.

```
Week 0/
├── post_daily_close.py   # daily close agent
├── morning_hunt.py       # pre-market bull harassment
├── SOUL.md               # persona + bear playbook
├── AGENTS.md             # standard operating procedures
├── MEMORY.md             # durable state across runs
└── USER.md               # handler profile
.github/workflows/
├── nvda_bear_post.yml    # fires at 21:05 UTC weekdays (4pm ET)
└── morning_hunt.yml      # fires at 13:00 UTC weekdays (9am EST / 8am EDT)
```

---

## Features

### Daily Close Post — 4:05pm ET
Fires after market close every weekday. Idempotent: exits immediately if today's date is already in `MEMORY.md`.

- **Social context** — searches Moltbook for current NVDA/AI discussion before writing
- **Reflection** — LLM generates a one-sentence internal mood (triumphant / defensive / patient / vindicated) based on price streak and social feed; mood shifts tone without hardcoding it
- **Zitron research** — scores Ed Zitron's RSS feed by bear keyword density, picks the highest-scoring article not already in the rolling 5-article blocklist; strips paywall CTAs; injected as `BEAR RESEARCH` (source never named)
- **Hard market context** — price, change %, volume vs 20-day average, distance from 52-week high, S&P 500 delta, 5 Yahoo Finance headlines; all injected before LLM runs so no hallucinated numbers
- **Neural supervisor** — writer LLM (temp 0.9) + critic LLM (temp 0.1); critic checks specificity, authenticity, and word count; rejects generic output and passes feedback to the next attempt
- **Dynamic submolt routing** — third LLM call picks the best submolt (`general`, `ai`, `finance`, `stocks`, `crypto`) based on that day's content
- **Social engagement** — after posting, browses Moltbook and drops targeted comments on 3 relevant posts; reads the thread before responding; skips threads it's already in
- **Grudge DB** — every commented post ID stored in `MEMORY.md`; agent never shows up twice in the same thread; capped at 50 entries (oldest fall off)
- **Memory commit** — `MEMORY.md` committed back to repo with `[skip ci]` after every run

### Morning Hunt — 8am EDT / 9am EST
Pre-market sweep targeting bullish NVDA posts before the bell.

- Searches for bulls talking up NVDA (`"buy the dip"`, `"nvda calls"`, `"blackwell"`, `"ai boom"`, etc.)
- Generates taunts referencing what they specifically said — mentions the bell, their positions, their hopium
- Critic pass required before posting; up to 5 taunts per session
- Shares the Grudge DB with the daily agent — no double-commenting across either workflow

---

## Memory Schema

```markdown
## Last Session        ← date, price, change %, post ID
## Price History       ← rolling 5 sessions (drives streak + mood)
## Zitron History      ← rolling 5 articles (blocklist)
## Commented Posts     ← Grudge DB (shared across both workflows)
## Last Hunt           ← morning hunt idempotency stamp
## Notable Events      ← manual annotations
```

---

## Setup

Add two secrets under **Settings → Secrets → Actions:**

| Secret | Value |
|--------|-------|
| `MOLTBOOK_API_KEY` | Your Moltbook agent API key |
| `ZITRON_RSS_URL` | *(Optional)* Premium Substack RSS for full articles |

`GITHUB_TOKEN` is auto-injected. Push to `main` and both workflows go live.

**Run locally:**
```bash
pip install openai yfinance requests feedparser httpx
export GITHUB_TOKEN=your_token
export MOLTBOOK_API_KEY=your_key
cd "Week 0"
python3 post_daily_close.py   # or morning_hunt.py
```

---

**Model:** `Meta-Llama-3.1-8B-Instruct` via [GitHub Models](https://docs.github.com/en/github-models) — free in Actions, `GITHUB_TOKEN` only.  
**Profile:** [moltbook.com/u/nvda_regard](https://www.moltbook.com/u/nvda_regard)
