# How to run a book

Short CLI guide. The full description of the tool is in [README.md](README.md).

From the repo root, with `GOOGLE_API_KEY` or `GEMINI_API_KEY` set:

```bash
python3 -m pip install -r requirements.txt
```

Replace `mathematics_en_eg08_t1` with your English book id. The Arabic sibling is
the same id with `_ar_` instead of `_en_` (`mathematics_ar_eg08_t1`). Both XML
folders must already exist under `data/input/`.

See the commands first without running them by adding `--dry-run`.

---

## 1. Generate one book

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1
```

One section only:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 -s 1.1
```

## 2. Generate a book and its Arabic translation

Always pass the **English** id. `--translate` builds English, then the Arabic twin.

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --translate
```

One section on both books:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --translate -s 1.1
```

## 3. Translate an existing English book

If the English book is already on disk, the same command is enough. Finished English
stages are skipped; Arabic sources, `translate`, Arabic papers, and Arabic audio/package
still run.

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --translate
```

To start at the Arabic side and not even invoke the English stages:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from ar_sources --translate
```

If Arabic source PDFs are already split:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from translate --translate
```

That is also the command that **resumes** a translation that stopped partway
through. An accepted section is skipped entirely: labels, groups, review, and
figures stay as saved and make no model calls. Only missing or failed work goes
back to the model. Do not add `--update`, `--rereview`, `--updateboth`, or
`--retranslate-on-prompt-change`: those rebuild work you already have. To stop
once the Arabic translation is done, before papers and audio:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from translate --to translate --translate
```

### Re-check logged failures without calling the model

`scripts/replay_translation_failures.py` only reads files. It writes nothing and
makes no model calls.

After a run that ended with exercise-translation failures, see which logged
replies the checker would now accept (pass the **Arabic** id):

```bash
python3 -B scripts/replay_translation_failures.py --book mathematics_ar_eg08_t1 --since 2026-10-18
python3 -B scripts/replay_translation_failures.py --book mathematics_ar_eg07_t1 --book-dir data/output/mathematics_ar_eg07_t1.backup-20261021-123318 --since 2026-10-15 --v2
```

`now passes` is a reply the code used to reject wrongly. `still fails` is either
a real model error or a rejection still to look at. Add `--section 4.2` for one
section, or `--verbose` for every attempt.

Before resuming after any change to the translation checker, confirm that groups
already on disk would still be kept:

```bash
python3 -B scripts/replay_translation_failures.py --book mathematics_ar_eg08_t1 --kept
```

It should end with `0 would be retranslated`. If it lists rejected groups,
resuming would send those back to the model.

`scripts/replay_new_questions_checks.py` is the same idea for `new_questions`
error logs (read-only; `--all`, `--accepted`, `--parabolas`, `--regen-list`).
`scripts/regen_prepare.py` moves a 07/08 file aside and, for named objectives,
writes a `.partial` sidecar of the kept work. Dry-run by default; `--apply` to
act. Then run `pipeline.py -s … --only …` **without** `--update`. Do not
implement group-level salvage of a half-good objective reply.

```bash
python3 -B scripts/replay_new_questions_checks.py --all --accepted --parabolas --regen-list
python3 scripts/regen_prepare.py pure_mathematics_en_eg12_t0 02_02_01 --objective objective_4
```

---

## `--from` and `--to`

Both names are inclusive. Stages run in the order listed below, never backwards.

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from papers
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from groups --to audios
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from papers --to groups --translate
```

With `--translate`, `--to groups` means through **Arabic** groups (`ar_groups`), and
`--to cost` means through **Arabic** cost (`ar_cost`).

Resume after a failure from the stage the log names:

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from papers -s 1.1
```

Print the names this run will use:

```bash
python3 -B scripts/pipeline.py --list
python3 -B scripts/pipeline.py --list --translate
```

### Stage names without `--translate`

Use these with `--from` / `--to` on a single-language run.

