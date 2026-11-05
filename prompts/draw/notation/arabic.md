## Notation: Arabic

The paper writes its mathematics in Arabic notation: Arabic letters for points,
vertices and unknowns (أ، ب، ج، د؛ س، ص), Arabic unit abbreviations (سم، م، كجم),
and expressions laid out right to left. The description names the points and
values in those letters, and the figure prints exactly what the description names.

- **Label a point with the Arabic letter the description gives it, in maths:**
  `\node [above] at (A) {$أ$};`. **Name the coordinate in ASCII**, `\coordinate
  (A)`, and keep every TikZ name ASCII — coordinate names, node names, `name
  path`, the arguments of `\anglemark` — because those are read by TikZ and not
  printed. Pick ASCII names that follow the description's order (أ→A, ب→B, ج→C, and
  so on), so that the picture can be read against the description.
- **Decide, for every printed Arabic token, whether it is mathematics.** The paper
  renders each bare letter inside `$...$` as its own isolated math glyph, so a word
  left bare in maths does not print as that word.
  1. **Ordinary wording — a node, not maths.** An axis name, a category, a step name:
     `\node [below] at (2.5,-0.6) {التكرار};`. Never `{$افهم$}` and never a prose
     node wrapped as maths.
  2. **One-letter variables and points — bare letters inside `$...$`.**
     `\node [above] at (A) {$أ$};`, `{$\triangle أبج$}`, `{$ل$}`, `{$2س + 1$}`.
     Do not wrap these in `\text{...}`.
  3. **A word that belongs inside a formula — `\text{...}` inside `$...$`.** A unit,
     a function name, or a multi-letter abbreviation of a quantity (مح for محيط):
     `{$6\text{ سم}$}`, `{$\text{مح} = 50\text{ م}$}`. Never `{$مح = 50\text{ م}$}`
     and never `{$مح$}`.
  - Test: if you would *say it as one word* (محيط → مح), use `\text{...}`. If you
    would *name each letter* (length ل, vertex أ), leave the letters bare in maths.
- **Write each expression in logical order, first operand first**, exactly as the
  description has it: `{$2س + 1$}`, not a reversed copy. The paper mirrors the
  layout of the maths itself; an expression reversed by hand is mirrored back to
  front.
- Never fall back on a Latin letter where the description has an Arabic one: a
  triangle described as أبج is labelled أ، ب، ج and nothing else.
- Digits follow the numerals rule above, whatever letters stand beside them.
