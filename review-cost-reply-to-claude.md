# Reply to Claude — questions review cost (for internal discussion)

**From:** Tarek / pipeline side  
**To:** Claude  
**Date:** 2026-11-12  
**Scope:** `section_question_review` only. No production pipeline change in this note. `thinking=high` stays frozen until the first five fixes are in.

This is the agreed picture after your corrections, plus decisions to take to a cost review.

---

## 1. Bottom line

Review is the largest step: **about $1,588 of $3,837** across the book set (41%).

Almost all of that money is **thinking tokens**, billed at the output rate. The JSON the model returns is tiny. The unary prompt cache already works (~96% of input). Chasing more unary cache will not move the bill.

We overstated two things in the first note. You were right on both:

| First note | Corrected |
|---|---|
| Batch saves ~50% | **~34–40%** unless Batch can use a cached prompt (~50% if that test works) |
| G9 was 5.82× paid work | **1.95× paid.** Extra calls were almost all **$0** errors |

**Realistic target if we do the quality-safe fixes (Batch, digest reuse, Arabic false-alarm guard, math spares-on-demand):** on the 13-book sample, about **−55%** with Batch and the prompt resent, about **−67%** if Batch accepts a cached prompt. That is a sample, not a promise for every book.

We will **not** drop `thinking=high` to chase a bigger cut until those fixes are in and measured.

---

## 2. What we accept from your report

- Thinking+output is ~94% of paid review spend; output alone ~2%.
- Unary cache is done; Batch currently sends the full prompt with no `cached_content`.
- HTML `wasted ≈ $0` hides **paid re-reviews**, not free retries.
- Log reuse must key on a **content digest** (`prompt + exercise JSON`), not on exercise id. Reusing by id would stamp an old verdict on a rewritten question.
- Skipping Pass-1 rejects at review time saves ~nothing.
- Per-set calls are a **small** dollar lever (about 4–8% after Batch), not 4–8× fewer calls. Last, A/B only.
- “2–4× less thinking” is unmeasured. English-math median thinking is already ~654 tokens. A/B only, later.
- Spares (`EXTRA_EXERCISES = 8`) are 27–30% of reviewed items. Reviewing them only when needed is a real save (~14% on the sample), with a paper-fill gate.
- Many Arabic `wording` flags are mechanical mark-placement, and a large share of “tanween on alif” claims quote text that is **already correct**. That is a quality bug as well as a cost leak. Do **not** treat `كلا` / `كل` as verified false alarms.

---

## 3. One correction we still hold

**429 / cache-expiry storms are not “free” in the business sense.**

The failed calls themselves billed **$0**. A sample of the G9 book showed **268 cache-stale 403s in 404 sampled calls, all unbilled.** Success thinking on that sample averaged **~2,962 tokens**.

Those free failures still **cause** paid work: a section review file is written only when every exercise has a result, so the next run re-pays the whole section. In G9, **$83.77 of $168.50 (50%)** is duplicate paid review. Batch and digest reuse both matter because they break that chain, not because 403s appear on the invoice.

---

## 4. Decisions (already made)

1. **Batch proof run** on a **copy** of a finished book, never the production folder. About 150 exercises from:
   - a geometry-heavy `mathematics_en_in10` section (~$0.40 at Batch rates)
   - a high-flag `arabic_ar_om10_t1` section (~$1.10)
   
   Checks: `batch: true` in the log, Batch rates in the cost report, cached-prompt test in the same session, agreement with the old unary verdicts. Exact section names can be confirmed before the run.

2. **Spares-on-demand: yes, math books first.** Review by group. Unreviewed spares must not enter the accepted JSON. Compare paper selection before vs after on one book: printed, budget, added, last-page fill.

3. **Arabic: yes to the output guard now** (drop a mark-placement finding only when the quoted word already has the marks in the correct order — quality only improves). Yes to extending the pre-review repair, behind a replay check. **Keep the prompt bullet.** Do not special-case `كلا` / `كل`.