| Name | What it does |
| --- | --- |
| `sources` | Validate the XML and split the source PDFs |
| `specs` | Write one lesson spec per section |
| `review` | Review the assembled specs as one book |
| `labels` | Name each objective for the paper headings |
| `budgets` | Share each section's exercise budget among its objectives |
| `source_questions` | Transcribe questions already in the source PDFs. After MAX_TOKENS the stage splits per PDF at the same thinking level; medium is a last resort |
| `new_questions` | Write new practice exercises. Retries send the last rule-check failure back as `feedback.txt` |
| `questions_review` | Check the new exercises and drop the wrong ones |
| `extract` | File every figure description for drawing |
| `draw` | Draw each figure as TikZ and compile it |
| `frontmatter` | Set the cover, title, copyright and contents pages |
| `papers` | Typeset the section papers and bind the book |
| `groups` | Write the working JSON and the bbox overlay PDF |
| `transcripts` | Write a spoken transcript for each printed group |
| `audios` | Synthesise speech for each transcript |
| `align` | Align each WAV with its spoken transcript |
| `package` | Assemble the practice package |
| `cost` | Price the book's API call logs |

`review`, `extract`, `draw`, `frontmatter`, and `cost` are whole-book. `-s` is ignored
there. A first-section run of a book that has no review yet will stop at `review`
until every section has a spec.

### Extra stage names with `--translate`

The English names above still work. These are added, in this order, when `--translate`
is set. `--from` / `--to` must use a name from this longer list.

| Name | What it does |
| --- | --- |
| `ar_sources` | Split the Arabic sibling's source PDFs |
| `translate` | Translate EN specs, labels and exercises into the Arabic sibling |
| `ar_frontmatter` | Arabic cover, title, copyright and contents |
| `ar_papers` | Typeset the Arabic papers |
| `ar_groups` | Arabic working JSON and bbox overlay |
| `ar_transcripts` | Arabic spoken transcripts |
| `ar_audios` | Arabic speech |
| `ar_align` | Arabic alignment |
| `ar_package` | Arabic practice package |
| `ar_cost` | Price the Arabic book's API call logs |

Order with `--translate`: English `sources` → `groups`, then `ar_sources` →
`ar_groups`, then English `transcripts` → `cost`, then `ar_transcripts` → `ar_cost`.

---

## `--strip-figures`

This is not a pipeline flag. After `questions_review` has accepted exercises, drop
every accepted exercise that needs a drawn figure:

```bash
python3 -B scripts/questions_review.py mathematics_en_eg08_t1 --strip-figures
```

One section:

```bash
python3 -B scripts/questions_review.py mathematics_en_eg08_t1 -s 1.1 --strip-figures
```

