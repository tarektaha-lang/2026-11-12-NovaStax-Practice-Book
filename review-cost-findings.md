# Questions review: findings, agree/disagree, fixes

**Scope:** `section_question_review` only. Pipeline not changed. Quality must not drop.

---

## The cost problem (agreed)

Review is **$1,588 of $3,837** (41%). Thinking is the bill (~94% of paid tokens). Output is ~12–25 tokens. Unary prompt cache already hits ~96%. HTML `wasted ≈ $0` hides paid re-reviews.

One call per exercise, `thinking=high`, historical runs were **unary** (`batch: None`). Script default is now Batch; pipeline does not override it.

---

## Agree with Claude

| Topic | Why |
|---|---|
| Thinking is the bill; unary cache is already done | My G12 sample: 97% of $/call is thinking+output. Cache 96%. |
| G9 5.82× mixed free errors with paid work | My own sample was ~66% `status=error`. Cost table: 55.2M input / 14,467 successes ≈ 3,814, matches ~3.8k/call. Errors are **call-count**, not dollars. Paid multiple is **1.95×**. My “$29 one pass” was wrong (mean included $0 errors). Use **~$86 unary / ~$52 Batch**. |
| Re-runs + 429 storms are one chain | Free errors leave the section file unwritten → next run re-pays every exercise. G9: $83.77 of $168.50 is duplicate paid work. |
| Batch is **−34% to −42%**, not −50% | Unary already caches input. Batch halves thinking but **loses** that cache unless Batch accepts `cached_content`. Math books closer to −30%. Fleet ~**$950–1,050**, not $800. |
| Log reuse must key on **digest**, not exercise id | `load_logged_exercise_checks` matches `(section, id)` only. A rewritten exercise would keep the old verdict. `review_job_digest(prompt, part)` already exists. |
| Pass-1 skip saves ~nothing | `new_questions` already drops odd `$`, unbalanced braces, runaway text. Drop this fix. |
| Per-set call is small | I overstated “4–8× fewer calls”. Sets are 13–40% of items; shared stem is ~100–230 chars. After Batch, ~**4–8%**. Last, A/B only. |
| “2–4× less thinking” is unmeasured | English-math median thinking ~654 tokens. Do not claim 2–4×. A/B only, after Batch. |
| Spares-on-demand is a real lever (~−14%) | `EXTRA_EXERCISES = 8` → 27–30% of reviewed items are spares. Math: spares exceed rejects 12–17×. I named extras as coupling; Claude measured the skip. |
| Arabic mark-placement false alarms are a real quality bug | Guard pattern already exists (`is_answer_format_issue`). `repair_mark_placement` already forbids alif-then-tanween in stored text, so those model claims cannot be right. |
| Test Batch + `cached_content` | That is the gap between −34% and −50%. My “don’t chase cache” applied to **unary only**. |
| 13-book stack −55% / −67% as a **sample** order of magnitude | Direction is right. Not a 49-book promise. |

---

## Do not agree (or only with a gate)

**1. “429 storms are not a cost problem.”**  
Disagree as stated. The error calls themselves are $0. They **cause** the paid duplicates (G9 $84). Batch still matters for dollars because it breaks that chain, not because 429s are billed.

**2. Spares-on-demand as a clean −14% with no layout risk.**  
Agree on the call math. Do not ship it until: (a) unreviewed spares never reach accepted JSON, (b) last-page fill is checked. `papers.py` already prints over budget from the pool (`added over budget to fill pages from the pool`). If you review fewer spares, papers can go sparse. Flag rate ≠ reject rate until proven.

**3. Arabic mechanical = −6% cost, then strip the prompt bullet.**  
Agree: **drop findings that quote text already correctly marked** (quality goes up).  
Do not: treat 71–97% “only mechanical” as all false, or remove `language/ar.md:7` until the checker is proven on model-clean items. `كلا` / `كل` were **not** verified. A too-wide guard would keep bad marks in the book.

**4. Stacked −55%/−67% as the fleet target.**  
13 books, pre-Batch, Arabic fixes only apply to Arabic-prose (44% of that sample). Fleet mix is more math. Use the stack as **ordering**, not a committed $ number.

