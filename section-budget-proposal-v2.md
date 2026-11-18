# Section exercise budgets: redesign proposal, v2

Stage: `budgets` (`scripts/budgets.py`, `prompts/budgets/`)
Date: 18 November 2026
Status: proposal, revised after the review of v1. Nothing in the pipeline has been changed.

## What we want from you

1. **Run the dry run first** (section 9.1). It is read-only and makes no model calls. Run it on at least one math book that already has extracts and budgets (for example `mathematics_en_in10`) and one language book, and paste the output. This is the real-data check the design still needs.
2. **Run the reference tests** (sections 9.2–9.3). Save `budget_rules.py` and `test_budget_rules.py` into an empty temporary folder (not the repo) and run `python3 -B -m unittest test_budget_rules -v`. Expected: `Ran 24 tests … OK`.
3. **Check that every v1 review point is handled** (the "Changes since v1" table below), and say if anything is still wrong.
4. **If the dry-run totals look sensible, propose the implementation** (a plan or a diff) following section 7. Do not change pipeline code until it is approved.

The full test suite already has failures that have nothing to do with this change (section 10).

## Changes since v1

| v1 review point | v2 |
|---|---|
| Q2: require every extract? | Only the **selected** sections need a valid extract. The book profile uses whatever extracts exist and says how many (6.1, 5.2). |
| Orphan sections (gone from the table of contents) | Refused before any call, as `source_questions.py:922` does (6.1). |
| Missing PDF counted as 0 pages | Pages are **unknown** (`None`) if any PDF is missing or unreadable; `source_reliable` refuses 0 pages (4.4). |
| Loose numbers (`"3"`, `3.0`, `true`) slip through pydantic | `level` fields are `StrictInt`; tests parse JSON through the model (5.4, 9.3). |
| No `ge=1` on `level` (abort uses 0) | Kept: range is checked only when `status == "ok"` (5.4, 6.4). |
| Spec order before the split | `budget_from_reply` reorders by spec before splitting; tested with a shuffled reply (6.5). |
| One objective → level 5 | Python writes 5; the reply is never rejected for it (6.5). |
| `written_budget` needs the extract | Replaced by `budget_is_current(budget, ids, s, n)` (6.8). |
| Opening line | Prints sections, objectives, questions, the span of level totals and profile coverage (6.2). |
| Calls that cannot change anything | Skipped: collapsed range with one objective, or total = 4 × objectives (6.3). |
| Prompt: volume vs "heavier" | "Heavier means more demanding, not more questions" (Appendix A). |
| Prompt: `role` | One line: revision and assessment usually need less (Appendix A). |
| Sparse wording | "Low for its pages: normal for long items, but extraction may have missed some" (5.2). |
| Old-format files rewritten silently | One warning line per section (6.8). |
| `--translate` treats old 06 files as done | **Must fix:** `twin` counts a 06 file as done only if it has `level` (7). |
| Arabic 06 copy goes stale | Tidy-up: re-copy when the English `budget` differs. The Arabic 06 numbers are not read to build the Arabic book (7). |
| `EXTRA_EXERCISES` cap | In this change, **non-Arabic books only**, behind the paper-fill check (7.1, 9.5). |
| `new_questions.py:4162` message | Updated stage order in the message (7). |
| `section_range(source_count: int)` | Renamed `questions_per_book`, typed `float` (9.2). |
| Missing tests | Added: unknown pages, rest == 0, pydantic parsing, spec order and ties, reuse rule, skipped calls, spare cap (9.3). Translate, orphans and the opening line are in the integration list (9.4). |

---

## 1. Summary

Today every section gets `48 + 12 × objectives` exercises, and the model only divides them. The proposal:

- **Python works out a range for each section**, from the questions its source books print and its objective count, and turns it into **five level totals**.
- **The model picks levels, never numbers:** one level (1–5) for the section, which picks its total, and one level (1–5) per objective, which weights the split.
- **Python does all the arithmetic**, and every total and share is a multiple of 4.
- **Still one call per section, or none** when nothing could change. Each budget is written once and asked for again only when its inputs change.

## 2. Why change

`section_total()` in `scripts/budgets.py` (lines 69–75), the "exactly N" line in `section_part()` (137), the sum check in `budget_problems()` (168), and "48 plus 12" in `prompts/budgets/core.md` (line 4).

- **The objective count depends on how the spec was written:** 8 objectives get 144 exercises, while the same lesson written as 2 gets 72.
- **Subject, grade and how much the textbook drills the section play no part.**
- **One objective always gets 60,** which is too high for small and language sections.

## 3. Overview

```
source_questions ──► budgets ──► new_questions ──► …

Python prepares          The model judges           Python calculates          Written once
- book context line  ─►  - section level 1–5    ─►  - checks the reply     ─►  06_section_budgets/<stem>.json
- book profile           - objective levels 1–5     - total = level total      00_api_call_logs/…
- five level totals      - reasoning                - shares in blocks of 4
- textbook counts
- reviewed spec
```

`source_questions` moves before `budgets` in `scripts/pipeline.py`. The folder names (`06_`, `07_`) stay.

## 4. The rules

### 4.1 Inputs

- `n` = the objectives in the section's reviewed spec. It is at least 1 (`specs.py:228`, `min_length=1`).
- `count` = `questions.count` in `07_section_source_questions/<stem>.json`. It totals the questions across **all** of the section's source books.
- `sources` = the distinct source PDFs for the section in `section_spans(book_id, None)[number]`.
- **`s = count ÷ sources`**, the questions per book (`questions_per_book`, a float). For a one-book section, `s = count`.
  - Egyptian books may draw every section from more than one book. All sections of such a book have the same books, and the books never print the same question, so `count` is the exact sum and `s` the exact average.
  - If a book's sections draw on different numbers of books, print a warning and still divide each section by its own number.

### 4.2 Settings

| Setting | Value | Why |
|---|---|---|
| `N_MIN` | 4 | Least per objective: one block |
| `N_MAX` | 20 | Most per objective when the objectives set the range |
| `MIN_SECTION` | 20 | Least per section |
| `CAP` | 148 | Most per section; just above today's largest common total (144); a multiple of 4 |
| `SOURCE_MIN_FACTOR`, `SOURCE_MAX_FACTOR` | 1.5, 3 | Minimum and maximum as multiples of `s` |
| `LEVELS` | 5 | |
| `BLOCK` | 4 | Every total and share is a multiple of this |
| `SPARSE_QUESTIONS_PER_PAGE` | 2.0 | Same threshold `source_questions.py:638` warns at |

