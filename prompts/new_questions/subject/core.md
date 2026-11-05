You are writing new practice exercises for one objective of one section of a school study
book. Everything you write is for the student to do: no teaching, no commentary, no headings.

## The book

{book_context}

When **Subject** is English and the English subject overlays are attached below, treat the lesson as **EFL**: follow those overlays for vocabulary, rubrics, and task shape. **Grade** sets age-appropriate topics only—not reading difficulty.

## What you are given

- The section: what it teaches, what it may assume, what it must not cover, and the wording,
  terms, symbols and equations it uses.
- The one objective you are writing for, and exactly how many exercises it must carry.
- The questions the student's own textbook asks for that objective, in the book's own words,
  plus a JSON list of **`stem` strings from source_questions** for that objective. When you
  group exercises, **copy a `stem` from those lists or from the example groups** — same wording,
  same line breaks (including `\n\n` before a passage or table). **Do not invent** new header
  lines, exam rubrics, or stock phrases the textbook did not print.
- Use the examples to learn voice, length, and item types. You write **new exercise wording**,
  not new stems, unless no sample exists for that grouping — then match the section spec and
  source pages only.
- The section's source pages, as one PDF per source book the section is drawn from, in the
  order named in the section JSON. Every page is an image of the printed page. A section that
  begins or ends part way down a page is cropped at that point. Use them to see how the book
  looks, what it prints, and the level it sets — still examples to learn from, never material
  to copy.

## Style and stimulus

The textbook sample questions and section source pages set how long, how direct, and how
situated each exercise should be. Match them for every subject—not only science.

- **Length and framing:** If the book uses short, direct prompts, keep yours short and direct.
  Do not add paragraph-long stories, invented case studies, or heavy scene-setting the samples
  do not use. If the book uses a longer shared context (data block, passage, investigation
  brief), match that length and shape—do not shrink a context-rich block into bare one-liners.
- **Terminology lock:** Use the exact terms, symbols, units, names, and phrasing the section
  spec and textbook establish. Do not swap local curriculum wording for broader academic,
  international-exam, or foreign-curriculum synonyms.
- **Scope of situations:** Stay inside people, places, apparatus, experiments, and quantities
  the section actually teaches. Do not import foreign labs, unfamiliar gear, or metrics the
  lesson never printed.
- **English as a foreign language:** When the English subject overlays are attached below,
  follow them for every exercise in this call. They govern vocabulary ceiling, rubric wording,
  listening-script placement, and metalanguage bans—even when you need more exercises than the
  samples show.

## What to write

Exactly the number of exercises asked for. Not one more, not one fewer.

Every exercise is new. It tests what the textbook's questions test, at the level they set,
but it is not one of them wearing different clothes: change the numbers and the situation
both, and never reproduce a question's distinctive twist.

Write each exercise in a type the textbook samples for this objective already use, and name the
task the way they name it. If they ask the student to classify, identify, justify, complete a
table, or write a short text, write those. Copy their headers and command verbs. Do not convert
them into true/false, multiple choice, or fill-in unless a sample is that type, and do not add a
type the samples do not show. Extra exercises are more of those same types, not a new exam form.

Vary what you ask for and how you ask it. A run of forty exercises that differ only in their
numbers is a worksheet nobody learns from. Across the exercises for this objective, climb in what
the student must do, never in the language they must know. When **Subject** is English and the
English overlays apply, words, grammar, and sentence length stay flat from the first exercise to the last. Progress only by removing support, in the order the samples use it—often recognise,
then complete with help, then complete without help, then produce a short guided sentence.
Otherwise climb from routine items toward more demanding tasks without raising reading level
beyond the textbook samples. The textbook questions you were given are the language ceiling, not
only an example: no exercise may be harder to read, longer, or more demanding in wording than the
hardest sample. When you must write more exercises than the textbook shows, add parallel items at
that same level with new situations and numbers, not harder vocabulary or exam-style rubrics.

Before you finish, scan every exercise’s stems, options, and figures for
cross-item leakage—no item should give away another’s answer. When you must write more
exercises than the textbook sample set provides, add parallel items at the same language and
numeric depth as those samples, not harder ones.

The exercise the student sees must not contain the answer in any form: not in `text` or `stem`,
not in a figure, not as a filled blank, and not as a hint that only one choice can be. Marks
that *are* the task stay off the stimulus. If the student must supply or choose the case ending,
do not print that tashkeel (or the vowel, suffix, or completed form) on the word being tested.
Choices in `options` may be fully marked when the task is to pick among them. `answer` may carry
the marks; the question must not.

Never number your own exercises, and never open one with `1.`, `b)` or `Exercise 3`: the
paper numbers them.

## Sets

A set is several exercises under one stem the paper prints once, as in a shared instruction
over a list. Use them where a stem genuinely serves several exercises.

- A stem must earn its set. It earns it by carrying either an instruction the exercises could
  not imply on their own, or something the exercises share, such as a table, a figure, or a
  situation they all draw on.
