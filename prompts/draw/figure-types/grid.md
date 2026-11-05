## Drawing a square grid

**A lattice of equal squares, counted by the student.** Hundreds charts, tenths
strips, fifths, a five-by-five — these figures are the squares themselves. The
student reads the answer by counting cells, so every cell must be a whole square,
every corner an integer, and the number of shaded cells must be exactly the
number the description names. A circle cut into sectors is not this type; it is
plane geometry. A chart's background lattice behind axes is not this type either.

**Never shrink the unit.** Do not write `[x=…]`, `[y=…]`, `scale=` as a size
factor (for example `scale=0.4`), or `xstep` / `ystep` on a grid.
`\begin{tikzpicture}` takes no options. A cell is one centimetre, which is
TikZ's own unit, and a line of the lattice is a step of one. Shrinking the unit
to squeeze a ten-by-ten into a narrow column is what makes the grey lines vanish
and the shading miss the squares. The one allowed reflection is Arabic's
`scale=-1` inside a `scope` (see below) — that flips the picture; it does not
change the cell size.

    % wrong — the unit is scaled, so a 10×10 reads as a handful of cells
    \begin{tikzpicture}[x=0.4cm, y=0.4cm]
      \fill [shaded] (0,0) rectangle (3,10);
      \draw [gridline] (0,0) grid (10,10);
    \end{tikzpicture}

    % right (English) — no options on the picture; one unit is one cell
    \begin{tikzpicture}
      \pgfmathsetmacro{\g}{4}
      \pgfmathsetmacro{\a}{1}
      \pgfmathsetmacro{\b}{2}
      \fill [shaded] (0,0)--(\a,0)--(\a,\g)--(0,\g)--cycle;
      \fill [shaded] (\a,0)--(\a+1,0)--(\a+1,\b)--(\a,\b)--cycle;
      \draw [gridline] (0,0) grid (\g,\g);
    \end{tikzpicture}

**The origin of every grid is an integer, almost always `(0,0)`.** Cells are
counted in whole numbers, and a grid that begins at `8.5` cannot be counted
cleanly: the second grid's lines fall on tenths, the last line is written
`15.51` to make it appear, and the shading `11.3` is four cells that no longer
look like four. Each lattice starts at `(0,0)`. When there is more than one
grid, do **not** invent a second origin at `8.5` or a step of `0.7` — use the
`\s` / `\foreach` pattern in “Percentages past a hundred” below.

    % wrong — second grid begins at 8.5, last lines at .01, cells of 0.7
    \fill [shaded] (0,0) rectangle (7,7);
    \draw [gridline] (0,0) grid [xstep=0.7, ystep=0.7] (7.01,7.01);
    \fill [shaded] (8.5,0) rectangle (11.3,7);
    \draw [gridline] (8.5,0) grid [xstep=0.7, ystep=0.7] (15.51,7.01);

**One unit is one cell.** A four-by-four runs from `(0,0)` to `(4,4)`, a
five-by-five to `(5,5)`, a ten-by-ten to `(10,10)`, a strip of ten tenths to
`(10,1)`. Shade in those same units, with integer corners only. Do not write
the same lattice as ten steps of `0.7` out to `7`, and do not nudge the far
corner to `7.01` or `10.01` to force a last line. An integer origin, an
integer far corner and a step of one already draw that line.

### Coding style for shading and the lattice

Name the sizes with `\pgfmathsetmacro` **inside** the picture, then shade with
closed `--cycle` paths, then draw the lattice. Use the book styles only:
`shaded` for the fill and `gridline` for the lattice. Do not write `Orange1`,
`Grey`, `gray`, `thin`, or any other colour or line weight — those are already
carried by the styles.

- `\g` — side length of a **square** grid (cells along one edge). For a
  non-square lattice (for example a \(10\times5\)), use two macros such as
  `\gw` and `\gh` in place of `\g` in the fill height and in the `grid` path.
- `\a` — number of **full** columns shaded on the (remainder) grid.
- `\b` — height of the **partial** next column (cells). Omit `\b` and the
  second fill when every shaded cell on that grid is a full column, or the
  whole grid.
- `\s` — number of **additional fully shaded** grids when the percentage is
  past a hundred. Omit `\s` and the `\foreach` when there is only one grid.

When the count is zero, draw only the lattice. Do not add a separate outer
`\draw … rectangle`; the `grid` already draws the frame.

### The order the cells are filled

Where the description does not say which cells are shaded — only how many —
fill them in a fixed order. The Language section above says whether this book
is Arabic or English; that chooses the corner you start from.