### 4.3 Range and level totals

```
minimum = max(1.5 × s, 4 × n, 20)   rounded up to a multiple of 4
maximum = max(3 × s, 20 × n)        rounded up to a multiple of 4
both capped at 148

level k = minimum + (k − 1) × (maximum − minimum) ÷ 4
          rounded to the nearest multiple of 4 (an exact half rounds up), k = 1 … 5
```

- Work in blocks of 4 and round with `floor(x + 0.5)`. Python's `round()` rounds 136.5 down to 136.
- The maximum is never below the minimum. More than 37 objectives cannot each get 4 within 148, so such a section is refused.
- Example: s = 40, n = 4 → 60 · 76 · 92 · 104 · 120.

### 4.4 Reliability of the count

- `pages` = the total pages of the section's PDFs (`02_section_sources/<stem>_<md5>.pdf`, counted with `sources.pdf_page_count`). It is **`None` if any PDF is missing or unreadable**; never add a 0 for a missing file (`section_pages`).
- **Unreliable** when `count == 0`, or when `pages` is known and `count ÷ pages < 2.0`. `source_reliable` raises on `pages <= 0`.
- 2.0 is a detector for cut-off extractions. **An unreliable count still goes into the formula** (the books print at least that many); only the message changes (5.2).

### 4.5 Sharing the total between objectives

The levels must be in **spec order**.

1. `blocks = total ÷ 4`. Give every objective 1 block.
2. Share the remaining `blocks − n` in proportion to the objective levels: round each down, then give the leftover blocks to the largest fractions, ties to the earlier objective in the spec.
3. `exercises = 4 × blocks`.

Properties (all tested): the shares add up to the total; each is at least 4 and a multiple of 4; a higher level never gets fewer exercises than a lower one; equal levels end at most one block apart. When `total == 4 × n` (for example n = 5 at the level-1 total of 20), every objective gets 4 whatever its level.

Example: total 92 with levels 1, 5, 3, 3 → 23 blocks; one each leaves 19; 19 × level ÷ 12 = 1.58, 7.92, 4.75, 4.75 → rounded down 1, 7, 4, 4; the 3 leftover blocks go to .92, .75, .75 → **8, 36, 24, 24**.

## 5. The model call

### 5.1 Part 1: the instructions

`prompts/budgets/core.md` (Appendix A), assembled by `src.prompting.assemble("budgets", meta)` as today. `{book_context}` comes from the `<book>` element of `data/input/<book_id>/<book_id>.xml` through `sources.book_metadata()`, and `{language_rules}` from `prompts/budgets/language/<code>.md` (no change needed). There are no new placeholders.

### 5.2 Part 2: the section message (`section_part`)

```
Section level totals (exercises): 1 = {L1}, 2 = {L2}, 3 = {L3}, 4 = {L4}, 5 = {L5}

{SOURCE BLOCK}

{PROFILE LINE}
section | title | objectives | textbook questions
{number} | {title} | {n} | {s or "not extracted"}
…
{number} | {title} | {n} | {s}   <- this section
…

Section spec JSON:
{"number": …, "title": …, "module_path": …, "spec": {…}}      (compact, one line, as today)
```

`{SOURCE BLOCK}`:

| Case | Text |
|---|---|
| One book, reliable | `Textbook questions for this section: 40, 3 of them tagged with no objective.`<br>`Per objective (a question can carry two tags): objective_1 12, objective_2 18, objective_3 0, objective_4 13` |
| Several books, reliable | `Textbook questions for this section: 80 across 2 source books, 40 per book on average; 3 of them tagged with no objective.`<br>`Per objective, across all books (a question can carry two tags): …` |
| Low for its pages | `Textbook questions for this section: 4 over 5 pages (0.8 per page). That is low for its pages: normal for long items such as passages, but the extraction may have missed some. Weigh it with the spec.`<br>followed by the per-objective line |
| None extracted | `Textbook questions for this section: none extracted. Judge from the spec and the book profile.` |

`{PROFILE LINE}`:
- All extracted: `Book profile (textbook questions per source book): 44 sections, about 22 per section.`
- Partial: `Book profile (textbook questions per source book): 40 of 44 sections extracted so far, about 22 per extracted section.`

Rows are in table-of-contents order. `s` is shown with one decimal place when it is not a whole number. The question text is **not** sent.

### 5.3 Call settings (unchanged)

`gemini-3.8-flash`, high thinking, `contents=[instructions, section message]`, JSON reply held to `BudgetReply`, up to 4 attempts with waits of 2, 4 and 8 s, at most 25 sections at once.

### 5.4 Reply model

```python
class ObjectiveLevel(BaseModel):
    id: str
    level: StrictInt                 # 1–5, checked only when status is ok

class BudgetReply(BaseModel):
    status: Literal["ok", "abort"]
    abort_reason: str = ""
    reasoning: str = ""
    level: StrictInt = 0             # no ge/le: an abort carries 0
    objectives: list[ObjectiveLevel] = []
```

`StrictInt` rejects `"3"`, `3.0` and `true` when the JSON is parsed (pydantic 2.13 accepts all three for a plain `int`, turning `true` into 1). Confirm that `google-genai` accepts `StrictInt` in `response_schema`; if not, use `int` in the schema passed to Gemini and a strict model for parsing.

## 6. The run, step by step

### 6.1 Refusals before any call

- A **selected** section (all of them when `-s` is not given) whose extract is missing or unreadable, or whose `questions.objectives` ids differ from the reviewed spec: stop, list them, and say "run source_questions.py first".
- A reviewed section that is no longer in the table of contents: stop, as `source_questions.py:922-928` does. Do not call `questions_per_book` with 0 sources.
- A section with more than 37 objectives: refuse it.
- Sections that are **not** selected and have no extract are left out of the book profile and counted in its coverage line.

### 6.2 Opening line

There is no total before the replies come back, so print instead:

```
budgeting 12 section(s) with 12 worker(s), 41 objective(s), 518 textbook question(s);
level totals span 20–148; 2 set without a call; 3 already budgeted; profile covers 44 of 44 sections
```

