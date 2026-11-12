# Commands

Copy-paste from repo root. Swap `mathematics_en_eg08_t1` for your English book id
(`_en_` → `_ar_` for the Arabic sibling). Needs `GOOGLE_API_KEY` or `GEMINI_API_KEY`.

See the command first: add `--dry-run`. Full flags: `python3 -B scripts/<stage>.py --help`.
Narrative: [USAGE.md](USAGE.md).

## Setup

```bash
python3 -m pip install -r requirements.txt
export GOOGLE_API_KEY="..."
```

## Run a book

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 -s 1.1
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --translate
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --translate -s 1.1
python3 -B scripts/pipeline.py --list --translate
```

## Slice / resume

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from papers
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from groups --to audios
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --only frontmatter papers groups
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --skip audios align package
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --dry-run
```

`--from` / `--to` are inclusive. With `--translate`, `--to groups` means through
Arabic groups (`ar_groups`). Resume from the stage the log names.

## Translate an existing English book

```bash
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from ar_sources --translate
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from translate --translate
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from translate --to translate --translate
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --translate --from ar_frontmatter --to ar_groups
python3 -B scripts/translate.py mathematics_en_eg08_t1 -s 4.1 --workers 25
```

Do not add `--update`, `--rereview`, `--updateboth`, or `--retranslate-on-prompt-change`
on resume. Those rebuild paid work.

## Typeset / package (no model)

```bash
python3 -B scripts/frontmatter.py mathematics_en_eg08_t1
python3 -B scripts/papers.py mathematics_en_eg08_t1
python3 -B scripts/papers.py mathematics_ar_eg08_t1 -s 4.1 --section-only --membership-from data/output/mathematics_en_eg08_t1/12_section_papers
python3 -B scripts/groups.py mathematics_en_eg08_t1
python3 -B scripts/package.py mathematics_ar_eg08_t1 --dry-run-catalog
```

Drawings already on disk, compile only:

```bash
python3 -B scripts/draw.py mathematics_en_eg08_t1 --rebuild
python3 -B scripts/draw.py mathematics_en_eg08_t1 --mend
```

## Speech / transcripts

```bash
python3 -B scripts/audios.py mathematics_ar_eg08_t1
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --translate --from ar_audios --to ar_align
python3 -B scripts/transcripts.py mathematics_en_eg07_t1 -s 1.2 --no-batch
python3 -B scripts/pipeline.py mathematics_en_eg07_t1 --from transcripts --batch-parallel 2
python3 -B scripts/audios.py mathematics_en_eg07_t1 --download-workers 2
```

Never add `--update` here. Ctrl-C is safe during Batch; rerun the same command.
Do not `--discard-batch-state` while a remote job is running.

## Cost / metrics (read-only)

```bash
python3 -B scripts/cost.py mathematics_en_eg08_t1
python3 -B scripts/cost.py mathematics_en_eg08_t1 --step section_spec
python3 -B scripts/translation_metrics.py data/output/mathematics_ar_eg08_t1
python3 -B scripts/translation_metrics.py data/output/mathematics_ar_eg08_t1 --section 1.2 --json
```

## Replay failures (read-only, no model)

```bash
python3 -B scripts/replay_translation_failures.py --book mathematics_ar_eg08_t1 --since 2026-10-18
python3 -B scripts/replay_translation_failures.py --book mathematics_ar_eg08_t1 --kept
python3 -B scripts/replay_new_questions_checks.py --all --accepted --parabolas --regen-list
python3 -B scripts/src/prompting.py
```

`--kept` should end with `0 would be retranslated`.

## Repair on disk

```bash
python3 -B scripts/questions_review.py mathematics_en_eg08_t1 --strip-figures
python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from extract
python3 scripts/regen_prepare.py pure_mathematics_en_eg12_t0 02_02_01 --objective objective_4
python3 scripts/regen_prepare.py pure_mathematics_en_eg12_t0 02_02_01 --objective objective_4 --apply
python3 scripts/merge_question_sets.py science_and_technology_education_ar_dz05 --dry-run
```

`regen_prepare.py` is dry-run until `--apply`. Then run `pipeline.py -s … --only …`
**without** `--update`.

## Tests

```bash
python3 -B -m unittest discover -s scripts/tests -p 'test_*.py'
```

## Paid-work flags

Do not pass unless you mean to regenerate work already on disk:

- `--update` / `--updateboth`
- `--rereview` / `--retranslate-on-prompt-change`
- `--discard-batch-state` (does not cancel the remote Batch job)
