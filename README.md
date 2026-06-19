# inshust

An Instagram **group-chat** bot that remembers every reel/post shared in a Direct
group thread, and when someone re-shares the **same** reel/post, replies to the
original message: *"already sent by @X at &lt;time&gt;"*.

> **Status:** project scaffold. The bot's logic is built feature-by-feature via
> TDD — see the PRD and the GitHub issues. Running `inshust` today just prints a
> placeholder.

## Why this design

The official Instagram Graph/Business API **cannot** read arbitrary group-DM
history or receive group messages, so this uses the unofficial private API via
[`instagrapi`](https://github.com/subzeroid/instagrapi) (actively maintained,
Python). Capability was verified against the installed build (`2.16.10`):

- Read group thread + full history — `direct_thread(thread_id, amount)` paginates via `oldest_cursor`
- Detect a shared reel/post — `DirectMessage.clip` / `.media_share` / `.xma_share`
- Stable dedupe key — media `pk` / `shortcode` (resolve `xma_share` URLs via `media_pk_from_url()`)
- Reply to the original message — `direct_send(..., reply_to_message=<original>)` (native quote-reply)
- Realtime receive — `realtime_connect()` + `realtime_on(event, handler)` (MQTT/FBNS)
- Session reuse / 2FA — `dump_settings`/`load_settings`, `totp_generate_code`, challenge handlers

**It cannot run on Vercel.** Serverless functions can't hold the long-lived
MQTT connection the realtime listener needs, and free-tier cron only fires once
per day. It's a single **always-on worker** — run locally on your own machine
first (Phase 1), then deploy to Fly.io / Docker with a durable volume for the
session blob + SQLite (Phase 2).

### Architecture (target)

```
                ┌──────────── MQTT realtime (primary, low-latency) ───────────┐
 Instagram DM ──┤                                                              ├──► dedupe handler ──► reply
                └──────────── polling every ~45s (safety net) ────────────────┘            │
                                                                                            ▼
                                                              SQLite: seen media (pk → first sender, time,
                                                              original message id) + processed message ids
```

Realtime is the fast path; polling is the dependable backbone so the bot keeps
working if Instagram changes the (experimental) private MQTT transport.

History is loaded once by a **separate manual backfill script** (human-like
pacing) into the SQLite db; the live bot only reads/extends that db — it never
backfills on start.

## Tech stack

- Python 3.12, managed with [uv](https://docs.astral.sh/uv/)
- `instagrapi` (Instagram private API), `python-dotenv`
- SQLite (stdlib) for the seen-index
- Tests: `pytest` + `pytest-mock`; lint/format: `ruff`
- Deploy: Docker → Fly.io (always-on worker)

## Local development

```bash
uv sync                  # create .venv and install deps (incl. dev group)
cp .env.example .env     # then fill in a DEDICATED burner account + thread id
uv run inshust           # run the bot (placeholder until features land)
uv run inshust-backfill  # one-shot history backfill into ./data/inshust.db (run by hand once)
uv run pytest            # run tests
uv run ruff check .      # lint
uv run ruff format .     # format
```

### Run plan (phased)

**Phase 1 — local end-to-end on your own machine.** No inbound ports are needed,
so the bot runs fine locally. Run it here for a few days until you're satisfied.

**One-time history backfill (manual).** A separate one-shot script walks the
group's full history with human-like pacing and writes it into `./data/inshust.db`.
You run it by hand **once**; the bot itself never backfills on start.

**Phase 2 — Fly.io.** Once happy, deploy the always-on worker and ship the
already-populated `inshust.db` to the `/data` volume, so backfill is never re-run.

## Deploy (Fly.io)

```bash
fly apps create <your-app-name>
fly volumes create inshust_data --size 1 --region nrt
fly secrets set IG_USERNAME=... IG_PASSWORD=... IG_TOTP_SEED=... IG_PROXY=...
fly deploy

# ship the locally-built, pre-populated SQLite db to the /data volume (once):
fly ssh sftp shell   # then:  put ./data/inshust.db /data/inshust.db
```

## ⚠️ Risk & Terms of Use

Automating a personal Instagram account via the private API **violates Instagram's
Terms** and carries a real, non-trivial risk of checkpoint/ban — the `instagrapi`
maintainer states best practices still don't make you "safe." Mitigations baked
into the design: use a **dedicated burner account** (never your main), persist one
device/session, route through one stable **residential/mobile proxy**, add random
action delays, and pause on `challenge_required`. Use at your own risk.

## Roadmap

1. ✅ Feasibility research + scaffold (this repo)
2. 📝 PRD (`/write-a-prd`) → GitHub issue
3. 🧩 Break PRD into issues (`/prd-to-issues`)
4. 🔁 Implement via TDD (`ultra-tdd`)

## Repo layout

```
src/inshust/        # package (entry point: `inshust`); feature modules added via TDD
tests/              # pytest suite
Dockerfile          # uv-based image for the always-on worker
fly.toml            # Fly.io worker config (+ /data volume)
.env.example        # configuration reference
```
