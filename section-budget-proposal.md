# Section exercise budgets: redesign proposal

Stage: `budgets` (`scripts/budgets.py`, `prompts/budgets/`)
Date: 18 November 2026
Status: proposal. Nothing in the pipeline has been changed.

## What we want from you

1. **Review the design** in sections 2–8 against the code. Point out anything that is wrong, missing or risky, with `file:line` references.
2. **Run the reference tests** in section 9. Save `budget_rules.py` and `test_budget_rules.py` into an empty temporary folder (not into the repo) and run:
   ```bash
   python3 -B -m unittest test_budget_rules -v
   ```
   All 16 tests pass on our side. Report the result and anything you think the tests miss.
3. **Answer the review questions** in section 11.
4. **Do not change pipeline code yet.** If you have a proposed implementation, describe it as a plan or a diff for review.

---

## 1. Summary

Today every section gets `48 + 12 × objectives` exercises, and the model only divides them between the objectives. The proposal:

- **Python works out a range for each section**, from how many questions its textbook prints and how many objectives it has, and turns it into **five level totals**.
- **The model picks levels, never numbers:** one level (1–5) for the section, which picks its total, and one level (1–5) per objective, which says how the total is shared.
- **Python does all the arithmetic:** the total is the chosen level's total, and the shares are worked out in blocks of 4.
- **Every total and every share is a multiple of 4**, so each objective's printed sets fill whole rows.
- **Still one model call per section.** Each budget is written once and only asked for again when its inputs change.

## 2. Why change

Current code: `section_total()` in `scripts/budgets.py` (lines 69–75), the "exactly N" line in `section_part()` (line 137), the sum check in `budget_problems()` (line 168), and "48 plus 12" in `prompts/budgets/core.md` (line 4).

- **The objective count depends on how the spec was written.** The same lesson written as 8 objectives gets 144 exercises; written as 2 objectives, it gets 72.
- **Subject and grade play no part in the total.**
- **How much the textbook drills the section plays no part either.**
- **The floor is too high for small and language sections:** one objective always gets 60.

## 3. Design overview

```
source_questions ──► budgets ──► new_questions ──► …
   (extract)         (this stage)

Python prepares          The model judges           Python calculates          Written once
- book context line  ─►  - section level 1–5    ─►  - checks the reply     ─►  06_section_budgets/<stem>.json
- book profile           - objective levels 1–5     - total = level total      00_api_call_logs/…
- five level totals      - reasoning                - shares in blocks of 4
- textbook counts
- reviewed spec
```

**Stage order changes:** `source_questions` moves before `budgets` in `scripts/pipeline.py`. Folder names (`06_`, `07_`) stay as they are.

## 4. The rules

### 4.1 Inputs

- `n` = the number of objectives in the section's reviewed spec.
- `count` = `questions.count` in the section's extract (`07_section_source_questions/<stem>.json`). It totals the questions across **all** of the section's source books.
- `sources` = the number of source PDFs the table of contents lists for the section (`section_spans(book_id, None)[number]`, already de-duplicated by md5).
- **`s = count ÷ sources`**, the average per source book. For a one-book section, `s = count`.
  - Egyptian books may draw each section from more than one book. Every section of such a book has the same set of books, and the books never print the same question, so `count` is the exact sum and `s` is the exact average.
  - If the sections of one book have different numbers of sources, print a warning and still divide each section by its own number of sources.

### 4.2 Settings

| Setting | Value | Why |
|---|---|---|
| `N_MIN` | 4 | Least per objective: one row of a set |
| `N_MAX` | 20 | Most per objective when the objectives set the range |
| `MIN_SECTION` | 20 | Least per section |
| `CAP` | 148 | Most per section, just above today's largest common total (144), a multiple of 4 |
| `SOURCE_MIN_FACTOR` | 1.5 | Minimum = 1.5 × questions per book |
| `SOURCE_MAX_FACTOR` | 3 | Maximum = 3 × questions per book |
| `LEVELS` | 5 | |
| `BLOCK` | 4 | Every total and share is a multiple of this |
| `SPARSE_QUESTIONS_PER_PAGE` | 2.0 | The same threshold `source_questions.py` already warns at (line 638) |

### 4.3 Range and level totals

```
minimum = max(1.5 × s, 4 × n, 20)   rounded up to a multiple of 4
maximum = max(3 × s, 20 × n)        rounded up to a multiple of 4
both capped at 148

level k = minimum + (k − 1) × (maximum − minimum) ÷ 4
          rounded to the nearest multiple of 4 (an exact half rounds up), k = 1 … 5
```

