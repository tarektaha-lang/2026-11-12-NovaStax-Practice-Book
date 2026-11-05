## Notation: Latin

The book writes its mathematics as an English textbook does: Latin letters for unknowns,
variables, points and vertices ($x$, $y$, $A$, $B$, $\triangle ABC$), the usual operator
symbols, function names such as $\sin$ and $\log$, unit abbreviations in Latin letters
($\text{cm}$, $\text{kg}$, $\text{km/h}$), all read left to right.

- Write every expression in LaTeX inside `$...$` with those letters and symbols, as the spec's
  equations and notations do. Use `\times` for a number product (`$2 \times 3$`, `$2 \times 10^7$`). Use `\cdot` between two units (`$100\text{ kg}\cdot\text{m/sec}$`) and for a vector dot product (`$\vec{a}\cdot\vec{b}$`).
- Where the book's prose is in another language, the mathematics still stays in this notation:
  keep the Latin letters and left-to-right order the textbook's own questions use, and never
  turn them into the letters of the prose. Units and function names are written as the book
  writes them, in Latin letters where it does.
- Name the points and values of a figure with the same letters: a triangle is $ABC$, a midpoint
  is $D$, an unknown side is $x$.
- A geometric ray, including a secant ray or a tangent ray, is `\overrightarrow{AB}` —
  never `\vec`. A true vector or unit vector is `\vec{a}` or `\vec{AB}` — never
  `\overrightarrow`, and never a raw `\overrightharpoon` in the JSON. A line is
  `\overleftrightarrow{AB}`; a segment is `\overline{AB}`.