### 6.3 Sections set without a call (`fixed_budget`)

When all five level totals are equal **and** either `n == 1` or `total == 4 × n`, no reply can change the file. Write it directly: `level` 3, objective level 5 for one objective (3 each otherwise), and reasoning `The range collapsed to {total}; … Set without a model call.` The reuse rule (6.8) accepts these files.

### 6.4 Checks on a reply (a failure is retried like a parse failure)

- An abort is reported in the summary; nothing is written.
- `level` is 1–5.
- Every spec objective id appears exactly once, and there are no unknown ids. Order does not matter.
- Every objective level is 1–5.
- There is **no** check that a one-objective section says 5 (6.5).

### 6.5 Building the budget (`budget_from_reply`)

Put the levels in spec order. A single objective is written as level 5, since it takes the whole total anyway. `total = level_totals(s, n)[level − 1]`, then the shares come from `split(total, levels)`.

### 6.6 Written file

Same path and envelope as today: `data/output/<book_id>/06_section_budgets/<stem>.json` = `{book_id, **book_attributes, number, title, module_path, budget}`.

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

`BUDGET_KEYS` = `("total", "level", "reasoning", "objectives")`. Readers of 06 use only `id` and `exercises`: `new_questions.budget_shares` (:3269), `regen_prepare.budget_rows` (:76), `translate.copy_budget` (:2659, copies the dict whole), and `twin.stage_output_exists` (:505, existence only). `papers` reads the budget from 08, not 06.

### 6.7 Call log (`call.json` extras)

Remove `total_exercises`. Add `source_questions`, `sources`, `questions_per_book`, `source_pages`, `source_reliable`, `level_totals`, and `set_without_call` for 6.3.

### 6.8 Reuse (`budget_is_current`, replacing `written_budget`)

`build_budgets` loads the extracts once, before deciding what to skip. A file is reused only when the envelope keys match, the budget keys are exactly `BUDGET_KEYS`, the ids match the spec, every level is a real int from 1 to 5, `total == level_totals(s, n)[level − 1]`, and the exercises equal `split(total, levels)`. Otherwise the section is asked for again. When the reason is the old shape (no `level`), print:

```
  2.2: budget on disk is the old shape; asking again (its new_questions output will go out of date)
```

## 7. Files to change

| File | Change |
|---|---|
| `scripts/budgets.py` | Remove `BASE_EXERCISES`, `EXERCISES_PER_OBJECTIVE`, `section_total`. Add the settings and the reference functions (9.2). New refusals, page counting, book profile, `section_part`, `BudgetReply`, `fixed_budget` path, reply checks, `budget_from_reply`, `BUDGET_KEYS`, `budget_is_current`, opening line, warnings, log fields, docstring. |
| `prompts/budgets/core.md` | Replace with Appendix A. |
| `scripts/pipeline.py` | Move `source_questions` before `budgets`; rewrite the comment at lines 96–101. |
| `scripts/src/twin.py` | **Must fix:** for `budgets`, count a 06 file as done only if `budget` has `level` (around line 505). Otherwise `--translate` skips the English stage on old files, and twin books never change. |
| `scripts/translate.py` | Tidy-up: around line 4208, also re-copy when the Arabic `budget` differs from the English one, not only when the file is missing. |
| `scripts/new_questions.py` | Spare cap (7.1), and the message at :4162 → "run source_questions.py and budgets.py first". |
| `scripts/regen_prepare.py`, `scripts/tests/test_regen_prepare.py` | Use the spare helper instead of `+ EXTRA_EXERCISES` (lines 199/203/227 and 71/191). |
| `scripts/source_questions.py` | Optional: make 2.0 a named constant and import it in `budgets.py`. |
| `README.md` (184–191, 222), `USAGE.md` (155–156) | New stage order. |
| `scripts/tests/test_budgets.py` (new) | Port 9.2–9.3 and add 9.4. |

No change: `prompts/budgets/language/*.md`, `prompts/README.md` (still accurate), folder names.

### 7.1 Spare exercises (`EXTRA_EXERCISES`)

Do it in this change, since rewriting 06 makes 08 out of date anyway:

```
extra_exercises(share, arabic) = 8                 if the book is Arabic (language_code "ar")
                               = 4 if share <= 8, else 8   otherwise
```

- Share 4 → write 8; share 8 → 12; share 12 or more → share + 8, as today. All multiples of 4.
- **Math is safe:** the review-cost notes record far more spares than rejections (en_in10: 2,784 spares vs 225 rejected; en_in11: 2,968 vs 174).
- **Arabic books keep 8** until their rejection rate is measured, because the same notes show high-flag Arabic books use their spares. This applies to Arabic-native books; Arabic twins copy English exercises and do not run `new_questions`.
- Places in `new_questions.py` that use `EXTRA_EXERCISES`: `:137` (constant → helper), `:3121` (`written_exercises`), `:3188` and `:3213` (`kept_objectives`), `:4207` (tasks).
- **Gate:** the paper-fill comparison in 9.5 must pass before it ships.
- This overlaps with the planned "review spares only on demand" change, which also touches spares and paper fill. Ship one, measure it, then the other.

## 8. Edge cases and limits

- **Collapsed ranges.** One objective with up to 6 questions → 20; 97 or more per book → 148. The level then makes no difference (and 6.3 may skip the call).
- **Narrow ranges repeat totals** (s = 7, n = 1 → 20, 20, 24, 24, 24).
- **At the floor, objective levels cannot fully separate,** and at `total == 4n` they do nothing.
- **Equal levels can differ by one block;** the tie goes to the earlier objective.
- **`s` can be fractional** with two books (81 ÷ 2 = 40.5 → 64 · 80 · 96 · 108 · 124).
- **Language maxima are often set by `20 × n`, not `3 × s`** (s = 8, n = 3 → maximum 60). Revisit `N_MAX` for language books after their dry run.
- **Every existing budget will be asked for again** (old shape), and the matching 08 files go out of date. New books are not affected.

## 9. Test cases

### 9.1 Dry run on real books: `budget_dry_run.py`