Then continue the book from `extract` (figures, papers, and everything after):

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from extract
```

---

## Useful extras

Map merge may ask once, in the terminal, whether two Arabic spellings of the same
unit (for example دينار / دنانير, or سم / سنتيمتر) are the same unit. The answer is
stored next to that book's map as `unit_equivalences.json` and reused. Unattended
runs write `unit_equivalences.pending.json` instead and stop; re-run `--translate`
in a terminal to answer. Suffix plurals such as جنيه / جنيهات stay automatic.
Exercise translation does not prompt.

| Flag | Use |
| --- | --- |
| `--dry-run` | Print the stage commands and exit |
| `--only papers groups` | Run only those stages, still in pipeline order |
| `--skip audios align package` | Drop those stages from the run |
| `--no-output-backup` | Forward to `package` and `ar_package`: write the served zip, skip the dated book-output archive |
| `--update` | Rebuild English stages that would otherwise skip |
| `--updateboth` | With `--translate`, rebuild the selected range on both books |
| `--transcripts-batch` / `--no-transcripts-batch` | Forward `--batch` / `--no-batch` to transcripts and ar_transcripts (omitted, those scripts keep Batch) |
| `--tts-batch` / `--no-tts-batch` | Forward `--batch` / `--no-batch` to audios (`--tts-batch` is the pipeline default) |
| `--batch-parallel N` | `0 = no cap (default); N = at most N jobs in flight`; forwarded to transcripts, audios and their ar_ copies |
| `--download-workers N` | Parallel downloads of finished batch jobs (default 3 on the stage scripts; `1` = serial). Each audio worker can peak at ~1–1.5 GB; use 2 or 1 when several books run. Forwarded to transcripts, audios and their ar_ copies. |
| `--model`, `--thinking-level`, `--workers` | Forwarded only to stages that take them; omitted, each stage keeps its default |
| `--tts-model` | Speech model for audios (`--model` does not reach audios) |
| `--list` | Print the stage order and exit (`--list --translate` for the long list) |

`transcripts.py` and `audios.py` default to the Gemini Batch API: every chunk of
a round is submitted at once (up to 24h, billed at the Batch rate). `--no-batch`
is unary `generateContent` with 25 workers. `--unary-fallback N` on
`transcripts.py` (default 0) sends at most N remaining items unary at the current
attempt instead of the last Batch round. `429 RESOURCE_EXHAUSTED` at upload or
create re-queues the chunk without spending an attempt. Polling uses a cheap
masked status call (60s); each finished job is downloaded once (300s per-read
timeout, 3 timeouts → those keys fail and the CLI exits 1; rerun adopts again).
A WAV with no JSON audio row is regenerated. Audio stays inline; JSONL output
is phase 2.

State files `data/output/<book>/00_transcripts_batch_state.json` and
`00_audios_batch_state.json` hold a `jobs` list (a legacy single `job` is still
read). A rerun adopts live saved jobs and submits leftover chunks in the same
round. `--discard-batch-state` deletes only that local JSON; it does not cancel
the remote job, so discarding a running job and resubmitting bills twice.
`00_transcripts_batch.lock` and `00_audios_batch.lock` allow one run per book at
a time. `--no-batch` still polls and applies every saved in-flight job before
going unary.

A failed job, or one that stopped while being applied, resends only the keys
still missing on disk; written rows are never paid for twice. 30 poll errors in
a row on one job stop the run and keep the state file: rerun the same command
to resume. There is no time limit.

---

## Transcripts, audio and alignment: what gets skipped

With `--translate`, the pipeline skips `transcripts`, `audios` and `align` (and
their `ar_` copies) only when every real output is on disk (`01.txt`, `01.wav`,
alignment JSON per tap). The log shows `N/M on disk`. Otherwise, and on any
single-language run, the stage runs and does only the missing items.

Voice only what is missing, never add `--update` here (it pays for all audio again):

```bash
python3 -B scripts/audios.py mathematics_ar_eg08_t1
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --translate --from ar_audios --to ar_align
```

---

## Retypeset the Arabic book without model calls

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --translate --from ar_frontmatter --to ar_groups
```

Add `-s 1.1` for one section.

---

## Package and the catalog

`package` matches the book to one row of `data/book_database.json`. If the book
has no row, the first real run appends one (four ids from the 12digit service);
existing rows are never changed. A row that exists but is not ready stops the
run with `has 0 rows`; fill it in by hand. `--no-output-backup` still writes
the served `15_package/<md5>.zip` and skips the dated archive of
`data/output/<book_id>/`. Preview first:

```bash
python3 -B scripts/package.py mathematics_ar_eg08_t1 --dry-run-catalog
```

It prints the existing or would-be row and writes nothing.

---

## Cost report

```bash
python3 -B scripts/cost.py mathematics_en_eg08_t1
python3 -B scripts/cost.py mathematics_en_eg08_t1 --step section_spec
```

No model or network call. Writes `data/output/<book>/cost_report.html` (`-o` to
change). Time is shown twice: **Sequential wall clock** (interactive calls) and
**Batch job time** (once per Batch job, jobs run in parallel).

---

## Safety

- `--update`, `--updateboth`, `--rereview`, `--retranslate-on-prompt-change`
  regenerate paid work. Use them only when you mean to.
- Do not `--discard-batch-state` while a Batch job is running.
- Ctrl-C is safe during Batch: the remote job keeps going; rerun the same command.
- Add `--dry-run` to see the commands before a long run.
- All flags: `python3 -B scripts/<stage>.py --help` (no API call). Full list in
  [README.md](README.md).