- Level 1 is the minimum and level 5 is the maximum. Work in blocks: `a = minimum ÷ 4`, `b = maximum ÷ 4`, `level k = 4 × round_half_up(a + (k − 1)(b − a) ÷ 4)`.
- Python's `round()` uses banker's rounding (136.5 → 136), so use `floor(x + 0.5)`.
- Because 20 × n ≥ 4 × n and 3s ≥ 1.5s, the maximum is never below the minimum.
- A section with more than 37 objectives cannot give each one 4 within 148: refuse it with a clear error.

Worked example, s = 40 and n = 4: minimum = max(60, 16, 20) = 60, maximum = max(120, 80) = 120, one step = 15, level totals 60 · 76 · 92 · 104 · 120 (75 → 76, 90 → 92, 105 → 104).

### 4.4 Reliability of the count

- **Unreliable** when `count == 0`, or when `count ÷ pages < 2.0`. Here `pages` is the total pages of the section's source PDFs (`02_section_sources/<stem>_<md5>.pdf`, counted with `sources.pdf_page_count`), which matches how `source_questions.questions_per_page` measures it.
- If the pages can't be counted (a PDF is missing), only `count == 0` makes the count unreliable.
- **An unreliable count still goes into the formula** (the textbook prints at least that many). It only changes what the model is told; see 5.2.

### 4.5 Sharing the total between objectives

Given the chosen total and the objective levels `w`:

1. `blocks = total ÷ 4`. Give every objective 1 block.
2. Share the remaining `blocks − n` in proportion to `w`: round each objective's exact share down, then give the blocks left over to the largest fractions, with ties going to the earlier objective.
3. `exercises = 4 × blocks` for each objective.

Properties (the reference tests check all of them):
- the shares add up to the total;
- every share is at least 4 and a multiple of 4;
- a higher objective level never gets fewer exercises than a lower one;
- two objectives with the same level end at most one block apart.

Worked example, total 92 with levels 1, 5, 3, 3: 23 blocks; 1 each leaves 19; 19 × level ÷ 12 = 1.58, 7.92, 4.75, 4.75; rounded down 1, 7, 4, 4 leaves 3 blocks, which go to the fractions .92, .75, .75; the blocks are 2, 9, 6, 6, so the shares are **8, 36, 24, 24**.

An earlier version of this method rounded down first and then took a missing block from the largest share. It can give a higher-level objective fewer exercises than a lower-level one (total 20 with levels 1, 1, 4, 3 gave 4, 4, 4, 8), so it was replaced by "one block each first".

## 5. The model call

### 5.1 Part 1: the instructions

`prompts/budgets/core.md` (full text in Appendix A), assembled by `src.prompting.assemble("budgets", meta)` exactly as today:

- `{book_context}` is filled from the `<book>` element of `data/input/<book_id>/<book_id>.xml` through `sources.book_metadata()`. For `mathematics_en_in07_p1` it reads: `Subject: Mathematics | Grade: 7 | Language: English | Language code: en | Numerals: western | Notation: latin | Country: Republic of India` (a term of 0 is dropped).
- `{language_rules}` is filled from `prompts/budgets/language/<code>.md` (unchanged).
- The prompt gains no new placeholders. The numbers go in part 2.

### 5.2 Part 2: the section message

Built by `section_part()`. Templates:

```
Section level totals (exercises): 1 = {L1}, 2 = {L2}, 3 = {L3}, 4 = {L4}, 5 = {L5}

{SOURCE BLOCK}

Book profile (textbook questions per source book): {sections} sections, about {mean} per section.
section | title | objectives | textbook questions
{number} | {title} | {n} | {s}
…
{number} | {title} | {n} | {s}   <- this section
…

Section spec JSON:
{"number": …, "title": …, "module_path": …, "spec": {…}}      (compact, one line, as today)
```

The `{SOURCE BLOCK}` takes one of these forms:

| Case | Text |
|---|---|
| One book, reliable | `Textbook questions for this section: 40, 3 of them tagged with no objective.`<br>`Per objective (a question can carry two tags): objective_1 12, objective_2 18, objective_3 0, objective_4 13` |
| Several books, reliable | `Textbook questions for this section: 80 across 2 source books, 40 per book on average; 3 of them tagged with no objective.`<br>`Per objective, across all books (a question can carry two tags): …` |
| Count is 0 | `Textbook questions for this section: none extracted. Judge from the spec and the book profile.` |
| Sparse | `Textbook questions for this section: 4, but the count is unreliable (0.8 per page). Judge from the spec and the book profile.` |

