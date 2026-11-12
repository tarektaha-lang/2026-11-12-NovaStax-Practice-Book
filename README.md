# NovaStax Practice Book

A short command guide is in [USAGE.md](USAGE.md). Copy-paste cheat sheet: [COMMANDS.md](COMMANDS.md).

This project turns textbook source PDFs and a book table of contents into a complete
practice book:

- reviewed section specifications;
- newly written and checked exercises;
- TikZ figures;
- typeset section papers and a bound PDF;
- question/group JSON with PDF coordinates;
- spoken transcripts, audio, alignment, and a package;
- API-call and cost reports.

It can also create an Arabic twin from a completed English book. The Arabic workflow
translates the reviewed English content, validates it, reviews it again, redraws or
adapts figures, and typesets the Arabic book according to its own language, numeral,
notation, and RTL profile.

## Requirements

- Python 3.11 or later
- Packages in `requirements.txt`
- LuaLaTeX/TeX Live with the required fonts
- `rsvg-convert` from librsvg for covers
- `pdftoppm` for figure previews and visual checks
- A Gemini API key for model-backed stages

Install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Set either API-key environment variable:

```bash
export GOOGLE_API_KEY="..."
```

or:

```bash
export GEMINI_API_KEY="..."
```

If both are set, the Google SDK uses `GOOGLE_API_KEY`.

No other environment variable is read. `align`, `cost`, `papers`, `groups`,
`frontmatter`, `extract`, `--help`, `--list` and `--dry-run` need no key.
`package` needs no key; it calls the 12digit id service only when a book has no
catalog row (see `package` below).

## Book IDs and input

Every book has an ID such as:

```text
mathematics_en_eg08_t1
mathematics_ar_eg08_t1
```

The ID identifies the subject, language, country, grade, and term.

The main input folder is:

```text
data/input/<book_id>/
```

It contains:

- `<book_id>.xml`: book metadata and module/section structure;
- the textbook/source PDFs referenced by the XML;
- the selected cover illustration where required.

The `<book>` element defines the rendering profile:

- `language_code`: for example `en` or `ar`;
- `numerals`: `western` or `arabic`;
- `notation`: `latin` or `arabic`.

Supported profiles are:

1. English prose, Western numerals, Latin mathematics.
2. Arabic prose, Western numerals, Latin mathematics.
3. Arabic prose, Western numerals, Arabic mathematical notation.
4. Arabic prose, Arabic-Indic numerals, Arabic mathematical notation.

Model output always stores digits and mathematics in logical source order. The
typesetter applies the final numeral style, mathematical direction, table direction,
TikZ-label direction, and page direction.

For an Arabic twin, translation is map-first. Every section's spec call receives that
section's Arabic textbook pages and returns an ordered terminology/symbol/unit fragment
for a deterministic inventory. All section fragments must merge into one conflict-free
per-book map before labels, exercise groups, reviews, figures, or papers run. Canonical
unit identities are validated against English, then accepted Arabic specs, exercise JSON,
and printable figure TikZ store the map's Arabic unit forms. Mathematics stays in logical
order; the `ar`/`western`/`latin` renderer performs bidi layout only. Reader-visible digits
in Arabic exercise content are stored inside math islands so they use the math face. A
section-only translation still verifies or builds the complete book map first.

## The normal pipeline

Run a complete book:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1
```

List all stages:

```bash
python3 -B scripts/pipeline.py --list
```

Preview commands without running them:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --dry-run
```

Run one section:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 -s 4.1
```

Resume from a stage after a failure:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from papers
```

Run a stage range:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --from groups --to audios
```

Run only selected stages, still in pipeline order:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --only frontmatter papers groups
```

Skip selected stages:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --skip audios align package
```

Model options can be forwarded to stages that support them:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --model gemini-3.8-flash \
  --thinking-level medium \
  --workers 25
```

`--model` does not select the speech model. Use `--tts-model` for `audios.py`.
`transcripts.py`, `audios.py`, `new_questions.py` and `questions_review.py`
default to the Gemini Batch API.
`--transcripts-batch` / `--no-transcripts-batch` forward `--batch` / `--no-batch`
to `transcripts` and `ar_transcripts` only (omitted, those scripts keep Batch).
`--tts-batch` (the pipeline default) forwards `--batch` to `audios`;
`--no-tts-batch` forwards `--no-batch`. `--batch-parallel N` (0 = no cap,
default) is forwarded to transcripts, audios and their `ar_` copies.

## Pipeline stages

The stages run in this order:

