# Prompts

Everything the pipeline sends to the model lives here, one folder per stage. Nothing in
this folder is documentation: each file is input to a program, and editing one changes
what the model is asked.

## One folder per stage

```
prompts/
  specs/             specs.py            one section → its spec
  review/            review.py           the whole book → cross-section defects
  labels/            labels.py           one section → a heading per objective
  budgets/           budgets.py          one section → exercises per objective
  source_questions/  source_questions.py one section's pages → the book's own questions
  new_questions/     new_questions.py    one objective → its exercises
  questions_review/  questions_review.py one exercise → its faults
  draw/              draw.py             one figure description → TikZ
```

## What is in a stage's folder

```
<stage>/
  core.md              what holds for every book
  language/en.md       what holds for a book written in English
  language/ar.md       ... in Arabic
  numerals/western.md  what holds for a book whose numbers are printed 0–9
  numerals/arabic.md   ... ٠–٩
  notation/latin.md    what holds for a book whose maths is written with Latin letters, LTR
  notation/arabic.md   ... with Arabic letters, RTL
```

`core.md` carries a slot for each part — `{language_rules}`, `{numerals_rules}`,
`{notation_rules}` — where `prompting.py` drops the book's file in, and `{book_context}`
for the one-line description of the book. A stage whose work does not depend on the digits
or the notation (review, labels, budgets) has no `numerals/` or `notation/` folder and no
slot for them. The assembler refuses a slot with no file to fill it and a folder with no
slot to fall into, so a rule cannot go missing silently.

`draw/` also holds `repair.md`, `relabel.md` and `widen.md` (one per follow-up step, each
sent after the assembled drawing prompt) and `figure-types/<type>.md` (dropped into the
`{figure_type}` slot). Those do not vary with the book.

## Subject overlays (specs and new_questions)

Mathematics books use each stage's root `core.md` and the three profile folders only. Arabic,
English, and Science books use the same profile dimensions from `subject/language/`,
`subject/numerals/`, and `subject/notation/`, plus `subject/core.md` and the blocks listed in
`subject/<family>/blocks.json`. The pipeline book id's first token selects the family:
`mathematics`, `arabic`, `english`, or `science`. Any other token is refused for those two
stages. Other stages always use the root core and ignore the subject token.

## Which parts a book gets

Read off the `<book>` element of its table of contents (`language_code`, `numerals`,
`notation`), checked by `sources.py` before anything is rendered. Only these four books
exist; anything else is refused:

| Book | language | numerals | notation |
|---|---|---|---|
| English | `en.md` | `western.md` | `latin.md` |
| Arabic prose, maths as in an English book | `ar.md` | `western.md` | `latin.md` |
| Arabic prose, Western digits, Arabic letters in the maths | `ar.md` | `western.md` | `arabic.md` |
| Arabic prose, Arabic digits and letters in the maths | `ar.md` | `arabic.md` | `arabic.md` |

## Two conventions every part relies on

**Digits are always written 0–9 in the model's output**, whatever the book prints.
Reader-visible digits in Arabic exercise content go inside `$...$`; structural IDs,
`[figure N]`, and TikZ coordinates/dimensions stay technical 0–9 text. The paper renders
the math digits as the profile requires.

**Mathematics is always written in logical order** (first operand first) as LaTeX in
`$...$`. For Arabic notation the letters inside are Arabic (`$س^2 - 4 = 0$`, `$\triangle
أبج$`) and the LaTeX commands stay ASCII; the typesetter mirrors the layout.

Arabic sibling translation also builds a per-book terminology/symbol/unit map. Each
section's `translate/specs.md` call returns a fragment from the attached Arabic textbook
pages; Python merges every section before any downstream translation call. Accepted Arabic
JSON and printable stored TikZ use the frozen map's Arabic unit forms (`cm` → `سم`) while
validators retain canonical unit identity and power. The renderer changes bidi/layout only.
A prompt must never invent a second local unit convention after that map is frozen.

## Checking

`python scripts/src/prompting.py` assembles every stage for every book and prints what went
in, without reading a book or calling anything. Every call's log records the files its
prompt was assembled from under `prompt_files`.
