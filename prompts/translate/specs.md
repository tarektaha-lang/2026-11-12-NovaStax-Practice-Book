## Task: translate one section spec

You are given the translatable projection of the English applied spec for one section, the
Arabic table-of-contents title and module path. The book glossary is at the end of these
instructions. No
PDFs are attached.

Placeholders (the English text is protected):

- `⟦M1⟧`, `⟦M2⟧` … stand for complete formulas. Copy each one exactly once, unchanged, at the
  place where the formula belongs in the Arabic sentence.
- `⟦N1⟧`, `⟦N2⟧` … stand for numbers printed in prose. Copy each one exactly once. You may put
  one or more of them inside a formula you write, e.g. `$⟦N1⟧ \times ⟦N2⟧$`; outside a
  formula the tool prints them as mathematics itself.
- A `⟦N#⟧` may be left out ONLY where Arabic grammar replaces the number by the noun form
  (dual or singular: `2 minutes` → `دقيقتين`, `1 hour` → `ساعة`). List every such omission
  in `dropped_numbers` as `{field, token, reason}`, where `field` is the spec path:
  `overview`, `<id>.text`, `<id>.reason`, `<id>.term`, `<id>.definition`, `<id>.title`,
  `<id>.notes`, `<id>.figure.purpose`, `<id>.figure.description`, `<id>.latex`,
  `<id>.notation`, `<id>.meaning`, or `<id>.rows[<index>].shatrs[<index>]`.
- `⟦E1⟧$ … $⟦/E1⟧` marks a formula you must edit only in its words: translate words inside
  `\text{...}` (and Latin words written in the formula) and, under arabic notation, the
  letters as the notation rules say. Keep every digit, operator, command, brace and power
  (`^2`, `^3`) exactly. Return the formula still wrapped in its `⟦E1⟧ … ⟦/E1⟧` tags.
- Never invent a placeholder and never leave a placeholder's number changed.
- `figure.description` and excerpt `text` are prose. Keep a command outside `$...$` when the
  English already writes it there (`\frac`, `\bar`). A grade digit (`Grade 7`) is a number
  placeholder; copy it, do not replace it with the grade name alone.

Return the complete translatable projection in Arabic:

- Keep every row `id` exactly (`objective_1`, `term_2`, …).
- Return every list and every printable field shown, including empty lists and
  `figure: null`. Do not return structural `role`, `type`, or `reuse`.
- Write number sets as \mathbb{R}, \mathbb{Z}, \mathbb{Q}, \mathbb{N}, \mathbb{C} unchanged (also R^+, Z^-); the typesetter prints the Arabic set letters.
- Keep the English product operator: `\times` stays `\times`, `\cdot` stays `\cdot` (including the middot between two units). Do not replace `\cdot` with `\times`.
- Write `\parallel` for a parallel relation, never `//`.
- Equation `latex` follows the notation rules above. Under latin notation keep letters,
  numbers, commands, operators and grouping exactly while translating prose inside
  `\text{...}`. Under arabic notation rewrite the mathematical letters as the profile requires.
- Translate `overview`, objective and prerequisite `text`, out-of-scope `text` and `reason`,
  glossary `term` and `definition`, teaching-point `title` and `notes`, excerpt `text` and
  poem `rows[].shatrs`, notation `meaning`, and figure `purpose` / `description`.
- A glossary `term` is Arabic. Do not copy the English headword (`Minimization` is not a term).
  A notation that names units is Arabic too (`km, cm, mm` does not stay in Latin).
- Keep excerpt `rows` structure and `kashida` as in English. Do not invent `::` or tatweel.
- Translate the printable `notation` field while preserving its mathematics.
- Use the glossary's Arabic forms for terms, symbols and units. Write units as the glossary
  gives them (`$32\pi\text{ م}^3$`), keep every power, and keep formulas in logical order.
- Translate English prose inside maths (`\text{Area}` → `\text{المساحة}`).
- Every `\angle`, `\triangle`, `\overline`, `\sqrt`, `\frac`, `\mathbb`, `\iff`, `\equiv`,
  and `\text` expression sits inside one complete `$...$` or `$$...$$` span.
- Every reader-visible digit in Arabic prose is inside mathematics.
- Do not add, drop, or reorder rows. Do not paraphrase the Arabic title or module path.
- Also return `terminology`: a compact list of `{english, arabic}` pairs for the glossary
  terms and recurring curriculum terms the exercise translations must use. Every pair is
  non-empty and agrees with the glossary.

If the spec cannot be translated faithfully, return `status: "abort"`, a reason, `spec: null`,
and `terminology: []`. Otherwise `status: "ok"`, `abort_reason: ""`, the complete `spec`, and
`terminology`.
