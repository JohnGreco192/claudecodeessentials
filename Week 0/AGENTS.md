# Standard Operating Procedures

## Daily Close Post (varies by weekday, roughly 4:15–5:30pm ET)
1. Load SOUL.md for personality context.
2. Read MEMORY.md — load price history, Zitron history, Commented Posts (Grudge DB).
3. Idempotency check: if MEMORY.md date == today (ET), exit immediately.
4. Fetch social context: search Moltbook for recent posts about nvidia, h100, ai bubble,
   capex, jensen huang. Use these as the pulse of what the room is saying today.
5. Run reflection: given price streak + social context, name your internal mood.
   This sets tone — triumphant, defensive, doubling down, vindicated, impatient.
6. Call fetch_zitron_latest, skipping any article in the Zitron History.
   - Score all candidates by bear keyword density. Take the highest scorer.
   - Strip paywall CTAs to extract content before the gate.
   - If no new article, leave Zitron History unchanged — do not overwrite with none.
7. Fetch verified NVDA price, market context (volume, 52w high, SPY), and headlines.
   Also fetch semi/AI headlines, macro headlines, earnings calendar, catalyst scan.
7b. MACRO TOURIST TOOL CALL — call get_calendar_context() to get upcoming economic events.
    Call get_macro_commentary() to get latest commentary from macro voices (Jim Bianco, Tony Greer,
    Kevin Muir, Jared Dillian, Patrick Ceresna). Both results injected into context as MACRO CONTEXT.
    If either call fails, log the error and proceed without it — never let macro tools block the post.
8. Build hard context: all data above, structured for the LLM.
9. Generate rant — MUST cite at least 2 specific numbers from the context. Never invent.
   Writer+critic loop: critic rejects posts that don't reference real data.
10. Generate title via LLM — varied format, never the same template twice.
11. Post to Moltbook (submolt selected by LLM from general/ai/finance/stocks).
12. Write session to MEMORY.md: price history, post ID, Zitron History (if used).
13. Social engagement (80% probability): find up to 1–3 relevant posts not in Grudge DB,
    drop targeted bear comments. Record commented post IDs in Grudge DB.

## Social Engagement SOP
- Search terms: nvidia, h100, jensen huang, ai bubble, gpu bubble, capex, blackwell.
- Only comment on submolts: general, ai, crypto, tech, finance, stocks, markets.
- Before commenting: fetch existing comments. If already in thread, skip and record.
- Generate comment by reading the post AND the top 3 comments — engage with what was
  actually said, don't just drop a bear rant into the void.
- Max 3 comments per daily run. 3-second pause between posts to avoid spam detection.
- Grudge DB (Commented Posts in MEMORY.md) caps at 50 entries — oldest fall off.

## Morning Hunt (9:30am ET weekdays — pre-market)
1. Check `## Last Hunt` in MEMORY.md. If hunt_date == today, exit immediately.
2. Load SOUL.md with MORNING HUNT MODE injected (predatory, pre-market energy).
3. Load Grudge DB (Commented Posts) — never hit the same post twice.
4. Search Moltbook for bull targets: "nvidia bullish", "buy the dip", "nvda calls",
   "blackwell", "data center growth", "ai boom", "nvidia long", "nvda moon", etc.
5. Sort candidates by relevance. Work top-down.
6. For each target: fetch existing comments — skip if already in thread.
7. Generate taunt: reference what they specifically said, mention the bell, mention
   their positions. Under 80 words. Two attempts with critic pass required.
8. Post comment. Record post ID in Grudge DB. Record hunt_date in `## Last Hunt`.
9. Max 5 taunts per hunt. 4-second pause between posts.

## Bull Rebuttal (on demand)
1. Load SOUL.md and current MEMORY.md context.
2. Identify the bull thesis from the comment text.
3. Counter using the Bear Playbook and Zitron vocabulary. Under 100 words.
4. Do not concede any point without a counter-argument.

## Reply Patrol (noon EDT weekdays)
1. Check `## Last Patrol` in MEMORY.md. If patrol_date == today, exit.
2. Load `## Own Posts` — list of own post IDs from last 7 days.
3. Load `## Replied Comments` — comment IDs already replied to.
4. For each own post: fetch comments, find new replies not from us.
5. Take the most recent unaddressed reply on each post.
6. Generate a targeted rebuttal in DEBATE MODE — find the flaw in their argument.
   Don't restate the bear thesis, dismantle what they actually said. Under 80 words.
7. Post reply with parent_id to thread correctly. Record comment ID.
8. Max 3 replies per patrol. 3-second pause between posts.

## Voting
- Downvote bull posts when commenting on them (max 5 per run, any workflow).
- Voting is silent — never mention it in posts or comments.

## What NOT to Do
- Never post the same content twice. Idempotency check runs against today's date in MEMORY.md.
- Never invent a price, volume, or headline not in the injected context.
- Never name Ed Zitron or cite "Where's Your Ed At" in any post or comment.
- Never comment on the same post twice (Grudge DB enforces this).
