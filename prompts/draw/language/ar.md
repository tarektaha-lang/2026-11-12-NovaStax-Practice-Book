## Language: Arabic

The paper is in Arabic, and so is every word on a figure: an axis name, a unit
written out, a category under a bar, a word the description puts on the picture.

- Set Arabic words as text, in a node, exactly as the description gives them:
  `\node [below] at (2.5,-0.6) {التكرار};`. The paper sets the node right to left
  itself; write the word in logical order and add no direction marks or bidi
  control characters (U+200E, U+200F, U+202A–U+202E, U+2066–U+2069).
- `$...$` is only for mathematics. An Arabic word that is not a variable, a point name,
  or part of a formula is a node, not maths: `\node [below] at (2.5,-0.6) {التكرار};`.
  Never `{$افهم$}`.
- An Arabic word that *does* stand inside a formula — a unit, a function name, a
  multi-letter abbreviation such as مح — goes in `\text{...}`: `{$6\text{ سم}$}`,
  `{$\text{مح} = 50\text{ م}$}`. Never set a bare Arabic word in maths mode (`{$مح$}`).
- A word the description gives in Latin letters — a proper name, a unit written in
  Latin — stays in Latin letters as given.
- Everything TikZ reads as a name stays in ASCII: coordinate names, node names,
  `name path`, style keys, option values. Arabic goes only into what is printed —
  the text of a node, a label, a tick label given by hand — never into how the
  picture is put together.
- A hyphen joining two numbers into a span, `{20-30}`, is set as text as the
  general rules say, and the paper reads the span the right way round; do not
  reverse the numbers to suit the direction of the page.
