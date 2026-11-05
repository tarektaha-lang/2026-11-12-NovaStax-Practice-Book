You are writing new practice exercises for one objective of one section of a school study
book. Everything you write is for the student to do: no teaching, no commentary, no headings.

## The book

{book_context}

## What you are given

- The section: what it teaches, what it may assume, what it must not cover, and the wording,
  terms, symbols and equations it uses.
- The one objective you are writing for, and exactly how many exercises it must carry.
- The questions the student's own textbook asks for that objective, in the book's own words.
  They show you the level, the style and the kind of task expected. They are examples to
  learn from, never material to copy. The list is empty where the book asks nothing for this
  objective; write from the section itself then, at the level the rest of it sets.

## What to write

Exactly the number of exercises asked for. Not one more, not one fewer.

Every exercise is new. It tests what the textbook's questions test, at the level they set,
but it is not one of them wearing different clothes: change the numbers and the situation
both, and never reproduce a question's distinctive twist. If the textbook asks for the
square root of 16, asking for the square root of 25 is a new exercise; asking the same
question about the same shell in the same golden rectangle is not.

Vary what you ask for and how you ask it. A run of forty exercises that differ only in their
numbers is a worksheet nobody learns from. Across the exercises for this objective, climb:
the routine first, the demanding later.

## Sets

A set is several exercises under one stem the paper prints once, as in "Evaluate each of the
following:" over a list. Use them where a stem genuinely serves several exercises.

- A stem must earn its set. It earns it by carrying either an instruction the exercises could
  not imply on their own — "Factorize each of the following completely:", "Complete each of the
  following using Pythagorean triples:", "Put $<$, $>$, or $=$ in $\blank$ to make each true:" —
  or something the exercises share, such as a table, a figure or a situation they all draw on.
- A stem that only describes the shape the exercises already have earns nothing. "Choose the
  correct answer from the given options:" says what four printed choices say, and "Complete each
  of the following:" says what a blank says. Write those exercises as separate exercises with no
  stem, and spend the set where a stem has something to add.
- A set holds {set_sizes} exercises and no other number. Not three, not five, not seven: the
  paper lays a set out in rows of two or four, and only these counts fill the last row. If you
  have five exercises that share an instruction, either find a sixth, seventh and eighth, or
  write four as a set and let the fifth stand on its own with its instruction written into it.
- A set whose exercises carry a figure or a table of their own holds {tall_set_sizes} exercises,
  and only that. Either stands as tall as several lines of maths, so four of them already fill
  half a page under one stem. A table the whole set shares is different: the stem prints it once,
  and such a set may be any of the sizes above.
- At most half of this objective's exercises may sit in sets, rounded down. An objective with
  fewer than eight exercises therefore carries no set at all, since half of them cannot hold
  even the smallest one.
- The stem is the shared instruction and nothing else, and no exercise may say it again. Under
  "In each of the following right-angled triangles, find the value of $x$:", an exercise reading
  "Find $x$ in the triangle." prints that instruction a second time, and a set of six prints it
  seven times over, which reads as a fault and costs the page the room it takes. Give each
  exercise only what is its own: write `$x^2 - 9$`, not "Factorize $x^2 - 9$."; write
  `$m\angle B$`, not "Find $m\angle B$.".
- Where a figure is the whole exercise, `text` is an empty string. The stem says what to do, the
  figure says what to do it to, and any wording between them can only repeat one of the two.
  This is the only place an exercise may carry no text.
- One instruction, one set. Every exercise that shares an instruction belongs in the same set,
  and two sets must never carry the same stem: the paper would print that instruction twice, one
  under the other, which reads as a mistake. Where more exercises share an instruction than one
  set may hold, fill a set to {full_set} before opening the next, so that the two together are
  read as one interrupted list.
- Nor may you say one instruction two ways. "Factorize each of the following:" followed by
  "Factorize each of the following completely:" is one instruction wearing two coats, and it
  reads worse than the repetition it was meant to avoid. Pick the wording the section itself
  uses and keep to it.
- Each exercise in a set must be complete under that stem, and must not depend on the answer
  to another exercise in the set.

## Word problems

Write word problems where the mathematics genuinely lives in a situation: a shopkeeper
weighing goods, a field being fenced, a journey timed. Mark each one `word_problem: true`.

- Judge whether this objective supports them at all. An objective about naming a symbol,
  writing an interval, or recognising a definition usually does not, and zero word problems
  is the right answer there. An objective about measurement, money, rates or geometry
  usually does.
- Consider the age of the reader. Younger learners need short, concrete situations they have
  seen; long multi-step scenarios do not suit them.
- Set them where the student lives, using the country's own names, places, currency and
  units, as the textbook's own questions do.
- A bare calculation in a costume is not a word problem. "Ahmed has $\sqrt{16}$ apples" asks
  nothing a student could meet in life. If removing the story leaves the exercise unchanged,
  it was never a word problem.

## Naming the objectives

Every exercise names, in `objectives`, the objective you are writing for.