1. `sources` — validate the XML and split/crop source PDFs by section.
2. `specs` — make a curriculum specification for each section.
3. `review` — review all section specifications as one coherent book.
4. `labels` — create short objective headings.
5. `budgets` — distribute the exercise count among objectives.
6. `source_questions` — transcribe exercises already present in the sources.
   A truncated reply (MAX_TOKENS, even with valid JSON, or a size-abort) is not
   retried as the same whole-section call: the stage splits per source PDF at
   the same thinking level, and only then retries once at medium if the level
   was high. `finish_reason` is stored on `call.json`. A successful extraction
   under 2.0 questions per page prints a warning.
7. `new_questions` — write new exercises to the required budget.
   Restatement is maths-aware (clause-start, sibling uniqueness by content words
   and maths keys). A parabola whose label is an explicit Latin quadratic gets
   its vertex from that label (`quadratic_from_label` in
   `figures/function_graph.py`; one parser, Latin only); `opens` is corrected
   from the label and counted (`parabola_vertex_derived`,
   `parabola_opens_corrected`). All-math choices are wrapped (`math_choices_wrapped`);
   `\(...\)` becomes `$...$` (`paren_delimiters_converted`). The last rule-check
   failure is sent back as `feedback.txt` (`feedback_from_attempt` on
   `call.json`); `finish_reason` is logged even when JSON parsing fails.
   Group-level salvage of a half-good objective reply is not implemented.
8. `questions_review` — check the exercises and reject faulty ones.
   Pass `--strip-figures` to drop accepted exercises that need a drawn figure, then exit.
9. `extract` — collect all requested figure descriptions.
10. `draw` — create, repair, review, and compile TikZ figures.
11. `frontmatter` — create the cover, title, copyright, and contents pages.
12. `papers` — select exercises, fit pages, compile sections, and bind the book.
13. `groups` — build working JSON, question JSON, and bbox-overlay PDFs.
14. `transcripts` — write spoken text for each printed group (Gemini Batch by default).
15. `audios` — synthesize transcript audio (Gemini Batch by default).
16. `align` — align transcript text with audio timings.
17. `package` — assemble the delivery package. The first package of a book
    with no `data/book_database.json` row appends one row and calls
    https://12digit.nagwa.com/get.bulk.codes/4/cps/cps.system/. Existing rows
    are never modified. `python3 -B scripts/package.py <book_id> --dry-run-catalog`
    previews the row without minting.
18. `cost` — price the recorded API usage.

With `--translate` the order is (`python3 -B scripts/pipeline.py --list --translate`):

```text
sources specs review labels budgets source_questions new_questions questions_review
extract draw frontmatter papers groups
ar_sources translate ar_frontmatter ar_papers ar_groups
transcripts audios align package cost
ar_transcripts ar_audios ar_align ar_package ar_cost
```

`--from` / `--to` are inclusive and must name a stage from the list in use.
`--to groups` with `--translate` means through `ar_groups`; `--to cost` means
through `ar_cost`.

### All `pipeline.py` flags

| Flag | Meaning |
| --- | --- |
| `book_id` | the book; with `--translate`, the **English** id |
| `-s, --section` | hold section-scoped stages to this section (whole-book stages still run) |
| `--update` | redo work already on disk on the stages that skip (draw.py gets `--again`) |
| `--translate` | after the English book, translate into the `_ar_` sibling and run its stages |
| `--updateboth` | with `--translate`, rebuild the selected range on both books |
| `--model` | model for stages that call one (not speech); omitted, each stage keeps its default |
| `--thinking-level {minimal,low,medium,high}` | forwarded to stages that take it |
| `--tts-model` | speech model for `audios` |
| `--tts-batch` / `--no-tts-batch` | `audios` through Gemini Batch (default) or unary |
| `--transcripts-batch` / `--no-transcripts-batch` | forward `--batch` / `--no-batch` to `transcripts` and `ar_transcripts`; omitted, they keep Batch |
| `--batch-parallel N` | at most N Batch jobs in flight (0 = no cap, default); forwarded to transcripts, audios and their `ar_` copies |
| `--workers` | most calls in flight for stages that take `--workers` |
| `--from` / `--to` | first / last stage, inclusive |
| `--only STAGE ...` | run only these stages, in pipeline order |
| `--skip STAGE ...` | drop these stages |
| `--no-output-backup` | forward `--no-output-backup` to `package` and `ar_package` (served zip only; skip the dated book-output archive) |
| `--list` | print the stage order and exit |
| `--dry-run` | print the commands without running them |

`pipeline.py` itself calls no model. It runs each stage script and stops at the
first non-zero exit. Each script decides what to skip.

### Stage-script flags

