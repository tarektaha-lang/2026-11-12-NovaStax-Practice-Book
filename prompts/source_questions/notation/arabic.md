## Notation: Arabic

The book writes its mathematics in Arabic notation, laid out right to left: Arabic letters
for unknowns and variables (س، ص، ع، ن), for points and vertices (أ، ب، ج، د، هـ، و), and
Arabic abbreviations for functions and units (جا، جتا، ظا؛ سم، م، كجم، ل), exactly as
printed.

- Transcribe each expression in LaTeX inside `$...$` with the Arabic letters the book
  prints in place of Latin ones: `$س^2 - 4 = 0$`, `$\overline{أب}$`, `$\triangle أبج$`. The
  LaTeX commands keep their ASCII names — `\frac`, `\sqrt`, `\times`, `\div`, `\angle`,
  `\overline`, `^`, `_` — and only the letters and words in them are Arabic.
- **Write each expression in logical order, first operand first, however it is laid out
  on the page.** The book prints «١٧/٢٨ − ١١/٢٨» with the first fraction on the right; you
  write `$\frac{17}{28} - \frac{11}{28}$`, the expression as it is read. Read carefully
  which operand is which — in a subtraction, a division or an inequality the order is the
  mathematics — and never reverse an expression to make it look right-to-left: the
  typesetter mirrors the layout, and an expression reversed by hand is mirrored back to
  front.
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
- A symbol the book writes in a house style of its own — how it names an angle, a length,
  a set, a ratio — is transcribed as the book has it, with the standard LaTeX for that mark
  and the book's own letters in it. Do not substitute the convention of another Arabic
  curriculum.
- Digits follow the numerals rule above, whatever letters stand beside them.
- A geometric ray, including a secant ray or a tangent ray, is `\overrightarrow{AB}` —
  never `\vec`. A true vector or unit vector is `\vec{a}` or `\vec{AB}` — never
  `\overrightarrow`, and never a raw `\overrightharpoon` in the JSON. A line is
  `\overleftrightarrow{AB}`; a segment is `\overline{AB}`.
