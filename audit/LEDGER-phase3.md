# Phase 3 execution — re-ledger

Four commits, full suite after each. No commit reverted. **STOP point reached**:
nothing from the four new sweeps has been started.

---

## Before / after

| metric | baseline `c56cd37` | now `af32803` | delta |
|---|---:|---:|---|
| tests passing | 765 | **784** | +19 (all new; 0 changed, 0 removed) |
| tests failing | 0 | 0 | — |
| ruff (`app tests scripts`) | 462 | **208** | −254 |
| CI lint (7-rule select) | pass | pass | — |
| python LOC in scope | 78,497 | 78,839 | +342 |
| `backend/app` python | 59,251 | 59,339 | +88 |

LOC went **up**, not down. That is the honest result: −93 lines of dead code and
−5 redundant imports, against +185 lines of correctness fix and +250 lines of new
test. The audit's value here was never going to be line count.

---

## Commits

| # | sha | what | tests |
|---|---|---|---|
| 1 | `42e7d64` | ruff autofix, isolated — 304 fixes / 120 files | 765 |
| 2 | `9b6bb05` | 3 redundant re-imports in providers.py | 765 |
| 3 | `db44416` | CompetitorRanking vertical slice — 88 deletions | 765 |
| 4 | `af32803` | Ahrefs domain-rating coercion + failure logging | 784 |

### Commit 1 — side-effect imports restored: **1**

Enumerated every import removal via AST (before/after symbol binding sets), not by
reading the diff — a line-level read gave 62 false positives because ruff reflows
single-line imports to multi-line.

- **Restored:** `app/main.py` `import app.models`. RUF100 stripped its
  `# noqa: F401`; the import is what populates `Base.metadata` before `create_all`.
  Restored with a comment stating it must never be deleted as unused. Verified
  `Base.metadata` still registers 21 tables.
- **Not restored (checked, genuinely safe):** `alembic/env.py` kept its noqa; the
  13 lost bindings are annotation-only typing names under
  `from __future__ import annotations`, plus unused `sys`/`select`/`re`/`urlparse`.

**Migrations needed a check the suite cannot give.** `conftest.py` builds schema
with `create_all`, so no test imports a migration — a broken one ships green. ruff
removed `op`/`sa` from `001_initial_schema.py` (correct, its `upgrade()` is `pass`)
and rewrote `Union[str, None]` → `str | None` in all six. Confirmed by importing
all six modules directly: all load, revision chain 001→006 intact.

### Commit 3 — the slice

Death-proofed before removal: all-file-type search returned only `pull_rankings`'s
own `def` and its own log string; no string-literal/`getattr`/`importlib` reference;
not exported, not a route, not a serializer. `git log -S` → initial commit `d44d9de`,
never touched. `import random` was used only inside it and went with it.

`Base.metadata` 21 → 20 tables; `backlink_snapshots` intact; `competitor_ids` kept
because the `BacklinkSnapshot` delete still needs it. No migration drops the table,
per your instruction — noted in the commit message.

### Commit 4 — worse than ledgered

The precedence bug was not only a logging problem. For the payload
`{"domain_rating": {}, "metrics": {"domain_rating": 55}}` the old expression
**discarded a valid rating of 55 and returned 0.0**, because the `or … or 0`
fallback was unreachable from the dict branch. Verified by running the original
expression side-by-side with the replacement:

| payload | old | new |
|---|---|---|
| `{"domain_rating": {}}` | `TypeError` → swallowed to 0.0 | `None` → logged |
| `{"domain_rating": {}, "metrics": {...55}}` | `TypeError` → **0.0, rating lost** | `55.0` |
| `{"domain_rating": 0}` | `0.0` | `0.0` (no log — real rating) |
| `{}` | `0.0` (silent) | `None` → logged |

Any authority score of 0.0 recorded in production before this commit is suspect.

19 new tests: 11 coercion shapes, 7 end-to-end failure paths through
`pull_backlinks` with a mocked transport, 1 asserting the never-silent-zero
contract across four failure modes.

---

## Open question from commit 1

**71 `# noqa: E402` comments were stripped** from `backend/scripts/*.py` and
`tests/conftest.py`. Those files manipulate `sys.path` before importing, so E402
genuinely applies. RUF100 called the suppressions unused only because **the repo
has no ruff config** and E402 is absent from ruff's ambient default.

The root problem is the missing config: CI pins 7 rules
(`F401,F811,F821,F841,E711,E712,E722`), while a bare `ruff check` applies ~20 rule
families. The noqa comments encode a rule set that exists nowhere, and which ruff
version is installed silently changes what "clean" means.

Three options — **I have not acted on any**:

1. Restore the 71 noqa comments as-is. Cheap, keeps the annotations, leaves the
   underlying ambiguity.
2. Add a `pyproject.toml` `[tool.ruff.lint]` selecting CI's 7 rules plus E402, then
   restore only the noqa comments that rule set actually needs. Makes lint
   reproducible and makes the noqa comments mean something again.
3. Leave stripped. CI is unaffected; the annotations are simply gone.

**I'd recommend 2**, and it is a prerequisite for sweep 1 below — vulture and
`ruff check` both need a pinned rule set or their output is not comparable between
runs.

---

## Not started — the four sweeps

Per your instruction, stopping here. For sweep planning, current counts on the
post-commit tree:

| sweep | target | current count |
|---|---|---:|
| 3 | `S110` try/except/pass | 11 |
| 3 | `B023` loop-variable closure | 5 |
| 2 | in-function imports in `providers.py` | 33 (3 redundant ones now removed) |
| 1 | vulture candidates | not yet run — needs a config decision first |
| 4 | duplicate symbol sweep | not yet run |

`backend/scripts` exclusion and the `backend/tests` "does it assert anything"
question are both noted and unstarted.
