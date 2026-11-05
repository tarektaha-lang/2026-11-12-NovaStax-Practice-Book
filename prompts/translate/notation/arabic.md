## Notation: Arabic

The book writes its mathematics in Arabic notation, laid out right to left: Arabic letters for
unknowns and variables (س، ص، ع، ن), for points and vertices (ا، ب، جـ، د، هـ، و), and Arabic
abbreviations for the functions it uses (جا، جتا، ظا، لو، …), exactly as the
spec's equations and notations and the textbook's own questions have them. When the book
glossary names a different form, use that form.

- Write every expression in LaTeX inside `$...$` with the Arabic letters the book uses in place
  of Latin ones: `$س^2 - 4 = 0$`, `$\overline{ا ب}$`. The LaTeX commands keep
  their ASCII names.
- Inside maths a letter is never أ/إ/آ; always ا. Multiple-choice labels «(أ)» in prose stay as they are.
- A letter that names a function (f, g, h, or any letter written as name(argument) and defined or used as a function, e.g. f(θ), d(t), L(t), f^{-1}(x)) is written \text{د}(…), never a bare letter. A bare letter is a variable, point or label. Examples: `$\text{د}(\theta) = \text{جتا}\,\theta$`, `$\text{ر}(س)$`, `$\text{د}^{-1}(س)$`. A coordinate point such as `$F(2, 3, 6)$` is a point label, not a function: keep it as a letter with numbers, do not wrap it as `\text{د}`.
- Write number sets as \mathbb{R}, \mathbb{Z}, \mathbb{Q}, \mathbb{N}, \mathbb{C} unchanged (also R^+, Z^- etc.); the typesetter prints the Arabic set letters.
- **Latin function commands must become Arabic abbreviations inside `\text{...}`.** Never leave `\sin`, `\cos`, `\tan`, `\cot`, `\sec`, `\csc`, `\ln`, `\log`, `\exp`, `\lim`, `\operatorname{Re}`, `\operatorname{Im}`, `\operatorname{Arg}`, `\Re`, `\Im`, or `\arg` in the Arabic output. Default map (glossary wins when it differs):
  - `\sin` → `\text{جا}` · `\cos` → `\text{جتا}` · `\tan` → `\text{ظا}` · `\cot` → `\text{ظتا}` · `\sec` → `\text{قا}` · `\csc` → `\text{قتا}`
  - `\ln` → `\text{لو}_هـ` · `\log` → `\text{لو}` · `\log_b` → `\text{لو}_b`
  - `\exp` → `\text{هـ}`
  - `\lim` → `\text{نها}`
  - `\operatorname{Re}` / `\mathrm{Re}` / `\Re` / `\operatorname{حقيقي}` → `\text{ح}`
  - `\operatorname{Im}` / `\mathrm{Im}` / `\Im` / `\operatorname{تخيلي}` → `\text{تخ}` (not `\text{ت}`; `ت` is the imaginary unit)
  - `\operatorname{Arg}` / `\arg` / `\operatorname{سعة}` → `\text{سعة}`
  Examples: `$\ln x$` → `$\text{لو}_هـ س$`, `$\log_2 x$` → `$\text{لو}_2 س$`, `$\sin\theta$` → `$\text{جا}\,\theta$`.
