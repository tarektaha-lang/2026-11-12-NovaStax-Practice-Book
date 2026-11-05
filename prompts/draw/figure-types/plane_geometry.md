## Drawing a plane geometry figure

**Place the points from the lengths and the angles the description names, and
from those `answer` states.** A generic triangle with numbers hung on it is the
wrong picture, even when every label is present. Put every stated length into one coordinate unit and every
stated angle in as a direction or a TikZ angle. A triangle ABC with AB = 6,
AC = 8 and angle A = 50° is three coordinates, then the sides:

    \coordinate (A) at (0,0);
    \coordinate (B) at (6,0);
    \coordinate (C) at (50:8);
    \draw (A) -- (B) -- (C) -- cycle;

A length or angle that appears only in `answer` is placed the same way, and is
not labelled with that value. Do not recompute it.

The same holds for a quadrilateral, parallels cut by transversals, and congruent
figures. `scale=` shrinks coordinates only: `\anglemark` radii and `\lengthmark`
offsets are real centimetres and do not follow it, so a scaled-down shape with
the default mark dumps every measure in the middle, and two of them then print
on top of each other. Do not `scale=` far below 1 to fit centimetre-true
coordinates. Shrink the unit so the shape and the marks share a size —
`\coordinate (C) at (0, 4.8)` for a 12 cm side, not `scale=0.45` on a 12-unit
side. A skinny triangle or rhombus that is much taller than wide uses a
small unit so the height stays beside the stem:

    \coordinate (Z) at (0, 3);
    \coordinate (C) at (4, 5);

never $Z$ at $(0,6)$ or $(0,7.75)$, and never $C$ at $(11.5, 27)$ or
$(4.3, 10)$ with `x=0.22cm`. An 18 cm median on a 12 cm base still
uses $C$ near `(4, 5)`, not a 10-unit stem. Do not encode centimetres
as coordinates and shrink them twice. A perpendicular bisector $L$ of a base XY stops at the midpoint
of XY and goes up through $Z$ — not through the measure under the base.
Centre $(y+2)$ under the whole of XY, no pos slide:

    \draw (0, 0) -- (0, 3.5) node[above] {$L$};
    \lengthmark[][upright]{X}{Y}{Z}{$(y+2)\text{ cm}$}

**When the description puts the figure on squared paper, the shape is drawn on
the lattice.** A triangle of base 6 units and height 4 is not a free sketch
with a grey grid dropped behind it: every vertex sits on a crossing, every
horizontal or vertical side lies on a grid line, and a stated length in units
is that many cells. The student counts squares; a side that spans 6 units but
4.2 cells is the wrong figure.

Rule the paper first, with `gridline` and `step=1` — the unit of the
coordinates, not a centimetre. The cells are square, so `x` and `y` are the
same. One empty cell of grid stands beyond every vertex, so the shape does
not sit on the outer frame, and a vertex letter has that cell to occupy,
outside the interior:

    \draw [gridline] (0,0) grid [step=1] (8,6);
    \coordinate (A) at (1,1);
    \coordinate (B) at (7,1);
    \coordinate (C) at (9,5);
    \coordinate (D) at (3,5);
    \draw (A) -- (B) -- (C) -- (D) -- cycle;

That is a parallelogram of base 6 and height 4. A at (1,1) is one cell in
from the origin; C at (9,5) is one cell in from (8,6). Do not write
`\draw [gridline] (0,0) grid (8,6)`: a bare `grid` steps 1cm, and the moment
`x=` is not 1cm the grey lines miss the vertices. Do not copy
`xstep=0.98, ystep=0.62` from a chart, and do not write `step=1cm`.

Every vertex is an integer coordinate. No vertex in a cell, and no vertex on
a grid line between two crossings: `(3.5, 2)` is neither. A horizontal side
is a segment of a horizontal grid line and a vertical side of a vertical
one — they cover the grey, they do not run beside it at `y=1.2`. An oblique
side runs crossing to crossing: a slant of 2 along and 3 up is
`(x,y) -- (x+2,y+3)`, not a free angle.

**Construct transformations from one source shape.** Where figures are
congruent, or one is the image of another under translation, rotation, or
reflection, draw one and copy it with the stated transform. Do not independently
sketch corresponding figures. Leave a clear gap between the copies, enough that
a vertex of the source and a vertex of the image never share a label slot. A
length or angle that names a part of the image belongs on the image edge, not on
the source and not in the strip between them. Corresponding lengths on two copies sit **outside** each polygon. $L$ is
the mirror, not a side; the strip toward $L$ is exterior, not a face.
The two $6\text{ cm}$ stay above the tops. BC and YZ sit away from a
point **inside** the polygon, so the type goes toward $L$, not into the
angles:

    \lengthmark[][sloped]{A}{B}{C}{$6\text{ cm}$}
    \lengthmark[][sloped]{B}{C}{A}{$(2x-1)\text{ cm}$}
    \lengthmark[][sloped]{Y}{X}{Z}{$6\text{ cm}$}
    \lengthmark[][sloped]{Y}{Z}{X}{$9\text{ cm}$}

Give the copies enough height that the interiors are not one slot.