- "Untagged" = questions whose `objectives` list is empty or missing.
- The book profile lists **every** section of the reviewed book, with `s` (per book) for each, in table-of-contents order, and marks this section. Show `s` with one decimal place when it isn't a whole number.
- The question text is **not** sent.

### 5.3 Call settings (unchanged)

`gemini-3.8-flash`, high thinking, `contents=[instructions, section message]`, `response_mime_type="application/json"`, `response_schema=BudgetReply`, up to 4 attempts with waits of 2, 4 and 8 s, at most 25 sections at once.

### 5.4 Reply schema (replaces today's `BudgetReply`)

```python
class ObjectiveLevel(BaseModel):
    id: str     # exactly as the spec prints it
    level: int  # 1–5

class BudgetReply(BaseModel):
    status: Literal["ok", "abort"]
    abort_reason: str = ""
    reasoning: str = ""   # 2–4 sentences, English: section level and why, then highest/lowest objective levels and why
    level: int = 0        # section level 1–5; 0 only on abort
    objectives: list[ObjectiveLevel] = []
```

## 6. After the reply

### 6.1 Checks (a failure is retried like a parse failure)

- `status` is `ok` or `abort`. An abort is reported in the run summary and nothing is written.
- `level` is a whole number from 1 to 5 (not a bool or a string).
- Every spec objective id appears exactly once; no unknown ids; order does not matter (Python puts the rows back in spec order).
- Every objective `level` is a whole number from 1 to 5.

### 6.2 Written file

Path and envelope unchanged: `data/output/<book_id>/06_section_budgets/<stem>.json` = `{book_id, **book_attributes, number, title, module_path, budget}`.

```json
"budget": {
  "total": 92,
  "level": 3,
  "reasoning": "…",
  "objectives": [
    { "id": "objective_1", "level": 1, "exercises": 8 },
    { "id": "objective_2", "level": 5, "exercises": 36 },
    { "id": "objective_3", "level": 3, "exercises": 24 },
    { "id": "objective_4", "level": 3, "exercises": 24 }
  ]
}
```

`BUDGET_KEYS` becomes `("total", "level", "reasoning", "objectives")`. Downstream readers use only `id` and `exercises` (`new_questions.budget_shares`, around line 3269). `translate.copy_budget` (around line 2659) copies the `budget` dict whole.

### 6.3 Call log (`call.json` extra fields)

Remove `total_exercises`. Add `source_questions` (count), `sources`, `questions_per_book` (s), `source_pages`, `source_reliable`, and `level_totals`.

### 6.4 Skip-if-valid (`written_budget`)

A section on disk is reused, with no call made, only when **all** of these hold under the current inputs:

- the envelope keys and `BUDGET_KEYS` match;
- the objective ids match the reviewed spec;
- `level` and every objective `level` are 1–5;
- `total == level_totals(s, n)[level − 1]`;
- each objective's `exercises` equals `split(total, objective levels)`.

So a new review (spec change), a new extraction (count change) or an old-format file (no `level`) is asked for again. `--update` still forces every section.

### 6.5 Refusals before any call

- Any section of the **book** (not only those selected with `-s`) has a missing or unreadable extract, or its `questions.objectives` ids differ from the reviewed spec: stop the run, list the sections, and say "run source_questions.py first". The whole book is needed because every call carries the book profile.
- A section with more than 37 objectives: refuse it.

## 7. Files to change

| File | Change |
|---|---|
| `scripts/budgets.py` | Remove `BASE_EXERCISES`, `EXERCISES_PER_OBJECTIVE`, `section_total`. Add the settings and the reference functions. Read every section's extract and source page counts. Build the book profile. New `section_part`, `BudgetReply`, reply checks, share calculation, `budget_document`, `BUDGET_KEYS`, `written_budget`, log context, opening line and docstring. |
| `prompts/budgets/core.md` | Replace with Appendix A. |
| `prompts/budgets/language/ar.md`, `en.md` | No change expected; check the wording still fits. |
| `scripts/source_questions.py` | Optional: make 2.0 a named constant (`SPARSE_QUESTIONS_PER_PAGE`) and import it in `budgets.py`. |
| `scripts/pipeline.py` | Move the `source_questions` stage before `budgets`; update the comment at lines 96–101. |
| `README.md` (stage list near lines 184–191 and 222), `USAGE.md` (lines 155–156) | New stage order. |
| `scripts/tests/test_budgets.py` (new) | Port the reference tests and add the integration tests in 9.3. |