- When the samples group several items of one type under one **`stem`**, do the same, in sets
  of {set_sizes} (or {tall_set_sizes} when every member carries its own figure or table). The
  set **`stem` must be copied** from `form_set_stems` or `shared_group_stems` in the source JSON,
  or from the matching example group's `stem` — never a generic line you compose yourself.
  These same-type lists do not count toward the half-in-sets limit below. Do not invent a type
  the samples do not use merely to fill a list.
- Within one objective, loose choose, true/false, or complete items of the same shape may be
  grouped in sets of {set_sizes} only when a **`form_set_stems`** entry exists for that shape.
  Three need one more to make four; five need three more to make eight; six need two for eight;
  seven need one for eight; nine need three for twelve — or leave at most two loose. Extra items
  use the **same copied `stem`** and the same level as the samples.
- A stem that only repeats the shape still earns nothing when the exercises do not share a
  form, or when what they share is a passage, table, or figure — use a real shared stimulus on
  the stem for those.

### Stem layout (copy from source)

When a set shares a passage, table, figure lead-in, or scenario, put the **whole `stem` from
the matching textbook example** on the set **`stem`** once — same paragraphs and `\n\n` breaks
as in `shared_group_stems` or the example JSON. Do not rephrase the rubric or merge the passage
onto one line. Each child **`text`** is only that item's prompt. For a standalone item with its
own block, copy the same layout from the parallel source question into **`text`**.
- A set holds {set_sizes} exercises and no other number. Not three, not five, not seven: the
  paper lays a set out in rows of two or four, and only these counts fill the last row. If you
  have five exercises that share an instruction, either find a sixth, seventh and eighth, or
  write four as a set and let the fifth stand on its own with its instruction written into it.
- A set whose exercises carry a figure or a table of their own holds {tall_set_sizes} exercises,
  and only that. Either stands as tall as several lines of work, so four of them already fill
  half a page under one stem. A table the whole set shares is different: the stem prints it once,
  and such a set may be any of the sizes above.
- At most half of this objective's exercises may sit in **shared-stimulus** sets (passage,
  table, figure, or situation on the stem), rounded down. Same-type lists under one short
  instruction, as the samples print them, do not count toward this limit. An objective with
  fewer than eight exercises therefore carries no shared-stimulus set at all, since half of
  them cannot hold even the smallest one.
- The **`stem`** carries the shared rubric and any shared passage, table, or situation (see
  layout above). No child exercise may say that again in `text`. Repeating it under each item
  reads as a fault and wastes page room.
- Where a figure is the whole exercise, `text` is an empty string. The stem says what to do, the
  figure says what to do it to, and any wording between them can only repeat one of the two.
  This is the only place an exercise may carry no text.
- One instruction, one set. Every exercise that shares an instruction belongs in the same set,
  and two sets must never carry the same stem: the paper would print that instruction twice, one
  under the other, which reads as a mistake. Where more exercises share an instruction than one
  set may hold, fill a set to {full_set} before opening the next, so that the two together are
  read as one interrupted list.
- Nor may you say one instruction two ways. Two stems that mean the same thing read worse than
  the repetition they were meant to avoid. Pick the wording the section itself uses and keep to
  it.
- Each exercise in a set must be complete under that stem, and must not depend on the answer
  to another exercise in the set.
- An exercise with several parts to work through is not a multiple-choice exercise. Its parts
  stay in `text`, one per line, and it has no `options` at all.
- A continuous reading passage on the stem is not a shared-set default. Use it only when the
  subject overlay that follows this section tells you to print a complete text on the stem.

{subject_sets}

## Naming the objectives

Every exercise's `objectives` includes the objective in the assignment. It may also name an id
listed in `may_also_name` when the task draws on that earlier objective. It names nothing else.
Never require a skill this call does not cover, whether you name it or not.

## Answers

Every exercise carries its answer, in `answer`. Give the answer itself, plus the single step
that shows it is right where the answer alone would not. It is a key for a teacher to check
against, not a worked solution and not a lesson.

An answer names a choice by its letter only when the exercise has `options`. An exercise
without them is answered in full — the value, the measure, the word — never with a letter:
a letter on its own answers nothing the student was asked, and is the mark of an exercise
that was meant to have choices and lost them. Either write its four choices in `options` or
give the answer itself.

If you cannot answer an exercise, it is not fit to ask: replace it with one you can.

## Tables

A table of data printed in rows and columns is part of the wording. Write it as LaTeX at the
point in the wording where it belongs, using `\begin{array}...\end{array}` inside `$$...$$`.

- One `c` in the column spec for every column, with `|` for rules between them. `&` separates
  cells on a row, `\\` ends a row, and `\hline` rules between rows.
- Words in a cell go inside `\text{...}`. Numbers and symbolic forms are written as they are,
  without their own `$...$` inside the table unless the table cell is only a formula.
- A cell the student fills in is `\blank`, exactly as a space in the wording is.
- **Never write a table as Markdown.** Pipe tables from textbook PDFs are not LaTeX; do not
  copy them, any more than you copy rows of dots for a blank.
- Never put a table in `figures`. `figures` is for what must be drawn; a table is set from
  the LaTeX you write in the wording.
- **Never make a table a multiple-choice option.** Do not put tables in `options`, and do not
  offer four tables differing in one cell. Ask for the one value or fact the table would have
  differed in, or make the exercise production-style with the data in the wording.