**5. Per-set is worthless except as last A/B.**  
Agree it is last for **dollars**. Weak disagree: shared-figure thinking still happens N times, which is a quality/consistency reason to A/B later, not a reason to build it now.

**6. Digest reuse “only after a Batch production run.”**  
Disagree on order. Digest reuse stops G9-style $84 duplicates on **unary or Batch**. It can land in parallel with the Batch proof run. Claude’s quality rule (same digest ⇒ same verdict) is the right gate; waiting for Batch is not.

---

## Fixes to do (quality-safe first)

Do these. Do not touch `thinking=high` or `core.md` issue types until 1–4 are in.

| # | Fix | Agree? | Save | Quality gate |
|---|---|---|---|---|
| 1 | **Prove Batch.** One real review run. `call.json` must show `"batch": true`. `cost.py` must use `batch_periods`. | Yes | −30% to −42% on new runs | None. Same model, same thinking. |
| 2 | **Test Batch + cached prompt** on ~200 requests. Read `cached_content_token_count`. If it works, keep it. | Yes | Extra ~15 points (34% → ~50%) | None if the API accepts it. If it 400s, drop and keep resent-prompt Batch. |
| 3 | **Digest-keyed skip.** Reuse a success only when `review_job_digest(prompt, part)` matches. Keep `00_api_call_logs` in every zip. Do not key on exercise id. | Yes | G9: $168 → ~$86 unary / ~$52 Batch. 13-book ~−11% | Same digest ⇒ same verdict. |
| 4 | **Arabic false-alarm guard** (like `is_answer_format_issue`): drop a mark-placement finding if the quoted word already has the marks in the correct order. Then extend `repair_mark_placement` whitelist (top words + nisba-adverb pattern). Do **not** remove the prompt bullet yet. | Yes, this shape | ~−6% of 13-book $ **and** fewer good items wrongly dropped | Replay logged mechanical findings. Checker on model-clean items: near-zero hits; eyeball any hits. |
| 5 | **Spares on demand.** Review budgeted items first. Review only enough spares to replace rejects. | Yes, with gates | ~−14% after dedupe (0% on high-flag Arabic books) | No unreviewed spare in accepted output. Compare last-page fill vs today. |
| 6 | **Thinking medium / two-pass** | Later only | Unmeasured | Replay 5,271 flagged `wrong_answer` / `impossible` / `options` / `figure` calls. Keep a level only if recall holds. |
| 7 | **Per-set call** | Last | ~4–8% after Batch | A/B one Arabic + one geometry section. Incomplete reply ⇒ discard whole call. |

**Dropped:** Pass-1 skip. Shortening `core.md`. Unary cache work. Using HTML wasted=0 or the “whole book” mean as a target. Matching reuse by exercise id.

---

## Do this week

1. One Batch review on one section. Confirm `batch: true` and Batch rates in the cost report. (~1–2 h of run time, not coding if default already Batch.)
2. Same run: attach `cached_content` on ~200 Batch requests. Record whether usage shows cache hits.
3. Land digest-keyed reuse (code change). Parallel with 1–2.

Then 4 (Arabic guard), then 5 (spares), then 6–7.

---

## Tarek’s answers (2026-11-12)

1. **Batch proof.** Copy of a finished book, not production. About 150 exercises from a geometry-heavy `mathematics_en_in10` section and a high-flag `arabic_ar_om10_t1` section. Check `batch: true`, Batch rates, cached-prompt test, agreement with old unary verdicts. Cost ~$0.40 + ~$1.10.
2. **Spares-on-demand.** Yes, math books first. Review by group. Unreviewed spares stay out of accepted JSON. Compare paper selection (printed, budget, added, last-page fill) before vs after on one book.
3. **Arabic.** Guard now (quality only goes up). Pre-review repair behind the replay check. Prompt bullet stays. `كلا` / `كل` not treated as verified false alarms.
4. **`thinking=high` frozen until fixes 1–5 are in.** Yes. Read-only thinking replay may be prepared in parallel but **must not call the API (~$3–6) without a separate go-ahead.**