**Not in this change (follow-up):** `EXTRA_EXERCISES = 8` per objective (`new_questions.py:137`, also used at 3121, 3188, 3213 and 4207, `regen_prepare.py` 199/203/227 and `test_regen_prepare.py` 71/191). With shares as small as 4, the spares are 2× what is printed, so they need a cap. Changing them makes every written section out of date.

## 8. Edge cases and limits

- **The range collapses** when minimum = maximum (one objective with up to 6 questions → 20; 97+ questions per book → 148). All five levels are then equal, and the section level makes no difference.
- **Narrow ranges repeat totals** (s = 7, n = 1 → 20, 20, 24, 24, 24).
- **At the floor, objective levels cannot fully separate** (20 shared by levels 1, 5, 3 → 4, 8, 8).
- **Equal objective levels can differ by one block;** the tie goes to the earlier objective.
- **s can be fractional** with two books (81 ÷ 2 = 40.5 → 64 · 80 · 96 · 108 · 124).
- **Existing budgets will all be asked for again,** because old files have no `level`. Their sections' `new_questions` output then goes out of date. New books are not affected.

## 9. Test cases

### 9.1 Reference implementation: `budget_rules.py`

Pure functions, with no imports from the repo.

```python
"""Reference rules for the section exercise budget (proposal, not wired into the pipeline)."""

from __future__ import annotations

import math

N_MIN = 4                 # least exercises per objective (one block)
N_MAX = 20                # most exercises per objective when objectives set the range
MIN_SECTION = 20          # least exercises per section
CAP = 148                 # most exercises per section (a multiple of 4)
SOURCE_MIN_FACTOR = 1.5   # minimum = 1.5 x textbook questions
SOURCE_MAX_FACTOR = 3     # maximum = 3 x textbook questions
LEVELS = 5
BLOCK = 4                 # every total and share is a multiple of this
SPARSE_QUESTIONS_PER_PAGE = 2.0


def per_book(question_count: int, sources: int) -> float:
    """s: the section's extracted questions divided by its source books (an average per book).

    The extract's count already totals every source book of the section, so dividing by the
    number of sources puts a two-book section on the same scale as a one-book section.
    """
    if sources < 1:
        raise ValueError("a section needs at least one source")
    return question_count / sources


def half_up(x: float) -> int:
    """Round to the nearest whole number, an exact half going up (Python's round() does not)."""
    return math.floor(x + 0.5)


def section_range(source_count: int, objectives: int) -> tuple[int, int]:
    """The least and most exercises this section may get, both multiples of BLOCK."""
    if objectives < 1:
        raise ValueError("a section needs at least one objective")
    cap_blocks = CAP // BLOCK
    if objectives > cap_blocks:
        raise ValueError(f"{objectives} objectives cannot each get {N_MIN} within {CAP}")
    low = math.ceil(max(SOURCE_MIN_FACTOR * source_count, N_MIN * objectives, MIN_SECTION) / BLOCK)
    high = math.ceil(max(SOURCE_MAX_FACTOR * source_count, N_MAX * objectives) / BLOCK)
    return min(low, cap_blocks) * BLOCK, min(high, cap_blocks) * BLOCK


def level_totals(source_count: int, objectives: int) -> list[int]:
    """The five totals the model chooses between, evenly spaced and rounded to BLOCK."""
    low, high = section_range(source_count, objectives)
    a, b = low // BLOCK, high // BLOCK
    return [BLOCK * half_up(a + k * (b - a) / (LEVELS - 1)) for k in range(LEVELS)]


def split(total: int, levels: list[int]) -> list[int]:
    """Share a total between objectives: one block each, the rest in proportion to the levels.

    The leftover blocks after rounding down go to the largest fractions, ties to the
    earlier objective. A higher level never gets fewer exercises than a lower one, and
    two equal levels end at most one block apart.
    """
    if total % BLOCK:
        raise ValueError(f"total {total} is not a multiple of {BLOCK}")
    if not levels or any(not 1 <= level <= LEVELS for level in levels):
        raise ValueError("every objective needs a level from 1 to 5")
    blocks = total // BLOCK
    if blocks < len(levels):
        raise ValueError("fewer blocks than objectives")
    rest = blocks - len(levels)
    raw = [rest * level / sum(levels) for level in levels]
    out = [math.floor(value) for value in raw]
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - out[i]), i))
    for i in order[: rest - sum(out)]:
        out[i] += 1
    return [BLOCK * (1 + share) for share in out]


def source_reliable(count: int, pages: int | None) -> bool:
    """False for an empty extract, or one under 2 questions per source page."""
    if count == 0:
        return False
    return not pages or count / pages >= SPARSE_QUESTIONS_PER_PAGE


def reply_problems(reply: dict, ids: list[str]) -> list[str]:
    """Every reason a model reply cannot be used. An abort is never checked further."""
    if reply.get("status") == "abort":
        return []
    if reply.get("status") != "ok":
        return [f"status {reply.get('status')!r} is not ok or abort"]
    problems: list[str] = []
    level = reply.get("level")
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= LEVELS:
        problems.append(f"section level {level!r} is not a whole number from 1 to 5")
    rows = reply.get("objectives") or []
    named = [row.get("id") for row in rows]
    for duplicate in sorted({name for name in named if named.count(name) > 1}):
        problems.append(f"{duplicate} is levelled more than once")
    for unknown in [name for name in named if name not in ids]:
        problems.append(f"{unknown} is not an objective of this section")
    for missing in [name for name in ids if name not in named]:
        problems.append(f"{missing} has no level")
    for row in rows:
        value = row.get("level")
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= LEVELS:
            problems.append(f"{row.get('id')} level {value!r} is not a whole number from 1 to 5")
    return problems
```