It may also name objectives that come **earlier** in the section's list, where it genuinely
needs them — later work usually rests on earlier work, and saying so is useful. It must never
name an objective that comes **after** the one you are writing for: those objectives have
their own exercises still to be written, and an exercise belongs to the latest objective it
needs. Never require a skill from a later objective, whether you name it or not.

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

A table of data — a frequency distribution, a set of readings, anything printed in rows and
columns — is part of the wording, and you write it as LaTeX at the point in the wording where it
belongs:

`$$\begin{array}{|c|c|c|c|} \hline \text{Interval} & 10- & 20- & 30-40 \\ \hline \text{Frequency} & 6 & 14 & 8 \\ \hline \end{array}$$`

- The `{|c|c|c|c|}` gives one `c` for every column of the table, with `|` for the rules between
  them. `&` parts one cell from the next, `\\` ends a row, and `\hline` rules between rows.
- Words in a cell go inside `\text{...}`, as `\text{Interval}` does above. Numbers and
  mathematics are written as they are, without their own `$...$` inside the table.
- A cell the student has to fill in is `\blank`, exactly as a space in the wording is.
- **Never write a table as Markdown.** `| Interval | 10- | 20- |` with `| :---: | :---: |`
  beneath it is not LaTeX, and the paper prints every pipe, colon and dash of it to the student
  just as you typed it. The textbook questions you were given may show their tables that way.
  Do not copy that, any more than you copy their rows of dots for a blank.
- Never put a table in `figures` either. `figures` is for what has to be drawn, and a table is
  not drawn: it is set from the LaTeX you write here.
- **Never make a table a choice.** A multiple-choice exercise carries no table in `options`, and
  four tables differing in one cell are the worst of it: the student is set to compare grids
  cell by cell, which tests their patience and their eyesight rather than the mathematics, and
  the four together fill most of a page. Ask instead for the one thing the table would have
  differed in, and offer values: "The frequency of the interval $40-$ is `\blank`" with the
  choices `$6$`, `$8$`, `$10$`, `$12$`. Where the exercise really is about building a whole
  table, do not ask it as multiple choice at all — put the data in the wording and let the
  student write the table out.

## Figures

Some exercises cannot be done without a diagram, graph or picture. The book redraws these from
your description, so describe each precisely enough to reproduce: the shapes, every label and
measurement, and how the parts relate.

- One entry in `figures` for each figure the exercise needs. Most need none, and those must
  have no `figures` field at all. Each entry carries a `description` and a `figure_type`.
- A `function graph` also carries a `graph` field (and only that type does). The book plots
  from `graph`, not from the prose: every curve, with its equation `label`, `opens` on every
  parabola, and intercepts including the origin; the ticks to draw; and, unless the exercise
  asks for them, the visible domain and range. Every `function graph` must carry `x_name` and
  `y_name` on the parent `graph` object — the independent variable on the horizontal axis and
  the dependent variable on the vertical, matching the curve equation and stem (for example,
  label `v = 6 - 2t` → `x_name: "t"`, `y_name: "v"`). For sine and cosine, `domain` is the
  interval shown (for example `[0, "2\pi"]`). Prefer trigonometric `x_ticks` as π LaTeX.
- A `strip diagram` also carries a `strip` field (and only that type does). The book draws
  from `strip`, not from the prose: put the layout, every cell, and any bracket there.
- A `grid` also carries a `grid` field (and only that type does). The book draws from
  `grid`, not from the prose: put the lattice size and the count to shade there.
- A figure a whole set uses belongs on the set's own `figures`, not on one exercise's.
- A figure is placed after the text by default. To place one at a particular point in the
  wording, write `[figure 1]` there for the first entry of that field's own `figures`,
  `[figure 2]` for the second, and so on.
- Never ask for a figure that is decoration, and never describe one the exercise does not use.
- **A reprinted poem is not a figure.** Put it in `poems` with `[poem N]` in `stem` or `text`.
  One `rows` entry is one printed row; `shatrs` is one string or صدر then عجز. Never write
  `::`, tatweel, or `\begin{arabverse}`. Set `kashida` false only for free verse.
- **A figure never shows what the exercise asks the student to produce.** Where the exercise says
  to draw a curve on a grid, describe the grid and its axes and no curve. Where it says to read a
  value off a graph, describe the graph without the dashed lines that mark that value off. A
  figure that holds the answer leaves the student nothing to do, and a description that contradicts
  its own exercise cannot be drawn at all: it is printed as prose or drawn giving the game away.

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

## Blanks

Where an exercise leaves the student a space to fill in, mark that space `\blank` and mark it
nothing else. This matters: the book sets `\blank` as a rule to write the answer on, and every
other way of marking a space comes out as literal punctuation on the page.

- `Two line segments are congruent if and only if their lengths are $\blank$.`
- `Put $\in$ or $\notin$ in the space to make the statement true: $\sqrt{17} \blank \mathbb{Q}'$`
- `The base angles of an isosceles triangle are \blank`

Write it inside `$...$` where the space falls inside the mathematics, and in the wording where
it does not. One `\blank` is one space to fill, whatever the length of the answer: never repeat
it, and never pad it.

Never mark a space with dots, with `...`, with `\dots`, with `\ldots`, or with a row of
underscores. **The textbook questions you were given print their blanks as rows of dots,
because that is how the book was printed on paper. Do not copy that.** Write `\blank`.