**Keep separate transversals separate.** Two transversals that the description
does not say meet are two cuts, not an X. Parallels cut by two transversals
stay as two lines through the parallels; they do not cross between those lines.

**Use the marks a textbook uses.** Single and double ticks for congruent
segments, matching arcs for congruent angles, a small square for a right angle,
arrowheads on parallel lines. Draw ticks and arcs as they appear in a school
book, not as decoration. A rectangle needs one square when a diagonal already
occupies a corner; four squares plus that diagonal collide at the far vertex.
Do not put a parallel arrow and a length on the same stretch of a line.

**Points are labelled outside the shape**, on the side away from its interior.

**An angle that carries a measure is marked with `\anglemark`.** This command is
provided for you. It takes a point on one arm, the vertex, a point on the other
arm, and the measure as it is to be printed:

    \anglemark{A}{B}{C}{$30^\circ$}

Degrees are `^\circ` and nothing else: `{$30^\circ$}`, `$(3x + 10)^\circ$`.
Never `^\text{o}`.

It draws the arc across the angle at B and sets the measure beyond the arc, on the
bisector, still inside the angle. The radius follows the opening. The distance out
to the measure follows the opening too, so two short marks at the same vertex with
the same opening sit at the same distance. Only if the measure will not sit next
to the arc inside the opening — a long `$(5y-10)^\circ$` in a sharp apex — is it called out **outside** the shape, beside the vertex,
with a short curved arrow pointing at the arc. `$110^\circ$` that fits its
own wedge stays in. Two angle measures stay **both on their bisectors**
when they do not meet. `$80^\circ$` and `$110^\circ$` on a copy that already
has room stay in — do not copy `[outside]` onto that polygon because the
other copy needed it. If they meet, **one** goes `[outside]` — whichever
has a free exterior slot (empty paper away from the interior, not the
side that already has a length), not always the longer type. The other
stays in. Do not send both out. That is two cases:

Same vertex, two openings facing each other:

    \anglemark{P}{Q}{R}{$80^\circ$}
    \anglemark[outside]{S}{Q}{T}{$(2x+7)^\circ$}

One face, two vertices, and the two boxes meet (A and B, not the same head):

    \anglemark[outside]{D}{A}{B}{$110^\circ$}
    \anglemark{A}{B}{C}{$(3y+5)^\circ$}

A **single** algebra in a roomy opening stays on the bisector. Two marks
that meet — same head or two heads of one face — is not that case: write
`[outside]` on the one that has room outside. Do not grow the arc to fit the type. A measure on an upward acute bisector is then pushed just
past the arc so it does not fall back onto the mark. If it would still hit the
shape, the command moves it until it does not: down when the arc is at the top,
left or right when the arc is on a side. Name the arms in the order that sweeps
the angle you mean — the arc runs from the first arm round to the second — so at a
vertex where two angles meet, `\anglemark{A}{B}{C}` and `\anglemark{C}{B}{D}` mark
one each.

Never place a measure inside the arc, and never draw the arc yourself and put the
measure in a node of your own. Do not pass a radius: two angles with the same
opening are marked the same size, and a radius on only one of them is ignored.
`[outside]` on the first optional parks the measure **away from the
interior** — left of A, not onto AB — with a short curved arrow at the
arc, when two marks would meet inside. Do not
pass `left`, `right`, `anchor=`, `xshift`, or `yshift`: those are ignored,
and they would take the measure off the arc. A left or right arc is placed just
past the mark, not into the opposite side.

None of this touches a right angle, which keeps its small square, or an angle
marked only to show it equal to another, which is arcs and no measure.

**A stated length on a segment is marked with `\lengthmark`.** This command is
provided for you. It takes the two ends of the segment, a point to sit away
from — the line or face that would steal the reading, not a generic interior —
and the measure. Never a `\node at`, never a node on the path, never a raw
`\draw` with `|<->|`. Ticks that only show two segments equal stay ticks.

    \lengthmark[][sloped]{A}{E}{B}{$(x+4)$ cm}

That is AE, parallel to AE, centred on the midpoint, off the ink, on the side
away from B. Write `[][sloped]` first on a diagonal. A near-horizontal or
near-vertical side may be left without `sloped` — the command keeps those
upright — or forced with `[upright]`. The command sets one paper gap (0.22cm)
to the **inner edge** of the type on every side in the figure. `15\text{ cm}`,
`20\text{ cm}` and `$(x+2)\text{ cm}$` sit the same distance off their sides.
Do not write `[0.4cm]` on one and `[0.8cm]` on another for looks.

**If that mark still hits ink, raise the offset on that call only**, still
sloped:

    \lengthmark[0.55cm][sloped]{B}{D}{A}{$(x+2)$ cm}

Leave every length that was already clear on the default gap. Do not push
the whole figure out because one measure collided.

**If a horizontal reading is the one that clears that mark**, drop the slope
on that call only:

    \lengthmark[0.55cm][upright]{B}{D}{A}{$(x+2)$ cm}