### 9.2 Reference tests: `test_budget_rules.py`

```python
"""Reference tests for the section exercise budget rules. Run: python -m unittest test_budget_rules -v"""

import itertools
import unittest

from budget_rules import (
    BLOCK,
    CAP,
    LEVELS,
    MIN_SECTION,
    N_MIN,
    level_totals,
    per_book,
    reply_problems,
    section_range,
    source_reliable,
    split,
)

# (questions per book s, objectives n, (minimum, maximum), five level totals)
LEVEL_CASES = [
    (40, 4, (60, 120), [60, 76, 92, 104, 120]),     # typical math section
    (30, 1, (48, 92), [48, 60, 72, 80, 92]),        # one objective
    (8, 3, (20, 60), [20, 32, 40, 52, 60]),         # language, few heavy items
    (0, 5, (20, 100), [20, 40, 60, 80, 100]),       # no textbook questions
    (0, 2, (20, 40), [20, 24, 32, 36, 40]),
    (0, 1, (20, 20), [20, 20, 20, 20, 20]),         # collapses to the floor
    (5, 1, (20, 20), [20, 20, 20, 20, 20]),
    (6, 1, (20, 20), [20, 20, 20, 20, 20]),
    (7, 1, (20, 24), [20, 20, 24, 24, 24]),         # narrow range repeats totals
    (13, 2, (20, 40), [20, 24, 32, 36, 40]),
    (20, 8, (32, 148), [32, 60, 92, 120, 148]),     # maximum capped
    (52, 3, (80, 148), [80, 96, 116, 132, 148]),
    (96, 5, (144, 148), [144, 144, 148, 148, 148]),
    (97, 5, (148, 148), [148, 148, 148, 148, 148]), # first count fully at the cap
    (100, 5, (148, 148), [148, 148, 148, 148, 148]),
    (0, 12, (48, 148), [48, 72, 100, 124, 148]),
    (40.5, 4, (64, 124), [64, 80, 96, 108, 124]),   # two books, 81 questions
]

# (total, objective levels, shares)
SPLIT_CASES = [
    (92, [1, 5, 3, 3], [8, 36, 24, 24]),
    (104, [1, 5, 3, 3], [12, 40, 28, 24]),
    (60, [1, 5, 3, 3], [8, 20, 16, 16]),
    (20, [1, 5, 3], [4, 8, 8]),
    (20, [1, 1, 4, 3], [4, 4, 8, 4]),
    (20, [1, 1, 1, 1, 5], [4, 4, 4, 4, 4]),
    (16, [1, 5], [4, 12]),
    (32, [1, 5], [8, 24]),
    (148, [5], [148]),
    (60, [3, 3, 3], [20, 20, 20]),
    (40, [2, 2], [20, 20]),
    (120, [1, 2, 3, 4, 5], [12, 16, 24, 32, 36]),
]

# (question count, source pages or None, reliable)
RELIABLE_CASES = [
    (0, None, False),
    (0, 10, False),
    (40, 19, True),
    (38, 19, True),    # exactly 2.0 per page
    (37, 19, False),   # 1.95 per page
    (4, 5, False),
    (10, None, True),  # pages unknown: only an empty extract is unreliable
]

IDS = ["objective_1", "objective_2", "objective_3", "objective_4"]


def ok_reply(**changes):
    reply = {
        "status": "ok",
        "abort_reason": "",
        "reasoning": "x",
        "level": 3,
        "objectives": [{"id": name, "level": 3} for name in IDS],
    }
    reply.update(changes)
    return reply


class PerBookTests(unittest.TestCase):
    def test_one_book_is_the_count(self):
        self.assertEqual(per_book(30, 1), 30)

    def test_two_books_average(self):
        self.assertEqual(per_book(80, 2), 40)
        self.assertEqual(per_book(81, 2), 40.5)

    def test_no_sources_is_refused(self):
        with self.assertRaises(ValueError):
            per_book(10, 0)


class LevelTotalTests(unittest.TestCase):
    def test_table(self):
        for s, n, expected_range, expected_levels in LEVEL_CASES:
            with self.subTest(s=s, n=n):
                self.assertEqual(section_range(s, n), expected_range)
                self.assertEqual(level_totals(s, n), expected_levels)

    def test_properties_over_a_grid(self):
        for s in range(0, 301):
            for n in range(1, 13):
                low, high = section_range(s, n)
                levels = level_totals(s, n)
                with self.subTest(s=s, n=n):
                    self.assertEqual(len(levels), LEVELS)
                    self.assertTrue(all(total % BLOCK == 0 for total in levels))
                    self.assertEqual(levels, sorted(levels))
                    self.assertEqual((levels[0], levels[-1]), (low, high))
                    self.assertTrue(MIN_SECTION <= low <= high <= CAP)
                    self.assertTrue(low >= max(MIN_SECTION, N_MIN * n) or low == CAP)

    def test_objective_limits(self):
        with self.assertRaises(ValueError):
            section_range(10, 0)
        with self.assertRaises(ValueError):
            section_range(10, 38)  # 38 x 4 > 148
        self.assertEqual(section_range(0, 37), (148, 148))


class SplitTests(unittest.TestCase):
    def test_table(self):
        for total, levels, expected in SPLIT_CASES:
            with self.subTest(total=total, levels=levels):
                self.assertEqual(split(total, levels), expected)

    def test_properties(self):
        totals_seen = 0
        for n in range(1, 7):
            for levels in itertools.product(range(1, 6), repeat=n):
                totals = {
                    level_totals(s, n)[k] for s in (0, 5, 10, 25, 40, 70, 100) for k in range(LEVELS)
                }
                for total in totals:
                    shares = split(total, list(levels))
                    totals_seen += 1
                    self.assertEqual(sum(shares), total)
                    self.assertTrue(all(share >= N_MIN and share % BLOCK == 0 for share in shares))
                    for i, j in itertools.permutations(range(n), 2):
                        if levels[i] > levels[j]:
                            self.assertGreaterEqual(shares[i], shares[j], (total, levels, shares))
                        if levels[i] == levels[j]:
                            self.assertLessEqual(abs(shares[i] - shares[j]), BLOCK)
        self.assertGreater(totals_seen, 0)

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValueError):
            split(90, [3, 3])          # not a multiple of 4
        with self.assertRaises(ValueError):
            split(8, [3, 3, 3])        # fewer blocks than objectives
        with self.assertRaises(ValueError):
            split(40, [0, 3])          # level out of range


class ReliabilityTests(unittest.TestCase):
    def test_table(self):
        for count, pages, expected in RELIABLE_CASES:
            with self.subTest(count=count, pages=pages):
                self.assertEqual(source_reliable(count, pages), expected)


class ReplyCheckTests(unittest.TestCase):
    def test_valid_reply(self):
        self.assertEqual(reply_problems(ok_reply(), IDS), [])

    def test_order_does_not_matter(self):
        reply = ok_reply(objectives=[{"id": name, "level": 2} for name in reversed(IDS)])
        self.assertEqual(reply_problems(reply, IDS), [])

    def test_abort_is_not_checked(self):
        self.assertEqual(reply_problems({"status": "abort", "abort_reason": "empty"}, IDS), [])

    def test_bad_section_levels(self):
        for level in (0, 6, "3", 2.5, True, None):
            with self.subTest(level=level):
                self.assertTrue(reply_problems(ok_reply(level=level), IDS))

    def test_bad_objective_rows(self):
        cases = {
            "missing": [{"id": name, "level": 3} for name in IDS[:3]],
            "duplicate": [{"id": name, "level": 3} for name in IDS + ["objective_1"]],
            "unknown": [{"id": name, "level": 3} for name in IDS] + [{"id": "objective_9", "level": 3}],
            "level 7": [{"id": name, "level": 7 if name == "objective_2" else 3} for name in IDS],
        }
        for name, rows in cases.items():
            with self.subTest(case=name):
                self.assertTrue(reply_problems(ok_reply(objectives=rows), IDS))

    def test_unknown_status(self):
        self.assertTrue(reply_problems(ok_reply(status="maybe"), IDS))


if __name__ == "__main__":
    unittest.main()
```

