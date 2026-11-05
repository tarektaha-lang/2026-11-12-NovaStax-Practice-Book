## Drawing an ascending cumulative frequency figure

**Two quite different figures are filed under this heading, and the description
tells you which one you are drawing.**

Some of these exercises hand the student a table and ask them to *draw* the curve.
The figure printed beside such an exercise is the empty grid they draw it on:
axes, scales, names, gridlines, and nothing else. Its description says so — "no
curve is plotted", "grid lines are visible without any curve", "without any
plotted curve".

The rest print a curve that is already drawn, and ask the student to read
something off it — a median, a percentage, how many fall below a value. Their
descriptions list the points and say they are joined by a smooth ascending curve.

**Draw no curve on an empty grid.** This is the one way to ruin these figures, and
it is an inviting mistake, because the exercise beside the grid almost always
contains the very table the curve would be plotted from — and working out the
running totals and drawing the curve is the exercise. A curve on that grid prints
the answer next to the question. Where the description says no curve is plotted,
the figure is finished when the grid is drawn: no points, no dots, no line, and no
dashed readings-off either.

### The grid and the axes

**Every one of these figures is drawn on a grid.** Not only the ones whose
description happens to mention one: a cumulative frequency curve is read by
tracing a value across to the curve and down, and that is what the grid is for,
so a curve printed on bare axes cannot be used for the thing it is printed for.
Where the description says only "coordinate axes" or "coordinate plane" and says
nothing about a grid, rule the grid anyway.

A grid is one line at every labelled value on each axis, ruled the whole way
across and the whole way up, so the plotting area is a lattice of equal cells. Use
the `gridline` style, which carries the weight and the grey for you, and rule the
axes over the top of it with `axisline`:

    \draw [gridline] (0,0) grid [xstep=1.2, ystep=0.55] (6,4.4);
    \draw [axisline] (0,0) -- (6.3,0);      % the axes, over the grid
    \draw [axisline] (0,0) -- (0,4.7);
    \draw [axisline] (1.2,0) -- (1.2,-0.15); % a tick, ruled like its axis

Set no colour and no weight of your own on any of them, and give every tick mark
`axisline` as well, so the ticks match the axis they stand on. The grid and the
frame are the parts of these figures that are identical from page to page, and
they stay that way only if nobody writes their own grey or their own weight in.

**Scale each axis exactly as the description scales it.** "From 0 to 40 in steps
of 5" means a tick, a number and a gridline at 0, 5, 10, … , 40, and the axis
stops at 40. Do not round the top up to a friendlier number, do not add ticks the
description does not ask for, and do not leave any out because they crowd.

**The vertical axis stands at the first value on the horizontal scale, not at
zero.** Where the scale runs from 30 to 80, the axis is drawn at 30 and the grid
starts there. Only where the description's own scale begins at 0 does the figure
have an origin.

**Name both axes with the words the description gives them**, set as text, and
always in sentence case — `Upper limits of mass (kg)`, `Ascending cumulative
frequency`, `Study hours`. Where the description capitalises the words like a
heading, `Ascending Cumulative Frequency`, set it in sentence case anyway. These
figures print together and their axes should read alike; the description is
telling you which words to use, not which letters to capitalise. The horizontal name goes centred below the row of numbers; the
vertical name is turned through 90 degrees and set to the left of its numbers:

    \node [rotate=90, anchor=south] at (-1.1,2.2) {Ascending cumulative frequency};

**Mind how long that vertical name is.** "Ascending cumulative frequency" set
turned on its side is a tall piece of type, and where the plotting area is shorter
than the words, the label becomes the tallest thing in the picture and the chart
sits in the middle of a column of white. Draw the plotting area tall enough to
carry its own axis name.

### The curve, where there is one

**The curve is drawn in `curveblue`**, which is the one colour in the book and is
provided for you. The dots on it take the same colour, because they are part of
the curve and not a second thing marked on top of it:

    \draw [curveblue] plot [smooth] coordinates {(0,0) (1.2,0.6) (2.4,1.8) (3.6,3.9)};
    \fill [curveblue] (2.4,1.8) circle (1.6pt);

Everything else on the figure stays as it is — black axes, black numbers, black
names, the grey `gridline` grid. The blue is what separates the reading from the
paper it is read off, so it goes on the curve and nowhere else.

**Plot exactly the points the description lists** and join them with one smooth
curve.

**Mark each plotted point** with a small filled dot of radius `1.6pt`, so the
reader can see what the curve was drawn through. That size is the same on every
figure whatever the picture is scaled to, so do not scale it up on a large chart
or down on a crowded one.

**The curve only ever climbs.** A cumulative total cannot fall, so the curve must
not dip anywhere, not even slightly between two points. Smoothing can sag below a
point where the values change pace sharply; where it does, ease the smoothing or
join the points with straight segments rather than print a curve that falls.

**It begins on the horizontal axis.** An ascending cumulative frequency curve
starts at a height of nothing — the first point the description gives is almost
always at frequency 0 — and rises to the total at the last upper limit. Plot the
first point where the description puts it and do not extend the curve behind it.

**Rule nothing else on the figure.** No dashed line dropped from the curve to find
a median, no arrow, no reading marked off, no shaded area under the curve, unless
the description asks for it in so many words. What the student is asked to find is
never shown.
