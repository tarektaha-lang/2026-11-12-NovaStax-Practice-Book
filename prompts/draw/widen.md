## This figure is being drawn again, to a wider measure

Everything above still holds except the width, which has changed and is now
**{max_width}**. The figure is the same figure and the rules are the same rules.

### What you drew, at {old_width}

```
{tikz}
```

It came out **{old_shape}** — {reason}

### Why it is being drawn again

The labels are set at the size of the wording beside them, whatever the picture
does. So a figure the column has flattened is not merely small: the type stays
put while the geometry is squeezed between it, until the drawing is mostly
lettering with a few short lines among it. The way out of that is not smaller
type, which would be unreadable, but a wider measure, which is what you have now
been given.

### What to do with it

**Use the measure.** This is the whole point of drawing it again, and the one way
to get it wrong is to return the same picture under a larger limit. The width
above is not a ceiling to stay clear of — the figure should come close to it, and
a drawing that ends up much narrower than {max_width} has not been redrawn so much
as copied out. Scale the geometry up; leave the type alone.

**Draw the same figure.** Same shape, same points, same labels, same lettering.
What changes is how much room it takes, not what it shows. This is not an
opportunity to reconsider the figure.

**Let it get taller too.** Growing only sideways would flatten it further, which
is the fault being fixed. Keep the proportions the geometry actually has and let
the whole drawing grow.

**Nothing is set in points.** Scale by the coordinates, not by shifting labels
about to suit the new size. A figure whose points are right places its own labels.

{siblings}

**Return the whole picture.** The `tikz` field holds one complete
`\begin{tikzpicture}` … `\end{tikzpicture}`, as it did the first time — not a
diff, not a fragment, and not a note about what you changed.

Abort only if the description turns out not to describe a drawable figure. A
figure that was too flat at the narrow measure is not a reason to abort.