A near-vertical side whose sloped type (bottom to top) hits a vertex letter
is this case: `15\text{ cm}` beside AB, upright. A near-vertical midsegment
DE stays upright too; the third argument is the point to sit **away
from**, so `$4\text{ cm}$` left of DE (the larger face, not the $30^\circ$
at C) is away from C:

    \lengthmark[][upright]{D}{E}{C}{$4\text{ cm}$}

Do not drop to `[upright]` on a **slanted transversal** just because the
type is long: $3x-2$ on BD and $x+8$ on FH stay `[sloped]`, centred on
their own stretches, same outer side of $t_2$.

**If the midpoint sits on a vertex or another mark**, slide along the segment
without leaving the command. `0.35` is toward the first end, `0.65` toward
the second:

    \lengthmark[][sloped][0.35]{A}{G}{B}{$14$ cm}

A measure wider than the piece it names — `$(x+2)$ cm` on a short median,
`8\text{ cm}` on a short side — is not sloped. Park it upright outside.

**Use `[arrows]` when a whole length already has a point or ticks on it**
— $26\text{ cm}$ on hypotenuse AC with midpoint D, or $20\text{ cm}$ on
transversal DF with E on it. Type on the ink would be read as a piece.
The command draws a light bar parallel to the named stretch, the same
length, with end ticks and no heads, and the measure on the outer side of
that bar, like $7\text{ cm}$ on XE:

    \lengthmark[][arrows]{A}{C}{B}{$26\text{ cm}$}
    \lengthmark[][arrows]{D}{F}{A}{$20\text{ cm}$}

The bar on DF runs D to F, outside the parallels, type centred on the bar.
Ordinary sides stay without it: $15\text{ cm}$ on AB, $20\text{ cm}$ on BC.
`[0.55cm][arrows]` raises that bar only, same as any other offset. Two
named pieces of one stroke (AG and GD) stay `[sloped]`, never `[arrows]`.
**Crossing medians** — AD and BE through G — are not this case either: no
bar. Each whole median is `[sloped]`, **outside** the triangle; if the
midpoint is G, slide `[0.35]` toward the vertex so the number sits on the
outer half, not on G:

    \lengthmark[][sloped][0.35]{A}{D}{B}{$18\text{ cm}$}
    \lengthmark[][sloped][0.35]{B}{E}{A}{$15\text{ cm}$}

Parallel chevrons on $l_1$, $l_2$, $l_3$ stay; the bar is not a substitute
for them.

A length sits on the segment it names. 12 cm on AE and 6 cm on ED each sit on
their own part of AD — not in a face, and not on a crossing line that could
take the reading.

**Pieces of one supporting line share a side.** A supporting line is the
infinite line the pieces lie on. AB and BC on $t_1$ are two pieces of one
line; DE and EF on $t_2$ are two pieces of another. Every `\lengthmark` on
that line uses the same away-from point, so both measures sit on the same
side of it — both left of $t_1$, or both right; both left of $t_2$, each on
its own stretch. It is not a rule that every length in the picture goes
left: $t_1$ may be labelled on the left and $t_3$ on the right.

Two named pieces of one stroke — AG and GD on a median — are the same
rule, both `[sloped]`, same away-from:

    \lengthmark[][sloped]{A}{G}{C}{$(3x-2)$ cm}
    \lengthmark[][sloped]{G}{D}{C}{$(x+3)$ cm}

If those two still meet, flip **one** away-from so they sit on opposite
faces — AG away from C, GD away from B — and stay sloped. Do not switch
to `[arrows]`:

    \node [left] at (G) {$G$};
    \lengthmark[0.55cm][sloped]{A}{G}{C}{$(3x-2)$ cm}
    \lengthmark[0.55cm][sloped][0.65]{G}{D}{B}{$(x+3)$ cm}

A length that names the whole of a transversal cut by parallels — DF across
$l_1$, $l_2$, $l_3$ — sits **outside** the parallels, on the outer side of that
transversal, as `[arrows]` when a crossing (E) sits on it, so the bar names
the whole of DF. The pieces DE and EF stay on their own pieces. The whole
and the parts never share a side. The same holds for any length that spans
a line another length already names.

Intersection letters on one transversal share the **outer** side, away
from the other transversal: $A$, $B$, $C$ left of $t_1$; $D$, $E$, $F$
right of $t_2$. Do not park $E$ in the strip when $D$ and $F$ sit outside.
If the bar and $E$ share a slot, put $E$ slightly along $l_2$ or raise the
bar offset.

Named parallels — $l_1$, $l_2$, $l_3$ — sit at the **left-hand tips** of
the horizontals, every line on the same end. Lengths stay on the
transversal. Do not move $l_2$ to the right because $20\text{ cm}$ is
there.

A length on one ray of an X — AO, BO, CO, DO — sits on that ray, **outside**
the X, never on the parallel that closes the triangle (AB, CD).

A length of a full side and a length of a part of that side — AB and AE — go
on **opposite** sides of the line.

A sloped length on a median must not sit in the right-angle square at the
vertex, and must not sit on the base. Raise the offset into the larger face
on that call only. Vertex letters stay outside a grid of parallels; length
numbers stay on the transversals. Do not let $B$ and $3x-5$ share a slot.