Expected: `Ran 16 tests … OK`.

### 9.3 Integration tests to add in `scripts/tests/test_budgets.py` when implementing

Mock the Gemini client (`client.models.generate_content`) and patch `time.sleep`; build a small fake book under a temporary `OUTPUT_DIR`.

1. **Stage order:** in `pipeline.STAGE_NAMES`, `source_questions` comes before `budgets`.
2. **Prompt assembly:** `build_prompt(meta)` leaves no `{book_context}` or `{language_rules}` placeholder, and contains no "48 plus 12".
3. **Missing extract:** remove one section's extract → `RuntimeError` naming it, and no model call is made.
4. **Stale extract:** an extract whose tally ids differ from the spec → refused before any call.
5. **Happy path:** s = 40, n = 4, reply level 3 with levels 1, 5, 3, 3 → the file has `total` 92, `level` 3 and exercises 8, 36, 24, 24 in spec order.
6. **The section message** contains `1 = 60, 2 = 76, 3 = 92, 4 = 104, 5 = 120`, the source block, a book profile row for every section with this one marked, and no question text.
7. **Retry:** the first reply has `level: 6`, the second is valid → 2 attempts, one file.
8. **Abort:** nothing written; counted as aborted; the exit code is 1, as today.
9. **Skip-if-valid:** a second run makes no call. Change the extract count so the level totals change → asked again. A file in the old format (no `level`) → asked again.
10. **Two books:** a section with 2 sources and count 80 → s = 40 in the message, `questions_per_book` 40 in `call.json`, and level totals as for s = 40.
11. **Mixed number of sources** across the book's sections → a warning is printed.
12. **Unreliable:** count 0 → "none extracted" text; 30 questions on 19 pages → "unreliable" text; the level totals still come from the formula.
13. **Downstream:** `new_questions.budget_shares()` reads the new file and returns `exercises` in spec order.