Read-only; no model calls. Put it next to `budget_rules.py` and run from the repo root: `python3 -B budget_dry_run.py <book_id>`. It needs `04_book_review/book_after_review.json`, the 07 extracts and the table of contents. It uses the 06 files for today's totals when they exist, or 48 + 12n otherwise.

```python
#!/usr/bin/env python3
"""Compare a book's budgets today with the proposed level totals. Read-only, no model calls.

Needs a book that has been through review and source_questions. Put this file next to
budget_rules.py and run from the repo root:

    python3 -B budget_dry_run.py <book_id> [--data data]

For every section it prints today's total (from 06_section_budgets, or 48 + 12n if that file
is missing), the questions per book s, and the new level 1 / 3 / 5 totals. It ends with the book
totals and counts of sections that hit the floor, the cap or a collapsed range.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from budget_rules import CAP, MIN_SECTION, level_totals, questions_per_book  # noqa: E402


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sources_per_section(toc: Path) -> dict[str, int]:
    """Distinct source PDFs per section number path, as sources.section_spans counts them."""
    counts: dict[str, int] = {}

    def walk(node: ET.Element, numbers: list[str]) -> None:
        for child in node:
            if child.tag != "module":
                continue
            path = numbers + [child.get("number", "?")]
            md5s = {src.get("md5") for src in child if src.tag == "source" and src.get("md5")}
            if md5s:
                counts[".".join(path)] = counts.get(".".join(path), 0) + len(md5s)
            walk(child, path)

    walk(ET.parse(toc).getroot(), [])
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("book_id")
    parser.add_argument("--data", default="data", help="the folder holding input/ and output/")
    args = parser.parse_args()

    data = Path(args.data)
    out = data / "output" / args.book_id
    book = load(out / "04_book_review" / "book_after_review.json")
    if book is None:
        print(f"no reviewed book at {out / '04_book_review' / 'book_after_review.json'}", file=sys.stderr)
        return 1
    toc = data / "input" / args.book_id / f"{args.book_id}.xml"
    if not toc.is_file():
        print(f"no table of contents at {toc}", file=sys.stderr)
        return 1
    sources = sources_per_section(toc)

    header = f"{'section':<10} {'n':>3} {'books':>5} {'count':>6} {'s':>6} {'today':>6} {'L1':>5} {'L3':>5} {'L5':>5} {'L3-today':>9}"
    print(header)
    print("-" * len(header))

    skipped: list[str] = []
    sums = {"today": 0, "L1": 0, "L3": 0, "L5": 0}
    floor = cap = collapsed = formula_today = 0
    book_counts: set[int] = set()
    compared = 0
    for section in book["sections"]:
        number, stem = section["number"], section["stem"]
        n = len(section["spec"]["objectives"])
        extract = load(out / "07_section_source_questions" / f"{stem}.json")
        if extract is None:
            skipped.append(f"{number} (no extract)")
            continue
        books = sources.get(number, 0)
        if books < 1:
            skipped.append(f"{number} (not in the table of contents)")
            continue
        try:
            count = int(extract["questions"]["count"])
            s = questions_per_book(count, books)
            totals = level_totals(s, n)
        except (KeyError, TypeError, ValueError) as error:
            skipped.append(f"{number} ({error})")
            continue
        budget = load(out / "06_section_budgets" / f"{stem}.json")
        try:
            today = int(budget["budget"]["total"])
            mark = ""
        except (TypeError, KeyError, ValueError):
            today = 48 + 12 * n
            mark = "*"
            formula_today += 1

        compared += 1
        book_counts.add(books)
        floor += totals[0] == MIN_SECTION
        cap += totals[-1] == CAP
        collapsed += len(set(totals)) == 1
        for key, value in (("today", today), ("L1", totals[0]), ("L3", totals[2]), ("L5", totals[4])):
            sums[key] += value
        s_text = f"{s:g}" if s == int(s) else f"{s:.1f}"
        print(
            f"{number:<10} {n:>3} {books:>5} {count:>6} {s_text:>6} {str(today) + mark:>6} "
            f"{totals[0]:>5} {totals[2]:>5} {totals[4]:>5} {totals[2] - today:>+9}"
        )

    print("-" * len(header))
    print(f"sections compared: {compared} of {len(book['sections'])}")
    if skipped:
        print(f"skipped: {', '.join(skipped)}")
    if formula_today:
        print(f"* today's total taken from 48 + 12n for {formula_today} section(s) with no budget file")
    if compared:
        ratio = sums["L3"] / sums["today"] if sums["today"] else float("nan")
        print(
            f"book totals: today {sums['today']}, level 1 {sums['L1']}, level 3 {sums['L3']} "
            f"({ratio:.0%} of today), level 5 {sums['L5']}"
        )
        print(f"sections whose level 1 is the floor of {MIN_SECTION}: {floor}")
        print(f"sections whose level 5 is the cap of {CAP}: {cap}")
        print(f"sections whose range collapsed (all levels equal): {collapsed}")
        if len(book_counts) > 1:
            print(f"warning: sections draw on different numbers of source books: {sorted(book_counts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Output on a made-up four-section book (to show the format):

```
section      n books  count      s  today    L1    L3    L5  L3-today
---------------------------------------------------------------------
1.1          4     1     40     40     96    60    92   120        -4
1.2          4     2     81   40.5    96*    64    96   124        +0
1.3          2     1      0      0     72    20    32    40       -40
1.4          5     1    100    100    108   148   148   148       +40
---------------------------------------------------------------------
sections compared: 4 of 5
skipped: 1.5 (no extract)
* today's total taken from 48 + 12n for 1 section(s) with no budget file
book totals: today 372, level 1 292, level 3 368 (99% of today), level 5 432
sections whose level 1 is the floor of 20: 1
sections whose level 5 is the cap of 148: 1
sections whose range collapsed (all levels equal): 1
warning: sections draw on different numbers of source books: [1, 2]
```

**What to look for:** the level-3 book total compared with today; how many sections hit the cap or collapse; whether language sections land far below today; and any warnings.

### 9.2 Reference implementation: `budget_rules.py`

Needs only `pydantic` (in `requirements.txt`).

```python
"""Reference rules for the section exercise budget (proposal v2, not wired into the pipeline).

Pure functions plus the reply model. Needs only pydantic (already in requirements.txt).
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, StrictInt

N_MIN = 4                 # least exercises per objective (one block)
N_MAX = 20                # most exercises per objective when objectives set the range
MIN_SECTION = 20          # least exercises per section
CAP = 148                 # most exercises per section (a multiple of 4)
SOURCE_MIN_FACTOR = 1.5   # minimum = 1.5 x questions per book
SOURCE_MAX_FACTOR = 3     # maximum = 3 x questions per book
LEVELS = 5
BLOCK = 4                 # every total and share is a multiple of this
SPARSE_QUESTIONS_PER_PAGE = 2.0
EXTRA_SMALL = 4           # spares written for a share of 8 or less (non-Arabic books)
EXTRA_DEFAULT = 8         # today's EXTRA_EXERCISES


# ---------------------------------------------------------------- the reply model

class ObjectiveLevel(BaseModel):
    id: str = Field(description="The objective's id, exactly as the spec prints it")
    level: StrictInt = Field(description="1 to 5: how much of the section's practice it needs")


class BudgetReply(BaseModel):
    """What the model returns. No range constraints here: an abort carries level 0."""

    status: Literal["ok", "abort"]
    abort_reason: str = ""
    reasoning: str = ""
    level: StrictInt = 0
    objectives: list[ObjectiveLevel] = Field(default_factory=list)


# ---------------------------------------------------------------- inputs

def questions_per_book(question_count: int, sources: int) -> float:
    """s: the section's extracted questions divided by its source books (an average per book).

    The extract's count already totals every source book of the section. A section with no
    source in the table of contents is an orphan and must be refused before this is called.
    """
    if sources < 1:
        raise ValueError("a section needs at least one source")
    return question_count / sources


def section_pages(page_counts: list[int | None]) -> int | None:
    """Total pages of a section's source PDFs, or None when any of them cannot be counted."""
    if not page_counts or any(pages is None for pages in page_counts):
        return None
    total = sum(page_counts)
    return total if total > 0 else None