**Arabic.** Visually, start at the **top-right** cell, fill **down**, then move
**left**. Write that with the same path formulas as the English bottom-left
fill, wrapped in `\begin{scope}[scale=-1] … \end{scope}`. The flip turns
bottom-left-up-then-right into top-right-down-then-left on the page. Do not
compute mirrored corners by hand with `\n-\a` and the like — use `\g`, `\a`,
`\b` and `scale=-1`.

    % Arabic — single grid (book styles, not Orange1 / thin Grey)
    \pgfmathsetmacro{\g}{5}
    \pgfmathsetmacro{\a}{4}
    \pgfmathsetmacro{\b}{3}
    \begin{scope}[scale=-1]
      \fill [shaded] (0,0)--(\a,0)--(\a,\g)--(0,\g)--cycle;
      \fill [shaded] (\a,0)--(\a+1,0)--(\a+1,\b)--(\a,\b)--cycle;
      \draw [gridline] (0,0) grid (\g,\g);
    \end{scope}

    % Arabic — 37 of 100: three full columns, then 7 of the next
    \pgfmathsetmacro{\g}{10}
    \pgfmathsetmacro{\a}{3}
    \pgfmathsetmacro{\b}{7}
    \begin{scope}[scale=-1]
      \fill [shaded] (0,0)--(\a,0)--(\a,\g)--(0,\g)--cycle;
      \fill [shaded] (\a,0)--(\a+1,0)--(\a+1,\b)--(\a,\b)--cycle;
      \draw [gridline] (0,0) grid (\g,\g);
    \end{scope}

    % Arabic — 6 of 16 on a four-by-four
    \pgfmathsetmacro{\g}{4}
    \pgfmathsetmacro{\a}{1}
    \pgfmathsetmacro{\b}{2}
    \begin{scope}[scale=-1]
      \fill [shaded] (0,0)--(\a,0)--(\a,\g)--(0,\g)--cycle;
      \fill [shaded] (\a,0)--(\a+1,0)--(\a+1,\b)--(\a,\b)--cycle;
      \draw [gridline] (0,0) grid (\g,\g);
    \end{scope}

**English.** Start at the **bottom-left** cell. Fill **up** that column to the
top. When the column is full, move one column **right** and fill that one from
the bottom up again. Keep going right until the count is met. A partial last
column is filled from the bottom upward only as far as the remaining count
needs. Do **not** use `scale=-1` for English.

    % English — 37 of 100: three full columns on the left, then 7 from the bottom
    \pgfmathsetmacro{\g}{10}
    \pgfmathsetmacro{\a}{3}
    \pgfmathsetmacro{\b}{7}
    \fill [shaded] (0,0)--(\a,0)--(\a,\g)--(0,\g)--cycle;
    \fill [shaded] (\a,0)--(\a+1,0)--(\a+1,\b)--(\a,\b)--cycle;
    \draw [gridline] (0,0) grid (\g,\g);

    % English — 6 of 16 on a four-by-four
    \pgfmathsetmacro{\g}{4}
    \pgfmathsetmacro{\a}{1}
    \pgfmathsetmacro{\b}{2}
    \fill [shaded] (0,0)--(\a,0)--(\a,\g)--(0,\g)--cycle;
    \fill [shaded] (\a,0)--(\a+1,0)--(\a+1,\b)--(\a,\b)--cycle;
    \draw [gridline] (0,0) grid (\g,\g);

Never invent a scattered pattern, a block in the middle, or the other language's
corner, when the description only gives a count. Where the description *does*
name which cells or which region — the top two rows, the left half, a particular
strip — follow the description and ignore the default order. Still use
`\pgfmathsetmacro` and `--cycle` paths with `shaded` (and the Arabic
`scale=-1` scope when the book is Arabic).

**Shade only the cells the description calls shaded**, and shade them with
`shaded`. Fill first, then the lattice, so the lines sit on top of the fill.
Write nothing beside either style — no `Orange1`, no `Grey`, no `thin`, no
`line width`.

Count before you fill. If the description says nine shaded and sixteen
unshaded, those nine are what you fill — not the sixteen, even when the answer
the student is asked for is the unshaded percentage. Filling the complement
prints the answer as the coloured part and leaves the question about the white
part pointing at the wrong region.

### Percentages past a hundred

A description that names two or three hundreds charts — one or more full, plus
a remainder — for a percentage past a hundred is several grids side by side.
It is not one wide lattice. Do not draw a brace, a `%`, or a mixed number under
the picture; the exercise text carries that.

Set `\s` to the number of **fully shaded** hundreds charts beyond the remainder
grid. Draw the remainder first, then loop `\foreach \x in {1,...,\s}` to place
each full grid. The shift
`\pgfmathsetmacro{\y}{((\x-1)*\g)+(2*\x)}` with
`xshift=-\g cm -\y cm` (Arabic, inside `scale=-1`) leaves a two-cell gap
between grids. Do not hand-place a second origin or use a bare `xshift=12cm`
for this case.

