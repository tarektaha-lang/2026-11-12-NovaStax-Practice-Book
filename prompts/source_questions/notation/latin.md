## Notation: Latin

The book writes its mathematics as an English textbook does: Latin letters for unknowns,
variables, points and vertices ($x$, $y$, $A$, $B$), the usual operator symbols, function
names such as $\sin$ and $\log$, unit abbreviations in Latin letters ($\text{cm}$,
$\text{kg}$, $\text{km/h}$), all read left to right.

- Transcribe each expression in LaTeX inside `$...$` with the letters and symbols the
  book prints: `$x^2 - 4 = 0$`, `$\triangle ABC$`, `$36 - N = 28$`.
- Use `\times` for a number product (`$2 \times 3$`, `$2 \times 10^7$`). Use `\cdot` between two units (`$100\text{ kg}\cdot\text{m/sec}$`) and for a vector dot product (`$\vec{a}\cdot\vec{b}$`).
- Where the book's prose is in another language, the mathematics still stays in this
  notation: keep the Latin letters and the left-to-right order the book prints, and never
  turn them into the letters of the prose.
- A symbol the book writes in a house style of its own — a bar over a segment, a
  particular arrow, a particular way of naming an angle — is transcribed as the book has
  it, with the standard LaTeX for that mark.
- A geometric ray, including a secant ray or a tangent ray, is `\overrightarrow{AB}` —
  never `\vec`. A true vector or unit vector is `\vec{a}` or `\vec{AB}` — never
  `\overrightarrow`, and never a raw `\overrightharpoon` in the JSON. A line is
  `\overleftrightarrow{AB}`; a segment is `\overline{AB}`.
