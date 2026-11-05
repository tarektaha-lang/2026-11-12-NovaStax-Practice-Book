## Task: translate every exercise group of one objective

You are given a compact Arabic context for ONE objective (approved objective text, overview,
equations, notations, curriculum terminology) and a JSON LIST of
English exercise groups of that objective. No PDFs are attached to this call.

Return `groups`: one complete Arabic projection per input group, in the SAME ORDER, with the
SAME `id` on every group and every exercise. A group you cannot translate goes in `aborted`
as `{id, reason}`. Never omit a requested group: it is either in `groups` or in `aborted`. An
`ok` reply that leaves out a requested group without an `aborted` entry is invalid. Return
`status: "abort"` only when nothing at all can be translated.

Consistency: write the same English term the same way in every group of this call.

For every group:

- Keep `id` on the group and on every exercise. Never omit `stem`, `text`, `options`,
  `answer`, `figures`, or `poems`.
- Do not add `objective`, `objectives`, `word_problem`, `figure_type`, `graph`, `grid`,
  `strip`, or any other structural key. The tool preserves those from English.
- Keep the number of exercises, options, figures and poems (and each poem's rows and shatrs).
- Translate `stem`, `text`, `options`, `answer`, figure `description`, and poem shatrs. Each
  field is only the Arabic (and maths) that prints: no other scripts, no fullwidth
  punctuation, no commentary. A table `\begin{array}{|c|…|}` stays an `array`.
- Keep the English product operator: `\times` stays `\times`, `\cdot` stays `\cdot` (including the middot between two units). Do not replace `\cdot` with `\times`.
- Write `\parallel` for a parallel relation, never `//`.
- Map multiple-choice letters `A` `B` `C` `D` (`E` `F`) to `أ` `ب` `ج` `د` (`ه` `و`). Options
  stay unlabelled; `answer` names the correct one by its Arabic letter.
- Keep canonical `[figure N]` and `[poem N]` tokens exactly; never write `[الشكل N]`. Do not
  insert tatweel or `::`. Do not put `$...$` around an Arabic word that is not mathematics.

Placeholders (the English text is protected):

- `⟦M1⟧`, `⟦M2⟧` … stand for complete formulas. Copy each one exactly once, unchanged, at the
  place where the formula belongs in the Arabic sentence.
- `⟦N1⟧`, `⟦N2⟧` … stand for numbers printed in prose. Copy each one exactly once. You may put
  one or more of them inside a formula you write, e.g. `$⟦N1⟧ \times ⟦N2⟧$`; outside a
  formula the tool prints them as mathematics itself.
- A `⟦N#⟧` may be left out ONLY where Arabic grammar replaces the number by the noun form
  (dual or singular: `2 minutes` → `دقيقتين`, `1 hour` → `ساعة`). List every such omission
  in the group's `dropped_numbers` as `{field, token, reason}`, where `field` is `stem`,
  `figures[0].description`, `<exercise id>.text`, `<exercise id>.options[<index>]`,
  `<exercise id>.answer`, or `<exercise id>.figures[<index>].description`.
- `⟦E1⟧$ … $⟦/E1⟧` marks a formula you must edit only in its words: translate words inside
  `\text{...}` (and Latin words written in the formula) and, under arabic notation, the
  letters as the notation rules say. Keep every digit, operator, command, brace and power
  (`^2`, `^3`) exactly. Return the formula still wrapped in its `⟦E1⟧ … ⟦/E1⟧` tags.
- Never invent a placeholder and never leave a placeholder's number changed.
- An answer keeps every placeholder from that answer, including each formula in the
  explanation after the choice letter. Do not shorten an explained choice to the letter
  alone, and do not merge several `⟦E⟧` formulas into one formula.

Units:

- Use a glossary entry only where its concept actually appears; the English article a,
  preposition in, possessive 's, and point labels are not units.
- Write every unit in Arabic as the glossary gives it. Use `arabic` in formulas; an `also`
  form is allowed in prose.
- A word unit (`abbreviation_only: false`, such as `ساعة`, `دقيقة`, `جنيه`) is inflected by
  the rule below, not left in one form for every number in the exercise.

{unit_agreement}
- Keep every unit's power and every quantity's unit: do not drop, add, or change a unit, and
  do not turn `m²` into `m` or `m` into `m²`.
- The English word `in` is a preposition unless it is attached to a number as a length
  (`5 in`); a point name such as `A` is a letter, not a unit; `m(∠A)` is an angle measure.

If any field cannot be translated faithfully, put that group in `aborted`. Otherwise return
`status: "ok"`, `abort_reason: ""`, every group, and `aborted` for the rest (usually `[]`).
