## Task: translate a TikZ picture for this Arabic book

You are given the English TikZ, the English figure description, the **already-translated**
Arabic description, this book's Arabic profile, and the relevant entries of the book glossary. Follow the notation rules above and the glossary's terms exactly. Write unit labels with the glossary's Arabic form; an abbreviation is never inflected, and every unit power stays. Keep technical TikZ dimensions,
coordinates, styles, and internal names unchanged. Put every reader-visible digit in a
printed prose label inside `$...$`. Return **one** `tikzpicture` environment.

Copy the English picture as the starting figure. Do not invent new shapes or a different figure. Coordinate names, node names, `name path`, and style keys stay ASCII. Arabic goes only into what is printed. Write Arabic in logical order with no direction marks; the paper sets the node right to left. A word the description leaves in Latin letters stays in Latin letters. Expressions inside `$...$` stay in logical order even when the picture's direction changes.

**Latin-notation Arabic** (`latin` maths): keep the English image direction (LTR axes, same left and right, a number line that still increases the English way). Change only printed prose (`\text{...}`, axis names, word nodes). Keep printed Latin maths labels (`{$A$}`, `{$x$}`). Do not mirror or flip the diagram.

**Arabic-notation Arabic** (`arabic` maths): print Arabic maths letters on the figure (`{$A$}` → `{$ا$}`, `{$x$}` → `{$س$}`), matching the translated description. Inside maths a letter is ا, never أ. Keep the English product operator: `\times` stays `\times`, `\cdot` stays `\cdot` (including the middot between two units). Do not replace `\cdot` with `\times`. Write `\parallel` for a parallel relation, never `//`. **Decide directionality** where an Arabic-notation textbook would reverse the picture (a number line that should increase leftward, a ray, left/right in a word problem). The typesetter does not flip coordinates, so write the directed picture yourself. Do not skip converting a Latin point label only because the figure has no prose.

If latin notation and there is nothing to translate, return the English TikZ unchanged. Under latin notation, every numeric/coordinate token and every drawing-command count must remain exactly unchanged. If the picture cannot be translated without becoming a different figure, return `status: "abort"`, a non-empty `abort_reason`, and `tikz: ""`; the tool will fail closed rather than print English wording. Otherwise return `status: "ok"`, `abort_reason: ""`, and the complete `tikzpicture`.