def source_reliable(count: int, pages: int | None) -> bool:
    """False for an empty extract, or one under 2 questions per source page.

    `pages` is None when unknown; then only an empty extract is unreliable.
    """
    if pages is not None and pages <= 0:
        raise ValueError("pages must be a positive number or None")
    if count == 0:
        return False
    return pages is None or count / pages >= SPARSE_QUESTIONS_PER_PAGE


# ---------------------------------------------------------------- range and levels

def half_up(x: float) -> int:
    """Round to the nearest whole number, an exact half going up (Python's round() does not)."""
    return math.floor(x + 0.5)


def section_range(questions_per_book: float, objectives: int) -> tuple[int, int]:
    """The least and most exercises this section may get, both multiples of BLOCK."""
    if objectives < 1:
        raise ValueError("a section needs at least one objective")
    cap_blocks = CAP // BLOCK
    if objectives > cap_blocks:
        raise ValueError(f"{objectives} objectives cannot each get {N_MIN} within {CAP}")
    s = questions_per_book
    low = math.ceil(max(SOURCE_MIN_FACTOR * s, N_MIN * objectives, MIN_SECTION) / BLOCK)
    high = math.ceil(max(SOURCE_MAX_FACTOR * s, N_MAX * objectives) / BLOCK)
    return min(low, cap_blocks) * BLOCK, min(high, cap_blocks) * BLOCK


def level_totals(questions_per_book: float, objectives: int) -> list[int]:
    """The five totals the model chooses between, evenly spaced and rounded to BLOCK."""
    low, high = section_range(questions_per_book, objectives)
    a, b = low // BLOCK, high // BLOCK
    return [BLOCK * half_up(a + k * (b - a) / (LEVELS - 1)) for k in range(LEVELS)]


# ---------------------------------------------------------------- shares

