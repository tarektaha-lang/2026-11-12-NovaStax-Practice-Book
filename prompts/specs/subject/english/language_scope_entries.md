## Language Scope Entries
When this applies, fill spec.language_scope_entries with one or more {target_vocabulary, target_grammar, functional_phrases, rubric_phrases, notes} rows (ids are added on save). Else [].

- This array is the ceiling for every later text, option, and instruction.
- `target_vocabulary` = every word and fixed expression the section teaches or uses in its texts, tasks, and pictures, copied as printed; earlier-section words only when this section reuses them.
- `target_grammar` = each pattern with one example from the book.
- `functional_phrases` = the question and answer patterns the section models.
- `rubric_phrases` = the task instructions copied exactly.
- One `notes` row states the support words allowed: names, this country's cities, numbers, days, and basic function words. Topic vocabulary is not a support word.
