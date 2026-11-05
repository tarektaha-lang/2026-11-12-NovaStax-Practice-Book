## Notation: Latin

The book writes its mathematics as an English textbook does: Latin letters for unknowns,
variables, points and vertices ($x$, $y$, $A$, $B$, $\triangle ABC$), the usual operator
symbols, and function names such as $\sin$ and $\log$, all read left to right. Unit words are
Arabic and follow the book glossary.

- Keep every expression in LaTeX inside `$...$` with those letters and symbols, as the English
  spec's equations do. Do not turn them into Arabic letters.
- Where the prose is Arabic, the mathematics still stays in this notation.
- Write unit words in Arabic as the glossary gives them, inside `\text{...}` when they stand in
  a formula (`$27\text{ سم}^3$`), keeping the Latin formula, its logical order, and every power.
- Name the points and values of a figure with the same Latin letters: a triangle is $ABC$, a
  midpoint is $D$, an unknown side is $x$.
- Do not mirror an expression to make it look right-to-left in the file: the typesetter
  mirrors the layout.
- A geometric ray, including a secant ray or a tangent ray, is `\overrightarrow{AB}` — never
  `\vec`. A true vector or unit vector is `\vec{a}` or `\vec{AB}` — never `\overrightarrow`,
  and never a raw `\overrightharpoon` in the JSON. A line is `\overleftrightarrow{AB}`; a
  segment is `\overline{AB}`.
