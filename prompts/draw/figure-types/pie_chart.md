## Drawing a pie chart

**One circle, cut into sectors, one sector per class.** The whole circle is the
total, each sector's central angle is the share, and nothing else is in the
figure: no axes, no grid, no frame around it, no key beside it.

**Draw each sector as a wedge and let the wedges make the rim.** A line to the
rim, an arc along it, and back to the middle:

    \draw [piesector] (0,0) -- (90:3.4)
      arc [start angle=90, end angle=36, radius=3.4] -- cycle;    % 54 degrees

`piesector` carries the fine black outline and the pale blue fill that every
sector in the book is drawn with — the same pale blue the bars of a histogram
are filled with, so that the two types are plainly from one book. Every sector
takes it, they all take the same one, and you write nothing beside it: no
`fill`, no `blue`, no `gray`, no `thin`, no `line width`. The colour is not
telling the reader which sector is which — the radii already do that — so do not
shade one sector differently from another, and do not pick one out.

Do not draw a circle over the top of the finished wedges. Their outer edges
already meet all the way round, and a circle on top of them prints that rim at
twice the weight of the radii inside it.

**Start at twelve o'clock and work clockwise**, taking the classes in the order
the description lists them, so the reader follows them round in the same order
they are met in the table. Clockwise means the angle counts down, and each
sector begins where the last one ended:

    90 to 36        % first class, 54 degrees
    36 to -90       % second, 126 degrees
    -90 to -198     % third, 108 degrees
    -198 to -270    % fourth, 72 degrees, closing the circle

Add the angles up before you start: they come to 360, and if they do not, you
have misread one.

**Draw the circle at a radius of 3.4 cm, in every pie chart.** They are all the
same figure at the same size in the book, whatever their sectors happen to be.
The size is what it is because everything a sector carries is set inside it at
the paper's 12pt, and that type does not shrink to fit a smaller circle: a
sector has to hold its class name and its angle mark, one clear of the other and
both clear of the rim, and 3.4 cm is where that stops being a squeeze. Do not
scale it up to fill the width and do not shrink it to fit a long label in.

**Label every sector with its class**, on the line that halves the sector, 2.65
out from the middle — near the rim, with the space nearer the middle left for
the angle mark. It is set as text and never in `$...$`, because the dash in
`20-` is a hyphen holding a class together and inside maths it comes out as a
minus sign:

    \node at (63:2.65) {20-};       % right
    \node at (63:2.65) {$20-$};     % wrong, prints a minus

**Write an angle on a sector exactly where the description gives you one, and
nowhere else.** Where a description gives three of the four angles, the fourth
is what the student works out by subtracting from 360, and printing it hands
over the exercise. That sector still gets its class label; it simply gets no
number and no arc.

**Where the description gives no angle for a sector at all**, you must still
draw it at some size. Share out what is left of the 360 degrees between the
sectors that have none, in uneven, round amounts — four equal quarters is not
what data looks like, and neither is a split that lands on tenths of a degree —
and mark none of them, with neither a number nor an arc. Only the angles the
description states are ever printed, and only those get the mark.

**An angle that is written is marked with an arc across its sector**, swept
about the middle of the circle from one arm of the sector to the other, with an
arrowhead at each end pointing at the arm it reaches. The angle itself sits in a
break in the middle of that arc.

So it is two arcs and a node, all at a radius of 1.5, and the break is 17
degrees either side of the line that halves the sector. Each arc is drawn
outwards, starting at the edge of the break and ending on the arm, so that the
head — which `anglearc` puts at the end of the path — lands where it is meant
to. For the first sector above, running from 90 down to 36 and halved at 63:

    \draw [anglearc] (80:1.5) arc [start angle=80, end angle=90, radius=1.5];
    \draw [anglearc] (46:1.5) arc [start angle=46, end angle=36, radius=1.5];
    \node at (63:1.5) {$54^\circ$};

`anglearc` carries the weight and the arrowhead. Write nothing beside it: no
`<->`, no `->`, no `Stealth`, no `line width`, no `thin`. An angle is a number
and is set as maths, with `^\circ`.

**The break in the arc is not optional.** A sector's angle mark, repeated round
a chart where every angle is written, closes up into a small circle about the
middle, and the reader sees a ring drawn inside the pie rather than four angles
marked. The numbers standing in the breaks are what keeps them four separate
marks. Never draw the arc unbroken, and never move the number out of the break
to sit beside the class name instead: on a sector whose halving line runs near
the horizontal, an angle and a class name on the same line print side by side
and read as one thing — `70-` and `100^\circ` become `70-100°`, which looks like
a class.

**A sector under about 45 degrees has no room for the mark**, since the break
alone would take the whole of it. Such a sector is labelled outside the rim
instead, in a single node beyond the end of it, with the class above and the
angle below, and nothing at all is drawn or written inside it:

    \node [align=center] at (63:3.9) {20-\\ $54^\circ$};

Do the same for a sector too narrow to hold its class name, whether or not it
has an angle. But do not run a leader line out to a label, and do not gather the
classes into a key at the side: a pie chart is read by looking at a sector and
reading what is written on it.

**Nothing else is written on the chart.** No title, no total in the middle, no
percentages beside the angles, no frequencies, and no marking of whichever
sector the question is about — unless the description asks for it in so many
words. The exercise's own wording is never copied into the figure.

**`pgf-pie` is not loaded.** Draw the wedges yourself, as above.
