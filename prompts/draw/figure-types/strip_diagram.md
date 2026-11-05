## Relabelling a strip diagram

**The picture is already plotted from the figure's `strip` field.** Rectangles,
splits and brackets are settled. Do not redraw them, do not add a second row,
and do not turn the strip into a pie chart or a histogram.

**Move labels, not geometry.** If a `\node` sits on a rule or on another label,
shift that node only. Keep every `\draw` coordinate exactly as it is.

**Percent labels stay escaped.** A cell that shows forty percent is `$40\%$`.
Never write a bare `%` inside `$...$`: TeX reads it as a comment and the picture
does not compile.
