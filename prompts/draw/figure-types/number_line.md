## Drawing a number line

**A rule with an arrowhead at each end, ticked at the whole numbers, numbered
underneath.** The arrowheads are not decoration: they say the line carries on
past the last number printed, which is the difference between a number line and
a measuring stick. Both ends always have one, on every figure, whatever is
marked on the line.

    \draw [numberline] (-5.9,0) -- (5.9,0);
    \draw [axisline] (-3,-0.12) -- (-3,0.12);      % a tick, ruled like the line
    \node [below=1.5pt] at (-3,-0.12) {$-3$};      % its number

`numberline` carries the weight and both arrowheads; `axisline` is the same
black at the same weight, for the ticks. Write nothing beside either — no
`<->`, no `->`, no `Stealth`, no `thick`, no `line width`.

**Tick every whole number the description names, and number every tick.** A
description that says "integer ticks labelled from -4 to 4" means nine ticks,
each with its number under it, and no others. The numbers are maths, set in
`$...$`, so that a minus sign prints as a minus sign and the digits match the
exercise beside them.

**Set the scale so the line fills the measure.** Put the unit into the picture
rather than into every coordinate, and work it out from the ticks: the line runs
from 0.9 before the first tick to 0.9 after the last, so a line ticked from -5 to
5 is 11.8 units long and each unit is the width divided by 11.8.

    \begin{tikzpicture}[x=13.7mm]     % 162mm across 11.8 units

That overshoot at each end is what the arrowheads are drawn into, and it is also
what keeps the first and last numbers from hanging off the edge of the column.
Do not set a `y` unit; the heights below are in centimetres and are the same on
every number line whatever its scale.

**What is marked on the line is drawn in blue, over the black.** Four styles
carry it, and they are the whole vocabulary:

    \draw [intervalmark] (-3,0) -- (2,0);   % a stretch between two ends
    \draw [intervalray] (-1,0) -- (4.9,0);  % a stretch running off to infinity
    \node [endclosed] at (2,0) {};          % an end that is included
    \node [endopen] at (-3,0) {};           % an end that is not

A solid dot means the number belongs to the set and a hollow one means it does
not, and the description says which: "solid", "shaded", "filled" for the first,
"hollow", "unshaded", "open" for the second. Getting these two the wrong way
round changes the answer, so read that word before you draw either.

**A ray drawn along the line takes over that end of it.** It runs the whole way
out, to the same 0.9 past the last tick the line itself would reach, and its
blue head is the only head at that end. So the black is drawn as the part the
ray has not taken — outwards from the ray's endpoint to the opposite end, in
`numberrest`, which heads the end it finishes at and leaves the end it starts
from bare:

    \draw [numberrest] (-1,0) -- (-4.9,0);      % black, from the dot leftwards
    \draw [intervalray] (-1,0) -- (4.9,0);      % blue, from the dot to infinity
    \node [endclosed] at (-1,0) {};

Note that neither of those is the whole line: together they are, and the ticks
and numbers go on top of both.

Both are drawn outwards from the endpoint, away from each other, because both
styles put their head at the end of the path. Do not draw a whole `numberline`
underneath as well: a blue head and a black head one after the other at the same
end print as a single long double arrow, and they are pointing at the same
infinity anyway.

This is only for a ray lying along the line. A ray drawn above it or below it,
as one of two intervals, leaves the line alone: that figure keeps its ordinary
`numberline` with a black head at each end.

**One interval sits on the line; two sit above and below it.** A single interval
or ray is drawn along the rule itself, blue over black, and its dots sit on the
line. Two of them cannot both do that where they overlap, so they are lifted
off: the first named goes above at `y=0.5`, the second below at `y=-0.95`, both
drawn parallel to the rule with their own dots, and the rule between them is
left plain. The lower one is that far down because the numbers are under the
line and it has to clear them.

Where the description says which goes above and which below, follow it. Where it
names two intervals and says nothing, the first named goes above.

**Name what the description names.** Where the intervals are called `M` and `N`,
or `A` and `B`, set the letter as maths above the upper one and below the lower
one, over the middle of its length:

    \node [above=2pt] at (0.5,0.5) {$M$};
    \node [below=2pt] at (2.5,-0.95) {$N$};

**Points marked on the line take a dot and a letter above it.** Where a
description places `A`, `B`, `C`, `D` at positions between the ticks, each is an
`endclosed` dot on the line with its letter set above it, and nothing else — no
segment, no ray:

    \node [endclosed] at (4.3,0) {};
    \node [above=2.5pt] at (4.3,0) {$B$};

Read the position from the words: "between 4 and 5, closer to 4" is about 4.3
and "closer to 5" is about 4.7. Two such points can fall inside one interval
between ticks, and they must still be told apart, which is another reason the
line is drawn as wide as the column allows.

**Where the description says nothing is plotted, plot nothing.** Several of
these figures are an empty rule for the student to mark, and the exercise beside
one of them is asking for the very mark that would be made. The line, its ticks
and its numbers are the whole figure, and no dot, no segment and no letter goes
on it.

**Nothing else is written.** No title, no axis name, no `x` at the right-hand
end, no set-builder notation printed beside the line, no arrow or bracket
notation for the interval. What the figure shows is the marks; what they are
called is the answer.