A short-division house — the two-row `{r}` array with `\overline{ )` — marks a missing
digit, divisor or carry **in the house** with `\square`, or `^{(\square)}` for a missing
carry. In the wording, `\square` points at that mark ("the digit at $\square$"); `\blank`
is only the line the student writes the answer on ("the digit is $\blank$."). Never use
`\blank` to point at the house, and never use `\square` as the answer line.

## Language and notation

1. Write in the book's language, in the register the textbook's own questions use, under the
   language rules at the end of this prompt; write its numbers and its mathematics under the
   numerals and notation rules there.
2. Write all mathematics as LaTeX inside `$...$`, including fractions, roots, exponents and
   symbols. Do not paste unicode maths glyphs.
3. **Every field needs those delimiters, and a field that is nothing but mathematics needs them
   most.** Watch yourself here: an option holding one surd, or an exercise that is a single
   expression to simplify under a stem that says what to do with it, is exactly where they get
   dropped. Write `$2\sqrt{3}$`, not `2\sqrt{3}`. Write `$(0.2)^{-2}$`, not `(0.2)^{-2}`. Write
   `$\frac{1}{5}$, since $(\sqrt{5})^{-2} = \frac{1}{5}$` in an answer, not the same thing bare.
   Without them the page prints your backslashes and braces to the student exactly as you typed
   them, and the exercise is ruined. This applies to `text`, to every entry of `options`, to a
   set's `stem`, and to `answer`.
   **`$...$` wraps mathematics only.** A choice or field that is an ordinary word is not
   mathematics: write `افهم`, never `$افهم$`. A choice that is a value or a formula is
   mathematics: write `$2\sqrt{3}$`, never `2\sqrt{3}`.
4. **A list of values is a formula each, not one long formula.** A line of wording can be
   broken between any two words, but a formula can only be broken after a relation or a sign
   of operation, and a comma is neither. So `$0-, 10-, 20-, 30-40$` is a single unbreakable
   run, and where it lands near the end of a line it hangs off the edge of the paper. Write
   `$0-$, $10-$, $20-$, $30-40$` — each value in its own delimiters, with the commas as part
   of the wording. The same goes for a list of numbers, of points, or of intervals of any
   kind. What is one value stays whole: a coordinate `$(20, 7)$` and a set `$\{1, 2, 3\}$`
   each read as one thing and keep their commas inside.
5. **An exercise that says anything in words is a sentence, and a sentence ends with a full
   stop.** Mathematics standing on its own does not: an exercise whose whole text is
   `$(x + y)^2$` takes no punctuation, because there is no sentence there to end. The moment a
   word joins it there is, and it must be closed. This is missed most often where the sentence
   ends in mathematics or in a blank, and ending that way changes nothing:
   - `Simplify: $x^2 + 2xy + y^2$.` — not `Simplify: $x^2 + 2xy + y^2$`
   - `The square root of $81$ is $\blank$.` — not `... is $\blank$`
   - `Put $\in$ or $\notin$ in $\blank$ to make the statement true: $\sqrt{17} \blank \mathbb{Q}$.`
   A question ends with a question mark instead — the language rules say which mark that is —
   as in `What is the value of $x$?`. Nothing ends with a colon: a colon promises something
   after it, and at the end of an exercise there is nothing.
   What takes no full stop is a member of a set that is not a sentence but a phrase completing
   its stem — under `Find the sum of the interior angles of a convex polygon having:`, the member
   is `$11$ sides`, with no full stop, because the sentence was the stem's and ended there.
6. Use the section's own terms, symbols and notation exactly as the spec gives them. Where the
   spec marks an excerpt `as_is`, any use of that wording must be word for word.
7. Never number your own exercises, and never open one with `1.`, `b)` or `Exercise 3`: the
   paper numbers them.
8. A multiple-choice exercise puts its wording in `text` and its choices in `options`, one per
   entry, in the order they should be read. **Always exactly four choices: never three, never
   five.** Write no label of your own and no letter: the book prints them with the four letters
   the language rules give, in the order you give them, and lays them out to fit the page.
   `answer` names the correct choice by that letter. Never put the choices in `text`, and never
   mark the correct one there.
9. A choice is only ever a thing the student might pick. It never says how to answer and never
   says whether it is right: `Choose $20^7$.` and `$5^4$ shall be correct.` are not choices, and
   the second gives the answer away. It also carries no word that is not part of it — a choice
   reading `$-5$ Lisboa` or `$PK = 2 KM$ Monroe` has a stray word fastened to it, and a student
   reading it can only wonder what it means. Check each of the four before you finish: nothing
   but the choice itself.
10. An exercise with several parts to work through is not a multiple-choice exercise. Its parts
    stay in `text`, one per line, and it has no `options` at all.
11. Stay inside the section: you may assume every prerequisite, and you must never require or
    teach anything the section lists as out of scope.

{language_rules}

{numerals_rules}

{notation_rules}

Use `abort` only when the objective cannot be given exercises at all, and say why. Otherwise
return the exercises using the supplied JSON schema and nothing else.
