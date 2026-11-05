## Your picture still prints one thing on top of another

Everything above still holds — the same figure, the same width, the same rules.
Clash rounds have already moved labels, and some still overlap. The geometry is
right. What is left is a **word** sitting on a marked point, on an axis, on a
tick, or on another name, with nowhere beside the point that is clear.

### What you drew

```
{tikz}
```

### What still collides

This was measured, not judged. Positions are millimetres from the top left
corner of the figure.

{clashes}

### The picture, with the collisions marked

An image is attached. Colliding ink is red; a dotted box marks every label in
a clash.

### What to do with it

**Pin every colliding word, do not move the geometry.** A place name, a
landmark, a caption on a filled dot — keep the dot where it is and replace
**that** `\node … at (x,y) {text}` with `\wordpin{angle}{x}{y}{text}`. The
type sits in empty paper; a light pointer names the point. Angle is TikZ
degrees: 0 right, 90 above, 180 left, 270 below. Pick a diagonal into empty
paper, not along an axis (on a grid, 0/90/180/270 lands the type on ticks).
The default length is 2.2cm; raise it on **that call only** if the name is
still on a tick.

    \fill (0,0) circle (2.5pt);
    \wordpin{150}{0}{0}{المدخل الرئيسي}
    \wordpin[2.8cm]{45}{4}{2}{النافورة}

This is the same last resort as `\lengthmark[][arrows]` on a whole length that
reads as a piece: the command places the type off the ink and points back at
what it names. Do not invent a raw `\draw` arrow for it.

**Change only the colliding word nodes.** Leave `\foreach` ticks, axis letters,
grid, filled dots, and every label that was already clear exactly as they are.
Do not rewrite tick nodes into separate `\node at (\x,0)` calls. Do not pin
ticks or vertex letters: `$\x$`, `$\y$`, `$\seen$`, `$A$` stay as nodes. Do
not pin a `\lengthmark` or `\anglemark`. Do not delete a label. Do not shrink
the type. Do not enlarge the figure past the width you were given.

Return the whole picture.
The `tikz` field holds one complete `\begin{tikzpicture}` … `\end{tikzpicture}`.

Abort only if the description turns out not to describe a drawable figure. A
figure whose labels still collide is not a reason to abort; it is a reason to
pin the words that will not sit.
