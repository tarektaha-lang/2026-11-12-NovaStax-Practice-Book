## Language: Arabic

The book is in Arabic, and `text` and `stem` are written in Arabic.

- Keep the book's own terms exactly as it prints them, including letter form and tashkeel
  on those terms — hamza (أ إ آ ء), taa marbuta (ة) against haa (ه), alif maqsura (ى)
  against yaa (ي), shadda, tanwin. Rewrite instructional sentences in short school Arabic;
  do not paste the printed sentence. Do not vocalise the paraphrase beyond what the book
  uses to disambiguate, and add no tashkeel the book does not print on the terms you keep.
- Keep the book's punctuation: the comma «،», the semicolon «؛», the question mark «؟»,
  the full stop «.».
- Write the text in logical order, exactly as it is read. Never insert direction marks or
  bidi control characters (U+200E, U+200F, U+202A–U+202E, U+2066–U+2069): the paper is set
  right to left by the typesetter, and a control character in a field is one it cannot
  render.
- An option's label is the letter the book prints, in the book's own letters, for example
  `(أ) $\sqrt{7}$` or `أ) $\sqrt{7}$` as the book has it. Do not turn it into a Latin letter.
- A word the book prints in Latin letters — a proper name, a unit written in Latin, an
  abbreviation — stays in Latin letters as printed.
- `$...$` is only for mathematics. An Arabic word that is not a variable, a point name,
  or part of a formula stays outside `$...$`: write `افهم`, never `$افهم$`.
- An Arabic word that *does* stand inside a formula or a table — a unit, a function name,
  a multi-letter abbreviation such as مح, a column heading — goes in `\text{...}`:
  `\text{التكرار}`, `$5\text{ سم}$`, `$\text{مح} = 48\text{ سم}$`. Never leave that word
  as bare letters in maths (`$مح$`, `$افهم$`): the paper prints those as isolated glyphs,
  not as a word.
- A blank the book prints as a row of dots or a line is transcribed as a row of dots
  (`....`), as the book has it; the writing stage is told separately how to mark its own.
