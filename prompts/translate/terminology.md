## Task: extract this section's Arabic terminology, symbols and units

You are given Arabic section PDFs (the terminology authority), their source manifest, an
ordered inventory of the English section's terms and symbols, and snippets of the English
section's exercises, spec and figures. Do not translate questions. Do not transcribe
exercises from the PDFs.

Return `map_fragment` with:

- `section`: the section number exactly as given (for example `4.5`).
- `observations`: exactly one row per inventory row, in the supplied order, copying only the
  supplied `id`. Do not invent, omit, or reorder ids.
  - `confirmed`: the Arabic pages show the form. Give the printable `plain_lemma` (terms) and
    `math_text` (symbols), a blank `basis`, and at least one `evidence` row with the
    manifest's `source_id`, a 1-based `split_page` in that PDF, and a short exact Arabic
    `quote`.
  - `inferred`: the exact form is absent, but the book's language and conventions determine one
    form. Give the form, a short non-empty `basis`, and any supporting evidence.
  - Under latin notation a symbol keeps its Latin letters in `math_text`.
  - Under arabic notation a symbol's letters inside maths are ا, never أ/إ/آ, and a
    multi-letter point name has one space between letters (`ا ب جـ`, `\overline{ا ب}`).
- `units`: every measurement or currency unit that appears in the English snippets, once
  each:
  - `english`: the base unit, without a power (`cm` for `cm²`, `km/h`, `EGP`, `hour`).
    Use one canonical base: `min` and `minutes` are `minute`, `meters` is `m`,
    `kilometers` is `km`. Not a unit: the preposition `in` (unless a number of inches),
    point or vertex letters (`A`, `B` naming a point), variables, function names, or
    angle measure `m(∠A)`. A single letter that is a physical unit is a unit: `N`
    (newton), `J`, `W`, `V`, `A` (ampere), `Pa`, `Hz`. Counting nouns (`time`, `page`)
    are not units unless the book prints them as a measured unit beside a number.
  - `arabic`: the form the book prints (`سم`, `كم/س`, `ج.م`, `ساعة`, `ن` for `N`).
    If the book prints `ج.م`, that is `arabic`, not a guessed word such as `جنيه`.
  - `also`: other Arabic forms the book prints for the same unit (for example `جنيه` beside
    `ج.م`), or `[]`. Do not put a Latin spelling (`LE`, `min`) in `also`.
  - `abbreviation_only`: `true` when the Arabic form is an abbreviation that is never
    inflected (`سم`, `مم`, `كم`, `كم/س`, `ج.م`); `false` for a word unit that Arabic inflects
    (`ساعة`, `دقيقة`, `جنيه`, `متر` written as a word). Always set it.
  - `status`, `basis`, `evidence` as for observations.

Return `status: "ok"`, `abort_reason: ""`, and the fragment. If the PDFs cannot be read,
return `status: "abort"`, a reason, and `map_fragment: null`.