def split(total: int, levels: list[int]) -> list[int]:
    """Share a total between objectives: one block each, the rest in proportion to the levels.

    `levels` must be in spec order: the leftover blocks after rounding down go to the largest
    fractions, ties to the earlier objective. A higher level never gets fewer exercises than a
    lower one, and two equal levels end at most one block apart. When total == 4 x objectives,
    every objective gets 4 whatever its level.
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


# ---------------------------------------------------------------- checking and using a reply

def reply_problems(reply: BudgetReply, ids: list[str]) -> list[str]:
    """Every reason a parsed reply cannot be used. An abort is never checked further."""
    if reply.status == "abort":
        return []
    problems: list[str] = []
    if not 1 <= reply.level <= LEVELS:
        problems.append(f"section level {reply.level} is not from 1 to 5")
    named = [row.id for row in reply.objectives]
    for duplicate in sorted({name for name in named if named.count(name) > 1}):
        problems.append(f"{duplicate} is levelled more than once")
    for unknown in [name for name in named if name not in ids]:
        problems.append(f"{unknown} is not an objective of this section")
    for missing in [name for name in ids if name not in named]:
        problems.append(f"{missing} has no level")
    for row in reply.objectives:
        if not 1 <= row.level <= LEVELS:
            problems.append(f"{row.id} level {row.level} is not from 1 to 5")
    return problems


def budget_from_reply(reply: BudgetReply, ids: list[str], s: float, n: int) -> dict:
    """The budget to write, from a reply that passed reply_problems.

    Rows are put back in spec order before the split, so ties go to the earlier objective in
    the spec, not in the reply. A single objective is written as level 5, since it takes the
    whole total at any level.
    """
    by_id = {row.id: row.level for row in reply.objectives}
    levels = [by_id[name] for name in ids]
    if len(ids) == 1:
        levels = [LEVELS]
    total = level_totals(s, n)[reply.level - 1]
    shares = split(total, levels)
    return {
        "total": total,
        "level": reply.level,
        "reasoning": reply.reasoning,
        "objectives": [
            {"id": name, "level": level, "exercises": share}
            for name, level, share in zip(ids, levels, shares)
        ],
    }


def fixed_budget(s: float, n: int, ids: list[str]) -> dict | None:
    """The budget when no call can change it, or None when the model must be asked.

    That is when the range has collapsed (all five totals equal) and either there is one
    objective, or the total leaves every objective exactly one block.
    """
    totals = level_totals(s, n)
    total = totals[0]
    if len(set(totals)) != 1 or not (n == 1 or total == BLOCK * n):
        return None
    levels = [LEVELS] if n == 1 else [3] * n
    shares = split(total, levels)
    return {
        "total": total,
        "level": 3,
        "reasoning": (
            f"The range collapsed to {total}; "
            + ("one objective takes the whole total." if n == 1 else "every objective gets 4.")
            + " Set without a model call."
        ),
        "objectives": [
            {"id": name, "level": level, "exercises": share}
            for name, level, share in zip(ids, levels, shares)
        ],
    }


BUDGET_KEYS = {"total", "level", "reasoning", "objectives"}


def budget_is_current(budget: dict, ids: list[str], s: float, n: int) -> bool:
    """True when a budget on disk can be reused under the current spec and extract."""
    try:
        if set(budget) != BUDGET_KEYS:
            return False  # old shape (no level) or foreign keys
        rows = budget["objectives"]
        if [row["id"] for row in rows] != ids:
            return False
        level = budget["level"]
        levels = [row["level"] for row in rows]
        if any(type(v) is not int or not 1 <= v <= LEVELS for v in [level, *levels]):
            return False
        total = level_totals(s, n)[level - 1]
        if budget["total"] != total:
            return False
        return [row["exercises"] for row in rows] == split(total, levels)
    except (KeyError, TypeError, ValueError):
        return False


# ---------------------------------------------------------------- spares (new_questions)

def extra_exercises(share: int, arabic: bool) -> int:
    """Spares new_questions writes on top of a share.

    4 for a share of 8 or less, 8 otherwise. Arabic books keep 8 until their rejection rate is
    measured, because they use their spares (review-cost notes).
    """
    if arabic:
        return EXTRA_DEFAULT
    return EXTRA_SMALL if share <= 2 * BLOCK else EXTRA_DEFAULT
```

### 9.3 Reference tests: `test_budget_rules.py`

```python
"""Reference tests for the section exercise budget rules (v2). Run: python3 -B -m unittest test_budget_rules -v"""

import itertools
import json
import unittest

from pydantic import ValidationError

from budget_rules import (
    BLOCK,
    CAP,
    LEVELS,
    MIN_SECTION,
    N_MIN,
    BudgetReply,
    budget_from_reply,
    budget_is_current,
    extra_exercises,
    fixed_budget,
    level_totals,
    questions_per_book,
    reply_problems,
    section_pages,
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

# (total, objective levels in spec order, shares)
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
    (36, [3, 3], [20, 16]),                          # tie goes to the earlier objective
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


def reply_json(level=3, levels=(1, 5, 3, 3), ids=IDS, status="ok"):
    return json.dumps({
        "status": status,
        "abort_reason": "",
        "reasoning": "x",
        "level": level,
        "objectives": [{"id": name, "level": value} for name, value in zip(ids, levels)],
    })


def parsed(**kwargs):
    return BudgetReply.model_validate_json(reply_json(**kwargs))


class InputTests(unittest.TestCase):
    def test_questions_per_book(self):
        self.assertEqual(questions_per_book(30, 1), 30)
        self.assertEqual(questions_per_book(80, 2), 40)
        self.assertEqual(questions_per_book(81, 2), 40.5)
        with self.assertRaises(ValueError):
            questions_per_book(10, 0)

    def test_section_pages(self):
        self.assertEqual(section_pages([10, 9]), 19)
        self.assertEqual(section_pages([0, 5]), 5)
        self.assertIsNone(section_pages([10, None]))   # a missing PDF makes pages unknown
        self.assertIsNone(section_pages([]))
        self.assertIsNone(section_pages([0]))

    def test_reliability_table(self):
        for count, pages, expected in RELIABLE_CASES:
            with self.subTest(count=count, pages=pages):
                self.assertEqual(source_reliable(count, pages), expected)

    def test_zero_pages_is_refused(self):
        for pages in (0, -1):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                source_reliable(10, pages)


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

    def test_levels_do_nothing_when_total_is_four_per_objective(self):
        self.assertEqual(split(20, [1, 2, 3, 4, 5]), [4] * 5)
        self.assertEqual(split(148, [1, 2, 3, 4, 5] * 7 + [5, 5]), [4] * 37)

    def test_properties(self):
        for n in range(1, 7):
            for levels in itertools.product(range(1, 6), repeat=n):
                totals = {
                    level_totals(s, n)[k] for s in (0, 5, 10, 25, 40, 70, 100) for k in range(LEVELS)
                }
                for total in totals:
                    shares = split(total, list(levels))
                    self.assertEqual(sum(shares), total)
                    self.assertTrue(all(share >= N_MIN and share % BLOCK == 0 for share in shares))
                    for i, j in itertools.permutations(range(n), 2):
                        if levels[i] > levels[j]:
                            self.assertGreaterEqual(shares[i], shares[j], (total, levels, shares))
                        if levels[i] == levels[j]:
                            self.assertLessEqual(abs(shares[i] - shares[j]), BLOCK)

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValueError):
            split(90, [3, 3])          # not a multiple of 4
        with self.assertRaises(ValueError):
            split(8, [3, 3, 3])        # fewer blocks than objectives
        with self.assertRaises(ValueError):
            split(40, [0, 3])          # level out of range


class ReplyModelTests(unittest.TestCase):
    """The pydantic model Gemini's JSON is parsed through, not raw dicts."""

    def test_valid_reply(self):
        self.assertEqual(reply_problems(parsed(), IDS), [])

    def test_abort_parses_with_level_zero(self):
        reply = BudgetReply.model_validate_json('{"status": "abort", "abort_reason": "empty spec"}')
        self.assertEqual(reply.level, 0)
        self.assertEqual(reply_problems(reply, IDS), [])

    def test_loose_numbers_are_rejected_at_parse(self):
        for bad in ('"3"', "3.0", "true"):
            raw = reply_json().replace('"level": 3,', f'"level": {bad},', 1)
            with self.subTest(level=bad), self.assertRaises(ValidationError):
                BudgetReply.model_validate_json(raw)
            raw = reply_json().replace('"level": 1}', f'"level": {bad}}}', 1)
            with self.subTest(objective_level=bad), self.assertRaises(ValidationError):
                BudgetReply.model_validate_json(raw)

    def test_out_of_range_levels_are_problems(self):
        for level in (0, 6):
            with self.subTest(level=level):
                self.assertTrue(reply_problems(parsed(level=level), IDS))
        self.assertTrue(reply_problems(parsed(levels=(1, 7, 3, 3)), IDS))

    def test_bad_objective_rows(self):
        cases = {
            "missing": (IDS[:3], (1, 5, 3)),
            "duplicate": (IDS + ["objective_1"], (1, 5, 3, 3, 2)),
            "unknown": (IDS + ["objective_9"], (1, 5, 3, 3, 2)),
        }
        for name, (ids, levels) in cases.items():
            with self.subTest(case=name):
                self.assertTrue(reply_problems(parsed(ids=ids, levels=levels), IDS))

    def test_order_does_not_matter_for_the_check(self):
        reply = parsed(ids=list(reversed(IDS)), levels=(3, 3, 5, 1))
        self.assertEqual(reply_problems(reply, IDS), [])