### 9.4 Manual check on a real book (after implementation; costs API calls)

1. Pick a book that has been through `source_questions`. Run `python3 -B scripts/budgets.py <book_id> -s <one section>` and read `reasoning` in the file and in `00_api_call_logs/…/call.json`.
2. Run the whole book. Count the section levels; if most sit on one level, the level descriptions or the factors need adjusting.
3. Repeatability: copy `06_section_budgets/` aside, run again with `--update`, and compare levels section by section.

## 10. Decisions already made

- The model returns levels only; Python does all arithmetic.
- N_MIN 4, N_MAX 20, MIN_SECTION 20, factors 1.5 and 3, CAP 148, 5 levels, everything in multiples of 4.
- `s` = count ÷ number of source books.
- No question text is sent (the spec carries the difficulty), and no new prompt placeholders are added.
- An unreliable count still counts toward the range; only the message changes.
- The prompt tells the model **not** to raise a section's level for its volume alone, since the range already grows with the count.

## 11. Review questions

1. Does anything else read `06_section_budgets/` or `section_total` that this proposal misses?
2. Should the run require **every** section's extract (for the book profile) even when started with `-s`, or should it build the profile from whatever extracts exist and say so?
3. Is 2.0 questions per page a sensible threshold for language books, where one item can be a long passage?
4. When the range collapses and the section has one objective, the call decides nothing. Should Python skip the call and write the budget directly?
5. Specs carry a `role` (`teach`, `revision`, `assessment`). Should revision or assessment sections be budgeted differently?
6. How should `EXTRA_EXERCISES` scale with small shares (the follow-up in section 7)?
7. Is there a reason to keep `--update` from re-budgeting a book whose `new_questions` output already exists?

---

## Appendix A: `prompts/budgets/core.md` (proposed full text)