The **order of the grids across the page** follows the language, so the full
hundred(s) read first:

**Arabic.** With `scale=-1`, the remainder is drawn at the origin and the
`\foreach` full grids appear on the **right** of the page; the remainder sits
on the **left**. Reading right-to-left, the full hundred comes first. For
125%: `\s=1` (one full grid) and remainder 25. For 240%: `\s=2` and remainder
40 — on the page, from left to right, `[remainder] [full] [full]`.

    % Arabic — remainder + \s full grids (example: \s=3)
    \pgfmathsetmacro{\g}{5}
    \pgfmathsetmacro{\a}{4}
    \pgfmathsetmacro{\b}{3}
    \pgfmathsetmacro{\s}{3}
    \begin{scope}[scale=-1]
      \fill [shaded] (0,0)--(\a,0)--(\a,\g)--(0,\g)--cycle;
      \fill [shaded] (\a,0)--(\a+1,0)--(\a+1,\b)--(\a,\b)--cycle;
      \draw [gridline] (0,0) grid (\g,\g);
      \foreach \x in {1,...,\s}{
        \pgfmathsetmacro{\y}{((\x-1)*\g)+(2*\x)}
        \begin{scope}[xshift=-\g cm -\y cm]
          \fill [shaded] (0,0)--(\g,0)--(\g,\g)--(0,\g)--cycle;
          \draw [gridline] (0,0) grid (\g,\g);
        \end{scope}
      }
    \end{scope}

    % Arabic — 125%: remainder 25, one full hundred (\s=1)
    \pgfmathsetmacro{\g}{10}
    \pgfmathsetmacro{\a}{2}
    \pgfmathsetmacro{\b}{5}
    \pgfmathsetmacro{\s}{1}
    \begin{scope}[scale=-1]
      \fill [shaded] (0,0)--(\a,0)--(\a,\g)--(0,\g)--cycle;
      \fill [shaded] (\a,0)--(\a+1,0)--(\a+1,\b)--(\a,\b)--cycle;
      \draw [gridline] (0,0) grid (\g,\g);
      \foreach \x in {1,...,\s}{
        \pgfmathsetmacro{\y}{((\x-1)*\g)+(2*\x)}
        \begin{scope}[xshift=-\g cm -\y cm]
          \fill [shaded] (0,0)--(\g,0)--(\g,\g)--(0,\g)--cycle;
          \draw [gridline] (0,0) grid (\g,\g);
        \end{scope}
      }
    \end{scope}

**English.** Place every **fully shaded** hundreds chart on the **left**, and
the **remainder** on the **right**. No `scale=-1`. Use `\s` the same way, but
shift full grids in the positive direction and put the remainder after them
(or at an `xshift` past the last full grid). For 125%: one full, then remainder
25. For 240%: two full, then remainder 40 — on the page,
`[full] [full] [remainder]`.

    % English — 125%: \s=1 full hundred left, remainder right
    \pgfmathsetmacro{\g}{10}
    \pgfmathsetmacro{\a}{2}
    \pgfmathsetmacro{\b}{5}
    \pgfmathsetmacro{\s}{1}
    \foreach \x in {1,...,\s}{
      \pgfmathsetmacro{\y}{((\x-1)*\g)+(2*\x)}
      \begin{scope}[xshift=\y cm]
        \fill [shaded] (0,0)--(\g,0)--(\g,\g)--(0,\g)--cycle;
        \draw [gridline] (0,0) grid (\g,\g);
      \end{scope}
    }
    \pgfmathsetmacro{\y}{(\s*\g)+(2*\s)+2}
    \begin{scope}[xshift=\y cm]
      \fill [shaded] (0,0)--(\a,0)--(\a,\g)--(0,\g)--cycle;
      \fill [shaded] (\a,0)--(\a+1,0)--(\a+1,\b)--(\a,\b)--cycle;
      \draw [gridline] (0,0) grid (\g,\g);
    \end{scope}

Never put the Arabic full hundred on the left, or the English full hundred on
the right, when the description is a percentage past a hundred. Within each
grid, keep shading in that language's cell order (Arabic via `scale=-1`;
English from the bottom-left with no flip).

**Shade whole cells**, unless the description explicitly shades a fraction of
one cell. A strip that looks like "a little extra" on the page is almost always
one more full column or one more full cell; do not invent a half cell to make
the ink match a sketch.

**No axes, no numbers on the cells, no count written beside the shading.** The
student's work is the count. A label, a `%`, a `37/100` inside the picture
hands over the exercise. The description's wording is never copied in.

**If the description cannot be drawn as equal squares, it is not this type.**
A circle in quarters, a triangle split by a median, a pentagon — those are
plane geometry, even when some of their regions are shaded.
