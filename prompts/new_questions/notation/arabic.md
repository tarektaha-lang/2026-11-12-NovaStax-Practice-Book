## Notation: Arabic

The book writes its mathematics in Arabic notation, laid out right to left: Arabic letters for
unknowns and variables (س، ص، ع، ن), for points and vertices (أ، ب، ج، د، هـ، و), and Arabic
abbreviations for the functions and units it uses (جا، جتا، ظا؛ سم، م، كجم، ل), exactly as the
spec's equations and notations and the textbook's own questions have them.

- Write every expression in LaTeX inside `$...$` with the Arabic letters the book uses in place
  of Latin ones: `$س^2 - 4 = 0$`, `$\overline{أب}$`, `$\triangle أبج$`. The LaTeX commands keep
  their ASCII names — `\frac`, `\sqrt`, `\times`, `\div`, `\angle`, `\overline`, `\blank`, `^`,
  `_` — and only the letters and words in them are Arabic.
- **Write every expression in logical order, first operand first**: `$\frac{17}{28} -
  \frac{11}{28}$` for "seventeen twenty-eighths minus eleven twenty-eighths". Never reverse an
  expression to make it look right-to-left in the file: the typesetter mirrors the layout, and
  an expression reversed by hand is mirrored back to front. The same holds for a list of values
  — `$2$, $5$, $8$` is written in the order it is read — and for the rows and cells of a table.
- **Decide, for every Arabic token, whether it is mathematics.** The paper renders each
  bare letter inside `$...$` as its own isolated math glyph, so a word left bare in
  maths does not print as that word.
  1. **Ordinary wording — no `$...$`.** Step names and word options: `افهم`, `خطط`, `حل`,
     `تحقق`. Never `$افهم$`, never `$\text{افهم}$`.
  2. **One-letter variables and points, and spaced products — bare letters inside `$...$`.**
     `$س$`, `$ل$`, `$م$`, `$ل ض ع$`, `$2(ل + ض)$`, `$أ$`, `$\triangle أبج$`,
     `$\overline{أب}$`. Do not wrap these in `\text{...}`.
  3. **A word that belongs inside a formula — `\text{...}` inside `$...$`.** A unit, a
     function name, or a multi-letter abbreviation of a quantity (مح for محيط, جا، جتا، ظا،
     سم، كجم): `$5\text{ سم}$`, `$\text{جا}\, س$`, `$\text{مح} = 48\text{ سم}$`.
     Never `$مح = 48\text{ سم}$` and never `$مح$`.
  - Test: if you would *say it as one word* (محيط → مح, جيب → جا), use `\text{...}`.
    If you would *name each letter* (length ل, width ض, vertices أ ب ج), leave the
    letters bare in maths. Latin `$P$` is one letter; Arabic مح is two letters, so it
    cannot be written like `$P$`.
- Name the points and values of a figure with the same letters: a triangle is $أبج$, a midpoint
  is $د$, an unknown side is $س$. Write them in the description exactly as they are to be
  printed on the figure.
- Use only the symbols and conventions the spec's notations and the textbook's own questions
  use — how an angle, a length, a set or a ratio is written. Do not substitute the convention
  of another Arabic curriculum, and do not fall back on Latin letters where the book has
  Arabic ones.
- Digits follow the numerals rule above, whatever letters stand beside them.
- A geometric ray, including a secant ray or a tangent ray, is `\overrightarrow{AB}` —
  never `\vec`. A true vector or unit vector is `\vec{a}` or `\vec{AB}` — never
  `\overrightarrow`, and never a raw `\overrightharpoon` in the JSON. A line is
  `\overleftrightarrow{AB}`; a segment is `\overline{AB}`.
