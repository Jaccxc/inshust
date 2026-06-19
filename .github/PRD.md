## Problem Statement

I run an Instagram **group chat** with friends. People constantly share reels and
posts, and the same reel/post often gets re-shared by someone who didn't notice it
was already posted. I want a bot account sitting in the group that automatically
notices when a reel/post has been shared before and calls it out by replying to the
new share with who shared it first and when — ideally as a native quote-reply that
points back to the original message.

Instagram's official Graph/Business API cannot read arbitrary group-DM history or
receive group messages, so the official API is a dead end for this. I want a simple,
self-hosted Python solution I can first run on my own computer and later move to a
small always-on host.

## Solution

A long-running Python bot, logged in as a **dedicated burner Instagram account** that
is a member of the group, built on the unofficial private API library `instagrapi`.

The bot maintains a persistent index of every reel/post ever shared in the watched
group thread (keyed by the media's stable identifier). It receives new messages in
near-real-time via the private MQTT realtime channel, with a periodic polling pass as
a dependable safety net. When a newly shared reel/post matches one already in the
index, the bot replies in the group — quoting the new share — with a message like
"already sent by @alice on 2026-06-10 14:32". First-time shares are silently recorded.

History is loaded **once** by a separate, manually-run backfill script that walks the
group's full message history with deliberately human-like pacing and writes everything
into a local SQLite database. The live bot never backfills on startup; it only reads
and extends that database. When I'm ready to move from my computer to Fly.io, I ship
the already-populated SQLite file to the host so the backfill is never repeated.

## User Stories

1. As a group member, I want the bot to reply when I re-share a reel/post that was already shared, so that I know it's a repeat and who shared it first.
2. As a group member, I want the bot's reply to quote/point at my new message, so that it's obvious which share it's talking about.
3. As a group member, I want the reply to name the original sender and the date/time they shared it, so that I have the full context.
4. As a group member, I want first-time shares to pass silently (no reply), so that the bot only speaks up for genuine duplicates.
5. As a group member, I want the same reel recognized as a duplicate whether it was shared as a reel, a feed post, or a rich "xma" share card, so that re-shares in any form are caught.
6. As a group member, I do not want the bot to reply multiple times to the same duplicate message, so that it doesn't spam the chat.
7. As the operator, I want the bot to log in as a dedicated burner account, so that my main account is never at risk.
8. As the operator, I want the session/device to be persisted and reused, so that the bot does not re-login on every start (which raises ban risk).
9. As the operator, I want 2FA (TOTP) and login challenges handled, so that the bot can authenticate without me babysitting it, and pause + alert me when manual action is genuinely required.
10. As the operator, I want to route the account through an optional residential/mobile proxy via configuration, so that I can lower detection risk.
11. As the operator, I want all API actions spaced with randomized human-like delays, so that the automation is less detectable.
12. As the operator, I want to run the bot end-to-end on my own computer first, so that I can validate it for a few days before deploying anywhere.
13. As the operator, I want a separate one-shot backfill script I run by hand, so that the group's entire existing history is recorded into the index exactly once.
14. As the operator, I want the backfill to mimic a real client (consistent device/headers, naturally-spaced requests, optional cap on messages walked), so that the large one-time read is less likely to trip detection.
15. As the operator, I want the backfill to write into a SQLite file that I can then ship to Fly.io, so that I never have to re-run the backfill in production.
16. As the operator, I want the bot to receive new messages in near-real-time, so that duplicate call-outs feel immediate.
17. As the operator, I want a polling safety net independent of realtime, so that the bot keeps catching duplicates even if the experimental MQTT transport breaks.
18. As the operator, I want realtime and polling to never produce a double reply for the same message, so that the two sources are safely deduplicated.
19. As the operator, I want the bot to survive restarts without re-replying to messages it already handled, so that restarts are safe.
20. As the operator, I want clear configuration via environment variables / a .env file, so that I can point the bot at the right account, group thread, paths, timezone, and pacing without editing code.
21. As the operator, I want the bot to discover the group's thread id easily, so that I can configure which group it watches.
22. As the operator, I want the "already sent at <time>" timestamp rendered in my timezone (Asia/Taipei), so that the times are meaningful to the group.
23. As the operator, I want the bot to retry/back off when Instagram returns transient errors on group sends, so that a flaky send doesn't silently drop a reply.
24. As the operator, I want the bot to pause and alert me (rather than hammering the API) when it hits a checkpoint/challenge, so that I can react and the account is protected.
25. As the operator, I want structured logs of what the bot saw and did, so that I can audit its behavior during the local trial.
26. As the operator, I want the bot to gracefully ignore non-media messages (text, links, stickers), so that only reels/posts drive dedupe logic.
27. As a developer, I want the media-identity, dedupe, store, and reply-formatting logic to be pure and testable without any network, so that the core behavior is covered by fast unit tests.
28. As a developer, I want the instagrapi calls isolated behind a thin adapter, so that the whole pipeline can be integration-tested with a fake client and no live account.
29. As a developer, I want live Instagram access required only for final manual end-to-end testing, so that all development and CI run offline with mocks.

## Implementation Decisions

**Language/tooling:** Python 3.12, managed with `uv` (already scaffolded). Library: `instagrapi` 2.16.10 (verified to expose group-thread read, shared-media fields, `direct_send(reply_to_message=...)`, MQTT realtime via `realtime_connect`/`realtime_on`/`realtime_read_once`, FBNS, session dump/load, `totp_generate_code`, challenge handlers). SQLite via the stdlib. No web framework — this is a worker, not a server.

**Module breakdown (favoring deep, isolated, testable modules):**

- **Config** — Loads typed configuration from environment / `.env` (account creds, TOTP seed, thread id, session path, db path, proxy, timezone, poll interval, action-delay range, backfill pacing + optional message cap, log level). No business logic.
- **Media identity (pure)** — Given a direct message, detect whether it carries a shared reel/post and produce a stable reference. Handles `clip`, `media_share` (full Media objects with `pk`/`code`), and `xma_share`/`generic_xma` (URL-only). Pure detection returns a "share reference" (kind + pk/shortcode if present, else a URL); returns nothing for non-media messages. Story shares are best-effort/out of scope for v1.
- **Media resolver (I/O, injectable)** — Turns a share reference into a canonical media key (media `pk` + shortcode). When only a URL is available (xma shares), resolves it via the client's `media_pk_from_url`, caching URL→pk results in the store for determinism and rate-limit safety. The Instagram client is injected so this is testable with a mock.
- **Store (deep, SQLite)** — Encapsulates persistence behind a small interface: look up whether a media key has been seen (returning the original share record), record a first-seen share (media pk, shortcode, first sender username + user id, first sent timestamp, original message id, original client context, thread id), the URL→pk resolution cache, and a processed-message-id set (`is_processed`/`mark_processed`). Owns the schema and migrations. Tested against a temporary real SQLite database.
- **Dedupe (deep, pure orchestration)** — The core decision. Given a normalized incoming media event (media key, sender, timestamp, message id) and the store, returns a decision: FIRST_SEEN (and records it) or DUPLICATE (returning the original record). Enforces idempotency: a message id already processed yields a no-op. Pure given an injected store; the heart of the app and the most heavily tested module.
- **Reply formatter (pure)** — Given the original share record and the configured timezone, produces the reply text ("already sent by @X on <localized date/time>"). Pure and trivially testable.
- **Instagram client adapter (thin I/O boundary)** — Wraps `instagrapi.Client`: login with session persistence (dump/load settings), TOTP + challenge handling (pause + alert, do not loop), optional proxy + locale/device consistency, action-delay range, fetch paginated thread history, send a reply (quoting the original message, with a plain-text fallback if native quote-reply can't be constructed), connect realtime + register a new-message handler, and a polling fetch of recent thread messages. No business logic lives here.
- **Listener / app (orchestration)** — Wires the two message sources (realtime primary + polling safety net) into one normalized stream → media identity → resolver → dedupe → (on duplicate) reply. Manages the run loop, restart safety, retry/backoff on transient group-send errors, and structured logging. Integration-tested with a fake client.
- **Backfill (one-shot script, separate entry point)** — Walks the full thread history (or up to an optional cap) with human-like pacing (randomized delays via an injectable sleeper) and consistent device/headers, feeding each message through media identity + resolver into the store. Records first-seen shares only; sends no replies. Designed to be run by hand exactly once, producing the SQLite file that is later shipped to Fly.

**Key behavioral decisions:**
- Canonical dedupe key is the media `pk` (equivalently shortcode); never message text or raw URL.
- Realtime is the low-latency primary; polling (~45s default) is the dependable backbone. Both go through the same processed-message-id gate so neither double-replies.
- Reply uses native quote-reply (`reply_to_message`) when possible; if the library can't construct/accept it for a group thread (a known unverified area), fall back to a plain reply that still names the original sender + time. Wrap sends in retry/backoff (group `broadcast/text` 500s are documented).
- On `challenge_required`/checkpoint: stop acting, alert the operator, and back off rather than retrying aggressively.
- Paths default to `./data/...` for local Phase 1; Fly overrides to the `/data` volume. The bot does not backfill on start.

## Testing Decisions

- **What makes a good test here:** assert observable behavior through public interfaces, not internals. For dedupe, that means "given these incoming shares, these replies are produced and these records stored" — not which private helper was called. No test touches the live Instagram API or the network; the `instagrapi.Client` is always mocked or replaced by a fake.
- **Unit-tested (pure / fast):** Config parsing; Media identity detection across clip / media_share / xma_share / non-media inputs; Dedupe decisions (first-seen vs duplicate, idempotency on repeated message ids, ordering); Reply formatter (timezone rendering, username/time formatting).
- **Store:** tested against a temporary real SQLite database — record/lookup round-trips, processed-id set, URL→pk cache, and behavior across a simulated restart (reopen the same file).
- **Media resolver:** tested with a mock client to confirm URL→pk resolution and caching (resolve once, reuse).
- **Backfill:** tested with a mock client returning scripted history pages and a fake sleeper — asserts the store is populated correctly, no replies are sent, and human-like pacing is invoked (delays requested between requests, cap honored).
- **Listener / orchestration:** integration test with an in-memory fake client that emits scripted realtime + polling messages — asserts duplicates trigger exactly one reply, first-seen shares are recorded silently, realtime+polling overlap does not double-reply, and restart does not re-reply already-processed messages.
- **Adapter:** the thin instagrapi wrapper is exercised via the fake/mock at the integration layer; its session-load/save and challenge-handling decisions are unit-tested with a mocked `instagrapi.Client` (no real login).
- **Prior art:** greenfield repo — these tests establish the patterns (a baseline smoke test already exists). Live end-to-end validation is a manual step performed once the real burner account is provided.

## Out of Scope

- Instagram official Graph/Business API integration.
- Watching more than one group thread at a time (single configurable thread for v1).
- Detecting duplicates of story shares, plain links, or non-reel/post content (best-effort at most; reels + feed posts + their xma share cards are the target).
- Sending media, slash-commands, leaderboards, or any bot feature beyond duplicate call-outs.
- A web UI/dashboard or hosted control plane.
- Automated provisioning of proxies or accounts; the operator supplies these via config.
- Fully automated, unattended challenge/CAPTCHA solving (the bot pauses and alerts instead).

## Further Notes

- Risk is the dominant concern: private-API automation violates Instagram's Terms and can lead to checkpoint/ban. Mitigations (burner account, persisted single device/session, optional residential/mobile proxy, randomized delays, pause-on-challenge) are first-class requirements, not afterthoughts.
- The MQTT realtime path in instagrapi is explicitly experimental and recently added; the polling safety net is what guarantees the feature keeps working if the private transport changes server-side.
- Native group quote-reply (`reply_to_message`) and empty `client_context` are known unverified areas — the plain-reply fallback exists specifically to de-risk this; it will be confirmed against the real group during manual E2E.
- Development sequence: this PRD → break into independently-grabbable tracer-bullet issues (`/prd-to-issues`) → implement via TDD with delegated agents. All development proceeds offline with mocks; the real account is only needed for the final manual end-to-end test.
