## Relabelling a function graph

**The picture is already plotted from the figure's `graph` field.** Axes, ticks,
curves and intercepts are settled. Do not redraw the axes, do not resample a
curve, and do not add or drop a plotted point.

**Move labels, not geometry.** If a tick number or a curve label sits on a
stroke or on another label, shift that `\node` only. Keep every `\draw` and
every plot coordinate exactly as it is.

**Keep mathematics as mathematics.** A percent in a label is `$40\%$`, never a
bare `%` inside `$...$`.