- **`${}^n\mathrm{C}_r$ becomes `${}^n ق_r$`.** A space before `ق` is allowed. The digits in `n` and `r` stay digits: `$1792$, from ${}^8\mathrm{C}_3 \times 2^5$` → `$1792$, from ${}^8 ق_3 \times 2^5$`.
- **When English has both `r` and `R` as radii, write `\text{نق}_1` and `\text{نق}_2`.** A single radius `$r$` is `\text{نق}` with no extra subscript.
- **Keep every Greek command and operator.** Never drop `\theta`, `\pi`, `\alpha`, `\beta`, … . Never change a product operator: keep `\times` as `\times` and `\cdot` as `\cdot` (including the middot between two units: `$100\text{ kg}\cdot\text{m/sec}$` → `$100\text{ كجم}\cdot\text{م/ث}$`). Do not replace `\cdot` with `\times`. Do not replace `\setminus` with `-`. A vector dot product (`\vec` on both sides) stays `\cdot`.
- **Never use a raw `_` as a blank or missing letter.** Answer blanks are `\blank`. Do not invent subscripts on a number (`3` must not become `_3`; `()=+^2` must not become `()=_+^2`). `\text{لو}_هـ` and `\text{نق}_1` are glossary forms, not blanks. Keep each variable letter (Arabic) that the source has.
- **Keep every source digit, including `0`.** Do not drop `0` from answers or options. Do not add numbers that the English field does not have, including in figure descriptions.
- Write `\parallel` for a parallel relation, never `//`. `\overline{AB}\parallel\overline{CD}`, not `//`.
- **Write every expression in logical order, first operand first.** Never reverse an
  expression to make it look right-to-left in the file: the typesetter mirrors the layout.
- **Decide, for every Arabic token, whether it is mathematics.** The paper renders each
  bare letter inside `$...$` as its own isolated math glyph, so a word left bare in
  maths does not print as that word.
  1. **Ordinary wording — no `$...$`.** Step names and word options: `افهم`, `خطط`, `حل`,
     `تحقق`. Never `$افهم$`, never `$\text{افهم}$`. An English word option (`Understand`)
     stays a word in Arabic, not a formula.
  2. **One-letter variables and points, and spaced products — bare letters inside `$...$`.**
     `$س$`, `$ل$`, `$م$`, `$ل ض ع$`, `$2(ل + ض)$`, `$ا$`, `$\triangle ا ب جـ$`,
     `$\overline{ا ب}$`. Do not wrap these in `\text{...}`.
  3. **A word that belongs inside a formula — `\text{...}` inside `$...$`.** A unit, a
     function name, or a multi-letter abbreviation of a quantity (مح for محيط, جا، جتا، ظا،
     لو، سم، كجم): `$5\text{ سم}$`, `$\text{جا}\, س$`, `$\text{مح} = 48\text{ سم}$`.
     Never `$مح = 48\text{ سم}$` and never `$مح$`. A one-letter Latin quantity name
     (`$P$` for perimeter) becomes `\text{مح}` when Arabic writes two letters, not `$مح$`.
  - Test: if you would *say it as one word* (محيط → مح, جيب → جا), use `\text{...}`.
    If you would *name each letter* (length ل, width ض, vertices ا ب جـ), leave the
    letters bare in maths. Latin `$P$` is one letter; Arabic مح is two letters, so it
    cannot be written like `$P$`.
- Digits follow the numerals rule above, whatever letters stand beside them.
- A geometric ray, including a secant ray or a tangent ray, is `\overrightarrow{AB}` — never `\vec`. A true vector or unit vector is `\vec{a}` or `\vec{AB}` — never `\overrightarrow`, and never a raw `\overrightharpoon` in the JSON. A line is `\overleftrightarrow{AB}`; a segment is `\overline{AB}`.
- Under this notation every formula arrives as an editable `⟦E#⟧$…$⟦/E#⟧` span. Convert its
  Latin letters to the Arabic letters above, rewrite Latin function commands with the map
  above, translate words inside `\text{...}`, and keep every digit, operator (`\times`,
  `\cdot`, `\setminus`, …), Greek command, brace and power exactly. Do not invent a
  product operator the English formula does not have.
- Use one Arabic letter for one English letter throughout a group: if `A` becomes `ا` in the
  stem, it is `ا` in every exercise, option, answer and figure description of that group.
  A multiple-choice label in prose stays «(أ)».
- Units follow the book glossary (`سم`, `كجم`, `ل` …); an abbreviation is never inflected.
