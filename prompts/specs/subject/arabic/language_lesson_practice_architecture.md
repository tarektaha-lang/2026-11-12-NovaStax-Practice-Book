## Language Lesson Practice Architecture
When this applies, fill spec.language_lesson_practice_architecture with one or more {strand, item_order, stimulus_rule, multi_part, standalone_rule} rows (ids are added on save). Else [].

- `strand` is one of: الاستماع, القراءة, النصوص الشعرية, النحو, الإملاء, التعبير الكتابي, التحدث, الخط.
- `item_order` for listening, reading, poetry, grammar, and spelling: objective items first (true/false, then multiple choice, then matching and other objective forms), then short essay items, then multi-part items.
- `stimulus_rule`: the listening script, the reading passage, or the poem is printed once on the set stem. A standalone item carries no script, no passage, and no verse. Do not change set size.
- `multi_part`: a set is the usual form for reading and poetry. At most one vocabulary child, comprehension children are the majority, and at most two children are higher-order. A listening set does not ask for literary taste or beauty.
- `standalone_rule`: a listening standalone tests general comprehension or a higher skill only, never a meaning or an antonym. A reading or poetry standalone tests general comprehension or a higher skill only — never meaning, antonym, singular, plural, intended expression, main idea, sub idea, or beauty.
