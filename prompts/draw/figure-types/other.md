## Drawing the ones that are not a sort

This folder is what is left over: figures that belong to no family and share no
conventions, one of a kind each. What follows is settled for the sorts that have
come up so far. If your description is not one of them, there are no conventions
to keep — draw the plainest figure the words describe, on the general rules
above, and add nothing they do not ask for.

### A Venn diagram of number sets

The universal set is a rectangle and every set inside it is an ellipse. Where
one set contains another — the naturals inside the integers inside the rationals
— the ellipses are nested, not overlapping and not side by side, because that
containment is the whole content of the figure:

    \draw (0,0) rectangle (7.0,4.4);
    \node [below right=3pt and 4pt] at (0,4.4) {$\mathbb{R}$};
    \draw (4.55,2.2) ellipse [x radius=2.15, y radius=1.75];
    \draw (4.75,2.2) ellipse [x radius=1.5,  y radius=1.2];
    \draw (4.95,2.2) ellipse [x radius=0.85, y radius=0.7];
    \node at (2.75,2.2) {$\mathbb{Q}$};
    \node at (3.6,2.2)  {$\mathbb{Z}$};
    \node at (4.95,2.2) {$\mathbb{N}$};
    \node at (1.3,2.2)  {$X$};

The ellipses sit towards one side of the rectangle so that there is open paper
on the other, and their centres shift a little as they shrink, so that each has
room for its own name inside its own ring. Every name is set just inside the
left of the ring it belongs to, the outermost first, and the innermost set takes
the middle. The universal set is named inside its top left corner.

**Do not divide the rectangle with a line.** A description may talk about the
interior in two parts — the region with the ovals in it and the region without —
but those two parts are made by the outermost oval and by nothing else. The
outside part is everything in the rectangle that is not in that oval, which
wraps around it, and a line ruled across the rectangle would cut that region in
half and leave the piece behind the ovals belonging to neither. The exercise is
usually asking what the outside region is, so this is the difference between a
figure whose answer is right and one whose answer is nearly right.

**Nothing is filled, shaded or coloured**, and no region is hatched to pick it
out. A region is named by a letter standing in the middle of it, with plenty of
paper around the letter so it is plainly in that region and not in the ring
beside it.

### The scissors method for factorising

Four terms at the corners of a square arrangement, with two lines crossing
between them. The left-hand column splits the first term of the quadratic, the
right-hand column splits the last, the two lines are the pair of products that
must add to the middle term, and each row read across is one of the factors.

    \node (tl) at (0,1.3)   {$2x$};
    \node (tr) at (2.8,1.3) {$+5$};
    \node (bl) at (0,0)     {$x$};
    \node (br) at (2.8,0)   {$+1$};
    \draw (tl) -- (br);
    \draw (bl) -- (tr);

Draw the lines between the nodes by name, as above, so that TikZ stops each one
at the edge of the type instead of running it through the letters.

Set each term exactly as the description writes it, sign and all — `$+5$` and
`$+1$` carry their plus signs, and a negative term carries its minus.

**Nothing is written along the diagonals.** Not the products, not their sum, not
an equals sign, and no arrows on the lines. A description that goes on to
explain that the products come to the middle term is telling you why the figure
is drawn, not what is printed on it. There is no box round the terms and no rule
between the columns or the rows either: the crossed pair of lines is the whole
of the diagram.
