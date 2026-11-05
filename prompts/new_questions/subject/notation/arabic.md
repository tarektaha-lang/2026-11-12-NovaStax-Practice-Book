## Notation: Arabic

The book writes its mathematics in Arabic notation, laid out right to left: Arabic letters for
unknowns and variables (س، ص، ع، ن), for points and vertices (أ، ب، ج، د، هـ، و), and Arabic
abbreviations for the functions and units it uses (جا، جتا، ظا؛ سم، م، كجم، ل), exactly as the
spec's equations and notations and the textbook's own questions have them.

- Write every expression in LaTeX inside `$...$` with the Arabic letters the book uses in place
  of Latin ones: `$س^2 - 4 = 0$`, `$\overline{أب}$`, `$\triangle أبج$`. The LaTeX commands keep
  their ASCII names — `\frac`, `\sqrt`, `\times`, `\div`, `\angle`, `\overline`, `\blank`, `^`,
  `_` — and only the letters and words in them are Arabic. `\blank` is the only command that
  may stand in the wording outside `$...$`. Every other command goes inside `$...$` or
  `$$...$$`, in `text`, `stem`, `answer`, and each choice. An underlined word is
  `$\underline{\text{الكلمة}}$`. A tick is `($\checkmark$)` and a cross is `($\times$)`.
  A formula is one span, `$\text{H}_2\text{O}$`. A line break is a real newline, never the
  two characters `\n`.
- **Write every expression in logical order, first operand first**: `$\frac{17}{28} -
  \frac{11}{28}$` for "seventeen twenty-eighths minus eleven twenty-eighths". Never reverse an
  expression to make it look right-to-left in the file: the typesetter mirrors the layout, and
  an expression reversed by hand is mirrored back to front. The same holds for a list of values
  — `$2$, $5$, $8$` is written in the order it is read — and for the rows and cells of a table.
- Arabic words that are part of the mathematics — a unit, a function name — go in `\text{...}`
  inside the `$...$`: `$5\text{ سم}$`, `$\text{جا}\, س$`.
- Name the points and values of a figure with the same letters: a triangle is $أبج$, a midpoint
  is $د$, an unknown side is $س$. Write them in the description exactly as they are to be
  printed on the figure.
- Use only the symbols and conventions the spec's notations and the textbook's own questions
  use — how an angle, a length, a set or a ratio is written. Do not substitute the convention
  of another Arabic curriculum, and do not fall back on Latin letters where the book has
  Arabic ones.
- Digits follow the numerals rule above, whatever letters stand beside them.
- A geometric ray, including a secant ray or a tangent ray, is `$\overrightarrow{AB}$` —
  never `$\vec{AB}$`. A true vector or unit vector is `$\vec{a}$` or `$\vec{AB}$` — never
  `$\overrightarrow{a}$`, and never a raw `\overrightharpoon` in the JSON. A line is
  `$\overleftrightarrow{AB}$`; a segment is `$\overline{AB}$`.