```markdown
You are an expert K–12 assessment designer deciding how much practice one section of a practice book needs, and how that practice is shared among the objectives the section teaches.

## Task
You receive one section: its reviewed spec (what it teaches, the objectives it serves, the teaching points behind them, its glossary, the equations and notations it uses, and short excerpts from the pages), how many questions the textbook itself prints for it, and a profile of the whole book. You make two judgements:

1. A section level from 1 to 5: how much practice this section needs. Each level stands for a fixed number of exercises, listed with the section.
2. An objective level from 1 to 5 for every objective: how much of the section's practice that objective needs compared with the section's other objectives.

You return levels only. The exercise counts are worked out from them.

Book context:
{book_context}

## You judge, you write nothing
Write no exercises. No stems, no questions, no worked examples, no sample items, not even one to illustrate a point. Another call writes the exercises later, from the counts your levels produce. Your entire answer is a level for the section, a level per objective, and a few sentences on how you arrived at them.

## The section level
The section comes with five totals, one per level, from the least to the most practice that suits it. They are already set from the textbook's question count and the number of objectives: do not recompute them, and do not answer with a number of your own. Choose the level from:

- The book profile. Use it to see what a typical section of this book looks like. The five totals already grow with the textbook's question count, so do not move a section up a level for its volume alone.
- Difficulty. How demanding the section's content is for this grade: new procedures, multi-step reasoning and subtle distinctions need more practice than definitions and recognition.
- The objectives. How many there are, and how many of them are heavy.
- Subject and grade. What one exercise asks of a learner differs by subject, and what counts as enough practice differs by grade: younger learners need more repetition on each new skill.

Level 3 is a typical section of this book. Levels 1 and 5 are for sections clearly lighter or clearly heavier than most. Say why you chose the level in `reasoning`.

When a section is drawn from more than one source book, its question count is also given as the average per book, and the book profile is on that same per-book scale.

When the section says the textbook prints no questions for it, or that its count is unreliable, judge from the spec and the book profile alone.

## The objective levels
Each objective's level says how much of the section's practice it needs compared with the other objectives:

- 1: recognition, such as a definition, a piece of notation or a fact to state.
- 3: a typical skill of the section.
- 5: the section's core, such as a procedure that needs fluency or the skill the rest of the section depends on.

Weigh breadth, depth of skill, cognitive demand, foundation value and failure risk.

The textbook's count per objective is a hint, not the answer: one question can carry two objectives, questions tagged with none count toward the section but toward no objective, and an objective the textbook never drills still needs real practice. Never give an objective level 1 only because the textbook has no questions for it.

Giving every objective the same level is almost always a sign of not having decided. A section with one objective gives it level 5.

## Field rules
- level: the section level, a whole number from 1 to 5.
- objectives: one entry per objective in the spec, in the spec's own order, each with its `id` exactly as the spec prints it and its `level` from 1 to 5. Never invent, omit or merge an id.
- reasoning: two to four sentences in English, for the maintainer: first the section level and why, set against the textbook's questions and the book profile; then which objectives got the highest and lowest levels, and why. Name objectives by id.

{language_rules}

## Abort
Abort only when the section cannot be judged at all: no objectives, or a spec so empty there is nothing to judge. On abort, give one line in `abort_reason` and return no level and no objectives.

## Output
Return the response envelope JSON: status, abort_reason, reasoning, level, objectives.
```

## Appendix B: worked example

Section 2.2, *Reading and Evaluating Complex Expressions*, of `mathematics_en_in07_p1`. The titles and page range (59.29–77.46, so 19 PDF pages) are real; the objectives and counts are made up, because the book has not been through specs or source_questions yet.

**Section message:**

```
Section level totals (exercises): 1 = 60, 2 = 76, 3 = 92, 4 = 104, 5 = 120

Textbook questions for this section: 40, 3 of them tagged with no objective.
Per objective (a question can carry two tags): objective_1 12, objective_2 18, objective_3 0, objective_4 13

Book profile (textbook questions per source book): 44 sections, about 22 per section.
section | title                                       | objectives | textbook questions
1.1     | A Lakh Varieties!                           | 3          | 21
1.2     | Land of Tens                                | 2          | 14
…
2.1     | Simple Expressions                          | 3          | 26
2.2     | Reading and Evaluating Complex Expressions  | 4          | 40   <- this section
3.1     | The Need for Smaller Units                  | 2          | 8
…

Section spec JSON:
{"number":"2.2","title":"Reading and Evaluating Complex Expressions","module_path":"2. Arithmetic Expressions > 2. Reading and Evaluating Complex Expressions","spec":{…}}
```

**Reply:**

```json
{
  "status": "ok",
  "abort_reason": "",
  "reasoning": "The textbook prints 40 questions here against a book average of about 22, and the range already reflects that. Evaluating expressions with several operations is a typical Grade 7 skill, so level 3. objective_2 (evaluate using the order of operations) is the core skill and gets 5; objective_1 (identify terms) is recognition and gets 1.",
  "level": 3,
  "objectives": [
    { "id": "objective_1", "level": 1 },
    { "id": "objective_2", "level": 5 },
    { "id": "objective_3", "level": 3 },
    { "id": "objective_4", "level": 3 }
  ]
}
```

**Python:** 40 ÷ 19 pages = 2.1 per page, so the count is reliable. Level 3 gives 92, which is 23 blocks; the shares are 8, 36, 24 and 24 (section 4.5). The file written is the one in 6.2.
