## Notation: Arabic

The book writes its mathematics in Arabic notation, laid out right to left on the page: Arabic letters for unknowns and variables (س، ص، ع، ن), for points and vertices (أ، ب، ج، د، هـ، و), and Arabic abbreviations for the functions and units it uses (جا، جتا، ظا؛ سم، م، كجم، ل).

- Expressions in the JSON are LaTeX inside `$...$` with those Arabic letters: `$س^2 - 4 = 0$`, `$\overline{أب}$`, `$\triangle أبج$`. The LaTeX commands keep their ASCII names. Work them as written.
- **Every expression is in logical order, first operand first**, however it will be laid out on the page. `$17 - 11$` is seventeen minus eleven, not the reverse. Do not treat logical order as a defect, and do not reverse an expression in your head to "make it Arabic".
- **Decide, for every Arabic token, whether it is mathematics.** The paper renders each
  bare letter inside `$...$` as its own isolated math glyph, so a word left bare in
  maths does not print as that word. Treat a mismatch as a **notation/wording defect**:
  1. **Ordinary wording — no `$...$`.** Step names and word options must be `افهم`,
     `خطط`, `حل`, `تحقق`. `$افهم$` and `$\text{افهم}$` are defects.
  2. **One-letter variables and points, and spaced products — bare letters inside `$...$`.**
     `$س$`, `$ل$`, `$م$`, `$ل ض ع$`, `$2(ل + ض)$`, `$أ$`, `$\triangle أبج$`,
     `$\overline{أب}$` are correct. Wrapping those letters in `\text{...}` is a defect.
  3. **A word that belongs inside a formula — `\text{...}` inside `$...$`.** A unit, a
     function name, or a multi-letter abbreviation of a quantity (مح for محيط, جا، جتا، ظا،
     سم، كجم): `$5\text{ سم}$`, `$\text{جا}\, س$`, `$\text{مح} = 48\text{ سم}$`.
     Bare `$مح = 48\text{ سم}$` or `$مح$` is a defect.
  - Test: if you would *say it as one word* (محيط → مح, جيب → جا), it must be `\text{...}`.
    If you would *name each letter* (length ل, width ض, vertices أ ب ج), the letters stay
    bare in maths. Latin `$P$` is one letter; Arabic مح is two letters, so `$مح$` is not
    the Arabic of `$P$`.
- Name the points and values of a figure with the same letters the description uses: a triangle is $أبج$, a midpoint is $د$, an unknown side is $س$.
- Digits follow the numerals rule above, whatever letters stand beside them.