4. **`thinking=high` frozen until fixes 1–5 are in.** A read-only replay of thinking levels can be prepared in parallel but **must not spend the ~$3–6 of API calls without a separate go-ahead.**

---

## 5. Work order we will follow

| # | Work | Why | Quality gate |
|---|---|---|---|
| 1 | One real Batch review on the copies above | Proves half-price thinking | Same model, same `thinking=high` |
| 2 | Cached prompt on ~200 Batch requests | Gap between −34% and −50% | If the API refuses cache, keep Batch without it |
| 3 | Digest-keyed skip; keep `00_api_call_logs` with every book | Stops G9-style $84 re-pays | Same digest ⇒ same verdict |
| 4 | Arabic false-alarm guard, then whitelist repair | Quality up, some cost down | Replay logged mechanical findings; checker on model-clean items should be near-zero |
| 5 | Spares-on-demand (math first) | ~−14% of review calls | No unreviewed spare in accepted output; last-page fill compared |
| 6 | Thinking medium / two-pass | Unmeasured | Replay previously flagged `wrong_answer` / `impossible` / `options` / `figure` only after 1–5 |
| 7 | Per-set call | Small $ | A/B only |

**Not doing:** shortening the examiner prompt; stripping issue types; switching to a cheaper model; using HTML “wasted = 0” as a KPI; matching log reuse by exercise id.

---

## 6. Money picture for a discussion

**Today (historical, mostly unary):** review ≈ $1,588 of $3,837.

**After quality-safe fixes, new books:**

- Batch, prompt resent: roughly **$950–1,050** fleet-scale if mix looks like today (−34–40% on review).
- Batch + cached prompt (if the test works): closer to **half** of today’s review.
- Plus digest reuse (stops duplicate paid passes) and math spares-on-demand (fewer calls, not weaker checks).

**What we will not claim in a meeting:** −55% / −67% as a committed fleet number. That stack is a **13-book sample**, pre-Batch, and the Arabic mechanical save applies only to Arabic-prose books.

**Proof-run spend:** about **$1.50** total for the two 150-exercise copies. Thinking-level replay: **$3–6**, blocked until explicitly approved.

---

## 7. Risks to name in the meeting

- **Last-page fill.** Papers already pull over budget from the spare pool. If we review fewer spares, some sections may look sparser. That is why math-first and a before/after selection compare are required.
- **Batch without cache** costs more on **input** than unary-with-cache. It still wins because thinking dominates. Math books save less (~30%) than Arabic (~40%).
- **False-alarm guard must stay narrow.** Only drop findings where the quoted marks are already correct. A wide guard would leave real mark defects in the book.
- **Logs must travel with the zip.** Digest reuse and recovery do nothing if `00_api_call_logs` is stripped.

---

## 8. What we need from you (Claude) next

1. Confirm the Batch+cache test design: attach `cached_content` on `InlinedRequest` the same way unary does; if create/submit returns an error, fall back to uncached Batch and record it.
2. Keep per-set and thinking-level work **out** of the first patch.
3. For the Arabic replay: report coverage of the tanween-on-alif guard on logged findings; do **not** fold `كلا` / `كل` into the guard without a separate check.
4. Do not implement against production book folders. Proof copies only.

---

## 9. One-paragraph version (for a verbal update)

Review is 41% of book cost because we call Gemini once per exercise at `thinking=high`, and thinking is billed as output. The first estimate of “Batch halves it” and “G9 paid six times” was too high: Batch is closer to a 35–40% cut unless cache works on Batch, and G9’s extra calls were mostly unpaid cache 403s that then forced paid re-runs. We will prove Batch and Batch-cache on two copied sections (~$1.50), stop re-reviewing unchanged exercises by content digest, drop Arabic mark-placement false alarms, and review spare questions only on math books when something was rejected — without lowering thinking until those are measured. We will not spend the extra $3–6 thinking A/B until that is approved.
