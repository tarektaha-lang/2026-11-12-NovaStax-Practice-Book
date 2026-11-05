## Task: merge this book's terminology into one glossary

You are given every terminology row extracted from the book's sections, grouped by kind and by the English text only so that similar rows sit together. That grouping is not a decision. You decide which rows are the same item.

Return JSON with `entries`, `dropped`, and `conflicts`.

### Same item

- The same English with different wording or case is one item (`Unit circle` and `unit circle`).
- Singular and plural, and an abbreviation and its full name, are one item (`m`, `meter`, `metres`; `hr`, `hour`; `rad`, `radian`). A unit row may carry `canonical_unit` as a hint. Use it. It does not override a quote.
- The same English with different definitions is one item when it is the same concept. Different concepts stay separate items and use distinct `english` values.

### One preferred Arabic form

- Choose one `arabic` per item.
- Prefer what a confirmed row prints, and what its quotes print, over an inferred row.
- For a unit, prefer the form printed next to a number. A superscript counts: `1^{\text{د}}` prints `د` next to 1. If the quotes show an abbreviation beside a number (`20 م`), that abbreviation is `arabic` and `abbreviation_only` is true. If the quotes show only the spelled word beside a number (`16 مترًا`, `10 أمتار`), `arabic` is the citation form of that word (`متر`), not an abbreviation that the quotes never print beside a number.
- For a term, prefer the dictionary form the book prints. When the book prints both a form with `ال` and the same words without it, choose the form with `ال`.

### Grammar is not a conflict

These are the same form, never a conflict: the article `ال`, plural, dual, tanween, `ة`/`ه`, `ى`/`ي`, and hamza spelling.

- For a unit, put the other printed forms in `also`.
- For a term or a symbol, `also` is `[]` and `abbreviation_only` is false.

### abbreviation_only

Set it from the evidence. True only for a unit whose preferred form is an abbreviation the book prints (for example `سم`, `م`, `د`, `ج.م`). False for a spelled word (`متر`, `ساعة`, `راديان`).

### Drop

Drop a row that is not a unit of measurement. Counting nouns such as `time`, `page`, and `cup` are not units unless a quote prints that word as a unit next to a number. Each dropped row goes in `dropped` as `{row_id, reason}`.

### Conflicts

List a conflict only when one item has two or more truly different Arabic forms, not grammar or spelling of one form. Each conflict has `english`, `kind`, `forms` (each `{arabic, sections}`), `chosen`, and a one-line `reason`. The chosen form is the entry's `arabic`.

### Latin notation

Under latin notation, a symbol's `arabic` equals its English symbol. Do not transliterate it.

Under arabic notation, a symbol is written with ا, never أ/إ/آ, inside maths. A multi-letter point name has one space between letters, one consistent form: `ا ب جـ`, `\overline{ا ب}`. Do not write `أب` in one place and `ا ب` in another.

### Coverage

Every input row `id` appears exactly once: either in some entry's `source_row_ids`, or as a `dropped.row_id`. Do not invent an id. Do not invent an Arabic form. `arabic` and every `also` form must be copied from those rows' `arabic`, `also`, or `quotes`.

Each entry has `kind`, `english`, `arabic`, `also`, `abbreviation_only`, `source_row_ids`, and a one-line `reason`.