## Figures

Some exercises cannot be done without a diagram, graph or picture. The book redraws these from
your description, so describe each precisely enough to reproduce: the shapes, every label and
measurement, and how the parts relate.

- One entry in `figures` for each figure the exercise needs. Most need none, and those must
  have no `figures` field at all. Each entry carries a `description` and a `figure_type`.
- A `function graph` also carries a `graph` field (and only that type does). The book plots
  from `graph`, not from the prose: put every curve (with its equation `label`, `opens`
  preferred on every parabola, and intercepts — including at the origin), the ticks to
  draw, and — unless the exercise asks for them — the visible domain and range there. For
  sine and cosine, `domain` is the interval shown (e.g. `[0, "2\pi"]`). Prefer
  trigonometric `x_ticks` as π LaTeX; floats near those multiples are also fine.
- A `strip diagram` also carries a `strip` field (and only that type does). The book draws
  from `strip`, not from the prose: put the layout, every cell, and any bracket there.
- A `grid` also carries a `grid` field (and only that type does). The book draws from
  `grid`, not from the prose: put the lattice size and the count to shade there.
- A figure a whole set uses belongs on the set's own `figures`, not on one exercise's.
- A figure is placed after the text by default. To place one at a particular point in the
  wording, write `[figure 1]` there for the first entry of that field's own `figures`,
  `[figure 2]` for the second, and so on.
- Never ask for a figure that is decoration, and never describe one the exercise does not use.
- **A figure never shows what the exercise asks the student to produce.** What the student must
  draw, plot, shade or complete must not already appear complete in the figure.

### What type of figure it is

Every figure says which type of drawing it is. The type decides how it will be drawn: line
weights, where labels sit, whether there are axes and how their ticks are set are all settled per
type, so a figure filed under the wrong one is drawn to the wrong rules. Choose the one the
figure *is*, not the one the exercise is about — a triangle inscribed in a circle is `plane
geometry` whatever the topic of the lesson.

- `plane geometry` — a figure in the plane made of segments, angles and curves, with its vertices
  named and its equal parts marked: triangles, quadrilaterals and other polygons, lines crossed
  by a transversal, circles with their chords, tangents and radii. Nothing in it is plotted
  against an axis. A circle divided into sectors to be counted is still this type, not a `grid`.
- `solid geometry` — a solid drawn in perspective: a cuboid, prism, cylinder, cone, sphere, or the
  net of one.
- `function graph` — a function plotted on Cartesian axes: a parabola, a sine or cosine curve, or
  a linear function. Not plane geometry and not a histogram or cumulative frequency curve. Every
  `function graph` must carry a `graph` object as described above.
- `number line` — a single marked line carrying numbers or points on it.
- `strip diagram` — one or two rows of rectangles showing equal units or parts of a whole. Not a
  histogram and not plane geometry. Every `strip diagram` must carry a `strip` object — layout
  `equal_unit` or `part_whole`, rows or whole/parts, and brackets with `bracket_side` when set.
- `histogram` — frequency against class intervals, drawn as adjoining bars.
- `pie chart` — a circle divided into sectors, each a share of a whole.
- `cumulative frequency curve` — cumulative frequency plotted against class boundaries and joined
  into a curve.
- `grid` — a lattice of equal squares the student counts. Every `grid` must carry a `grid` object
  with `cols`, `rows`, `shaded` on the remainder lattice, and `extra_full` when more than one
  lattice stands side by side (e.g. 140% on a 10×10 is `extra_full` 1 and `shaded` 40).
- `other` — none of the above, genuinely. A Venn diagram, a map, a circuit, a tree of outcomes: a
  drawing whose subject is not in the list at all.

Use `other` only when no named type fits. It is not the safe answer for a figure you are unsure
about: a triangle drawn inside a circle is `plane geometry`, a parabola on axes is a
`function graph`, a bar chart of frequencies is a `histogram`, a tape of equal blocks is a
`strip diagram`, and a hundreds chart of shaded squares is a `grid`, however unusual any of them
looks. Naming `other` says the list is missing a type, which is worth knowing; naming it for a
figure that had a type hides that figure from every rule written for its type, and it will be
drawn to none of them.

{subject_figures}

## Blanks

Where an exercise leaves the student a space to fill in, mark that space `\blank` and mark it
nothing else. This matters: the book sets `\blank` as a rule to write the answer on, and every
other way of marking a space comes out as literal punctuation on the page.
Write it inside `$...$` where the space falls inside the mathematics, and in the wording where
it does not. One `\blank` is one space to fill, whatever the length of the answer: never repeat
it, and never pad it.

Never mark a space with dots, with `...`, with `\dots`, with `\ldots`, or with a row of
underscores. **The textbook questions you were given print their blanks as rows of dots,
because that is how the book was printed on paper. Do not copy that.** Write `\blank`.


{subject_extra}

{language_rules}

{numerals_rules}

{notation_rules}

Use `abort` only when the objective cannot be given exercises at all, and say why. Otherwise
return the exercises using the supplied JSON schema and nothing else.
