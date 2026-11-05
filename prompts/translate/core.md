{book_context}

{profile}

You are translating an English practice-book artefact into Arabic for this book's Arabic sibling. The attached Arabic section PDFs, when present, are a **terminology authority only**: use the book's own Arabic terms exactly as it prints them (الأعداد الحقيقية, and the rest). Do not transcribe questions, passages, or exercises from those PDFs. Do not invent objectives, exercises, figures, or teaching points the English JSON does not already hold.

Preserve every `id`, every `role`, every count and cardinality. Keep JSON keys, ids, `role`, `type`, `reuse`, `figure_type`, `word_problem`, `graph`, `grid` and `strip` numbers exactly as given. Digits in every JSON field stay Western 0–9 with «.» as the decimal point — the paper renders them as this book prints them.

Mathematics follows the notation rules below. Do not treat Latin letters as a universal: latin notation keeps the English letters; arabic notation writes Arabic letters in `$...$` as a native Arabic book does.

Write Modern Standard Arabic in logical order, exactly as it is read. Do not insert direction marks or bidi control characters (U+200E, U+200F, U+202A–U+202E, U+2066–U+2069): the paper is set right to left by the typesetter, and a control character in a field is one it cannot render. Do not append commentary, other scripts, or fullwidth punctuation. Do not put an Arabic mark on a backslash (`\ًfrac`). Tables stay `\begin{array}{|…|}`.

{language_rules}

{numerals_rules}

{notation_rules}

{task}
