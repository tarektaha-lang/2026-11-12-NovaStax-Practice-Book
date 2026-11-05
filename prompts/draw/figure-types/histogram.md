## Drawing a histogram

**The bars touch.** This is the whole of what makes a histogram a histogram and
not a bar chart: the horizontal axis carries a continuous scale, the classes are
the stretches it is cut into, and each bar fills its class exactly. Adjacent bars
share an edge and there is no gap anywhere along the row — not between two bars,
and not between the first bar and the vertical axis.

**Draw it as rectangles on axes you draw yourself.** Two `\draw`s for the axes and
one rectangle per class is the whole figure, and it leaves you holding every
number and every label:

    \draw [histobar] (0,0) rectangle (2,1.5);   % one class
    \draw [histobar] (2,0) rectangle (4,3.5);   % the next, sharing an edge
    \draw [axisline] (0,0) -- (10.5,0);         % the horizontal axis
    \draw [axisline] (0,0) -- (0,6.5);          % the vertical axis
    \draw [axisline] (2,0) -- (2,-0.15);        % a tick, ruled like its axis

Draw the bars first and the axes over the top of them, so the axes are unbroken
where the first bar stands against them.

Both styles are provided and carry the weights and the colours for you. `histobar`
is the fine black outline and the pale blue fill that every bar in the book is
drawn with, and `axisline` is the heavier black the axes and their tick marks are
ruled in. Write nothing beside either — no `fill`, no `gray`, no `black!30`, no
`thin`, no `line width`. Every tick mark takes `axisline` too, so that the ticks
and the axis they stand on match.

**A histogram carries no grid.** The bars are read against the frequency scale by
their tops, and a lattice ruled behind them would be hidden by the fill wherever a
bar stands and visible only in the gaps above, which reads as a mistake rather
than as a scale.

PGFPlots is available if you would rather, but it is not the easier road here: its
own tick and label handling will fight the widths and the class notation below.

**The vertical axis stands at the first class boundary, not at zero.** Where the
classes run 100-, 150-, 200-, 250-300, the scale along the bottom begins at 100
and the axis is drawn there, with the first bar rising straight off it. Do not
stretch the axis back to zero to reach an origin: it would put a long empty run of
nothing in front of the figure and squeeze the bars into the far end of the width.

**Mark the horizontal axis at every class boundary.** Five classes have six
boundaries, and each is a tick with its number under it, because the bar edges are
the values the student reads the figure by.

Where the description names the classes in the form `20-`, `30-`, `40-`, `50-60`
— the lower boundary with a trailing dash, the last class written out closed —
that is the notation the paper uses and it is the class that is being named, not
the boundary. Set each such label centred under its own bar. Where instead the
description marks the axis with plain numbers, put plain numbers at the
boundaries. Follow whichever the description gives.

**A class label is set as text, never in `$...$`.** The dash in `20-` and in
`50-60` is a hyphen holding a class together, not a minus sign, and inside maths
it comes out as one — `$50-60$` prints as fifty minus sixty, spaced as a sum:

    \node [below] at (3.75,-0.1) {200-};        % right
    \node [below] at (5.25,-0.1) {250-300};     % right
    \node [below] at (3.75,-0.1) {$200-$};      % wrong, prints a minus sign

Plain boundary numbers with no dash in them are numbers and are set as maths in
the ordinary way.

If the description's list of labels cannot be squared with the bars it then
describes, the bars are what the figure is: draw the classes the bar heights are
given for, and label those.

**The vertical axis is frequency, and it starts at zero.** Tick it in the
increments the description gives — `from 0 to 12 in increments of 2` means ticks
and numbers at 0, 2, 4, 6, 8, 10, 12 — and carry it to the top of that range, which
must be at least the tallest bar. Where the description gives no increments,
choose a round step that numbers the axis in about five or six ticks.

**Name both axes** with the words the description gives them — `Scores`, `Hours`,
`Frequency`, `Marks`. The horizontal name is set centred under the row of boundary
numbers, and the vertical name is set turned through 90 degrees to the left of its
numbers, running up the axis:

    \node [rotate=90, anchor=south] at (-1.1,3.2) {Frequency};

Set both names in sentence case, whatever case the description writes them in.

**Where the description names no axis at all**, take the name from the exercise's
own wording rather than inventing one. A description that says only "intervals on
the horizontal axis: 20-, 30-, 40-, 50-60" beside an exercise about "the masses in
kilograms of 40 packages" is a chart whose horizontal axis is `Mass (kg)`, and one
beside "monthly electricity bills in Egyptian pounds" is `Bill (EGP)`. Name the
quantity being measured, with its unit in brackets. Never fall back on a word that
names the axis's own machinery — `Intervals`, `Classes`, `Values`, `Data` — which
tells the reader nothing the axis has not already told them.

**A histogram is a wide figure**, and the classes and their numbers need room
along the bottom. Give the drawing most of the width it is allowed rather than
setting a small chart in the middle of the measure, and keep it low enough that
the bars are read as a shape and not as a row of towers: something near half as
tall as it is wide is what these look like in the book.

**Where the description draws the mode construction on top of the histogram**,
draw it too, in black. It is a pair of segments that cross inside the tallest bar,
and it is always these two:

- from the **top-left** corner of the modal bar, across to the **top-left** corner
  of the bar **after** it;
- from the **top-right** corner of the modal bar, back to the **top-right** corner
  of the bar **before** it.

Both segments therefore slope, and they meet somewhere inside the modal bar. The
value under that meeting point is the mode, which is the whole reason the
construction is drawn: two segments that do not cross give the student nothing to
read.

**If either segment comes out vertical, you have joined the wrong pair of
corners.** Each one runs from one class to the next, so its ends are always a
class apart along the bottom. A segment whose two ends share an x is the left or
the right edge of the modal bar redrawn, and it is not part of the construction.
Check this before you finish: two sloping segments crossing, never two uprights.

Where the description names the four corners, label the points as it names them —
but take the segments from the shape above and not from the pairs of letters the
description happens to put together, which are sometimes the wrong two. The
corners it names will be the four ends; joining them correctly is yours.

Those segments are a construction laid over the data rather than part of it, so
they are drawn in black at the ordinary weight, and they read as something added
precisely because the bars beneath them are blue.

**No legend and no readings marked off.** No key beside the chart, no line ruled
across from the top of a bar to the frequency axis, no value written above a bar,
unless the description asks for it in so many words. The numbers on the axes are
what the chart is read by, and what the student is asked to find is never shown.