Every stage script takes `book_id` and `--help`. Section-scoped scripts take
`-s / --section`. Flags beyond those (from each script's argparse):

| Script | Flags |
| --- | --- |
| `translate.py` | `--update`, `--model`, `--thinking-level`, `--spec-thinking`, `--label-thinking`, `--exercise-thinking`, `--review-thinking`, `--tikz-thinking`, `--terminology-thinking`, `--escalation-thinking`, `--sweeps N`, `--retranslate-on-prompt-change`, `--rereview`, `--remerge`, `--no-cache`, `--map-only` \| `--downstream-only`, `--workers` (default 25, max 100) |
| `transcripts.py` | `--update`, `--model` (default gemini-3.8-flash), `--thinking-level` (default medium), `--batch` / `--no-batch`, `--unary-fallback N`, `--discard-batch-state`, `--batch-parallel N` |
| `audios.py` | `--update`, `--model` (default gemini-3.8-flash-tts), `--batch` / `--no-batch`, `--discard-batch-state`, `--batch-parallel N` |
| `align.py` | `--update`, `--workers` (default 4) |
| `package.py` | `--dry-run-catalog`, `--no-output-backup` |
| `papers.py` | `--allow-missing-figures`, `--report-english-glosses` / `--no-report-english-glosses`, `--section-only`, `--output-dir`, `--selection-from` \| `--membership-from` |
| `groups.py` | `--no-pdf`, `--section-only`, `--papers-dir`, `--output-dir` |
| `new_questions.py` | `--update`, `--model`, `--thinking-level` (default high), `--workers`, `--force-generate`, `--batch` / `--no-batch`, `--discard-batch-state`, `--batch-parallel N`, `--download-workers N` |
| `questions_review.py` | `--update`, `--model`, `--thinking-level` (default high), `--workers`, `--strip-figures`, `--batch` / `--no-batch`, `--discard-batch-state`, `--batch-parallel N`, `--download-workers N` |
| `cost.py` | `--step STEP`, `-o / --output` |

Run `python3 -B scripts/<stage>.py --help` for the rest; `--help` makes no call.

For whole-book runs, `extract` is complete only when its figure-data records exactly
match every figure requested by all accepted sections. Finding a few old figure files
does not cause later sections to be skipped.

Each stage can also be run directly:

```bash
python3 -B scripts/papers.py mathematics_ar_eg08_t1 -s 4.1
python3 -B scripts/cost.py mathematics_ar_eg08_t1
python3 -B scripts/<stage>.py --help
```

## Gemini Batch for transcripts, speech, new questions and review

`transcripts.py`, `audios.py`, `new_questions.py` and `questions_review.py`
submit every chunk of the current attempt round at once through the Gemini
Batch API (half the standard token rate in `prices.json`, billed when the job
lands). A job may take up to 24 hours.
`--batch-parallel N` caps how many jobs are in flight (`0 = no cap (default);
N = at most N jobs in flight`). Jobs that are already finished and only
downloading do not count toward that cap. `--download-workers N` (default 3;
`1` = serial) downloads finished jobs in parallel. Each worker can peak at
about 1–1.5 GB (JSON + base64 + PCM); 3 workers are about 3–4.5 GB per book,
and 4 books × 3 workers can need 12–18 GB, so lower to 2 or 1 when several
books run at once. `--no-batch` uses unary `generateContent` instead, with up
to 25 workers.

```bash
python3 -B scripts/transcripts.py mathematics_en_eg07_t1 -s 1.2
python3 -B scripts/transcripts.py mathematics_en_eg07_t1 -s 1.2 --no-batch
python3 -B scripts/transcripts.py mathematics_en_eg07_t1 --unary-fallback 5
python3 -B scripts/audios.py mathematics_en_eg07_t1 -s 1.2 --no-batch
python3 -B scripts/new_questions.py mathematics_en_eg07_t1 -s 1.2 --no-batch
python3 -B scripts/questions_review.py mathematics_en_eg07_t1 -s 1.2 --no-batch
python3 -B scripts/pipeline.py mathematics_en_eg07_t1 --from transcripts --batch-parallel 2
python3 -B scripts/audios.py mathematics_en_eg07_t1 --download-workers 2
```

`--unary-fallback N` (transcripts only, default 0) sends at most N remaining
items through unary `generateContent` at the current attempt instead of the last
Batch round. `429 RESOURCE_EXHAUSTED` at upload or create re-queues that chunk
without spending an attempt; create is retried after any live job finishes, or
after a 60s ×1.5 (cap 600s) wait when nothing is live.

In-flight jobs are recorded as a `jobs` list in
`data/output/<book_id>/00_transcripts_batch_state.json`,
`00_audios_batch_state.json`, `00_new_questions_batch_state.json` or
`00_questions_review_batch_state.json` (a legacy single `job` object is still read and
converted on the first save). English and Arabic books never share a state file.
A rerun adopts every still-running saved job and submits leftover chunks in the
same round (overlap). `--discard-batch-state` deletes only that local JSON; it
does **not** cancel the remote job, so discarding a running job and resubmitting
bills twice.

To switch a book that already has an in-flight job: Ctrl-C the run (safe; the
remote job keeps going), then rerun the same command **without `--update`**. Do
not pass `--discard-batch-state` while a job is running.

`00_transcripts_batch.lock`, `00_audios_batch.lock`,
`00_new_questions_batch.lock` and `00_questions_review_batch.lock` allow one run per book at
a time (released on Ctrl-C). `--no-batch` still takes the lock when a state file
exists: it polls and applies every saved job, then voices the rest unary.

Each job holds fewer than 150 requests and under 18 MB inline; a larger
chunk is uploaded as JSONL. Every item has at most 4 attempts (Batch rounds).

Resume and retry rules (all in `src/gemini_batch.py`):

- **Missing-only retry.** A failed job, or a job whose apply step stopped
  partway, re-queues only the keys that still have no output on disk. Rows
  already written are never sent again.
- **State before files.** On every finished, failed or gone job the entry is
  removed from the state file first, then its input/result files are deleted,
  so a crash in between cannot resubmit a finished job.
- **Adopting saved jobs.** A rerun adopts each saved job. A legacy audio job
  name with several remote matches adopts the one still running, else the
  newest (with a warning); it resubmits only if every match failed.
- **Adopted jobs skip what is already done.** A key that is already applied or
  stale is not rewritten. A key missing from the job's results but already on
  disk counts as done; it fails only if it is in neither.
- **Poll errors.** 30 consecutive errors polling one job stop the run and keep
  the state file, so the same command resumes. The count resets on any
  successful poll. There is no time limit.
- **Status vs download.** Resume and the poll loop make a cheap masked
  `batches.get` (60s timeout, `X-Goog-FieldMask` on `name,done,metadata.state,metadata.batchStats,error`;
  if that mask is off, workers still do one full get each).
  Each finished job is downloaded once, in a worker, with a 300s per-read
  timeout. Three timeouts on the same job mark its keys failed and exit 1
  without re-voicing; rerun the same command to adopt it again. A WAV with no
  matching JSON audio row is treated as missing and is regenerated.
- **Phase 2.** Audio results stay inline in this build. Streaming a JSONL
  result file to disk is later work.

`cost.py` prices a call at the Batch rate when `call.json` has `"batch": true`.
Every row of a Batch job logs the job's whole elapsed time and its `batch_name`;
`cost.py` counts that time once per job (see Cost reports).

### When transcripts, audios and align skip

With `--translate` (and no `--updateboth`), the pipeline does not even start
these stages for a book (or `-s` section) when the real outputs are all on
disk. Without `--translate` the script runs and skips each finished item itself.
"All on disk" means:

- `transcripts`: every printed group has `14_taps/<md5>/01.txt`;
- `audios`: every queued segment has its `01.wav` (a leftover `.wav.tmp` does
  not count);
- `align`: every `01.wav` has its alignment JSON.

The progress line shows `N/M on disk`, naming the gap. One missing file runs
the stage, which then does only the missing items.

## Package and the book catalog

`package.py` matches the book to one row of `data/book_database.json` by its
identity (subject, grade, term, language code and country code from the
book XML; the cover picks between Applied / Pure). It then writes `15_package/`
(the served `<md5>.zip`, and unless `--no-output-backup` a dated archive of
the book output tree).

- One **ready** row matches: it is used as is (cover choice decides between
  Applied / Pure rows).
- A row exists but is not ready (for example no `subject_id`): the run stops
  with `has 0 rows`. Fill the row by hand.
- No row: the first real package of the book appends one row at the end of
  the file, then continues. Existing rows are never modified.
  - Key: `<Subject>_G<grade>_T<term>_<track>_<lang>_<country>`, the subject
    taken from the first `_` part of the book id (`mathematics` →
    `Mathematics`), in English for Arabic books too.
  - `subject` and `book_title`: from the XML `subject`, as in existing rows.
  - `subject_id`: looked up in `data/subjects.csv` by grade code and
    `subject_url_text` (for example `EG07`, `mathematics-ar`); the short
    country name comes from `frontmatter/wording.json` →
    `country_short_names_en`. A missing or ambiguous match stops the run
    before any service call; add the row by hand.
  - The four ids are minted from
    `https://12digit.nagwa.com/get.bulk.codes/4/cps/cps.system/`. A reply that
    is not 4 new ids, or reuses an id or a practice md5 already in the
    catalog, stops the run and writes nothing.
  - Part books, and a computed key that already exists with a different
    identity, are refused.
  - The row is validated, spliced into the file text (other rows keep their
    exact bytes), written atomically, then matched again.

Preview without any service call or write (no PDF, taps or zip either):

```bash
python3 -B scripts/package.py <book_id> --dry-run-catalog
```

It prints the existing row, or the row that would be added with `<new>` ids,
and exits 0; it exits 1 where a real run would stop.

## Existing output and updates

Most model-backed stages skip valid output already on disk. This makes interrupted
runs resumable.

For a normal one-language run:

```bash
python3 -B scripts/pipeline.py <book_id> --update
```

`--update` is forwarded only to stages that support rebuilding.

For an English-to-Arabic run, `--update` applies to the English side. To rebuild the
selected stages on both English and Arabic:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --translate --updateboth
```

Use updates carefully: model-backed stages cost tokens. Prefer a section-limited test
before a full-book update.

### Updating front matter only

To recreate only the files under `01_frontmatter` for one book, run the stage
directly:

```bash
python3 -B scripts/frontmatter.py mathematics_en_eg08_t1
```

or:

```bash
python3 -B scripts/frontmatter.py mathematics_ar_eg08_t1
```

The equivalent pipeline command for one book is:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --only frontmatter
```

To recreate the English and Arabic front-matter folders together:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --translate \
  --only frontmatter ar_frontmatter \
  --updateboth
```

`frontmatter.py` updates the standalone cover/title/copyright/contents files. It does
not replace the front-matter pages already embedded in the bound `<book_id>.pdf`.

To update front matter and rebuild the bound PDF, grid PDF, bbox PDF, and working JSON
without model calls, run:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --only frontmatter papers groups
```

For both English and Arabic:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --translate \
  --only frontmatter papers groups ar_frontmatter ar_papers ar_groups \
  --updateboth
```

This recompiles/typesets existing accepted papers but does not regenerate specs or
questions and does not call a model.

### What a stage range does to existing output

This command selects every English stage from `sources` through `groups`:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --from sources --to groups
```

It invokes each selected script, but it does **not** automatically recreate every
existing model-backed stage:

- `sources`, specs, review, labels, budgets, source questions, new questions, question
  review, and drawings reuse valid existing output unless their own update flag is
  requested or their output is missing/invalid;
- `extract`, `frontmatter`, `papers`, and `groups` are inexpensive deterministic stages
  that run again and rewrite their derived artifacts;
- the command stops at the first failing stage;
- no Arabic twin stages run because `--translate` is absent.

Use `--dry-run` to inspect the command range. Add `--update` only when you deliberately
want supported English stages to rebuild existing work:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --from sources --to groups --dry-run
```

## English-to-Arabic translation

### Requirements

Translation starts from an English book ID containing `_en_` and requires an Arabic
sibling whose ID contains `_ar_`:

```text
mathematics_en_eg08_t1
mathematics_ar_eg08_t1
```

Both XML files must exist and must contain the same leaf-section numbers in the same
order. The Arabic XML is authoritative for:

- Arabic titles and module paths;
- country/grade/term metadata;
- language direction;
- numeral style;
- Latin or Arabic mathematical notation.

### Full translation pipeline

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --translate
```

Through papers and working JSON only:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --from sources --to groups --translate
```

One section:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --translate -s 4.1
```

Run only the translation stage directly, when the English reviewed artifacts already
exist:

```bash
python3 -B scripts/translate.py mathematics_en_eg08_t1 \
  -s 4.1 \
  --model gemini-3.8-flash \
  --thinking-level medium \
  --workers 25
```

Translation mode runs:

1. The normal English production stages through `groups`.
2. Arabic `sources`.
3. The special `translate` stage.
4. Arabic `frontmatter`, `papers`, and `groups`.
5. English tail stages from transcripts through cost.
6. Arabic tail stages from transcripts through cost.

Already complete stages are skipped unless the selected update mode says otherwise.

### What `translate.py` translates

The translation stage reads the accepted/reviewed English artifacts. It does not
translate draft questions that English review rejected.

It creates the Arabic:

- section specifications;
- objective labels;
- exercise files;
- accepted exercise files;
- figure data and compiled figures;
- applied-book review file;
- translation state manifests.

Budgets, IDs, objective assignments, graph/grid/strip numbers, and other structural
data are copied deterministically rather than rewritten by the model.

### Translation model calls

The default translation configuration is:

```text
model: gemini-3.8-flash
thinking: medium
default call workers: 25
explicit translation maximum: 100
```

The CLI can override the worker default. Model-backed generation, review, and
translation stages default to 25 concurrent unary calls because that is fast
without creating the large quota bursts seen at 100. Transcripts, speech, new
questions and question review default to the Gemini Batch API instead; 25
concurrent unary calls apply there only with `--no-batch` (or
`--no-transcripts-batch` / `--no-tts-batch` on the pipeline). Translation
still accepts an explicit value up to 100:

```bash
python3 -B scripts/translate.py mathematics_en_eg08_t1 --workers 100
```

Higher concurrency does not reduce validation quality, but it can trigger more
`429 RESOURCE_EXHAUSTED` waits or `504 DEADLINE_EXCEEDED` retries. Use 25 for stable
production and clean cost measurement. Raise it only when the account quota has been
observed to sustain the higher request/token rate.

Translation makes these calls, in order. Each one (what is sent, returned,
checked, retried, cached) is described in
[TRANSLATION_WORKFLOW.txt](TRANSLATION_WORKFLOW.txt).

1. `terminology_extract` — per section of the whole book, with that section's
   Arabic textbook PDFs; reused while current.
2. `glossary_merge` — once per book; merges all sections into one glossary.
3. `spec_translate` — per section.
4. `label_translate` — per section.
5. `exercise_translate` — per objective (large objectives split).
6. `translation_review` — per objective, up to 3 rounds.
7. Correction — an `exercise_translate` call with the review issues.
8. `tikz_translate` — per figure that needs it.

Code never writes or inflects an Arabic word. Arabic wording comes only from
these calls; code checks structure, numbers, formulas and units.

Explicit context caches (`--no-cache` turns them off) hold only each task's
fixed instructions plus the book glossary. TTL is 60 minutes; the tool makes a
new cache after 55 minutes and never extends one; caches are deleted at the end
of the run (also on failure or Ctrl-C). `glossary_merge` is not cached.

### Translation validation and resume behavior

Every translated group is checked for:

- exact group/exercise IDs and order;
- exact option and figure counts;
- no missing required text;
- no untranslated English prose;
- supported scripts and printable Arabic marks;
- balanced LaTeX and mathematics delimiters;
- no bare commands such as `\frac` outside `$...$`;
- preserved numbers, formulas, commands, operators, and `[figure N]` markers.
- source-aware Latin mathematical labels (`ABCD`, `ax`, `ay`, etc.) rather than
  treating them as untranslated prose;
- semantically identical units and powers, stored with the book map's Arabic forms
  (`cm` → `سم`, while `cm` → `كجم` is rejected);
- every reader-visible Arabic-content digit inside math mode, excluding structural
  IDs and canonical `[figure N]` tokens.

Stage `08_section_new_questions` stores validated candidates (maths-aware
restatement, parabola vertex from an explicit Latin label, wrapped all-math
choices, interval checks on options and answers). Stage
`09_section_questions_review` is written only after the whole section passes
deterministic validation and mandatory semantic review.

`00_translation_state` stores fingerprints of the source, prompts, profile, model,
validated groups, review status, and figure status. An interrupted run reuses valid
completed groups and recalls only missing or invalid work.

Retrying is cost-bounded:

- each item has at most four model calls across all section retry passes;
- two identical semantic-validation failures open a circuit immediately;
- terminal failures are not called again by later section retry passes;
- prompt/validator changes revalidate and preserve usable candidates instead of
  deleting completed work;
- accepted sections that still pass current deterministic checks remain accepted.

**Resume.** A whole-book run without `--update`, `--rereview` or
`--retranslate-on-prompt-change` skips every section already accepted
(including `passed_with_issues`) with zero model calls: labels, groups, review
and figures stay as saved. Only missing or failed work goes back to the model.

Translation is considered complete only when all required translated files exist and
the matching `00_translation_state/<section>.json` says:

- the source/prompt/profile fingerprint is current;
- deterministic validation passed;
- mandatory semantic review passed;
- figures passed;
- candidate and accepted exercise fingerprints agree.

Do not delete or omit `00_translation_state` when copying a resumable Arabic output
tree. Numbered stage folders alone are not enough to prove that translation passed the
current validation rules. If the state file is missing, `pipeline.py --translate`
intentionally runs `translate` again.

Check whether a translation run will be skipped without doing work:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 \
  --from sources --to groups --translate -s 4.1 --dry-run
```

### Arabic paper fitting

Translated Arabic papers keep the same printed exercise membership as English, but
they are fitted independently:

- the set of printed exercise IDs is identical;
- Arabic groups may be reordered within objectives for better page use;
- Arabic question numbers therefore may differ from English question numbers;
- the resulting Arabic order is saved in the Arabic `.selection.json`;
- `groups.py` and downstream artifacts replay the Arabic-local order.

Use `papers.py --selection-from` only when exact membership and exact order are both
required. Translation mode uses membership-only fitting.

### Arabic typesetting rules (no model call)

Applied by `src/latex.py`, `src/arabic_math.py` and `arabic_maths.lua` when
papers are set:

- **Arabic notation letters.** Arabic letters in maths become font macros
  (`اب` → `\alef \beh`). A fatha on a single maths letter is a prime
  (`سَ` → `\seen '`). `نق` is `\arrad` (radius). `ق` before `\angle`,
  also as `\text{ق}`, prints `\dotlessqaft` (angle measure). A radian or
  degree mark written as `^{\text{د}}` / `^{\text{جـ}}` prints as the letter
  macro `^{\dal}` / `^{\jeemi}`.
- **Structure check.** When comparing a translation with its English source,
  an empty `^{}` / `_{}` counts the same as a bare `^` / `_`; a real
  structure difference still fails.
- **Brackets autosize.** `( )`, `[ ]`, `\{ \}` become `\left…\right`.
  Arabic reversed intervals with one comma (`]a, b[`, `[a, b[`, `]a, b]`) are
  sized too. Left alone: `\[ \]`, `\( \)`, plain `{ }`, optional arguments,
  already-sized pairs, brackets in `\text`, pairs split by `&` or `\\`, and
  unmatched mixes. Idempotent.
- **Latin letter lists** in Arabic prose (`H، M، A، T`) are kept in one
  left-to-right box; English prose is not boxed.
- **Western digits in RTL books** are set in STIX (`wrap_stix_digits`),
  except a digit run touching a Latin letter.
- **Layout.** Long division ("house") arrays, carry rows and Arabic-Indic cells
  follow the house style. The page fitter reserves room for the next block in
  strict order, and a page never ends on a section or objective heading alone.
- **Contents.** Hanging section numbers (`\labelnumber`, no trailing point),
  with room kept so an entry is not split from its children.

## Output layout

Output is written under:

```text
data/output/<book_id>/
```

Important paths include:

```text
00_api_call_logs/             one folder per model/API call
00_transcripts_batch_state.json  in-flight Gemini Batch jobs for transcripts
00_audios_batch_state.json    in-flight Gemini Batch jobs for speech
00_new_questions_batch_state.json  in-flight Gemini Batch jobs for new questions
00_questions_review_batch_state.json  in-flight Gemini Batch jobs for question review
00_transcripts_batch.lock     exclusive lock; one transcripts run per book
00_audios_batch.lock          exclusive lock; one audios run per book
00_new_questions_batch.lock   exclusive lock; one new_questions run per book
00_questions_review_batch.lock  exclusive lock; one questions_review run per book
00_translation_state/         translation fingerprints and acceptance state
01_frontmatter/               cover/title/copyright/contents artifacts
02_section_sources/           cropped section source PDFs
03_section_specs/             section specifications
04_book_review/               reviewed/applied book specification
05_section_labels/            objective headings
06_section_budgets/           exercise budgets
07_section_source_questions/  source exercise transcription
08_section_new_questions/     generated or translated candidates
09_section_questions_review/  accepted exercises
10_book_figure_data/          normalized figure requests
11_book_figures/              compiled TikZ figure records
12_section_papers/            section TeX/PDF/bbox/selection files
13_question_jsons/            individual question payloads
14_taps/                      transcripts, audio, and alignment data
15_package/                   delivery package
<book_id>.pdf                 bound book
<book_id>_grid.pdf            layout-grid book
<book_id>_bboxes.pdf          question-bbox inspection book
<book_id>.json                working book JSON
cost_report.html              API token and price report (plus a one-pass page)
translation_report*.json/.md  Arabic book only: per-run translation report
```

Do not manually edit generated output as a permanent fix. Correct the responsible
script/prompt/validator and rebuild the affected stage.

## API-call logs

Every model call is written under `00_api_call_logs`. A call folder contains:

- `call.json`: model, timing, status, token usage, stage, section, attempt, and `batch` when the call went through the Gemini Batch API;
- `request.json`: ordered inventory of everything sent;
- `contents_01_prompt.txt`: exact assembled prompt;
- later `contents_*` files: exact text/PDF/inline content parts;
- split inspection files such as `english_group.json`;
- `response.json` and `response_text.txt`;
- validation errors for rejected attempts.

`contents_*` represents the actual request order. Named split files are inspection
copies, not additional billed inputs.

## Cost reports

Run:

```bash
python3 -B scripts/cost.py <book_id>
```

Rates are in `scripts/prices.json`. Pricing is:

```text
uncached input × input rate
+ cached input × cached-input rate
+ (output + thinking) × output rate
+ explicit cache token-hours × storage rate
```

**Time.** The report shows two times separately:

- **Sequential wall clock** — summed per call, interactive (unary) calls only.
- **Batch job time (queue + processing, jobs run in parallel)** — counted once
  per `batch_name` (the longest row if rows differ). An adopted job logs
  `0.0`, treated as unknown.

The whole-book projection scales interactive steps by mean call time × items,
and Batch steps by job time ÷ sections covered.

`--step STEP` limits the report to one step; `-o` writes the HTML elsewhere.

Rates are selected using the call timestamp and effective date. A report is marked
verified only when every model used in that report has verified model-level source,
date, and rates. Unknown models fail the report instead of being priced as zero.

The configured Gemini 3.6 Flash and Gemini 3.8 Flash Standard paid-tier rates
are verified from:

```text
https://ai.google.dev/gemini-api/docs/pricing
```

The price table records the exact model name, currency, Standard service tier,
effective start/end dates, input rate, output/thinking rate, cached-input rate,
cache-storage rate, official source URL, and checked date. A report is unverified
when any model appearing in its logs lacks any of that verified metadata.

Measured translation-only examples at the current 2026 Standard rates:

- a completed mixed Grade 8 mathematics translation measured about USD 15.76
  for 17 sections (about USD 0.93 per section);
- a more algebra/figure-heavy Grade 10 sample measured about USD 1.50 per section;
- a 17-section book is therefore commonly around USD 16–26 for EN-to-AR translation.

These are planning estimates, not fixed quotes. Section length, exercise-group count,
figure/TikZ work, semantic-review findings, and retries change the total. English book
generation, speech synthesis, and audio alignment are separate costs. Published
Gemini 3.6/3.8 Flash rates double from 2027-01-01 unless Google changes the schedule.

For comparison, one measured 17-section English-generation run using Gemini 3.6
Flash cost USD 31.04 including development retries; its one-pass projection was
USD 28.20. This is separate from the Arabic translation estimate above.

## Prompts

All model instructions are under:

```text
prompts/<stage>/
```

Prompt assembly selects rules from the book profile:

```text
language/<code>.md
numerals/<style>.md
notation/<style>.md
```

Editing a prompt changes future model calls. Every call log records the exact prompt
files used.

Prompt assembly can be checked without calling a model:

```bash
python3 -B scripts/src/prompting.py
```

## Useful verification commands

Run the regression suite:

```bash
python3 -B -m unittest discover -s scripts/tests -p 'test_*.py'
```

Rebuild papers without model calls:

```bash
python3 -B scripts/papers.py <book_id>
```

A full book is numbered and bound only when every accepted section has a paper.
`--section-only` writes that section's artifacts and does not touch the bound PDF.

Arabic papers report leftover Latin/English etymology words to
`english_gloss_report.json` by default (`--report-english-glosses`; pass
`--no-report-english-glosses` to fail the section instead).

Rebuild an Arabic paper using English membership but Arabic-native fitting:

```bash
python3 -B scripts/papers.py mathematics_ar_eg08_t1 \
  -s 4.1 \
  --membership-from data/output/mathematics_en_eg08_t1/12_section_papers
```

Rebuild working JSON and bbox overlays:

```bash
python3 -B scripts/groups.py <book_id>
```

## Troubleshooting

### A pipeline stage fails

Read the final error, correct the underlying source/code/prompt issue, then resume:

```bash
python3 -B scripts/pipeline.py <book_id> --from <failed_stage>
```

### A model call fails validation

Inspect its `00_api_call_logs` folder. The next attempt receives the validation reason.
Valid completed groups remain checkpointed.

### A paper does not compile

Inspect:

```text
12_section_papers/<section>.tex
12_section_papers/<section>.log
```

The paper stage also rejects missing glyphs, untranslated target prose, malformed
mathematics, unresolved figure markers, invalid bbox data, and selection-lock drift.

### A figure is missing

Run/re-run `extract` and `draw`, or inspect the translated figure record and its call
logs. Do not use `--allow-missing-figures` for final output.

### A cost report is missing

`cost.py` requires `00_api_call_logs`. If logs were removed, the original detailed
report cannot be reconstructed honestly from output PDFs alone.

## Typical commands

```bash
# Re-typeset the Arabic book from saved translations, no model calls
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --translate --from ar_frontmatter --to ar_groups

# Voice only what is missing (never add --update here)
python3 -B scripts/audios.py mathematics_ar_eg08_t1

# Resume a stopped translation
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from translate --translate

# Preview a package catalog row
python3 -B scripts/package.py mathematics_ar_eg08_t1 --dry-run-catalog
```

## Safety

- Never pass `--update`, `--updateboth`, `--rereview` or
  `--retranslate-on-prompt-change` unless you mean to pay to regenerate work
  already on disk.
- Never `--discard-batch-state` while a Batch job is running; it does not
  cancel the remote job and a resubmit bills twice.
- The first real `package` of a book with no catalog row calls the 12digit
  service and appends to `data/book_database.json`; use `--dry-run-catalog`
  first.

## Working rule

Test risky changes on one or two sections first. Validate JSON, figures, TeX, PDFs,
selection membership, page fitting, and cost logs before starting a full-book
model-backed rebuild.
