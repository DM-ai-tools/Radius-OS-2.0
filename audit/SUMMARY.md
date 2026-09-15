# Audit Summary — STOP POINT

**Nothing has been edited.** No source file differs from `c56cd37`. This is a ledger only.

---

## Coverage — read this first

| | |
|---|---|
| Phase 0 (inventory) | **complete** → `audit/00-inventory.md` |
| Phase 1 (line-by-line) | **1 of 153 modules**, and that one only partially |
| Phase 2 (this file) | complete for what was audited |

`backend/app` is **59,251 LOC of Python across 153 files**. Queue item #1 alone
(`providers.py`) is 2,549 LOC, of which I read 530 lines properly. A genuine line-by-line
pass over the whole tree is multi-session work.

I chose depth over breadth deliberately: the brief forbids "any claim of 'unused' that rests
only on a single grep", and a skim of 153 files would have produced exactly that. What is
below is fully evidenced. What is not covered is named as not covered.

---

## Verdict counts (audited scope only)

| verdict | count | LOC |
|---|---:|---:|
| DEAD | 5 | 64 |
| SIMPLIFY | 5 | ~290 affected, ~115 net removable |
| UNCERTAIN | 2 | — |
| KEEP | 16 symbols + 27 dependencies | — |

### Removable LOC by risk

| risk | LOC | items |
|---|---:|---|
| **mechanical** (provably safe, no behaviour change) | **64** | dead function `pull_rankings` (60) + 4 redundant imports (4) |
| **needs review** | ~115 | `validate_tracking` result-block table (#9) |
| **behavioural** — findings, not edits | 0 | rankings feature (Q1), DR coercion bug (#6) |

64 lines out of 2,549 is a small yield, and that is itself the finding: **this module is not
padded with dead code — it is padded with oversized functions.** One function,
`run_seo_audit`, is 769 lines (30 % of the file). No amount of dead-code removal addresses that;
it needs splitting, which is a reviewed refactor, not a cleanup.

---

## Findings by value

1. **`pull_rankings` is dead — 60 lines, fully proven.** All three death-proof searches came
   back empty across every file type, plus string-literal and public-API checks; `git log -S`
   shows it arrived in the initial commit and was never wired to anything.

2. **An entire feature is half-wired.** `CompetitorRanking` has a model, a live table, and a
   cascade-delete in `api/clients.py:300` — but nothing ever writes or reads a row.
   `pull_rankings` was its intended writer. The system carefully cleans up a table it never
   fills. *(Behavioural — needs your decision, see Q1.)*

3. **A latent `TypeError` is masked by a blind `except`** (`providers.py:52–58`). Python binds
   `A if C else B or D or 0` as `A if C else (B or D or 0)`, so the `or … or 0` fallback never
   protects the dict branch. When Ahrefs returns `domain_rating` as a dict with a missing inner
   key, `float(None)` raises and is swallowed at line 59, silently producing `authority = 0.0`.
   Authority scores may already be quietly wrong in production.

4. **`run_seo_audit` is 769 lines** — 30 % of the largest file in the codebase.

5. **Four imports re-bind names already imported at module scope** (`asyncio` ×2, `re` ×1,
   plus `random` which dies with #1). Found by AST comparison, not grep.

6. **`validate_tracking` repeats one dict shape 12 times across 175 lines.** Twelve occurrences
   clears the brief's "don't abstract for two" bar, but each branch's status and message string
   is observable output, so this needs review rather than mechanical application.

7. **`provider_map` is rebuilt on every loop iteration** (`providers.py:253`) and never varies.

8. **All 27 dependencies are justified — zero removable.** Six have no `import` statement;
   all six verified as genuine runtime deps (uvicorn via `start.sh`, asyncpg/aiosqlite via DSN,
   python-multipart via `UploadFile`, email-validator via `EmailStr`, greenlet via the
   `sqlalchemy[asyncio]` extra). No supply-chain trade is available or needed here.

9. **Churn is unusable as an audit signal in this repo** — 14 commits, 3 of them bulk. 60 % of
   files have exactly one commit. `LOC × churn` degenerates to LOC, and `git log -S` resolves
   almost every symbol to a release commit rather than a rationale. Expect UNCERTAIN verdicts to
   stay high for structural reasons.

10. **Of 462 ruff errors, ~242 are style or framework idiom** (`I001`, `FURB167`, and all 94
    `B008` which are FastAPI `Depends()` and correct). The ~97 worth reading are `S110` ×11,
    `RUF100` ×74, `B023` ×5, `BLE001` ×3, `PLW0127` ×2, `F401` ×2.

---

## UNCERTAIN — questions for you

**Q1. The competitor-rankings feature — finish it, or remove it?**
Model + table + cascade-delete exist; no writer, no reader. `pull_rankings` was the missing
writer. Three options: (a) delete `pull_rankings` only — leaves the dormant table, safest;
(b) wire `pull_rankings` up — it calls `ahrefs.organic_keywords`, which still exists;
(c) remove the model too — schema change, needs a migration, and `CompetitorRanking` is in
`models/__init__.__all__` so anything outside this repo importing it would break.
**I recommend (a) now and a separate decision on the feature.**

**Q2. The 36 in-function imports in `providers.py`.** Three are provably redundant. For the
other 33 I could not tell from the call sites whether they exist to break import cycles or
are just habit. Determining this per-site means loading each target module — do you want that
done, or should they be left alone?

**Q3. Scope for continuing.** At the depth the brief demands, roughly 500–2,500 LOC of source
can be audited per session. `backend/app` is 59,251 LOC. Do you want me to (a) continue down
the queue in order, (b) jump to the ~97 high-signal ruff hits across all files as a faster
sweep, or (c) target the specific subsystems you suspect?

**Q4.** Should `backend/tests` (16,965 LOC) and `backend/scripts` (2,281 LOC) be in scope at
all? The brief forbids deleting tests for line count, and the 14 `scripts/run_phase*.py`
runners look like developer tooling — I have not checked whether they are still used.

---

## Baseline for later comparison

| metric | value at `c56cd37` |
|---|---|
| tests | 765 passed, 0 failed |
| ruff (`app tests scripts`) | 462 errors |
| tracked source LOC | 113,899 / 395 files |
| python LOC in scope | 78,497 / 267 files |
| `backend/app` python | 59,251 / 153 files |

Working data: `audit/_files.json` (per-file LOC, churn, last-touched — regenerable).

---

## Awaiting your approval

Phase 3 is not started. On approval I would begin with the **64 mechanical LOC only**
(findings #1–#5, #7), as one commit per verdict class, full suite after each. Everything
marked *needs review* or *behavioural* stays untouched until you rule on Q1–Q4.
