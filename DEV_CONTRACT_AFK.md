# DEV_CONTRACT_AFK.md — inshust development contract (ultra-tdd / AFK)

**Single source of truth for the iron rules in THIS repo.** Every dev/verify agent
MUST Read this file in full before starting, and relay these rules to any sub-agent
it summons.

inshust is a **standalone, greenfield, single-repo Python project** — NOT the
multi-repo Postgres/Redis/Kafka stack the generic ultra-tdd rules assume. The rules
below are the authoritative, project-adapted set and supersede the generic ones.

## Verified environment

- Repo: `/home/jacc/projects/inshust` — GitHub `Jaccxc/inshust` (private).
- Python **3.12**, managed with **uv**. Package `inshust` under `src/inshust/` (src layout).
- Test: `uv run pytest`  •  Lint: `uv run ruff check .`  •  Format: `uv run ruff format .`
- `gh` is authenticated as `Jaccxc` on github.com. Use `gh issue view/comment/close <#> --repo Jaccxc/inshust`.
- **No database server.** Persistence = **SQLite via the stdlib `sqlite3`**, file at
  `./data/inshust.db` locally (overridden to `/data/inshust.db` on Fly). No Postgres,
  no alembic, no Redis, no Kafka, no psql.
- Behavior & module design source of truth = **PRD issue #1** + each card's issue body
  (read via `gh`). Identifier/field names follow the PRD.

## Iron rules

1. **Branch flow.** `main` is the work branch. Create `feat/<issue#>-<slug>` from `main`;
   when green, merge back with `git merge --no-ff` (**NEVER squash — preserve full
   commit history**). There are no `dev`/`stg`/`prd` branches; do not create them.
2. **Do NOT push.** Commit + merge **locally** only. The manager pushes `origin/main`
   at the end of the run. Do not `git push`.
3. **TDD is mandatory.** Tracer-bullet vertical slices: one failing test (RED) → minimal
   code to pass (GREEN) → refactor when green → repeat. Do NOT write a pile of tests then
   a pile of code. Tests assert **behavior through public interfaces**, never
   implementation details. The commit history must visibly show the RED→GREEN cadence.
4. **No live network in tests.** `instagrapi.Client` is ALWAYS mocked or replaced by a
   fake. No test logs into Instagram or hits the network. The real account is only for
   manual E2E (cards #9/#10), never these cards.
5. **SQLite schema, done right.** Real columns with precise types (no structured data
   dumped into a JSON/TEXT blob). Store timestamps consistently (epoch seconds or ISO-8601
   UTC). Index lookup columns (`media_pk`, `message_id`). Enum-like fields stored as TEXT
   with app-layer validation (no magic ints, no DB-native enum). Keep it simple but
   correct — this is a tiny embedded schema, not Postgres.
6. **Deep delegation.** Summon sub-agents for chores (adding deps via `uv add`, running
   lint, exploring `instagrapi` internals, reading legacy). Relay every rule to them.
7. **Keep the baseline green.** `uv run pytest` AND `uv run ruff check .` must pass
   before merging a card. Run `uv run ruff format .` so formatting is clean.
8. **Issue closeout.** When a card is done & green, comment the result on its issue
   (`gh issue comment <#> --repo Jaccxc/inshust`) and **close it**
   (`gh issue close <#> --repo Jaccxc/inshust`) — AFK cards have no manual follow-up.
9. **Stay in scope.** Implement exactly the card's slice. Don't pull work from later cards;
   leave clean seams (ports/interfaces) so the next slice extends without rewrites.

Generic ultra-tdd rules about Postgres/alembic/psql/Redis/Kafka/no-FK/no-DB-enum/DB
migrations **do not apply** — there is no such infrastructure here; rule 5 governs storage.