class BudgetTests(unittest.TestCase):
    def test_budget_from_reply(self):
        budget = budget_from_reply(parsed(), IDS, 40, 4)
        self.assertEqual(budget["total"], 92)
        self.assertEqual(budget["level"], 3)
        self.assertEqual([row["exercises"] for row in budget["objectives"]], [8, 36, 24, 24])
        self.assertEqual([row["id"] for row in budget["objectives"]], IDS)

    def test_shuffled_reply_gives_the_same_budget(self):
        shuffled = parsed(ids=list(reversed(IDS)), levels=(3, 3, 5, 1))
        self.assertEqual(budget_from_reply(shuffled, IDS, 40, 4), budget_from_reply(parsed(), IDS, 40, 4))

    def test_ties_follow_spec_order_not_reply_order(self):
        ids = ["objective_1", "objective_2"]
        reply = parsed(level=4, ids=list(reversed(ids)), levels=(3, 3))   # s=0, n=2: level 4 is 36
        budget = budget_from_reply(reply, ids, 0, 2)
        self.assertEqual([row["exercises"] for row in budget["objectives"]], [20, 16])

    def test_single_objective_is_written_as_level_5(self):
        reply = parsed(level=2, ids=["objective_1"], levels=(2,))
        budget = budget_from_reply(reply, ["objective_1"], 30, 1)
        self.assertEqual(budget["total"], 60)
        self.assertEqual(budget["objectives"], [{"id": "objective_1", "level": 5, "exercises": 60}])

    def test_budget_is_current(self):
        budget = budget_from_reply(parsed(), IDS, 40, 4)
        self.assertTrue(budget_is_current(budget, IDS, 40, 4))
        self.assertFalse(budget_is_current(budget, IDS, 52, 4))           # extract count changed
        self.assertFalse(budget_is_current(budget, IDS[:3], 40, 3))       # spec changed
        old = {"total": 96, "reasoning": "x", "objectives": [{"id": i, "exercises": 24} for i in IDS]}
        self.assertFalse(budget_is_current(old, IDS, 40, 4))              # old shape, no level
        tampered = json.loads(json.dumps(budget))
        tampered["objectives"][0]["exercises"] = 12
        self.assertFalse(budget_is_current(tampered, IDS, 40, 4))
        for bad in (True, "3"):
            loose = json.loads(json.dumps(budget))
            loose["level"] = bad
            with self.subTest(level=bad):
                self.assertFalse(budget_is_current(loose, IDS, 40, 4))

    def test_fixed_budget_skips_the_call_only_when_nothing_can_change(self):
        one = fixed_budget(5, 1, ["objective_1"])
        self.assertEqual(one["total"], 20)
        self.assertEqual(one["objectives"], [{"id": "objective_1", "level": 5, "exercises": 20}])
        self.assertTrue(budget_is_current(one, ["objective_1"], 5, 1))
        self.assertEqual(fixed_budget(100, 1, ["objective_1"])["total"], 148)
        many = [f"objective_{i}" for i in range(1, 38)]
        full = fixed_budget(0, 37, many)
        self.assertEqual([row["exercises"] for row in full["objectives"]], [4] * 37)
        self.assertTrue(budget_is_current(full, many, 0, 37))
        self.assertIsNone(fixed_budget(40, 4, IDS))       # a real range
        self.assertIsNone(fixed_budget(97, 5, [f"objective_{i}" for i in range(1, 6)]))  # collapsed, levels still matter
        self.assertIsNone(fixed_budget(6, 2, IDS[:2]))


class ExtraTests(unittest.TestCase):
    def test_extra_exercises(self):
        self.assertEqual(extra_exercises(4, arabic=False), 4)
        self.assertEqual(extra_exercises(8, arabic=False), 4)
        self.assertEqual(extra_exercises(12, arabic=False), 8)
        self.assertEqual(extra_exercises(148, arabic=False), 8)
        self.assertEqual(extra_exercises(4, arabic=True), 8)


if __name__ == "__main__":
    unittest.main()
