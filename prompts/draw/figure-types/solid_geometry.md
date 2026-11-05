## Drawing a solid

**A solid is drawn obliquely, not in true perspective.** The face nearest the
reader is drawn as its true shape — a rectangle stays a rectangle, with square
corners — and the depth runs off it up and to the right at a shallow angle,
shortened. Nothing converges on a vanishing point. It is the projection every
textbook draws a box in, and it is the only one that lets a reader see that
opposite edges are equal.

**A cuboid is two rectangles and four joins.** The near face, the far face
shifted by the depth, and an edge between each pair of corners:

    \coordinate (a) at (0,0);        \coordinate (b) at (3.5,0);
    \coordinate (c) at (3.5,2.1);    \coordinate (d) at (0,2.1);
    \coordinate (e) at (1.05,0.8);   \coordinate (f) at (4.55,0.8);
    \coordinate (g) at (4.55,2.9);   \coordinate (h) at (1.05,2.9);
    \draw [hiddenedge] (a) -- (e);  \draw [hiddenedge] (e) -- (f);
    \draw [hiddenedge] (e) -- (h);
    \draw (a) -- (b) -- (c) -- (d) -- cycle;
    \draw (b) -- (f) -- (g) -- (c);
    \draw (d) -- (h) -- (g);

Use that shift — 1.05 across and 0.8 up — on every solid you draw, so that two
boxes on facing pages are seen from the same place. Draw the hidden edges first
and the visible ones over them.

**The three edges behind the solid are dashed; the nine you can see are not.**
With the far face shifted up and to the right, the corner you cannot see is the
back lower left, and the three edges meeting there are the dashed ones — no
others. `hiddenedge` carries both the break and the weight, and nothing is
written beside it: no `dashed`, no `thin`, no `line width`, no `gray`. Every
other edge is an ordinary black `\draw` with nothing on it at all.

Drawing all twelve edges solid is the one thing that must not happen. A box with
no dashes is a hexagon with three lines in the middle, and the reader cannot
tell which corner is nearest.

**Nothing is filled and nothing is coloured.** A solid is geometry, not data:
black lines on white paper, no shading of the top face to suggest light, no tint
on the front. The one colour in the book belongs to the charts.

**Do not draw the solid to the sizes on its labels.** They are letters — `x`,
`x + 4`, `a^{2}b` — and have no length until the exercise is solved, which is
what the reader is being asked to do. Draw a well-proportioned box at the sizes
above and hang the labels on it. Even where the labels are plain numbers, a
cuboid 40 cm by 2 cm would be undrawable at any useful size: keep the drawn
proportions sensible and let the labels say what the sizes are.

**Which edge gets which label** follows the words, not the algebra. The length
is the long edge across the front, the height is the upright, and the width or
depth is the one running back. So a description that gives "length `a^{2}b`,
width `ab`, height `b^{2}`" puts `a^{2}b` under the bottom of the near face,
`b^{2}` beside its left-hand edge, and `ab` beside the short edge going back
from the bottom right corner — the visible one, never the dashed one.

    \node [below=2pt] at (1.75,0) {$a^{2}b$ cm};              % the length
    \node [left=2pt] at (0,1.05) {$b^{2}$ cm};                % the height
    \node [below right=0pt and 1pt] at (4.03,0.4) {$ab$ cm};  % the depth

**Every label goes outside the outline of the whole solid**, and the outline is
not the near face. A box drawn as above occupies everything from `(0,0)` up to
`(4.55,2.9)`, so the paper immediately to the right of the near face's right-hand
edge is the right-hand face of the box, with the depth edges running across it,
and a label set there lands on them. The height therefore goes on the left,
where there is nothing behind it, and the depth label sits below and outside the
edge it names rather than above it, where the box is.

**A unit is set upright, outside the maths.** `$ab$ cm`, with the letters
italic and the centimetres not, and a space between them. Never `$ab cm$`,
which sets the unit in italics as though c and m were two more unknowns.

**Nothing else is drawn.** No axes, no arrows along the edges, no right-angle
marks at the corners, no diagonal across a face, no volume written on the
figure. Where the description names the vertices, letter them; where it does
not, leave them unlettered.
