## Your picture prints one thing on top of another

Everything above still holds — the same figure, the same width, the same rules.
The picture compiled and the geometry is right. What is wrong with it is where
things landed: something is sitting on top of something else, and on the printed
page the two are read through each other.

### What you drew

```
{tikz}
```

### What collides

This was measured, not judged. Each label was rendered on its own and its ink
compared with the ink of the drawing and of every other label, so what follows is
where the marks actually fall, not an opinion about how the figure looks.
Positions are millimetres from the top left corner of the figure.

{clashes}

### The picture, with the collisions marked

An image is attached. It is your figure as it prints, with the colliding ink
picked out in red and a dotted box round every label involved: red where the
label overlaps something, amber where it is close enough to read as though it
does. Use it to see which label is meant — it is quicker and surer than working
back from the measurements.

### What to do with it

**Move the label, not the geometry.** The shape is right and the exercise depends
on it. A drawing that had not used its column has already been grown to fill it;
what is left is placement. A vertex label that lands on a side belongs on the far
side of that vertex, away from the interior; a length that lands on the line it
measures belongs clearly to one side of it; a label caught between two lines
belongs outside them both. A `\lengthmark` clash is the command's to place
(raise the offset, slide pos, flip away-from). Two `\anglemark` measures
in one face are not: the command only leaves a wedge when that wedge is
full, not when the neighbour is in the way. Keep both on the bisector
when they do not meet. `$80^\circ$` and `$110^\circ$` that sit clear stay
in; do not copy `[outside]` from the other polygon. If they meet — same
vertex, **or** two vertices of one face — write `[outside]` on
**whichever** has a free exterior slot, empty paper away from the
interior, not onto a length (`\anglemark[outside]{D}{A}{B}{...}`);
leave the other on its bisector. Do not send both out. The rules above
still hold.

For a `\lengthmark` that still hits ink: keep it sloped and raise the offset
on **that call only** — `\lengthmark[0.55cm][sloped]{...}` — the **first**
optional is centimetres of gap, the third is pos along the segment. Do not
write `[0.55]` as pos when you mean to raise the gap. Leave every
length that was already clear on the default gap. If a horizontal reading is
what clears that mark, drop the slope on that call only. If the midpoint sits
on a vertex, slide along the segment with the third optional (`[0.35]` toward
the first end) instead of leaving the command. A **whole** length that reads
as a piece because a point sits on it — AC with D, or transversal DF with
E — switch **that call** to `\lengthmark[][arrows]{...}` (tick-ended bar,
no heads, not a raw `\draw`). Crossing medians through G stay sloped,
outside the triangle; slide `[0.35]` off G — do not switch those to
`[arrows]`. Two marks on one short stroke that still meet: flip **one**
away-from so they sit on opposite faces — `\lengthmark[0.55cm][sloped]{A}{G}{C}{$(3x-2)$ cm}`
and `\lengthmark[0.55cm][sloped][0.65]{G}{D}{B}{$(x+3)$ cm}` — and stay sloped.
A centroid letter that meets a length **changes side** (`\node [left] at (G) {$G$}`
when $(x+3)$ is on the right); do not raise the length into the letter. A slanted
transversal piece (BD, FH) that was drawn `[upright]` goes back to
`[sloped]`. A facing length parked in a face (BC or YZ next to the
angles): flip the away-from to a point **inside** the polygon —
`\lengthmark[][sloped]{B}{C}{A}{$(2x-1)\text{ cm}$}` and
`\lengthmark[][sloped]{Y}{Z}{X}{$9\text{ cm}$}` — so the type sits
outside, toward $L$. $L$ is the mirror, not a side. Two angle measures
that still meet — same vertex **or** two vertices of one face: `[outside]`
on **whichever** has a free exterior slot, away from the interior. Do not
copy it onto the other polygon. Do not always pick the longer type. Do not
send both out. Do not move the facing lengths. A near-vertical DE whose measure sits on a $30^\circ$: the
third argument is the point to sit away from, so
`\lengthmark[][upright]{D}{E}{C}{$4\text{ cm}$}` puts it in the larger
face. $(y+2)$ under XY that was slid off centre: drop the pos, stop $L$
at the midpoint of XY, and centre the measure under the whole base.
A named parallel ($l_1$, $l_2$, $l_3$) that collides stays on the **left**
end of that line with its neighbours; do not send only one name to the
right. A **word** on a marked point — a place name, a landmark — that still
hits the axes, a tick, or another name: stop sliding `above`/`right`. Keep
the filled dot and switch **that label** to `\wordpin{angle}{x}{y}{text}`
(the type sits in empty paper, a pointer back at the point). Angle is TikZ
degrees, 0 right, 90 above; pick a diagonal into empty paper, not along an
axis. Default length is 2.2cm; `\wordpin[2.8cm]{150}{0}{0}{المدخل الرئيسي}`
raises it on that call only if ticks still sit under the type.
Do not pin ticks (`$\x$`, `$\seen$`) or vertex letters. Do not enlarge the
figure, and do not redraw the arc. Reach for the geometry only when no
placement of the label can work.

**Say where the label goes.** Prefer TikZ's own placements — `above`,
`below left`, `anchor=south west` — and the `pos` of a label on a path. For
`\anglemark` or `\lengthmark`, do not add a shift unless the measure has landed
on a line the figure has drawn. A stated placement survives the figure being
redrawn; a shift on anything else is a guess that happens to work once.

**Do not make the type smaller.** Shrinking a label to squeeze it into a gap is
the one fix that is always wrong here: the labels are set at the size the exercise
beside them is set at, and a smaller one is simply a label the student cannot
read. Where two labels cannot both fit where they want to be, part what they name
or draw the shape larger, up to the width you were given.

**Do not delete a label to resolve a collision.** Every label the description
calls for has to survive. If two of them collide, both still have to be there
afterwards, somewhere else.

**Mind the width.** Moving a label outwards makes the picture wider, and the
width above is still the width. Check what you have moved still fits.

**Return the whole picture.** The `tikz` field holds one complete
`\begin{tikzpicture}` … `\end{tikzpicture}`, as it did the first time — not a
diff, not a fragment, and not a note about what you changed.

Abort only if the description turns out not to describe a drawable figure. A
figure whose labels collide is not a reason to abort; it is a reason to place
them better.