```

Expected: `Ran 24 tests … OK`.

### 9.4 Integration tests to add in `scripts/tests/test_budgets.py`

Mock `client.models.generate_content`, patch `time.sleep`, and build a small fake book under a temporary `OUTPUT_DIR`.

1. Stage order: `source_questions` comes before `budgets` in `pipeline.STAGE_NAMES`.
2. `build_prompt(meta)` leaves no `{book_context}` or `{language_rules}` placeholder, and has no "48 plus 12".
3. A selected section with no extract → `RuntimeError` naming it, and no model call. With `-s`, a missing extract in an **unselected** section → the run goes ahead and the profile line says "N of M".
4. A stale extract (tally ids differ from the spec) → refused.
5. An orphan section (not in the table of contents) → refused before any call.
6. Happy path: s = 40, n = 4, reply level 3 with levels 1, 5, 3, 3 → the file has 92 and 8, 36, 24, 24 in spec order.
7. The section message has the five totals, the right source-block variant, every section in the profile with this one marked, and no question text.
8. Retry: the first reply has `"level": "3"` (rejected by `StrictInt`) or `level` 6, the second is valid → 2 attempts, one file.
9. Abort → nothing written, counted as aborted, exit code 1.
10. Reuse: a second run makes no call; changing the extract count → asked again; an old-shape file → asked again with the warning line.
11. `fixed_budget`: one objective with 5 questions → file written with **no** model call; `call.json` (or the run summary) says so.
12. Two books: count 80 with 2 sources → s = 40 in the message and in `call.json`.
13. Mixed numbers of books across sections → a warning.
14. A missing section PDF → `pages` is `None`, the count is treated as reliable unless it is 0, and nothing crashes.
15. Opening line: the format in 6.2.
16. `twin.stage_output_exists("budgets", …)` is False for old-shape files and True for new ones.
17. Translate: the English 06 is rewritten → the Arabic 06 is re-copied without `--update`.
18. `new_questions.budget_shares()` reads the new file; `written_exercises` and `kept_objectives` use `extra_exercises`.

### 9.5 Paper-fill comparison (gate for the spare cap)

On one math book: run `new_questions` → `questions_review` → `papers` before and after the change, and compare each section's `<stem>.selection.json` (`budget`, `written`, `printed`, `worst_interior_fill`, `last_page_fill`) and the "over budget to fill pages from the pool" notes. Pass if no objective prints fewer than its budget and the fill does not get clearly worse. Repeat on an Arabic-native book before extending the cap to Arabic.

### 9.6 Manual check on a real book (after implementation; costs API calls)

1. `python3 -B scripts/budgets.py <book_id> -s <section>`, then read `reasoning` in the file and in `call.json`.
2. A whole book: count the levels. If most sit on one level, adjust the level descriptions or the factors.
3. Repeatability: copy `06_section_budgets/` aside, run with `--update`, and compare levels section by section.
4. An Egyptian two-book book: confirm the warning in 4.1 does not fire.

## 10. Test failures that already exist in this checkout

Before any change, `python3 -B -m unittest discover -s scripts/tests -p 'test_*.py'` gives 862 tests with **26 errors** in `test_translate_retries` (the table of contents for `mathematics_ar_eg07_t1` is missing from `data/input`) and **1 failure** in `test_pyflakes` (`pyflakes` is not installed). Neither comes from this change. Compare against this baseline.

## 11. Decisions made

- The model returns levels only; Python does all arithmetic.
- N_MIN 4, N_MAX 20, MIN_SECTION 20, factors 1.5 and 3, CAP 148, 5 levels, everything in multiples of 4.
- `s` = count ÷ number of source books.
- Only selected sections must have extracts; the profile covers what exists and says so.
- No question text is sent, and there are no new prompt placeholders.
- An unreliable count still counts toward the range; only the message changes.
- "Heavier" means more demanding, not more questions.
- One objective is written as level 5 by Python, never enforced on the model.
- Calls are skipped when no reply could change the file.
- The spare cap goes in this change for non-Arabic books, gated by 9.5.
- `--update` keeps its meaning.

## 12. Still open, after real data

1. `N_MAX` for language books, after their dry run.
2. The spare cap for Arabic-native books, after measuring their rejection rate.
3. A `role` multiplier for revision and assessment sections, if the first real run shows they come out too heavy.

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

- Difficulty. How demanding the section's content is for this grade: new procedures, multi-step reasoning and subtle distinctions need more practice than definitions and recognition.
- The objectives. How many there are, and how many of them are heavy.
- The section's role. The spec's `role` says whether the section teaches, revises or assesses. Revision and assessment sections usually need less new practice than teaching sections, unless the spec shows they drill a lot.
- Subject and grade. What one exercise asks of a learner differs by subject, and what counts as enough practice differs by grade: younger learners need more repetition on each new skill.
- The book profile. Use it to see what a typical section of this book looks like. When the profile covers only some of the book's sections, compare against those.

A heavier section is a more demanding one, not one with more questions. The five totals already grow with the textbook's question count, so a section the textbook drills more than most is not, on its own, a reason to choose a higher level.

Level 3 is a typical section of this book. Levels 1 and 5 are for sections clearly lighter or clearly heavier than most. Say why you chose the level in `reasoning`.

When a section is drawn from more than one source book, its question count is also given as the average per book, and the book profile is on that same per-book scale.

When the section says its question count is low for its pages, that is normal for long items such as passages, but the extraction may also have missed questions: weigh the count together with the spec. When no questions were extracted, judge from the spec and the book profile alone.

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
- reasoning: two to four sentences in English, for the maintainer: first the section level and why, set against the spec and the book profile; then which objectives got the highest and lowest levels, and why. Name objectives by id.

{language_rules}

## Abort
Abort only when the section cannot be judged at all: no objectives, or a spec so empty there is nothing to judge. On abort, give one line in `abort_reason` and return no level and no objectives.

## Output
Return the response envelope JSON: status, abort_reason, reasoning, level, objectives.
```

## Appendix B: worked example

Section 2.2, *Reading and Evaluating Complex Expressions*, of `mathematics_en_in07_p1`: one source book, pages 59.29–77.46 (19 PDF pages). The titles and pages are real; the objectives and counts are made up.

```
Section level totals (exercises): 1 = 60, 2 = 76, 3 = 92, 4 = 104, 5 = 120

Textbook questions for this section: 40, 3 of them tagged with no objective.
Per objective (a question can carry two tags): objective_1 12, objective_2 18, objective_3 0, objective_4 13

Book profile (textbook questions per source book): 44 sections, about 22 per section.
section | title                                       | objectives | textbook questions
1.1     | A Lakh Varieties!                           | 3          | 21
…
2.2     | Reading and Evaluating Complex Expressions  | 4          | 40   <- this section
…

Section spec JSON:
{"number":"2.2","title":"Reading and Evaluating Complex Expressions","module_path":"2. Arithmetic Expressions > 2. Reading and Evaluating Complex Expressions","spec":{…}}
```

Reply:

```json
{
  "status": "ok",
  "abort_reason": "",
  "reasoning": "The textbook drills this section more than most, but the range already reflects that; evaluating expressions with several operations is a typical Grade 7 skill, so level 3. objective_2 (evaluate using the order of operations) is the core skill and gets 5; objective_1 (identify terms) is recognition and gets 1.",
  "level": 3,
  "objectives": [
    { "id": "objective_1", "level": 1 },
    { "id": "objective_2", "level": 5 },
    { "id": "objective_3", "level": 3 },
    { "id": "objective_4", "level": 3 }
  ]
}
```

Python: 40 ÷ 19 = 2.1 per page, so the count is reliable. Level 3 → 92 → 23 blocks → 8, 36, 24, 24 (4.5). The file is the one in 6.6.
