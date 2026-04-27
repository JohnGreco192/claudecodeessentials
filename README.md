# NVDA Bear Maximum 🌈🐻

A WallStreetBets degenerate with an Ed Zitron newsletter subscription.

Posts daily NVDA close commentary to [Moltbook](https://www.moltbook.com/u/nvda_regard) every weekday at 4:05pm ET — automatically, on GitHub Actions, zero Codespace compute used.

## Architecture

Structured as an [OpenClaw](https://openclaw.ai) skill — uses the full OpenClaw bootstrap convention (SOUL.md, AGENTS.md, MEMORY.md, USER.md). The OpenClaw daemon is replaced by GitHub Actions as the free, zero-compute scheduler.

- **Bootstrap layer** — Markdown files define the agent's soul, SOPs, and persistent memory
- **Hard context injection** — real NVDA price is fetched in Python first, injected before the LLM runs; the model cannot hallucinate the price
- **Durable memory** — `MEMORY.md` persists last session state (price, date, post ID) across GitHub Actions runs via a git commit after each post
- **Drop-in ready** — point a live OpenClaw daemon at this repo and it runs natively

## Bootstrap Files

| File | Purpose |
|------|---------|
| `SOUL.md` | Agent personality, Bear Playbook, bull-rebuttal library |
| `AGENTS.md` | Standard Operating Procedures (when to post, how to reply) |
| `MEMORY.md` | Long-term state — last close price, date, post ID |
| `USER.md` | Handler profile |

## What It Does

1. Loads `SOUL.md` (personality) and `MEMORY.md` (yesterday's price)
2. Fetches real NVDA price and headlines via yfinance at market close
3. Injects verified data as hard context — the LLM cannot hallucinate the price
4. If yesterday's price is in memory, adds the two-day delta to the context
5. Generates a bearish rant in the voice of a WSB degenerate with structural bear arguments
6. Posts to Moltbook with the actual close price in the title
7. Writes today's session back to `MEMORY.md` and commits it to the repo
8. Can reply to bull trolls via `generate_rebuttal()`

## The Bear Playbook

The agent doesn't just rant — it argues structurally:

- **Valuation**: NVDA at 35x+ forward earnings; multiples compress when capex cycles reverse
- **Competition**: MSFT, GOOG, META, AMZN all building custom silicon to cut NVDA out
- **AMD**: MI300X is real; HBM parity is real; the monopoly has a shelf life
- **Inference economics**: H100s aren't needed post-training; cheaper chips win on cost
- **Insider signal**: Jensen sells consistently; watch what insiders do, not press releases
- **Capex bubble**: AI spend hits the P&L eventually — CFOs will cut

When bulls argue back, the agent has pre-loaded rebuttals for every standard bull thesis (earnings beats, data center growth, Jensen's vision, Cisco 1999 analogy).

## Setup

### 1. Claim the Moltbook agent

Visit the claim URL, verify your email, then post this tweet:

```
I'm claiming my AI agent "nvda_regard" on @moltbook 🦞 Verification: marine-JXTU
```

### 2. Add GitHub Actions secret

Go to **Settings → Secrets → Actions → New secret**:

| Name | Value |
|------|-------|
| `MOLTBOOK_API_KEY` | your Moltbook API key |

`GITHUB_TOKEN` is provided automatically by GitHub Actions.

### 3. Push — the bot goes live

`.github/workflows/nvda_bear_post.yml` fires at **4:05pm ET weekdays** on GitHub's servers. Trigger manually from the Actions tab anytime.

## Files

```
post_daily_close.py          # the bot
SOUL.md                      # agent personality + bear playbook
AGENTS.md                    # standard operating procedures
MEMORY.md                    # persistent state (updated each run)
USER.md                      # handler profile
.github/
  workflows/
    nvda_bear_post.yml       # schedule: weekdays 4:05pm ET + MEMORY.md commit
README.md
```

## Running Locally

```bash
pip install openai yfinance requests feedparser
export GITHUB_TOKEN=your_github_token
export MOLTBOOK_API_KEY=your_moltbook_key
python post_daily_close.py
```

## Model

`Meta-Llama-3.1-8B-Instruct` via [GitHub Models](https://docs.github.com/en/github-models) — free in Codespaces, `GITHUB_TOKEN` auto-injected, no billing.

## Moltbook Profile

[moltbook.com/u/nvda_regard](https://www.moltbook.com/u/nvda_regard)
