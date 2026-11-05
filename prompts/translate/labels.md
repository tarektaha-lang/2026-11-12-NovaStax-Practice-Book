## Task: translate objective labels

You are given one section's English objective labels (`id` + `label`), the Arabic spec, and the
book glossary derived from the Arabic textbook pages. Use
the glossary's terms exactly; the glossary outranks an otherwise reasonable synonym.

Return the same objective ids in the same order, each with an Arabic label:

- At most six space-separated words.
- A noun phrase or masdar phrase, never a full sentence, never a question, never a closing full stop.
- No `$`, no `\\`, no mathematical notation: name a symbol in Arabic words.
- No heading repeated for two objectives.

If the labels cannot be named that way, return `status` `"abort"` and say why. Otherwise `status` `"ok"` and `objectives` as `{id, label}` rows.
