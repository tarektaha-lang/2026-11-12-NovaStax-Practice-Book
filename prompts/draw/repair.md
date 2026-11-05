## Your picture did not compile

Everything above still holds — the same figure, the same width, the same rules.
What follows is the picture you returned and what LaTeX said when it was run.
Read the error, find the fault, and return the picture again with the fault put
right.

### What you returned

```
{tikz}
```

### What LaTeX said

```
{error}
```

### What to do with it

**Fix the fault. Do not draw a different figure.** The picture you sent is very
nearly right; what is wrong with it is usually one command, one bracket or one
piece of syntax. Keep the geometry, the labels and the arrangement you chose,
and change what the error points at. A figure redrawn from scratch throws away
the part that was working and tends to arrive with a new fault of its own.

**Read the line the error names.** LaTeX prints `l.<number>` and then the source
it had reached, broken across two lines at the exact point it stopped. What sits
at that break is what it could not read. `! Undefined control sequence` means
the command just before the break does not exist — either it is misspelled, or
it belongs to a package that is not loaded, or it is `calc` syntax written in a
shape `calc` does not accept.

**Where a command is not available, use one that is.** Do not define the missing
command yourself and do not load a package: neither survives, because everything
outside `\begin{tikzpicture}` … `\end{tikzpicture}` is discarded. Draw the thing
with what TikZ and the listed libraries already give you.

**Return the whole picture, not a patch.** The `tikz` field holds one complete
`\begin{tikzpicture}` … `\end{tikzpicture}`, exactly as it did the first time. Do
not send a diff, a fragment, or a note about what you changed.

Abort only if the description turns out not to describe a drawable figure. A
picture that merely failed to compile is not a reason to abort — it is a reason
to fix it.
