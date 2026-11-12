#!/usr/bin/env python3
"""Typeset one section of newly written exercises as an A4 practice paper.

The exercises arrive as a dict written by a model, with the maths already marked
up as $...$ and $$...$$, so the prose around that maths is the only part that may
be escaped. Every field is therefore walked segment by segment: what lies between
the maths is escaped, what lies inside it is handed to TeX untouched.

Most of what follows that is repair work, because a model's LaTeX arrives broken
in a handful of predictable ways: a pair of delimiters with one half missing, a run
of underscores meant as an answer rule sitting where TeX reads a subscript, a
Unicode maths glyph in prose that the text font has no glyph for and that LaTeX
would drop with nothing but a line in its log. A repaired line is worth printing;
a section that will not compile, or that quietly loses a character, is not.

A figure arrives as a description of what should be drawn. Where the drawing
stage has turned that description into TikZ, the picture is set; where it has
not, the description is printed in italics, so a paper can be built at any point
and shows plainly which of its figures are still only words.

The exercises carry their answers, their objective ids and a word-problem flag,
and none of the three is typeset: this is the paper a learner works on, and the
rest stays in the JSON for whatever grades or audits it.

A paper is set for the kind of book it is from — its Profile, read off the table
of contents by the caller. An English book is STIX Two, left to right. An Arabic
book is Noto Naskh Arabic under babel, the page and every macro on it turned round
by the engine, with the few things the engine cannot know — which side of a number
its dot goes, what the choices are lettered, what glyph the heading's square and
rule are cut from, which way a table reads — said once each, in the preamble or on
the way in. The maths is STIX Two Math and reads left to right in both, because
both books write it with Latin letters; an Arabic book with Arabic notation is
refused until the mirrored maths is done.

This is a library. It renders whatever document it is handed and neither reads a
file of its own nor calls a model, so the stage that writes the exercises owns
where they came from and where the PDF lands.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from itertools import groupby
from math import ceil
from pathlib import Path

try:
    from pypdf import PdfReader
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# Which kind of book a paper is for: the language its prose is in, the digits its
# numbers print in, the notation its mathematics is written in. The combinations a book
# may be are prompting.py's to say, since they are what the prompts were written for;
# what this module adds is how each of them is set, which varies in the direction the
# page runs, the face the prose is in, and the small things that follow from those.
from src import arabic_math
from src.arabic_verse import (
    POEM_TOKEN,
    asked_poems,
    format_arabverse,
    marked_poem_indices,
    poem_number,
    replace_poem_marker,
)
from src.print_digits import TIKZ_NODE
from src.prompting import Profile

# The profile everything here was first built for, and the one a caller that says
# nothing gets: the grid page and a figure compiled outside any book are set this way.
ENGLISH = Profile("en", "western", "latin")

# A figure's description, as the writing stage wrote it, mapped to the drawing
# made from it: its `tikz`, and the `width` and `height` in points that the
# picture came out at when it was compiled on its own. The description is the key
# because it is the only thing the drawing was made from, so a paper can be set
# with whatever drawings exist and nothing has to be threaded here from the stage
# that made them. A row with no measurements is still set, only without the space
# on the page being held for it.
Drawings = dict[str, dict]

# lualatex rather than pdflatex, because the maths the model writes is full of
# Unicode and unicode-math needs an OpenType engine to set STIX Two with it.
LATEX_ENGINE = "lualatex"

# Every length in this module is a TeX point, 1/72.27 of an inch, which is the unit
# TeX itself measures in and the only one it will report in: `\the\ht` prints points
# whatever units it was given, and the height of every block the page fitter adds up
# arrives that way. Writing the paper in the same unit is what keeps the arithmetic
# that fills a page free of any conversion at all.
#
# A PDF measures in the other kind of point, 1/72 of an inch, and two places in the
# pipeline are properly in those: the /MediaBox of a section's own PDF, which is PDF
# space by definition, and the size a figure is found to be when it is read back off
# the page it was compiled on. POINTS_PER_BIG converts the second, once, where it is
# read. There is no third place, and adding one would be the mistake this comment
# exists to prevent.
POINTS_PER_INCH = 72.27
MM_PER_INCH = 25.4
POINTS_PER_BIG = POINTS_PER_INCH / 72.0
POINTS_PER_CM = POINTS_PER_INCH / 2.54

# Everything a drawing is allowed to use. Declared once because a drawing is
# compiled twice — on its own while it is being checked, and again inside the
# paper — and a library loaded for only one of those would let a figure pass its
# check and then break the section it belongs to.
# How an angle that carries a measure is marked: the arc across its opening, and the
# measure set beyond the arc on the bisector. The distance is a multiple of the
# radius, which is what the angles library's eccentricity means, and anything under
# one puts the measure inside the wedge — where the library's own default of 0.6 puts
# it, and where the arc is then drawn through the type. Every marked angle in the book
# goes through here so that they are all marked alike.
#
# Radius follows the opening, because a long measure at 30° needs a wider arc than
# the same measure at 120°, and a single 0.7cm dumps the first on its own arms.
# Distance out to the measure follows the opening too — ecc × radius — so two
# short marks at the same vertex sit at the same distance. A leftover [left],
# [anchor=west], xshift, or yshift is ignored: those park the type in the face
# or on a side, off the arc. The inner edge of the box still clears the arc by
# the box's reach along the bisector (|cos|·width/2 + |sin|·height/2) — and
# only for an acute opening. Obtuse and reflex stay on ecc × radius: a 110°
# hexagon corner has already parted, and radius+proj walked $(x+10)^\circ$
# onto the next mark. Obtuse eccentricity is 1.6. A short 125° on a diagonal
# bisector still needs its inner edge past the arc; that clearance is capped
# so $(x+10)^\circ$ does not walk onto the next corner. The extra half-width
# push for an upward acute bisector is the same case: a long measure would
# otherwise fall back onto the mark.
# A page y-shift to sit level with the arc is the same case: acute and up.
# Downward, obtuse, and exterior stay on the bisector. A narrow opening
# (<35°) gets a little extra distance, so 25° at an apex moves down off the
# vertex. If the measure still hits the shape — an arm, the arc, or a tick
# that sticks into the opening — it is moved until the box sits in the wedge
# and its inner edge is past the arc. Air of 0.15cm treats ticks as ink.
# Growth uses the real box reach, not a 0.55cm floor: that floor parked 40°
# far below its arc. A left or right arc shifts only far enough that the
# inner edge is past the arc, not into the opposite side. The escape may run
# out to 2.4cm. Only then, if the box still sticks out of the wedge, is the
# type called out outside the shape with a light hook (ARC_TIP) to the
# midpoint of the arc — $(5y-10)^\circ$ at a sharp apex, not $(2x+10)^\circ$
# in a 70° opening that already holds it. The callout parks opposite the
# bisector's x, away from the interior: 110° at A sits left of A, not on AB.
# Obtuse openings keep a 0.55cm arc.
# A radius written on one call and not on another of the same opening is
# ignored.
#
# The command closes its own path and eats a semicolon written after it, so that a
# picture writing \anglemark{{A}}{{B}}{{C}}{{...}} and a picture ending the line the way every
# other TikZ statement ends both come out the same. Left to TikZ the spare semicolon
# is an empty statement, which draws nothing but is set as a character in no font and
# reported as a glyph the paper is missing.
ANGLE_MARK = r"""
\makeatletter
\newlength{\am@need}
\newlength{\am@ht}
\newlength{\am@dp}
\newcount\am@n
\pgfkeys{
  /am@opt/xshift/.code={},
  /am@opt/yshift/.code={},
  /am@opt/left/.code={},
  /am@opt/right/.code={},
  /am@opt/above/.code={},
  /am@opt/below/.code={},
  /am@opt/anchor/.code={},
  /am@opt/outside/.code={\am@outsidetrue},
  /am@opt/.unknown/.code={}}
\newif\ifam@outside
\NewDocumentCommand{\anglemark}{O{} O{} mmmm t;}{%
\am@outsidefalse
\def\am@wantout{outside}%
\edef\am@optone{#1}%
\ifx\am@optone\am@wantout
  \am@outsidetrue
\fi
\pgfmathanglebetweenpoints{\pgfpointanchor{#4}{center}}{\pgfpointanchor{#3}{center}}%
\edef\am@start{\pgfmathresult}%
\pgfmathanglebetweenpoints{\pgfpointanchor{#4}{center}}{\pgfpointanchor{#5}{center}}%
\edef\am@end{\pgfmathresult}%
\pgfmathsetmacro{\am@sweep}{Mod(\am@end-\am@start,360)}%
\ifdim\am@sweep pt<25pt
  \def\am@radius{1}\def\am@ecc{1.6}%
\else\ifdim\am@sweep pt<35pt
  \def\am@radius{0.8}\def\am@ecc{1.7}%
\else\ifdim\am@sweep pt<45pt
  \def\am@radius{0.7}\def\am@ecc{1.7}%
\else\ifdim\am@sweep pt<55pt
  \def\am@radius{0.6}\def\am@ecc{1.8}%
\else\ifdim\am@sweep pt<65pt
  \def\am@radius{0.5}\def\am@ecc{1.9}%
\else\ifdim\am@sweep pt<89pt
  \def\am@radius{0.45}\def\am@ecc{2.2}%
\else\ifdim\am@sweep pt<180pt
  \def\am@radius{0.55}\def\am@ecc{1.6}%
\else\ifdim\am@sweep pt<250pt
  \def\am@radius{0.3}\def\am@ecc{2.2}%
\else
  \def\am@radius{0.25}\def\am@ecc{2.9}%
\fi\fi\fi\fi\fi\fi\fi\fi
\settowidth{\am@need}{#6}%
\settoheight{\am@ht}{#6}%
\settodepth{\am@dp}{#6}%
\IfBlankF{#2}{\pgfkeys{/am@opt/.cd,#2}}%
\pgfmathsetmacro{\am@half}{(\am@need/2)/1cm}%
\pgfmathsetmacro{\am@hh}{(\am@ht+\am@dp)/2/1cm+1/28.452755}%
\pgfmathsetmacro{\am@mid}{\am@start+\am@sweep/2}%
\pgfmathsetmacro{\am@cabs}{abs(cos(\am@mid))}%
\pgfmathsetmacro{\am@sabs}{abs(sin(\am@mid))}%
\pgfmathsetmacro{\am@floor}{\am@ecc*\am@radius}%
\ifdim\am@sweep pt<35pt
  \pgfmathsetmacro{\am@floor}{\am@floor+0.15}%
\fi
\pgfmathsetmacro{\am@proj}{\am@cabs*\am@half+\am@sabs*\am@hh}%
\pgfmathsetmacro{\am@dist}{\am@floor}%
\pgfmathsetmacro{\am@sinm}{sin(\am@mid)}%
\ifdim\am@sweep pt<90pt
  \pgfmathsetmacro{\am@dist}{min(max(\am@dist,\am@radius+\am@proj+3/28.452755),1.8)}%
  \ifdim\am@sinm pt>0pt
    \pgfmathsetmacro{\am@dist}{min(max(\am@dist,\am@radius+\am@half+3/28.452755),1.8)}%
    \ifdim\am@cabs pt>0.5pt
      \pgfmathsetmacro{\am@needx}{\am@half+sqrt(max((\am@radius+3/28.452755)^2-\am@hh*\am@hh,0))}%
      \pgfmathsetmacro{\am@dist}{min(max(\am@dist,\am@needx/\am@cabs),1.8)}%
    \fi
  \fi
\else
  \pgfmathsetmacro{\am@oclr}{min(\am@proj,0.42)}%
  \pgfmathsetmacro{\am@dist}{max(\am@dist,\am@radius+\am@oclr+3/28.452755)}%
\fi
\pgfmathsetmacro{\am@air}{0.15}%
\pgfmathsetmacro{\am@reach}{\am@cabs*\am@hh+\am@sabs*\am@half}%
\pgfmathsetmacro{\am@halfa}{max(sin(0.5*\am@sweep),0.08)}%
\pgfmathsetmacro{\am@fit}{(\am@reach+\am@air)/\am@halfa}%
\pgfmathsetmacro{\am@dist}{min(max(\am@dist,\am@fit),2.4)}%
\pgfmathsetmacro{\am@xs}{0}%
\ifdim\am@sabs pt<\am@cabs pt
  \pgfmathsetmacro{\am@wedge}{max(\am@reach+\am@air-\am@dist*\am@halfa,0)}%
  \pgfmathsetmacro{\am@past}{max(\am@radius+\am@air-(\am@dist-\am@proj),0)}%
  \pgfmathsetmacro{\am@xs}{sign(cos(\am@mid))*max(\am@wedge,\am@past)}%
\fi
\pgfmathsetmacro{\am@stuck}{\am@reach+\am@air-\am@dist*\am@halfa}%
\ifam@outside
  \pgfmathsetmacro{\am@stuck}{1}%
\fi
\ifdim\am@stuck pt>0.02pt
  \pgfmathsetmacro{\am@side}{-sign(cos(\am@mid))}%
  \ifdim\am@side pt=0pt
    \pgfmathsetmacro{\am@side}{-1}%
  \fi
  \pgfmathsetmacro{\am@ox}{\am@side*(1.25+\am@half)}%
  \ifdim\am@sinm pt<0pt
    \pgfmathsetmacro{\am@oy}{0.22}%
  \else
    \pgfmathsetmacro{\am@oy}{-0.22}%
  \fi
  \global\advance\am@n by1
  \edef\am@tipname{am@tip\the\am@n}%
  \edef\am@labname{am@lab\the\am@n}%
  \path pic [draw, angle radius=\am@radius cm] {angle = #3--#4--#5};
  \coordinate (\am@tipname) at ($(#4)+(\am@mid:\am@radius cm)$);
  \node (\am@labname) [inner sep=1pt, anchor=mid] at ($(#4)+(\am@ox cm,\am@oy cm)$) {#6};
  \ifdim\am@side pt<0pt
    \draw [line width=0.5pt, -{To[length=1.2mm,width=1mm]}]
      (\am@labname.east) to[bend left=25] (\am@tipname);
  \else
    \draw [line width=0.5pt, -{To[length=1.2mm,width=1mm]}]
      (\am@labname.west) to[bend right=25] (\am@tipname);
  \fi
\else
\pgfmathsetmacro{\am@ecc}{\am@dist/\am@radius}%
\pgfmathsetmacro{\am@cx}{\am@dist*cos(\am@mid)}%
\pgfmathsetmacro{\am@cy}{\am@dist*sin(\am@mid)}%
\pgfmathsetmacro{\am@ay}{\am@radius*sin(\am@mid)}%
\pgfmathsetmacro{\am@ys}{0}%
\ifdim\am@sweep pt<90pt
  \ifdim\am@cy pt>0pt
    \pgfmathsetmacro{\am@yclr}{\am@hh+7/28.452755}%
    \pgfmathsetmacro{\am@minr}{\am@radius+\am@hh+3/28.452755}%
    \pgfmathsetmacro{\am@yarc}{sqrt(max(\am@minr*\am@minr-\am@cx*\am@cx,0))}%
    \pgfmathsetmacro{\am@ymin}{max(\am@yclr,\am@yarc)}%
    \pgfmathsetmacro{\am@ny}{min(\am@cy,max(\am@ay,\am@ymin))}%
    \pgfmathsetmacro{\am@ys}{\am@ny-\am@cy}%
  \fi
\fi
\path pic [draw, angle radius=\am@radius cm, angle eccentricity=\am@ecc,
  "{#6}" {inner sep=1pt, anchor=mid, xshift=\am@xs cm, yshift=\am@ys cm}]
  {angle = #3--#4--#5};
\fi}
\makeatother
"""

# How a stated length on a segment is marked: the measure beside the named
# segment, off the ink, on the side away from a point the figure names. A node
# on the path sits on the line it measures; a node at a vertex is a tag on that
# point; a node parked in a face does not say which segment it belongs to.
# Every stated length in the book goes through here so that they are all
# marked alike.
#
# One paper gap is the default (0.22cm to the inner edge of the type). A
# clash raises the optional offset on that call only — [0.55cm] on the
# mark that hit ink, not on every length in the picture. The second
# optional is the mode: sloped, upright, arrows, or blank. Blank follows
# the segment: within 20° of horizontal or vertical the type stays upright
# (15 cm beside AB, 20 cm on BC); a real diagonal is rotated parallel to
# it, never upside-down, a near-vertical reading bottom to top. Explicit
# sloped or upright overrides that. A sloped mark is offset by air plus
# half the type height, beside the stroke, and is not filled: a white box
# on BD knocked a hole in the median. arrows draws a light parallel bar
# with end ticks only (no arrowheads), the same length as the segment, off
# the ink, and the measure on the outer side of that bar, sloped, unfilled
# — so a whole length with a point on it (26 cm on AC, 20 cm on DF) is
# read as the whole, not as a piece. Empty #2 is never arrows: existing
# calls must not grow bars on rebuild. The third optional is pos along the
# segment, 0.5 unless a clash at a vertex slides it — [0.35] toward the
# first end.
#
# The offset is a real centimetre, like an \anglemark radius: scale= on the
# picture grows the coordinates and not the gap. Air and box-reach are
# paper centimetres; the whole sum is divided by the current transform, or
# a scale=0.4 picture shrinks the air on 8 cm and inflates 17 cm off KM.
# Type does not scale, so the box-reach has to be converted too: leaving
# air in picture centimetres was the mixed unit. A sloped mark adds half
# the type height the same way. The type is measured from a node, not with
# \settowidth: inside the picture the text font is nullfont, so a
# \settowidth of $4\text{ cm}$ was the width of 4. The sizer is the same
# inner sep as the mark. It is hidden and flagged so the clash pass, which
# shows one node at a time by forcing text opacity, does not treat that
# origin node as a label.
#
# Those defaults are pgfmath numbers after a \setlength: \lm@gap/1cm was
# read as zero when the skip had not been set, and every sloped mark sat
# at half-height, on the ink. The optional offset is the air on every
# side, including a diagonal: [0.40cm] on ZW used to be thrown away. The
# command closes its own path and eats a semicolon written after it, for
# the same reason \anglemark does.
LENGTH_MARK = r"""
\makeatletter
\newlength{\lm@gap}
\newlength{\lm@need}
\newlength{\lm@ht}
\newlength{\lm@dp}
\newif\iflengthmarksizer
\newif\iflengthmarksloped
\newif\iflengthmarkarrows
\def\lm@wordsloped{sloped}
\def\lm@wordupright{upright}
\def\lm@wordarrows{arrows}
\NewDocumentCommand{\lengthmark}{O{} O{} O{0.5} mmmm t;}{%
\pgfmathanglebetweenpoints{\pgfpointanchor{#4}{center}}{\pgfpointanchor{#5}{center}}%
\edef\lm@dir{\pgfmathresult}%
\pgfmathanglebetweenpoints{\pgfpointanchor{#4}{center}}{\pgfpointanchor{#6}{center}}%
\edef\lm@to{\pgfmathresult}%
\pgfmathsetmacro{\lm@side}{Mod(\lm@to-\lm@dir,360)}%
\ifdim\lm@side pt<180pt \def\lm@turn{-90}\else \def\lm@turn{90}\fi
\lengthmarksizertrue
\node [inner sep=1pt, overlay, opacity=0, text opacity=0] (lm@sz) {#7};%
\lengthmarksizerfalse
\pgfextractx{\lm@need}{\pgfpointdiff{\pgfpointanchor{lm@sz}{west}}{\pgfpointanchor{lm@sz}{east}}}%
\pgfextracty{\lm@ht}{\pgfpointdiff{\pgfpointanchor{lm@sz}{south}}{\pgfpointanchor{lm@sz}{north}}}%
\pgfmathsetmacro{\lm@halfw}{(\lm@need/2)/1cm}%
\pgfmathsetmacro{\lm@halfh}{(\lm@ht/2)/1cm}%
\pgfgettransformentries{\lm@a}{\lm@b}{\lm@c}{\lm@d}{\lm@e}{\lm@f}%
\pgfmathsetmacro{\lm@scale}{veclen(\lm@a,\lm@c)}%
\ifdim\lm@scale pt=0pt \pgfmathsetmacro{\lm@scale}{1}\fi
\IfBlankTF{#1}{\setlength{\lm@gap}{0.22cm}}{\setlength{\lm@gap}{#1}}%
\pgfmathsetmacro{\lm@air}{(\lm@gap)/1cm}%
\pgfmathsetmacro{\lm@absin}{abs(sin(\lm@dir))}%
\pgfmathsetmacro{\lm@abcos}{abs(cos(\lm@dir))}%
\lengthmarkslopedfalse
\lengthmarkarrowsfalse
\def\lm@mode{#2}%
\ifx\lm@mode\lm@wordarrows
  \lengthmarkarrowstrue
\else
  \ifx\lm@mode\lm@wordsloped
    \lengthmarkslopedtrue
  \else
    \ifx\lm@mode\lm@wordupright
    \else
      \ifdim\lm@absin pt>0.342pt
        \ifdim\lm@abcos pt>0.342pt
          \lengthmarkslopedtrue
        \fi
      \fi
    \fi
  \fi
\fi
\iflengthmarkarrows
  \pgfmathsetmacro{\lm@baro}{\lm@air/\lm@scale}%
  \pgfmathsetmacro{\lm@laboff}{(0.06+\lm@halfh)/\lm@scale}%
  \pgfmathsetmacro{\lm@rot}{cos(\lm@dir)<0 ? \lm@dir+180 : \lm@dir}%
  \pgfmathsetmacro{\lm@rot}{sin(\lm@rot)<0 && abs(cos(\lm@rot))<0.34 ? \lm@rot+180 : \lm@rot}%
  \coordinate (lm@p) at ($(#4)+({\lm@dir+\lm@turn}:\lm@baro cm)$);%
  \coordinate (lm@q) at ($(#5)+({\lm@dir+\lm@turn}:\lm@baro cm)$);%
  \draw [line width=0.5pt, {Bar[width=1.6mm]}-{Bar[width=1.6mm]}] (lm@p) -- (lm@q);%
  \coordinate (lm@m) at ($(lm@p)!#3!(lm@q)!\lm@laboff cm!\lm@turn:(lm@q)$);%
  \node [inner sep=1pt, anchor=center, rotate=\lm@rot] at (lm@m) {#7};%
\else
\iflengthmarksloped
  \pgfmathsetmacro{\lm@off}{(\lm@air+\lm@halfh)/\lm@scale}%
  \pgfmathsetmacro{\lm@rot}{cos(\lm@dir)<0 ? \lm@dir+180 : \lm@dir}%
  \pgfmathsetmacro{\lm@rot}{sin(\lm@rot)<0 && abs(cos(\lm@rot))<0.34 ? \lm@rot+180 : \lm@rot}%
  \coordinate (lm@m) at ($(#4)!#3!(#5)!\lm@off cm!\lm@turn:(#5)$);%
  \node [inner sep=1pt, anchor=center, rotate=\lm@rot] at (lm@m) {#7};%
\else
  \pgfmathsetmacro{\lm@perp}{\lm@dir+\lm@turn}%
  \pgfmathsetmacro{\lm@nx}{abs(cos(\lm@perp))}%
  \pgfmathsetmacro{\lm@ny}{abs(sin(\lm@perp))}%
  \pgfmathsetmacro{\lm@reach}{\lm@nx*\lm@halfw+\lm@ny*\lm@halfh}%
  \pgfmathsetmacro{\lm@off}{(\lm@air+\lm@reach)/\lm@scale}%
  \coordinate (lm@m) at ($(#4)!#3!(#5)!\lm@off cm!\lm@turn:(#5)$);%
  \node [inner sep=1pt, anchor=center] at (lm@m) {#7};%
\fi\fi}
\makeatother
"""

# How heavy a line in a figure is, which is the paper's to say and not each figure's.
# The book had 131 figures drawing thick, another sixty naming a width of their own
# anywhere between 0.1pt and 2.2pt, and the rest taking whatever TikZ gives: two
# figures side by side on a page could differ fourfold. So one weight is set for
# everything a figure draws, and a lighter one for what stands behind the figure
# rather than in it — a grid, a line of construction, the frame and ticks of a chart,
# which are read past rather than read.
FIGURE_RULE = "1pt"
FIGURE_LIGHT = "0.5pt"

# How a line ends and how two of them meet. Rounded, so that a stroke finishes in a
# half circle the width of the line rather than square across.
#
# Both, and not only the cap, because which of the two a corner uses is decided by how
# the picture happens to have been written and not by anything about the figure. A
# triangle drawn as one closed path has no ends at all — its three corners are joins —
# while the same triangle drawn as three strokes has six ends and no joins. Setting the
# cap alone would round the second and leave the first untouched, so two identical
# triangles would print differently according to a choice the model made without
# knowing it was making one.
FIGURE_CAP = "round"
FIGURE_JOIN = "round"

# What a grid is drawn in, which is the paper's to say for the same reason the weights
# are: a chart whose grid is darker than the chart beside it looks like a different
# book. Twenty per cent black at the light weight sits behind a curve without competing
# with it and still survives being printed — a tenth of black on a half-point line
# washes out on paper, whatever it looks like on a screen.
#
# Given to a drawing as a style rather than as a pair of numbers in its prompt, because
# a number in a prompt is a number the model retypes from memory each time. Asked for
# in words, the grids of one run came back at four greys and two weights.
GRID_GREY = "black!20"

# The frame of a chart: its two axes and the tick marks along them, drawn at the full
# weight so that the chart is plainly framed and the reader can see where the scales
# are before reading anything off them.
#
# A style and not a weight in the prompt, because the prompt tells a figure to set no
# weight at all — `thin` is one of the words it is told not to bother writing, since
# anything it does write is rewritten anyway. Asking for `thin` on the axes was
# therefore asking it to break the rule above it, and it mostly did not: fourteen of
# sixteen curves drew their axes bare and got the 1pt every picture starts at, while
# the histograms beside them wrote `thin` and got 0.5pt, so one type printed its axes
# at twice the weight of the other. Naming the part rather than the measurement asks
# for nothing the rest of the prompt forbids.
AXIS_RULE = FIGURE_RULE

# The outline of a bar, which is the lighter of the two. A bar is read by its height
# and its fill rather than by its edge, so the edge only has to hold the shape: at the
# full weight it competes with the axes that frame the chart, and the row of bars reads
# as five heavy boxes instead of one distribution.
BAR_RULE = FIGURE_LIGHT

# The one colour in the book, for the curve a chart is actually about. Given as a name
# and not as a value for the same reason the grid is: what is being kept is not this
# particular blue but the fact that there is only ever one of it.
#
# A name also covers both ways a curve is drawn. Written as a path option, an xcolor
# name sets the stroke and the fill together, so `\draw [curveblue] plot` strokes the
# line and `\fill [curveblue] circle` fills the dots on it, and the two cannot drift
# apart into a blue curve marked with black points.
CURVE_BLUE = "1A5FB4"

# What a bar of a chart is filled with. Pale enough that the numbers and the class
# labels are read against paper rather than against a tint, and the same blue as the
# curve, so that a histogram and a cumulative frequency curve on facing pages are
# plainly the same book. The fill is the only blue on a histogram: its bars are
# outlined in black, and the colour is what the two types have in common rather than
# the way either of them is drawn.
BAR_FILL = "curveblue!12"

# A shaded region of a plane figure, filled the same as a bar and for the same reason.
# Some exercises name a region and then ask for its area — the ring between two
# concentric squares, the strip cut off a plot — and the shading is what says which
# region is meant, so it does the work a bar does and is given the same tint.
# Deliberately the same value as BAR_FILL rather than a value of its own: every filled
# area in the book is one colour, and a second pale tint beside it would read as a
# distinction that is not being drawn.
#
# Fill and no stroke, unlike the bars. A plane figure draws its own outlines as part of
# the geometry, and a style that also stroked would put a second line along an edge
# that has already been drawn.
SHADE_FILL = BAR_FILL

# The arrowed arc that marks an angle, ruled like the shape it is measured on rather
# than like the axes: it is an annotation on a figure, and at the full weight it prints
# heavier than the sector outlines it spans between and reads as part of the drawing.
#
# The head is written into the style along with the weight, for the reason all of these
# are: an arrow tip is a length, and a length in a prompt is a length the model picks
# afresh every time. Given here, every angle on every figure is marked with the same
# head. It points at the end of the path, so an arc drawn outwards from the middle of
# the angle lands its head on the arm it measures to.
ARC_RULE = FIGURE_LIGHT
ARC_TIP = "To[length=1.2mm,width=1mm]"

# The head on a line that goes on for ever: the two ends of a number line, and the open
# end of a ray along it. Bigger than the one on an angle mark, because it is read
# across the width of the page rather than looked at closely, and because it is the
# thing that says the line does not stop where the paper does.
LINE_TIP = "To[length=2.2mm,width=1.8mm]"

# How big a point marked on a line is. It has to be plainly larger than the 1pt rule it
# sits on, or a filled end reads as a thickening of the line rather than as a point,
# and the ring of a hollow one closes up.
DOT_SIZE = "2mm"

# Nothing a figure is compiled on carries space around it. A figure is set wherever it
# is set — beside an exercise here, down a review strip elsewhere — and the room above
# and below it belongs to whatever is doing the setting. So the page a figure is
# compiled on its own is the picture and no border, and the size that page comes out at
# is the room the figure actually needs rather than that plus a margin nobody asked for.
FIGURE_BORDER = "0pt"

# Under this, a width a figure asked for was asked for as a hairline and is set as the
# lighter of the two. TikZ's own default is 0.4pt, which is what a figure that named no
# width at all was drawn at, so the line is drawn under that: 0.4pt was somebody drawing
# the figure, 0.2pt was somebody drawing behind it.
HAIRLINE = 0.35
LINE_WIDTH = re.compile(
    r"line width\s*=\s*(?P<size>[\d.]+)\s*(?P<unit>pt|mm|cm|bp|in)?", re.IGNORECASE
)
POINTS_PER = {
    "pt": 1.0,
    "bp": POINTS_PER_BIG,
    "mm": POINTS_PER_CM / 10,
    "cm": POINTS_PER_CM,
    "in": POINTS_PER_INCH,
}

# The weights TikZ names are bent to the two the book keeps, so that a picture asking
# for thick, semithick or nothing at all comes out at the one weight, and a picture
# asking for thin comes out at the other. What a figure writes as a width of its own
# is taken away from it before it is set: see standard_lines.
#
# Then the parts of a figure that are not simply drawn in black at the ordinary weight,
# each as a style holding whatever the paper has settled for that part. They are here
# rather than described to whoever draws, because a drawing that names the part cannot
# get the measurement wrong: `\draw [gridline]` is a grid in the book's grey at the
# book's weight however the picture is written, where "a light grey line" is a value
# chosen afresh every time it is asked for.
#
# Every one of these has to be defined wherever a figure is compiled — here for the
# paper, and here for the stage that draws them, which reads this module. A style
# defined in one and not the other is a figure that draws perfectly in review and takes
# the whole section paper down with `I do not know the key '/tikz/gridline'`.
FIGURE_WEIGHTS = f"""\\tikzset{{
every picture/.append style={{line width={FIGURE_RULE},
                             line cap={FIGURE_CAP}, line join={FIGURE_JOIN}}},
ultra thin/.style={{line width={FIGURE_LIGHT}}},
very thin/.style={{line width={FIGURE_LIGHT}}},
thin/.style={{line width={FIGURE_LIGHT}}},
semithick/.style={{line width={FIGURE_RULE}}},
thick/.style={{line width={FIGURE_RULE}}},
very thick/.style={{line width={FIGURE_RULE}}},
ultra thick/.style={{line width={FIGURE_RULE}}},
help lines/.style={{line width={FIGURE_LIGHT}}}}}
\\pgfplotsset{{every axis/.append style={{line width={AXIS_RULE}}},
every axis plot/.append style={{line width={FIGURE_RULE}}}}}
\\tikzset{{gridline/.style={{line width={FIGURE_LIGHT}, draw={GRID_GREY}}}}}
\\tikzset{{axisline/.style={{line width={AXIS_RULE}, draw=black}}}}
\\definecolor{{curveblue}}{{HTML}}{{{CURVE_BLUE}}}
\\tikzset{{histobar/.style={{line width={BAR_RULE}, draw=black, fill={BAR_FILL}}}}}
% A sector of a pie is a share of a total, which is what a bar is, so it is filled and
% outlined as one. Named separately only so a prompt about pie charts never has to
% mention bars.
\\tikzset{{piesector/.style={{histobar}}}}
% A region an exercise has named and asked about. Fill only: the figure's own outlines
% are already drawn, and `even odd rule` is what makes a ring out of an outer path and
% an inner one.
\\tikzset{{shaded/.style={{fill={SHADE_FILL}, even odd rule}}}}
\\tikzset{{anglearc/.style={{line width={ARC_RULE}, draw=black, -{{{ARC_TIP}}}}}}}
\\tikzset{{numberline/.style={{line width={AXIS_RULE}, draw=black,
                            {{{LINE_TIP}}}-{{{LINE_TIP}}}}}}}
% What is left of the line when a ray has taken one of its ends: headed where the path
% finishes and bare where it starts, so it is drawn outwards from the ray's endpoint.
% Two arrowheads in a row at one end, a blue one and then a black one, read as a single
% long double arrow rather than as a ray on a line, and the end they are both pointing
% at is the same infinity in any case.
\\tikzset{{numberrest/.style={{line width={AXIS_RULE}, draw=black,
                            -{{{LINE_TIP}}}}}}}
% What is marked on a number line is the answer to the exercise beside it, so it is the
% blue, and the rule it is marked on stays black. A black mark would be the line redrawn
% at the line's own weight and could only be told from it by being thicker, which is a
% third weight this paper does not have.
\\tikzset{{intervalmark/.style={{line width={FIGURE_RULE}, draw=curveblue}}}}
\\tikzset{{intervalray/.style={{intervalmark, -{{{LINE_TIP}}}}}}}
\\tikzset{{endclosed/.style={{circle, fill=curveblue,
                            inner sep=0pt, minimum size={DOT_SIZE}}}}}
\\tikzset{{endopen/.style={{circle, draw=curveblue, fill=white,
                          line width={FIGURE_RULE},
                          inner sep=0pt, minimum size={DOT_SIZE}}}}}
% An edge of a solid that is behind the rest of it. Dashed for what it means and light
% for where it is: at the full weight the back of a box competes with its front, and the
% drawing flattens into a hexagon with a cross in it. The pattern is written out rather
% than left to TikZ's `dashed`, so that every hidden edge in the book is broken the same
% way whatever it is drawn at.
\\tikzset{{hiddenedge/.style={{line width={FIGURE_LIGHT}, draw=black,
                             dash pattern=on 3pt off 3pt}}}}
"""

# A word label pointed at a marked point from empty paper: the clash last
# resort for a name that will not sit beside its dot. The point is already
# drawn (a filled circle); this only places the type and a light pointer.
# The node is named, then the pointer is drawn from that node: TikZ starts
# a path at a node's border, so the line does not run through the glyph
# (a path that started at the node's centre would collide with its own type
# and the pin would be measured as worse than the label it replaced).
# Angle is TikZ degrees (0 right, 90 above). Optional first argument is the
# pin length, 2.2cm: a long name on a coordinate grid has to clear the first
# ring of ticks, and 1.15cm lands it on them.
WORD_PIN = r"""
\NewDocumentCommand{\wordpin}{O{2.2cm} mmmm}{%
  \node [inner sep=1pt, anchor=center] (wordpinlab) at ($(#3,#4)+({#2}:#1)$) {#5};%
  \draw [line width=0.5pt, shorten <=1pt, -{To[length=1.2mm,width=1mm]}]
    (wordpinlab) -- (#3,#4);%
}
"""

# Short division (القسمة المختصرة). The writer sends a two-row `{r}` array with
# the US house `\overline{ )`; that mark is not a house and RTL explodes it.
# \divform is the grid: divisor, bar over the dividend, quotient above, carries
# as circled digits. TLT so place-value stays left-to-right. Cells are `/`-lists;
# `{.}` is the decimal; the shorter row is padded on the left.
DIVFORM_PREAMBLE = r"""
% \cir{carry}{digit}: the carry sits on the digit's upper-left without widening
% the column. Its own TLT box, since babel sets tabular cells right to left.
% Isolated ٫ sits high in the maths face; drop it onto the digit baseline.
\NewDocumentCommand{\divdec}{}{%
  \raisebox{-.45ex}[0pt][0pt]{\ensuremath{٫}}}
\NewDocumentCommand{\cirmark}{m}{%
  \tikz[baseline=(c.base)]
  \node[circle,draw,inner sep=.3pt,font=\tiny,minimum size=.65em]
    (c) {\ensuremath{#1}};}
\NewDocumentCommand{\cir}{m m}{%
  \hbox{\textdir TLT\relax
    \smash{\llap{\raisebox{.5em}{%
      \tikz[baseline=(c.base)]
      \node[circle,draw,inner sep=.3pt,font=\tiny,minimum size=.65em]
        (c) {\ensuremath{#1}};}}}#2}}
\ExplSyntaxOn
\NewDocumentCommand{\divform}{m m m}
  {
    \hbox { \textdir ~ TLT \relax
      \novastax_divform:nnn {#1} {#2} {#3}
    }
  }
\seq_new:N \l__novastax_q_seq
\seq_new:N \l__novastax_d_seq
\int_new:N \l__novastax_n_int
\cs_new_protected:Npn \novastax_divform:nnn #1#2#3
  {
    \seq_set_split:Nnn \l__novastax_q_seq { / } {#2}
    \seq_set_split:Nnn \l__novastax_d_seq { / } {#3}
    \int_set:Nn \l__novastax_n_int
      {
        \int_max:nn
          { \seq_count:N \l__novastax_q_seq }
          { \seq_count:N \l__novastax_d_seq }
      }
    \novastax_div_pad:Nn \l__novastax_q_seq { \l__novastax_n_int }
    \novastax_div_pad:Nn \l__novastax_d_seq { \l__novastax_n_int }
    \renewcommand{\arraystretch}{1.6}
    \begin{tabular}{@{} r | c
        *{\int_eval:n { \l__novastax_n_int - 1 }}{@{\hspace{.8em}}c} @{\hspace{.3em}}}
      \multicolumn{1}{r}{} &
      \seq_use:Nn \l__novastax_q_seq { & } \\
      \cline { 2 - \int_eval:n { \l__novastax_n_int + 1 } }
      #1 &
      \seq_use:Nn \l__novastax_d_seq { & } \\
      \cline { 1 - 1 }
    \end{tabular}
  }
\cs_new_protected:Npn \novastax_div_pad:Nn #1#2
  {
    \int_while_do:nNnn { \seq_count:N #1 } < {#2}
      { \seq_put_left:Nn #1 { } }
  }
\ExplSyntaxOff
"""

DRAWING_PREAMBLE = (
    r"""\usepackage{tikz}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage{adjustbox}
\usetikzlibrary{angles,arrows.meta,calc,decorations.markings,intersections,
                patterns,positioning,quotes,shapes.geometric}
"""
    + ANGLE_MARK
    + LENGTH_MARK
    + WORD_PIN
    + DIVFORM_PREAMBLE
    + FIGURE_WEIGHTS
)

# The size of the body type, which the class option carries: everything derived
# from it — \large for the heading, the skips around a display — follows from
# there. Only 10, 11 and 12 mean anything to the class.
BODY_SIZE = 12
# The two smaller sizes maths is set at, for an index and for an index of an index. The
# class's own for 12pt body type, written down because the one maths family this module
# builds by hand (the letters of ARABIC_MATHS_FONT) has to be told them.
SCRIPT_SIZE = 8
SCRIPTSCRIPT_SIZE = 6

# The distance from one baseline to the next. Set here rather than left to the
# class, which leads 12pt type at 14.5pt. A number because the stage that plans the
# pages adds it up: the room a block takes above itself is this less what stands
# above it, and a page is filled to the point.
BODY_LEADING = 16.0

# The trim size, stated as the paper physically is rather than as a count of points,
# so that another size is a line to change rather than an arithmetic to redo. A4.
# The same millimetres are handed to geometry below, so the page TeX builds and the
# page this module plans for are one statement instead of two that have to agree —
# they did not, for a while: the numbers here were A4 measured in the PDF's kind of
# point while the margin beside them was in TeX's, and the paper came out 3pt taller
# than the module believed.
PAGE_MM = (210.0, 297.0)
PAGE_WIDTH = PAGE_MM[0] / MM_PER_INCH * POINTS_PER_INCH
PAGE_HEIGHT = PAGE_MM[1] / MM_PER_INCH * POINTS_PER_INCH

# The margin, and what it leaves. In millimetres for the same reason the trim size
# is: the same figure is handed to geometry below, so there is one statement of where
# the text sits and not two. Points as well, because the width of the text is what
# decides whether a picture has to be shrunk to fit and how much room to ask for
# before setting one, and the height is the room a page has to fill.
MARGIN_MM = 19.0
PAGE_MARGIN = MARGIN_MM / MM_PER_INCH * POINTS_PER_INCH
TEXT_WIDTH = PAGE_WIDTH - 2 * PAGE_MARGIN
TEXT_HEIGHT = PAGE_HEIGHT - 2 * PAGE_MARGIN

# The grid the paper is set on. One gutter parts every pair of columns, and a column
# is what is left of the measure when the gutters have been taken out of it, so the
# columns and the gaps between them tile the width exactly however many there are.
# At A4 with these numbers it comes to 84mm in a pair and 40mm in a four, and the two
# agree: 40 + 4 + 40 is 84, so a pair of four-columns is a two-column, and anything
# drawn or set to one grid is valid in the other. The column widths are not written
# down anywhere, which is why the margin is the only line to change to move them.
#
# A column holds an exercise whole — its number and then its wording — rather than the
# number standing outside in a gutter of its own. It is what makes the grid an actual
# grid: every column begins on a line the reader can see down the page, and the
# wording inside it begins a fixed step further in.
GUTTER_MM = 4.0
GUTTER = GUTTER_MM / MM_PER_INCH * POINTS_PER_INCH

# How far into its column an exercise's wording begins: the number stands in this much
# and the wording takes the rest. A round figure, and a stated one rather than a
# consequence of how wide a numeral happens to set, because it is the second step of
# the grid — the one every wrapped line, every option and every figure beside wording
# starts from — and a grid whose steps are round is one that can be reasoned about.
#
# It was a guess of 32pt until it was measured, generous by five points against the
# 21.13pt TeX makes of a bold "100.", and nothing said so but a comment. It is exact
# now, and exact in both directions: this module spends it here, and TeX is told the
# total rather than the gap, so the room is this whatever the face measures. Changing
# the body's font or size moves the gap and leaves the step where it is.
NUMBER_ROOM_MM = 10.0
NUMBER_ROOM = NUMBER_ROOM_MM / MM_PER_INCH * POINTS_PER_INCH


def grid_column(span: int) -> float:
    """The width in points of one column of `span`, gutters taken out."""
    return (TEXT_WIDTH - GUTTER * (span - 1)) / span


# The same grid, for TeX to set on. The columns are worked out from \\textwidth by the
# rule above rather than written down as lengths, so that the grid this module plans
# with and the grid the paper is set on cannot drift apart: there is one statement of
# the gutter and one of how a column follows from it.
GRID_PREAMBLE = f"""\\newlength{{\\gridgutter}}
\\setlength{{\\gridgutter}}{{{GUTTER_MM:g}mm}}
\\newlength{{\\gridpair}}
\\newlength{{\\gridquad}}
\\setlength{{\\gridpair}}{{\\dimexpr(\\textwidth-\\gridgutter)/2\\relax}}
\\setlength{{\\gridquad}}{{\\dimexpr(\\textwidth-3\\gridgutter)/4\\relax}}
"""

# A paper of short questions gives TeX few lines to work with, and now and then a
# sentence has no break that leaves a line it considers good enough: "preserve side
# lengths," would have to be followed by a line so loose that TeX rejects it, so it
# sets the line over-wide instead and the last word sits out in the margin. Emergency
# stretch is a last pass with this much extra give in every line, taken only when the
# alternative is exactly that. A line that fits is set as it always was; a line that
# does not comes out a little loose, which is a thing a reader accepts and ink outside
# the measure is not.
LINE_EMERGENCY = 24.0

# The paper is named to geometry rather than taken from the class, so that PAGE_MM is
# the one place the trim size is written. a4paper would say the same thing today and
# stop saying it the moment the trim size changed.
PAGE_PREAMBLE = f"""\\documentclass[{BODY_SIZE}pt]{{article}}
\\usepackage[papersize={{{PAGE_MM[0]:g}mm,{PAGE_MM[1]:g}mm}},\
margin={MARGIN_MM:g}mm]{{geometry}}
\\usepackage{{needspace}}
\\setlength{{\\parindent}}{{0pt}}
\\setlength{{\\emergencystretch}}{{{LINE_EMERGENCY:.0f}pt}}
"""

# The type setup, shared by the paper and by a figure compiled on its own, so a
# label in a figure is set in the same font at the same size either way. It is four
# pieces: the packages, the text face, the maths face, and what is said about the maths
# whichever face sets it, because the two faces are what change with the book;
# type_preamble() puts them together for one.
TYPE_PACKAGES = r"""\usepackage{amsmath}
\allowdisplaybreaks
\usepackage[none]{hyphenat}
\usepackage{fontspec}
\usepackage{unicode-math}
"""

# The face an English book is set in. The maths is set in its companion, below, so a
# variable in a sentence and the same variable in a displayed equation are one design.
LATIN_TEXT_FONT = r"""\setmainfont{STIXTwoText}[
  Extension      = .otf,
  UprightFont    = *-Regular,
  BoldFont       = *-Bold,
  ItalicFont     = *-Italic,
  BoldItalicFont = *-BoldItalic ]
"""

# The maths face of a book whose maths is written as an English book writes it.
# NagwaMath, as CLS loads it, from fonts/; the assignment sits next to
# ARABIC_MATHS_FONT, after FONT_DIR. The Arabic-notation face is that one.

TYPE_MATHS = r"""% The paper itself sets nothing in sans: its numbers and its headings are the body's
% own face, told apart by weight and by size. A sans is still declared, so that a
% drawing asking for one gets a face that was chosen rather than whatever the class
% would fall back to. Named by file rather than by family, because it ships with TeX
% Live and is found there whether or not it is installed on the system running this.
\setsansfont{NotoSans}[
  Extension      = .ttf,
  UprightFont    = *-Regular,
  BoldFont       = *-Bold,
  ItalicFont     = *-Italic,
  BoldItalicFont = *-BoldItalic ]
% Commands the model uses that unicode-math leaves undefined.
\providecommand{\square}{\mdlgwhtsquare}
\providecommand{\blacksquare}{\mdlgblksquare}
\providecommand{\blacklozenge}{\mdlgblklozenge}
\providecommand{\degree}{^{\circ}}
\providecommand{\rupee}{₹}
% The LaTeX kernel predefines \Box itself, as a stub that errors out asking
% for amssymb/latexsym, so \providecommand above would not touch it.
\renewcommand{\Box}{\mdlgwhtsquare}
% The kernel's \* is a discretionary whose pre-break text is character 2 of
% the symbol font. That slot is a glyph in cmsy and empty in NagwaMath, so
% the engine reports a missing character and the paper is thrown out. The
% writing means an asterisk, which is what * sets. \\* is the starred line
% break and is left as it is: the star there belongs to \\, not to this command.
\RenewDocumentCommand{\*}{}{*}
% A fraction is set full size wherever it appears, so that "$\frac{1}{2}$" in a
% sentence reads as easily as the same fraction in a displayed equation. School
% arithmetic is mostly fractions, and inline they come out too small to read.
% \mathchoice is what keeps that from reaching a fraction in an exponent or an
% index: those branches are left in the script size they belong to, so
% "$x^{\frac{1}{2}}$" is unchanged.
% Inline maths stays on one line: TeX may otherwise break after a binary
% operator (\\times next to \\blank), sending half the formula down.
\binoppenalty=10000
\relpenalty=10000
% Exercise wording is almost all inline $...$; set every such formula in display
% style so fractions, integrals, superscripts and differentials read at one size.
% \\mathchoice on \\frac and \\int still leaves script-size forms in exponents.
\everymath{\displaystyle}
\let\latexfrac\frac
\renewcommand{\frac}[2]{\mathchoice
  {\displaystyle\latexfrac{#1}{#2}}%
  {\displaystyle\latexfrac{#1}{#2}}%
  {\latexfrac{#1}{#2}}%
  {\latexfrac{#1}{#2}}}
% Definite integrals in exercise wording are usually inline next to a full-size
% fraction; text-style \\int reads too small beside them. Same \\mathchoice rule
% as \\frac: display size in text and display math, script sizes unchanged.
\let\latexint\int
\renewcommand{\int}{\mathchoice
  {\displaystyle\latexint}%
  {\displaystyle\latexint}%
  {\latexint}%
  {\latexint}}
% \\Big| beside a full-size \\frac is one step short on the amsmath delimiter
% scale; render \\Big at \\bigg instead (\\bigg and \\Bigg in source unchanged).
\let\Big\bigg
"""

# The least clear space to leave between two lines, and the leading used instead of
# the fixed one when that clearance cannot be met. A full-size fraction in a
# sentence stands taller and hangs lower than the type around it, and at a fixed
# 16pt baseline its denominator crowds the line beneath. Where that happens the
# baseline is abandoned for this pair of lines only, so the paragraph opens up
# exactly where it must and holds its rhythm everywhere else.
LINE_CLEARANCE = 2.0
LINE_OPENING = 4.0

# The leading, fixed by redefining \normalsize rather than by stretching the
# class's own. Redefined rather than simply selected, so that the body returns to
# this leading every time it returns to \normalsize — after the heading, or
# inside a list — instead of falling back to what the class would have used.
LEADING_PREAMBLE = f"""\\makeatletter
\\renewcommand\\normalsize{{\\@setfontsize\\normalsize{{{BODY_SIZE}pt}}{{{BODY_LEADING:.0f}pt}}}}
\\makeatother
\\normalsize
\\lineskiplimit={LINE_CLEARANCE:.0f}pt
\\lineskip={LINE_OPENING:.0f}pt
"""

# The two colours the paper uses, on their own lines so that a house style is
# changed here and nowhere else. Both are black: the paper is printed in one colour,
# and a heading is marked as one by its size and its weight rather than by its ink.
LABEL_COLOUR = "black"
HEADING_COLOUR = "black"

# How a number is set, how wide the widest one a paper reaches comes out, and what is
# left over for the gap after it. An exercise's number and a set's range go through
# \exerciselabel alike, so the two cannot drift apart, and the width is measured
# through it too, since it is the width of a set number that the column has to hold. A
# hundred exercises to a section makes "100." the widest, and measuring it is what puts
# that number flush against the margin with nothing to spare.
#
# The gap is the remainder rather than a length of its own: the step is what the layout
# is built on, so it is the step that is declared and the gap that gives way. An
# exercise's wording therefore always begins one \numberroom in, whatever the numerals
# measure, and the dots still line up because they are still set flush right against
# the wording — only now the last of them lands a fixed distance from the margin.
#
# The number and the dot are put together by \labelnumber, and a set's range by
# \labelrange, rather than written out as "13." and "1–12." where they are used. The two
# are direction_preamble()'s to define, because what "13." looks like depends on which way
# the page runs: the digits are always read left to right, and the dot always follows
# them in reading order, which puts it to their right in an English paper and to their
# left in an Arabic one.
LABEL_LENGTH = f"""\\usepackage{{xcolor}}
\\newcommand{{\\exerciselabel}}[1]{{\\textcolor{{{LABEL_COLOUR}}}{{\\bfseries #1}}}}
\\newlength{{\\numberroom}}
\\setlength{{\\numberroom}}{{{NUMBER_ROOM_MM:g}mm}}
\\newlength{{\\exercisenumber}}
\\newlength{{\\labelgap}}
\\AtBeginDocument{{\\settowidth{{\\exercisenumber}}{{\\exerciselabel{{\\labelnumber{{100}}}}}}%
\\setlength{{\\labelgap}}{{\\dimexpr\\numberroom-\\exercisenumber\\relax}}}}
"""

# How the page runs, and the handful of things that are written differently because of
# it. Nothing else in the paper is told which way it runs: the engine lays out every
# box, list and paragraph in the direction babel sets when the book is Arabic, and the
# macros below — the choices four across, the members of a set in their columns, the
# figure beside its wording — come out mirrored because they are written in reading
# order and the direction is applied to the whole of them at once. What has to be said
# here is only what the engine cannot know.
#
# A number is made of digits read left to right whichever way the prose runs, and the
# dot that closes a label follows it in reading order. Left to the bidi algorithm, "13."
# is a number with a decimal point after it and comes out with the dot on the wrong
# side, and "1–12." is a number with a dash that it turns around. So an Arabic label is
# built as boxes laid right to left, each holding one piece that has no direction of its
# own to argue about: the number, the dash, the number, the dot. A range of exercises
# reads from the right as the book it is written for reads it — "12–1" on the page for
# exercises 1 to 12, which is how the Iraqi textbook writes "الأسئلة 7–12". Each piece is
# set as English so that its digits and its dot come from the English face, the face of
# the digits in the maths, rather than from the Arabic face's own.
#
# The mark between the ends of a range is an en dash in every book, the same mark the
# cover puts between its years.
EN_DASH = "\u2013"
LTR_LABELS = r"""\newcommand{\labelnumber}[1]{#1.}
\newcommand{\labelrange}[2]{#1""" + EN_DASH + r"""#2.}
"""
RTL_LABELS = r"""\newcommand{\labelpiece}[1]{\hbox{\foreignlanguage{english}{#1}}}
\newcommand{\labelnumber}[1]{\hbox dir TRT{\labelpiece{#1}\labelpiece{.}}}
\newcommand{\labelrange}[2]{\hbox dir TRT{\labelpiece{#1}\labelpiece{""" + EN_DASH + r"""}\labelpiece{#2}\labelpiece{.}}}
"""
RTL_LATIN_MATH_CELL = r"""\newcommand{\rtlmathcell}[2]{%
\hbox dir TRT{\hbox{\textdir TRT\relax #1}\hskip.333em%
\hbox{\textdir TLT\relax $#2$}}}
"""

# A number the paper writes for itself — an exercise's label, the page's number in the
# foot — in a book whose numerals are Arabic-Indic. The numbers in the wording are
# rewritten before they reach TeX (see arabic_notation); these are counted or written
# by TeX, and are turned into the book's digits as they are printed. \indic expands what
# it is given first, so that \thepage, a counter, comes through as its digits; it maps
# only 0-9, and a Lua character class rather than a pattern escape because TeX would
# read the escape's percent sign as a comment.
#
# The label's pieces are set in the Arabic face, which has these digits, rather than
# in the English one that RTL_LABELS asks for: the digits in the maths are Arabic-Indic
# too, from a face drawn to Noto Naskh's design, so the two agree as before. The dash
# of a range is the same en dash as the other books; the Arabic face has not got it, so
# the latin fallback sets STIX Two's.
ARABIC_INDIC = r"""\newcommand{\indic}[1]{\directlua{tex.sprint(-2, (string.gsub("\luaescapestring{#1}",
  "[0-9]", function(d) return utf8.char(0x660 + tonumber(d)) end)))}}
"""
ARABIC_NUMERAL_LABELS = ARABIC_INDIC + r"""\newcommand{\labelpiece}[1]{\hbox{\indic{#1}}}
\newcommand{\labelnumber}[1]{\hbox dir TRT{\labelpiece{#1}\labelpiece{.}}}
\newcommand{\labelrange}[2]{\hbox dir TRT{\labelpiece{#1}\labelpiece{""" + EN_DASH + r"""}\labelpiece{#2}\labelpiece{.}}}
"""


def labels_preamble(profile: Profile) -> str:
    """How an exercise's number and a set's range are put together for this book."""
    if profile.indic_digits:
        return ARABIC_NUMERAL_LABELS
    return RTL_LABELS if profile.rtl else LTR_LABELS

# The letters the choices of a multiple-choice exercise are labelled with, in order,
# per language. The Arabic ones are the abjad order Arabic textbooks letter choices in,
# not the alphabet's, and the first four are what prompts/new_questions/language/ar.md
# tells the model the paper will print, so a stem that says "choose (ب)" points at what
# is there. Two more than the four the writing is held to, so an exercise that arrives
# with five or six is still lettered rather than set with a question mark.
CHOICE_LETTERS = {"en": "ABCDEF", "ar": "أبجدهو"}


def direction_preamble(profile: Profile) -> str:
    """The labels, and the choice letters, for a book that runs the way this one does."""
    letters = CHOICE_LETTERS[profile.language_code]
    cases = "".join(f"\\or {letter}" for letter in letters)
    mixed_cell = RTL_LATIN_MATH_CELL if profile.rtl and profile.notation == "latin" else ""
    return (
        labels_preamble(profile)
        + mixed_cell
        + f"\\newcommand{{\\choiceletter}}[1]{{\\ifcase#1{cases}\\else ?\\fi}}\n"
    )

# The rule a learner writes an answer on, which the model asks for by name: it is
# told to write \blank wherever an exercise leaves something to fill in, in prose
# and in maths alike. Defining it here is what makes every blank in the book the
# same width, so the line never hints at how long the answer is, and what lets
# that width be changed in one place.
#
# Inside maths it carries its own space on either side, because TeX throws away
# the spaces the model wrote there and the rule would otherwise touch what stands
# beside it: "$5 \blank [2, 5[$" set as 5________[2, 5[. In prose the model's own
# spacing is left to do the work.
BLANK_RULE = r"""\newcommand{\blankrule}{\rule{3em}{1pt}}
\newcommand{\blank}{\ifmmode\mskip5mu\blankrule\mskip5mu\else\blankrule\fi}
"""


def tally_preamble(profile: Profile) -> str:
    """The CLS tally strokes, with the fifth mark slashed the way this book's maths reads.

    The model writes a count as slashes in maths (``////\\backslash``). Those
    become \\CounterTallyMarks here, so a frequency table prints the same grouped
    strokes the old class drew with PDF literals. Latin-notation books (en-en
    and ar-en) lay the group left to right; Arabic-notation books (ar-ar) lay
    it right to left. The group is an ``\\hbox`` of that ``\\textdir`` so the
    kerns and the PDF slash sit on the same side of the verticals — otherwise
    math ``\\text`` on an Arabic page still runs LTR, and the Arabic slash
    is drawn to the left of the strokes instead of through them. A strut the
    height of the ink, ``\\vcenter``ed, keeps the strokes in the middle of a
    table row instead of sitting under 30bp of empty box.
    """
    rtl = profile.notation == "arabic"
    slash = (
        r"\kern3bp\pdfliteral{q 1 w -25 0 m 1 14 l S Q}\tallynum4\kern3bp"
        if rtl
        else r"\kern3bp\pdfliteral{q 1 w -1 0 m 25 14 l S Q}\tallynum4\kern3bp"
    )
    textdir = "TRT" if rtl else "TLT"
    return (
        r"\newcount\tmpnum"
        "\n"
        r"\protected\def\pdfliteral{\pdfextension literal}"
        "\n"
        r"\def\tallyI{\pdfliteral{q 1 w 0 -1 m 0 15 l S Q}}"
        "\n"
        rf"\def\tallyV{{{slash}}}"
        "\n"
        r"\def\tallynum#1{\bgroup\tmpnum=#1\relax"
        "\n"
        r" \loop \ifnum\tmpnum>0"
        "\n"
        r"       \kern3bp \tallyI \kern3bp"
        "\n"
        r"       \advance\tmpnum by-1"
        "\n"
        r"       \repeat"
        "\n"
        r" \egroup}"
        "\n"
        rf"\def\CounterTallyMarks#1{{\ensuremath{{\vcenter{{\hbox{{\textdir {textdir}\relax"
        r"\vrule height 15bp depth 2bp width 0pt"
        "%"
        "\n"
        r"  \tmpnum=#1"
        "\n"
        r"  \loop \ifnum\tmpnum<5 \kern1bp \tallynum\tmpnum \else \tallyV \fi"
        "\n"
        r"        \advance\tmpnum by-5"
        "\n"
        r"        \ifnum\tmpnum>0 \repeat"
        "\n"
        r"}}}}"
        "\n"
    )

# The air inside a table's cells. A table reaches the paper as a maths array, whose
# columns stand \arraycolsep apart and whose rows follow a text-line strut. At those
# defaults a heading touches the rule beside it, and a display fraction sits on the
# rules above and below: the table reads as a grid drawn over its own numbers rather
# than as cells holding them. Horizontal room is \arraycolsep. Vertical room is a
# gap around the cell's own box, so a mixed-number row grows and a heading row does
# not: \arraystretch only scales the strut and would pad every row alike. Each cell
# is then centred on the row, so a tally (or a fraction) does not leave the other
# entries sitting on the baseline at the foot of the cell.
CELL_PAD = 3.0
TABLE_PADDING = f"""\\usepackage{{tabularx}}
\\usepackage{{makecell}}
\\setlength{{\\arraycolsep}}{{{5.0 + CELL_PAD:.1f}pt}}
\\setcellgapes{{{CELL_PAD:.0f}pt}}
\\makegapedcells
\\renewcommand{{\\arraystretch}}{{1}}
\\makeatletter
\\renewcommand\\mcell@classz{{\\@classx
   \\@tempcnta \\count@
   \\prepnext@tok
   \\@addtopreamble{{%
      \\ifcase\\@chnum
         \\hfil
         $\\vcenter{{\\hbox{{\\mcell@agape{{\\d@llarbegin\\insert@column\\d@llarend}}}}}}$\\hfil \\or
         \\hskip1sp
         $\\vcenter{{\\hbox{{\\mcell@agape{{\\d@llarbegin\\insert@column\\d@llarend}}}}}}$\\hfil \\or
         \\hfil\\hskip1sp
         $\\vcenter{{\\hbox{{\\mcell@agape{{\\d@llarbegin \\insert@column\\d@llarend}}}}}}$\\or
         $\\mcell@agape{{\\vcenter
         \\@startpbox{{\\@nextchar}}\\insert@column\\@endpbox}}$\\or
         \\mcell@agape{{\\vtop
         \\@startpbox{{\\@nextchar}}\\insert@column\\@endpbox}}\\or
         \\mcell@agape{{\\vbox
         \\@startpbox{{\\@nextchar}}\\insert@column\\@endpbox}}%
      \\fi
      \\global\\let\\mcell@left\\relax\\global\\let\\mcell@right\\relax
    }}\\prepnext@tok}}
\\let\\@classz\\mcell@classz
\\makeatother
"""

# A table too wide for the column it stands in, held to that width. A table of seven
# columns headed "Ascending cumulative frequency" and "Less than 10" is 60pt wider
# than the measure, and unlike a sentence there is nothing about it that can be
# broken to fit: left alone it runs off the edge and the reader loses the last column
# of it. So a table is set at the size it comes out at where that fits, and scaled
# down to the width it has where it does not, which is what the paper already does
# with a drawing that came out wider than the page. Smaller type in one table is a
# table the student can read; the alternative is not.
TABLE_FIT = r"""\newcommand{\tablefit}[1]{%
\adjustbox{max width=\linewidth}{$\displaystyle #1$}}
"""

# Traditional Arabic verse: a two-column table. Kashida is inserted in Python.
# A row with no & collapses column 2, so a stacked poem is one centered column.
# \begingroup\centering avoids the trivlist \par of \begin{center}, which fights
# a stem wrapped in \qidline.
ARABVERSE_PREAMBLE = r"""\newenvironment{arabverse}[1]{%
  \setlength{\tabcolsep}{20pt}%
  \begingroup\centering
  \begin{tabular}{cc}%
}{%
  \end{tabular}\par
  \endgroup
}
"""

# How many choices a multiple-choice exercise carries. The writing stage holds the
# model to this number, and the layout below is built for it.
MCQ_OPTIONS = 4

# The four choices, labelled here rather than by the model, and laid out on the grid
# the rest of the paper is set on: four across where each fits the room a four-column
# leaves after its number's step, two across where each fits what a two-column leaves,
# and one to a line otherwise. Which of the three it is cannot be decided in Python,
# which has no idea how wide "$\sqrt{17}$" sets, so TeX measures the widest of the four
# and picks from that.
#
# A choice begins where an exercise's wording would begin in the column it stands in,
# which is the step inside that column's own left edge — the same line the wording of
# the exercise above it starts on. So the choices are stepped by a whole column and its
# gutter, not by a share of the room they have: a quarter of the wording's measure is
# 40.5mm where the grid's step is 44, and the drift was plain to see once the grid was
# drawn over a page, three of the four choices standing just left of the column they
# belonged to. \mcq is set inside the wording's own column, so a length here is
# measured from the first step and the boxes carry the choices out to the others.
#
# The last choice of a four-across row is set without a box, so one that
# measures a hair over its column runs on rather than overfilling the line.
# A two-across row boxes both cells to half the list measure: the MCQ lives
# inside the number's left margin, so a page-half \\gridpair box leaves the
# second choice too little room and TeX breaks before its drawing.
#
# Four choices that carry a drawing skip the width test and take the two-by-two
# layout as \mcqfig. \settowidth of a tikzpicture reports the unscaled drawing, so
# the pair branch of \mcq never wins for those options and they would otherwise
# stack four full-height figures — taller than a page.
#
# The letters come from \choiceletter, which direction_preamble() defines for the book's
# language, so this macro is the same for every book: \choice{2}{#2} is "(B) #2" in an
# English paper and "(ب) #2" in an Arabic one. In an Arabic paper the boxes are laid
# right to left by the engine, so (أ) stands at the right of the row and the rest follow
# leftwards, which is how the book's own choices read.
OPTIONS_PREAMBLE = r"""\newlength{\optioncell}
\newlength{\optionwidest}
\newlength{\optionstep}
\newcommand{\choice}[2]{(\choiceletter{#1}) #2}
\newcommand{\optionwiden}[1]{%
\settowidth{\optioncell}{#1}%
\ifdim\optioncell>\optionwidest\setlength{\optionwidest}{\optioncell}\fi}
\newcommand{\mcqpair}[4]{%
\par\nopagebreak\noindent
\setlength{\optionstep}{\dimexpr(\linewidth-\gridgutter)/2\relax}%
\begin{minipage}[c]{\optionstep}\raggedright\choice{1}{#1}\end{minipage}\hfill
\begin{minipage}[c]{\optionstep}\raggedright\choice{2}{#2}\end{minipage}\par
\begin{minipage}[c]{\optionstep}\raggedright\choice{3}{#3}\end{minipage}\hfill
\begin{minipage}[c]{\optionstep}\raggedright\choice{4}{#4}\end{minipage}\par}
\newcommand{\mcq}[4]{%
\par\nopagebreak%
\setlength{\optionwidest}{0pt}%
\optionwiden{\choice{1}{#1}}\optionwiden{\choice{2}{#2}}%
\optionwiden{\choice{3}{#3}}\optionwiden{\choice{4}{#4}}%
\ifdim\optionwidest>\dimexpr\gridpair-\numberroom\relax%
\choice{1}{#1}\par\choice{2}{#2}\par\choice{3}{#3}\par\choice{4}{#4}\par%
\else\ifdim\optionwidest>\dimexpr\gridquad-\numberroom\relax%
\mcqpair{#1}{#2}{#3}{#4}%
\else%
\setlength{\optionstep}{\dimexpr\gridquad+\gridgutter\relax}%
\makebox[\optionstep][l]{\choice{1}{#1}}\makebox[\optionstep][l]{\choice{2}{#2}}%
\makebox[\optionstep][l]{\choice{3}{#3}}\choice{4}{#4}\par%
\fi\fi}
\newcommand{\mcqfig}[4]{%
\mcqpair{#1}{#2}{#3}{#4}}
"""

# The gaps that divide a paper with nothing drawn on it. Space alone has to show the
# reader what belongs with what, so the sizes have to be told apart: the exercises
# under one stem sit closest, single exercises stand a step further apart, a set is
# opened and closed by a wider gap so that it reads as a block, and an objective's
# heading takes the widest of all. Declared ahead of the preamble because the macros
# there are built with them.
# Numbers rather than lengths, because the stage that plans the pages spends them:
# what a page holds is its height less the gaps the blocks on it stand apart by.
SET_GAP = 6.0
EXERCISE_GAP = 12.0
SET_MARGIN = 18.0
OBJECTIVE_GAP = 24.0

# Except directly under the section title, where the widest gap is the wrong one: the
# title carries its own space beneath it already, and a heading is not being separated
# from anything there but stacked under the line that introduces it. Charging both
# leaves the top of every paper opening with a hole in it.
TITLE_GAP = 4.0

# Held back from the height of every page when the pages are planned. The
# arithmetic that fills them is exact to within rounding, and this is what stands
# between that rounding and a page that spills its last block onto the next one,
# taking every page after it along with it.
PAGE_HOLDBACK = 2.0

# How much more to hold back when a plan turns out not to have held. A paper that
# spills is planned again rather than accepted, with a little more room to be wrong
# in each time: three tries at two, ten and eighteen points is the whole of it.
HOLDBACK_STEP = 8.0
PLANNING_TRIES = 3

# A figure small enough to stand beside its wording does, in a column of its own to
# the right, rather than below it across the whole width. It is how a textbook sets a
# figure and it saves the page the band of white either side of a narrow drawing.
#
# The figure takes a whole grid column and the wording takes what is left, which is a
# column less the step the number stands in: the two are not equal, and the figure is
# the wider of them. That is the price of the grid and it is worth paying, because it
# is what puts the left edge of every figure in the book on the same line down the
# page, whatever the exercise beside it happens to be. Where the wording is already
# inside a column — a member of a set — a grid column will not fit and the pair falls
# back to halving what room there is, which is off the grid but is a case that hardly
# arises: a figure that small has room under the wording instead.
#
# A box set [t] hangs from the first baseline of what is in it, which for a drawing is
# wherever that drawing happens to put its own origin: some sit on their feet, some
# hang from their tops, and a column of them lines up by nothing at all. An empty
# first line takes the choice away from the drawing and gives the box its top edge to
# align by. Only the figure needs it: wording has a first baseline of its own, and a
# number set against its top edge instead would stand a line above the words it
# numbers.
#
# Top edge to top edge is still not level with the wording, because a box beside
# wording aligns to that wording's baseline, and the top of a line of type is a
# capital's height above its baseline. The figure is lifted by exactly that, so the
# highest ink in the drawing starts where the highest ink in the first line does.
#
# The wording is set ragged right rather than justified, alone on the paper in being
# so. A column half the measure is too narrow to justify: the spaces between words
# have to stretch to reach the right edge, and in a line carrying two or three terms
# of algebra they stretch until the line reads as a row of fragments. Ragged right
# leaves every space the width it was drawn at and lets the edge fall where it falls,
# which is what the eye does anyway when a figure stands to the right of the words.
#
# No word in the column is ever broken across two lines. The kernel's \raggedright
# ends a line at no cost whatever it leaves over, so TeX gains nothing by hyphenating
# and does not, and the two penalties say so outright: the first forbids a break TeX
# would make inside a word, the second a break at a hyphen that was typed, which
# reads to the eye as the same thing. The cost is that a long word moves down whole
# and leaves the line before it short, and in a column this narrow "perpendicular"
# can take most of a line with it. That is accepted here: a hole is a thing the
# reader's eye passes over, and a broken word is a thing it stops at.
BESIDE_FIGURE = """\\newcommand{\\topaligned}{\\vspace{0pt}}
\\newlength{\\figurelift}
\\AtBeginDocument{\\settoheight{\\figurelift}{A}}
\\newlength{\\figurecolumn}
\\newlength{\\figurewords}
\\newcommand{\\besidefigure}[2]{%
\\setlength{\\figurecolumn}{\\gridpair}%
\\setlength{\\figurewords}{\\dimexpr\\linewidth-\\gridgutter-\\figurecolumn\\relax}%
\\ifdim\\figurewords<0.5\\figurecolumn%
\\setlength{\\figurecolumn}{\\dimexpr0.5\\linewidth-0.5\\gridgutter\\relax}%
\\setlength{\\figurewords}{\\figurecolumn}\\fi%
\\noindent\\begin{minipage}[t]{\\figurewords}%
\\raggedright\\hyphenpenalty=10000\\exhyphenpenalty=10000%
#1\\end{minipage}%
\\hfill%
\\raisebox{\\figurelift}{%
\\begin{minipage}[t]{\\figurecolumn}\\topaligned\\centering#2\\end{minipage}}%
\\par}
"""

# A figure too wide for a column of its own stands centred on a line of its own, with
# this much air above and below it. Without it the drawing sits on the line of words
# above at the interline clearance, four points, which is what two lines of type get
# between them and far too little between a paragraph and a picture: the reader cannot
# see where the wording stops and the figure starts.
#
# A line of the body, so that a figure reads as standing between two paragraphs rather
# than inside one. It is written as the leading and not as a number, because what makes
# the space read right is that it matches the rhythm of the type around it.
#
# The air is asked for with \addvspace and not \vspace, so that two figures set one
# under another are parted by one line rather than two, and it is discarded at a page
# break like any other glue, so a figure landing at the top of a page does not come down
# a line off the head of it.
#
# The box is set as the one line of a paragraph, and not appended to the vertical list,
# which is what it looks like it should be. A box on a vertical list sits flush with the
# left edge of the list it is on, and an exercise's indentation is not on that list: a
# one-item list indents with \parshape, which governs the lines of a paragraph and
# nothing else. Appended, the box came out the right width and ten millimetres to the
# left of the column it belongs to — centred on 100mm rather than 110 — and its ink ran
# back under the exercise's own number. As a paragraph line it is subject to \parshape
# and lands in the column. It costs nothing to set it this way: \parindent is zero
# through the whole paper and \noindent again here, \parskip is the class's zero, and
# the air around the figure is still only what is asked for below.
#
# The two penalties are what keep the picture with the wording that calls for it,
# discouraging the break rather than forbidding it: the room for the whole block was
# asked for before the wording was set, and a figure that will not fit anywhere must
# still be allowed to move.
#
# The space is an argument and not a constant because one figure wants none of it: the
# figure that is the whole of its exercise, which a set of drawings under one stem is
# four of. There is no wording for it to be told apart from, so the air separates it
# from nothing; what it does instead is push the drawing down the page away from the
# number that names it, which is set against the top of the box the figure is in.
FIGURE_SPACE = f"{BODY_LEADING:.0f}pt"
WIDE_FIGURE = f"""\\newcommand{{\\widefigure}}[2][{FIGURE_SPACE}]{{%
\\par\\nopagebreak[4]\\addvspace{{#1}}\\nopagebreak[4]%
\\noindent\\hbox to\\linewidth{{\\hfil#2\\hfil}}\\par%
\\nopagebreak[4]\\addvspace{{#1}}\\nopagebreak[4]}}
"""

# The most members a set will put on one line, and the most a set with a figure in it
# will. A cell spends NUMBER_ROOM on its number before its own wording starts, exactly
# as an exercise across the page does, which is what asks whether a figure fits the
# rest of the cell.
SET_SPAN = 4
SET_FIGURE_SPAN = 2

# The most a figure may be shrunk to make it share a line. A drawing is compiled at
# the size the paper's own type is set at, so shrinking it shrinks its labels: at this
# much they read as small print, and below it they stop being readable at all. Some
# tolerance is needed either way, or a set is sent back to one member a line by a
# single figure drawn a few points too wide.
SET_FIGURE_SHRINK = 0.85

# What an exercise's wording and everything under it is set across: the measure, less
# the column the numbers stand in.
NUMBERED_WIDTH = TEXT_WIDTH - NUMBER_ROOM

# The two widths the paper sets a figure at, in points. Centimetres are what a drawing
# is described in, and POINTS_PER_CM converts them.
#
# The first is for the figure that shares its line with something: the narrower of
# the two columns on offer — a whole grid column, where the figure stands beside the
# wording of its own exercise, and a grid column less the number's step, where it
# stands inside the cell of a set laid out two across — so that a figure drawn to it
# can be set either way. The second is for the figure too flat to be read in a column,
# which
# takes the whole measure and stands centred under the wording instead, drawn just
# inside the room there is so that nothing has to be shrunk to fit.
#
# Both are derived rather than chosen, so a change to the margin, to the gutter or to
# the room the numbers take moves the drawings with it.
FIGURE_BUDGET = min(grid_column(2), grid_column(SET_FIGURE_SPAN) - NUMBER_ROOM)
FIGURE_WIDE = 0.9 * NUMBERED_WIDTH

# Short members of a set share a line: as many across as their wording allows, up to
# the most the set is willing to take. Which it is cannot be settled here, because it
# depends on how wide the type sets, so the set is handed to TeX twice — once with
# \setitem measuring every member, once with it placing them, by which time the
# widest is known. What the widest is measured against is the grid: a member that fits
# a four-column is set four across, one that fits a two-column two across, and one
# that fits neither takes a line to itself. The gutter parts the cells, so a member is
# free to fill its column to the edge — the gap after it is the grid's, not slack left
# over from a threshold set a shade under the column it guards.
#
# \setitem is given the wording to measure and the whole member to place, which are
# the same thing for most members and are not where one carries a figure: measuring
# the figure would report the width of the drawing and send every such set back to
# one member a line, which is the layout being avoided.
#
# It is given the member twice, because how a member is set depends on the column it
# lands in and the column is what TeX is being asked to choose. A figure small enough
# to stand beside its wording across a whole line does not fit beside it in half of
# one, so the same member is two different layouts in the two cases; rendered for the
# shared column alone, it would be set with its figure beneath its wording and then
# placed on a line of its own, which is the band of white this avoids. The second
# rendering is left empty wherever the two would come out the same — which is every
# member carrying no figure, since nothing else here depends on the width.
#
# A row may fall to the next page, and must be allowed to: a set of figures runs to
# hundreds of points a row, and forbidding the break makes the whole set one block
# that no page has room for, which strands the stem at the foot of a page on its own.
#
# A member is set in a box as wide as its column, its number flush right in the same
# box the numbers down the page use, and everything else in a \parbox beside it. The
# \parbox is what makes the one-to-a-line case work as well as the others: a member
# too long to share a line still wraps inside its own column, aligned under itself
# rather than under its number, and a figure inside it is held to the column because
# adjustbox was asked for the line's width, which in there is the column's.
#
# \parbox restores a justified paragraph (\@parboxrestore zeros \rightskip), so a
# wrapping member stretches the spaces between its algebra until a line of two
# formulae reads as a row of fragments. \raggedright is put back on inside the
# box, as \besidefigure already does.
#
# A row starts with \noindent so the two \makebox cells share a paragraph. Fitting
# and the planned paper both set a block in a \vbox, where a \makebox after \par
# is a vertical-list item of its own and would stack the members one per line.
SET_GRID = f"""\\newlength{{\\setcellwidth}}
\\newlength{{\\setwidest}}
\\newlength{{\\setcolumn}}
\\newcounter{{setplaced}}
\\newif\\ifsetstarted
\\newcommand{{\\setspan}}{{1}}
% The member as it is set: the shared-column rendering, or the whole-line one where
% there is one and the line is what this member got.
\\newcommand{{\\setbody}}[2]{{%
\\ifnum\\setspan=1 \\ifx\\relax#2\\relax#1\\else#2\\fi\\else#1\\fi}}
\\newcommand{{\\setmeasure}}[5]{{%
\\settowidth{{\\setcellwidth}}{{\\makebox[\\exercisenumber][r]{{\\exerciselabel{{#1}}}}%
\\hspace{{\\labelgap}}#2}}%
\\ifdim\\setcellwidth>\\setwidest\\global\\setlength{{\\setwidest}}{{\\setcellwidth}}\\fi}}
\\newcommand{{\\setplace}}[5]{{%
\\ifnum\\value{{setplaced}}=0%
\\ifsetstarted\\par\\vspace{{{SET_GAP:.0f}pt}}\\fi\\setstartedtrue%
\\noindent
\\fi%
\\qidstart{{#5}}{{\\setcolumn}}%
\\makebox[\\setcolumn][l]{{%
\\makebox[\\exercisenumber][r]{{\\exerciselabel{{#1}}}}\\hspace{{\\labelgap}}%
\\parbox[t]{{\\dimexpr\\setcolumn-\\numberroom\\relax}}{{\\raggedright\\setbody{{#3}}{{#4}}\\par\\qidend{{#5}}{{\\setcolumn}}}}}}%
\\stepcounter{{setplaced}}%
\\ifnum\\value{{setplaced}}<\\setspan\\hspace*{{\\gridgutter}}%
\\else\\setcounter{{setplaced}}{{0}}\\fi}}
\\newcommand{{\\setgrid}}[2]{{%
\\par\\global\\setlength{{\\setwidest}}{{0pt}}%
{{\\let\\setitem\\setmeasure #2}}%
\\ifdim\\setwidest>\\dimexpr(\\linewidth-\\gridgutter)/2\\relax%
\\renewcommand{{\\setspan}}{{1}}%
\\else\\ifdim\\setwidest>\\dimexpr(\\linewidth-3\\gridgutter)/4\\relax%
\\renewcommand{{\\setspan}}{{2}}%
\\else\\renewcommand{{\\setspan}}{{#1}}\\fi\\fi%
\\ifnum\\setspan=1\\setlength{{\\setcolumn}}{{\\linewidth}}%
\\else\\ifnum\\setspan=2\\setlength{{\\setcolumn}}{{\\dimexpr(\\linewidth-\\gridgutter)/2\\relax}}%
\\else\\setlength{{\\setcolumn}}{{\\dimexpr(\\linewidth-3\\gridgutter)/4\\relax}}\\fi\\fi%
\\setcounter{{setplaced}}{{0}}\\setstartedfalse%
{{\\let\\setitem\\setplace #2}}%
\\par}}
"""

# How the paper heads the exercises for one objective: a small black square, a space, and
# the objective's short name, set bold and in capitals at the size the body is set at. The
# square and the capitals are what mark it as a division of the paper rather than a line of
# its prose, and they do it without the heading standing taller than the text it heads —
# there are five or six of them to a section, and every point of size they took was a
# point off the page. The name is held to what follows it, both by the space beneath it and
# by asking for enough of the page to be left that a heading cannot end up alone at the
# foot of one.
#
# The square is cut to the height of a capital in whatever font the heading is set in,
# read from the font itself with \fontcharht rather than written down as a length, so it
# stays a square of that height if the heading is ever set at another size. It stands half
# its own width off the name, which is a space that holds at any size for the same reason.
#
# Which letter it is read from depends on the book. Arabic has no capitals and the Arabic
# face has no A — asked for its height the engine answers nothing, and the square came out
# a point with no size at all — so an Arabic heading takes the height of an alif, the
# letter that stands tallest on the line, which is the same idea in that script.
#
# The rule under it runs from the left edge of the square to the end of the name and no
# further, which is what \uline does and what a rule of a fixed width cannot: the name is
# whatever the objective is called. Capitals are what make it possible — a rule on the
# baseline of a name in sentence case is crossed by every descender in it.
OBJECTIVE_HEAD_ROOM = r"4\baselineskip"
OBJECTIVE_TEXT_GAP = "8pt"
SQUARE_GLYPH = {"en": "A", "ar": "ا"}


def square_side(profile: Profile) -> str:
    return f"\\fontcharht\\font`{SQUARE_GLYPH[profile.language_code]}"


def objective_square(profile: Profile) -> str:
    side = square_side(profile)
    return f"\\rule{{\\dimexpr{side}\\relax}}{{\\dimexpr{side}\\relax}}"


def objective_square_gap(profile: Profile) -> str:
    return f"\\hspace{{\\dimexpr{square_side(profile)}/2\\relax}}"
# Set from the body's own size and leading rather than written down again, so that a
# heading at the size of the text stays at the size of the text if the text is ever set at
# another. The leading is what a heading that runs to a second line needs, and it does
# nothing to one that fits on a single line.
#
# A heading is set ragged right and never hyphenated. Justified, a title that runs
# a little past one line has to be stretched to the measure, and where the words
# are long enough that no break will do TeX gives up and lets the line hang into
# the margin with a word split across it: "ASCENDING CUMULATIVE FREQUENCY TABLE AND
# ITS GRAPH-" over the edge of the paper, and the "S" on the line below.
#
# \par goes inside the group with \raggedright, not after it. A paragraph is broken
# into lines at the \par, using whatever \rightskip is current then, so a \par
# outside the group is broken with the body's justification and the \raggedright
# does nothing at all.
#
# \raggedright is written for both directions. Under babel an Arabic paragraph runs the
# other way and the ragged edge falls at the left, where it belongs: the lines still
# begin together at the margin the reading starts from.
OBJECTIVE_HEAD_SIZE = f"{BODY_SIZE}pt"
OBJECTIVE_HEAD_LEADING = f"{BODY_LEADING:.0f}pt"


def objective_head(profile: Profile) -> str:
    return f"""\\newcommand{{\\objectivehead}}[1]{{%
\\par\\ifplanned\\else\\needspace{{{OBJECTIVE_HEAD_ROOM}}}\\fi%
{{\\fontsize{{{OBJECTIVE_HEAD_SIZE}}}{{{OBJECTIVE_HEAD_LEADING}}}\\selectfont%
\\bfseries\\raggedright\\hyphenpenalty=10000\\exhyphenpenalty=10000%
\\textcolor{{{HEADING_COLOUR}}}{{\\MakeUppercase{{\\uline{{%
{objective_square(profile)}{objective_square_gap(profile)}#1}}}}}}\\par}}\\nopagebreak%
\\vspace{{{OBJECTIVE_TEXT_GAP}}}\\nopagebreak\\par}}
"""

# A block set as a single box, which is what makes a planned page hold: nothing
# inside a box can fall to the next page, so where each question goes is settled
# before the paper is written and TeX is left with no page breaking to do at all.
#
# It also makes the arithmetic exact. The space a box takes above itself is
# \baselineskip less the depth of what came before and less the box's own height,
# or \lineskip where that is too little, and both of those are measured, so what a
# page holds can be added up in Python to a fraction of a point rather than
# guessed at.
UNBROKEN = r"""\newif\ifplanned\plannedfalse
\newcommand{\unbroken}[1]{\vbox{#1}}
% Room asked for ahead of a block, and asked for only where TeX is the one deciding
% where the pages end. A planned paper asks for nothing: every block on a planned
% page is set in a box that cannot break, so a request for room could be granted
% only by breaking where the plan says not to, and \needspace inside a box is worse
% than useless — it ends in a negative skip that a page stretches away and a box at
% its natural height does not, so the block would come out short of its own content.
\newcommand{\reserve}[1]{\ifplanned\else\Needspace{#1}\fi}
"""

# The measuring document: every block set in a box that is never printed, with the
# height and depth it came out at written to a file of its own. A file rather than
# the log, because the log wraps its lines at 79 columns and a wrapped number is
# one that has to be pieced back together.
MEASURE_BLOCKS = r"""\newwrite\measurements
\immediate\openout\measurements=\jobname.sizes
% Every block is set in a box of its own, kept until the run ends, rather than in
% one box reused and so emptied at each measurement. Emptying it is what LuaTeX's
% "attribute list that is free already" came from: babel's bidi keeps a hand on
% nodes of a paragraph it has processed, and freed a second time, over a hundred
% blocks of vocalised Arabic holding mathematics, they brought the run down. A
% section's hundred-odd boxes are nothing to the register file.
% The height and the depth are separated by a comma and not by a space, because a
% space after the number of a box is a space TeX skips while it is still reading
% that number, and the two lengths come out written into one.
\newcommand{\measure}[2]{%
\expandafter\newbox\csname measured@#1\endcsname%
\setbox\csname measured@#1\endcsname=\vbox{#2}%
\immediate\write\measurements{#1 \the\ht\csname measured@#1\endcsname, \the\dp\csname measured@#1\endcsname}}
"""

FONT_DIR = Path(__file__).resolve().parent.parent.parent / "fonts"

# The display faces the section number has been set in, each as the file it is read from
# and the folder holding it, and below them the one the book is set in now. They all live
# in the repository rather than being installed, so a book builds the same on a machine
# that has never heard of any of them, and each is named by file and found by path for the
# same reason.
#
# Written as a list and a choice because the choice has been made and unmade several times,
# and a face named in prose is a face whose prose has to be rewritten to change it. What
# changes now is NUMERAL_FACE_NAME and nothing else. A face tried and dropped is left in
# the list rather than deleted, so coming back to it is the one line again and not a
# rediscovery; a face that is no longer wanted at all can go from here, and its file with
# it.
#
# The folder differs by where a face came from. Myriad Pro shipped as OpenType and is read
# out of the folder Adobe packaged it in. The rest arrived as Type 1 — a PFB of
# outlines with an AFM of widths beside it — which LuaTeX will read but not safely:
# luaotfload keeps no cache for a Type 1 face, so every compile parses it again, and a book
# is set as seventeen papers compiling at once. Run that way the engine segfaults on about
# a third of them, in no fixed place, leaving a log that stops in the middle of the
# preamble and a section missing from the book. Such a face is converted once by
# fonts/type1_to_otf.py and the OTF put in fonts/ itself, beside the vendor folder it was
# made from rather than in it, because it is a file this repository made and not one it was
# given. That script says what the conversion keeps, what it drops, and what else was tried
# first, including the one way a Type 1 face can be loaded directly and why it is not used.
NUMERAL_FACES = {
    "Eurostile Bold": ("Eurostile-Bold.otf", FONT_DIR),
    "Eurostile Bold Extended": ("Eurostile-BoldExtendedTwo.otf", FONT_DIR),
    "Antique Olive Nord": ("AntiqueOlive-Nord.otf", FONT_DIR),
    "Myriad Pro Black SemiExtended": ("MyriadPro-BlackSemiExt.otf", FONT_DIR / "Myriad Pro"),
}
NUMERAL_FACE_NAME = "Eurostile Bold Extended"
NUMERAL_FONT, NUMERAL_DIR = NUMERAL_FACES[NUMERAL_FACE_NAME]
NUMERAL_FACE = f"""\\newfontfamily\\numeralface{{{NUMERAL_FONT}}}[
  Path = {NUMERAL_DIR}/,
  BoldFont = {NUMERAL_FONT} ]
"""

# The face an Arabic book is set in, and how the page is turned round for it.
#
# Noto Naskh Arabic, chosen over XITS and Amiri on a page of each: a naskh with an even
# line and no calligraphic flourish, which is what a practice paper wants of it. It ships
# in the repository like the display faces, for the same reason. Regular and Bold are the
# two weights the paper uses; the family has no italic, and fontspec falls back to upright
# where one is asked for, which nothing in the paper does.
#
# The face carries Arabic, the digits, and of ASCII only the space, the full stop, the
# comma, the colon and the exclamation mark. Everything else — a bracket in prose, a dash,
# a percent sign, a Latin letter in a unit or a name — has no glyph in it and would be
# dropped with a line in the log. Those characters are given to STIX Two Text instead,
# through luaotfload's fallback: a glyph the face has not got is looked for in the next
# face on the list, weight for weight, so a bold bracket comes from the bold. The paper's
# maths is STIX Two Math already, so a Latin letter in Arabic prose and the same letter
# in the maths beside it are one design.
#
# babel's bidi=basic is what turns the page. It sets the paragraph and the body right to
# left and reorders each line by the Unicode bidirectional algorithm, so a run of Latin
# or a number in an Arabic sentence reads left to right inside it and the maths, which is
# a box, stands where the sentence puts it. layout=lists is what puts a list's label on
# the right; tabular and graphics mirror a tabular's columns and put an included picture
# where the sentence is. English is provided beside Arabic so that a run of Latin letters
# is hyphenated as English rather than as Arabic; onchar=ids is what switches on the
# letters themselves rather than on a command the model would have to write. Its
# companion onchar=fonts, which would switch the face too, is left off on purpose: it
# rewrites the font of each glyph inside babel's bidi pass, and over a section of
# vocalised Arabic holding maths that pass touched nodes LuaTeX had already freed and
# brought the run down with "attribute list that is free already". The face is
# switched by the fallback above instead, glyph by glyph, which needs no Lua of babel's;
# Latin glyphs the face does have and that still want the English face — exercise
# labels, and western 0-9 in Maghreb prose — ask for it by name (RTL_LABELS,
# wrap_stix_digits).
#
# The fallback faces are shaped by HarfBuzz as the Arabic face is (babel gives an Arabic
# face to HarfBuzz of itself), and as Arabic. A bracket in right-to-left text is drawn
# the other way round, so that "(ب)" opens on the right; HarfBuzz does that turning for
# a right-to-left run, and babel leaves it to HarfBuzz for a face HarfBuzz shapes. Shaped
# by luaotfload's own node mode instead, the fallback's brackets fell between the two,
# turned by neither, and every "(ب)" on the paper came out ")ب(".
ARABIC_FONT_DIR = FONT_DIR / "Noto Naskh Arabic"
LATIN_ARABIC_SNIPPET = rf"""
\newfontfamily\arabicsnippetfont[
  Path         = {ARABIC_FONT_DIR}/,
  Extension    = .ttf,
  UprightFont  = *-Regular,
]{{NotoNaskhArabic}}
\newcommand{{\arabicsnippet}}[1]{{{{\arabicsnippetfont #1}}}}
"""
# Kohinoor is the macOS Devanagari face. STIX Two and NagwaMath have no U+096B.
LATIN_DEVANAGARI_SNIPPET = r"""
\newfontfamily\devanagarisnippetfont{Kohinoor Devanagari}
\newcommand{\devanagarisnippet}[1]{{\devanagarisnippetfont #1}}
"""
ARABIC_TEXT_FONT = f"""\\usepackage[bidi=basic,layout=lists tabular graphics]{{babel}}
\\babelprovide[import,main]{{arabic}}
\\babelprovide[import,onchar=ids]{{english}}
\\directlua{{
  luaotfload.add_fallback("latinfallback", {{"file:STIXTwoText-Regular.otf:mode=harf;script=arab;"}})
  luaotfload.add_fallback("latinfallbackbold", {{"file:STIXTwoText-Bold.otf:mode=harf;script=arab;"}})
}}
\\babelfont{{rm}}[
  Path         = {ARABIC_FONT_DIR}/,
  Extension    = .ttf,
  UprightFont  = *-Regular,
  BoldFont     = *-Bold,
  RawFeature   = {{fallback=latinfallback}},
  BoldFeatures = {{RawFeature={{fallback=latinfallbackbold}}}} ]{{NotoNaskhArabic}}
\\babelfont[english]{{rm}}[
  Extension      = .otf,
  UprightFont    = *-Regular,
  BoldFont       = *-Bold,
  ItalicFont     = *-Italic,
  BoldItalicFont = *-BoldItalic ]{{STIXTwoText}}
"""


def text_font(profile: Profile) -> str:
    """The text face and, where the book needs it, the direction the page runs."""
    if profile.rtl:
        return ARABIC_TEXT_FONT
    return LATIN_TEXT_FONT + LATIN_ARABIC_SNIPPET + LATIN_DEVANAGARI_SNIPPET


# The section number of a book with Arabic-Indic numerals is set in the display face's
# Arabic cut: Eurostile Bold Extended with the ten Arabic-Indic digits drawn in-house to
# the Latin figures' cap height, advance and side bearings, so the number stands at the
# weight and height of an English book's and NUMERAL_SHIFT lands its first digit on the
# rule as it does there. The point between the digits is the face's own. The text face's
# bold was used before it: a naskh scaled to that size is a text letter blown up, and
# stood lighter, taller and further off the rule than the Latin number on the facing book.
#
# The file is the Latin OTF beside it plus those ten glyphs, and is read by file and path
# like the rest. A book in Arabic that keeps Western digits never comes here: its number
# is set in the Latin face, which has all it needs.
ARABIC_NUMERAL_FONT = "Eurostile-BoldExtendedTwoAR.otf"
ARABIC_NUMERAL_FACE = f"""\\newfontfamily\\numeralface{{{ARABIC_NUMERAL_FONT}}}[
  Path = {FONT_DIR}/,
  BoldFont = {ARABIC_NUMERAL_FONT} ]
"""


def numeral_face(profile: Profile) -> str:
    """The face the section number is set in, which is the display face unless the digits rule it out."""
    return ARABIC_NUMERAL_FACE if profile.indic_digits else NUMERAL_FACE

# Latin-notation maths: NagwaMath, the same face CLS sets for an English formula,
# shipped in fonts/ like the Arabic maths face. Stylistic set 8 is the cut CLS
# asks for. Operator names — \cos, \sin, \log — would otherwise be \mathrm and
# take letters from the text roman. Noto Naskh has no Latin, and luaotfload's
# fallback does not run for a math alphabet, so those letters vanished from the
# log. \symup takes them from this face, which has them.
LATIN_MATHS_FONT = f"""\\setmathfont[
  Path         = {FONT_DIR}/,
  Extension    = .otf,
  StylisticSet = 8 ]{{NagwaMath}}
\\setoperatorfont{{\\symup}}
"""

# The maths of a book written in Arabic notation: Arabic letters for its unknowns and its
# points, and the whole of it laid out right to left, so that "$3س + 12 = 27$" reads from
# the right as the sentence around it does — the 3س first, then the plus, then the 12 —
# while each number in it still reads left to right. Mashriq books print those numbers
# as Arabic-Indic digits; Maghreb books keep 0-9 and still reverse each run.
#
# The face is NagwaArabicMath, which ships in the repository like the text face and for
# the same reason. It is the CLS Arabic math face: a MATH table, the rtlm feature, and
# the isolated and initial glyphs of Unicode's Arabic Mathematical Alphabetic Symbols
# at U+1EE00. rtlm is what turns the symbols round: an OpenType feature that swaps a
# bracket, a radical, an arrow, a relation for its mirrored form, which is what each of
# them is in a formula read from the right. It is asked for on the face as a whole,
# since every glyph in an Arabic formula is read that way.
#
# The engine does the laying out: \mathdir TRT sets every formula in the document right
# to left, atoms in reading order from the right margin, a superscript to the left of
# its base, a fraction bar with what was written first on top as before. What the
# engine does not know is that a number is not read that way, and arabic_maths.lua is
# what tells it — the same file every such paper and figure loads, which says in its own
# comments what it does and why it works on the engine's atoms rather than on the text.
#
# The letters of a formula are CLS macros, rewritten from the ordinary Arabic the model
# wrote: $\overline{أب}$ becomes $\overline{\alef\beh}$, and a textbook tatweel asks
# for the initial form, $\overline{جـد}$ becoming $\overline{\jeemi \dal}$. Bare
# ج is \\jeemi as well: that is the shape a formula uses. arabic_math
# is the rewrite and the \Umathchardef lines; a letter that still arrives as Unicode is
# mapped onto the same glyph by setmathcode, class 7 so that it follows the maths family
# as a Latin letter would. Heh has no isolated slot, so it is the initial form.
#
# The digits, the decimal and thousands marks and the per cent sign are ordinary
# characters of the maths; the Arabic comma is punctuation, as the comma is in a Latin
# formula, so a list of values gets the same thin space after each. LuaTeX lays the
# formula out right to left itself, and babel's bidi pass must then leave its glyphs
# alone: told nothing, it takes a formula's digits and operators for text and regroups
# them, undoing the layout. Every glyph the maths produces is therefore marked, as it
# leaves the maths, as already laid out.
ARABIC_MATHS_LUA = Path(__file__).resolve().parent.parent / "arabic_maths.lua"


def arabic_maths_font(profile: Profile) -> str:
    """NagwaArabicMath, RTL layout, and the digit policy this book's numerals choose."""
    western = not profile.indic_digits
    ascii_mathcodes = ""
    if western:
        ascii_mathcodes = """  for c = 0x30, 0x39 do tex.setmathcode(c, {0, 0, c}) end
  tex.setmathcode(0x2E, {0, 0, 0x2E})
  tex.setmathcode(0x25, {0, 0, 0x25})
"""
    western_lua = "true" if western else "false"
    return f"""\\setmathfont[
  Path       = {FONT_DIR}/,
  Extension  = .otf,
  RawFeature = {{+rtlm}} ]{{NagwaArabicMath-12-25}}
\\directlua{{
  for c = 0x660, 0x669 do tex.setmathcode(c, {{0, 0, c}}) end
  for _, c in ipairs({{0x66A, 0x66B, 0x66C}}) do tex.setmathcode(c, {{0, 0, c}}) end
  tex.setmathcode(0x60C, {{6, 0, 0x60C}})
{ascii_mathcodes}  {arabic_math.mathcode_lua()}
  for c, g in pairs(isolated) do tex.setmathcode(c, {{7, 0, g}}) end
  luatexbase.add_to_callback("post_mlist_to_hlist_filter", function(head, style, penalties)
    for n in node.traverse(head) do node.set_attribute(n, Babel.attr_dir, 128) end
    return head
  end, "maths opaque to babel bidi")
  arabic_maths_western_digits = {western_lua}
  dofile("{ARABIC_MATHS_LUA}")
}}
{arabic_math.macros_tex()}\\AtBeginDocument{{\\mathdir TRT}}
"""


def maths_font(profile: Profile) -> str:
    """The maths face, and for Arabic notation the direction and the letters of its maths."""
    return arabic_maths_font(profile) if profile.notation == "arabic" else LATIN_MATHS_FONT


def type_preamble(profile: Profile) -> str:
    """The type setup for one kind of book: packages, its text face, its maths."""
    return TYPE_PACKAGES + text_font(profile) + maths_font(profile) + TYPE_MATHS

# How the paper announces the section: the number on a line of its own, big and black, with
# the title under it in capitals eight points over the body. It was grey while it stood
# behind the title, where it had to be read through; on a line of its own nothing is read
# through it, and at that size it is what a thumb finds when a book is flicked through.
#
# One rule, and it is the number's. After \par, \prevdepth is the depth of the line just
# broken, so a kern of minus that lands the rule on the number's baseline, and a rule of no
# height and a little depth hangs below it rather than through it. It spans the measure
# because an \hrule knows nothing of the words above it, which is what is wanted of the way
# into a section. The title under it is left unruled, so that what closes the heading is
# said once.
#
# A rule leaves \prevdepth at -1000pt, meaning TeX adds no interline glue at all after it,
# so the air under it has to be asked for: without a gap of its own the capitals of the
# title would come up against the rule. TeX puts no glue around a rule either, so all the
# stage that plans the pages has to account for is that fraction of a point and the gap.
SECTION_HEAD_SIZE = "20pt"
SECTION_HEAD_LEADING = "24pt"
SECTION_HEAD_GAP = "12pt"
NUMERAL_SIZE = "72pt"
NUMERAL_COLOUR = "black"
NUMERAL_RULE_GAP = "7pt"
# How far the number is pulled left of the margin, so that the figure it starts with sits on
# the end of the rule beneath it rather than a few points inside it. A glyph carries air at
# its sides, and at this size the figures' is around five points of it: left alone the number
# stands off the margin while the rule runs to it, and the two read as not quite lined up.
#
# One amount for every number, which is the point of it. Measuring each figure and shifting
# by its own bearing would put every first digit exactly on the rule, and would also mean
# 2.3 and 3.5 starting at two different places on two facing pages — the alignment the eye
# actually checks is between one section's opening and the next, not between a digit and a
# rule. So the amount is the face's own figure bearing, 0.07 of the em: the 1, the 2 and the
# 5 land exactly on the rule, the 3 passes it by a third of a point and the 4 by a point,
# which is under the width of the rule itself.
#
# In em rather than points so that it follows NUMERAL_SIZE, which it is a fraction of; this
# face's em is its size to the point, so the two agree. The figure is Eurostile's, though,
# and a different display face would want its own: it is the median of what its digits carry,
# which fonts/type1_to_otf.py works out as figure_bearing() when it redraws the 1.
NUMERAL_SHIFT = "0.07em"
# The rule under the number, at the weight of the line a learner writes an answer on, which
# is the heaviest mark the paper otherwise makes: under a number that size a hairline reads
# as the edge of something rather than as a rule. A weight of its own, not the blank's, so
# that this one can be tried at other weights without the answer lines following it.
NUMERAL_RULE = "1pt"
HEAD_LINE = f"\\kern-\\prevdepth\\hrule height 0pt depth {NUMERAL_RULE}\\relax"
# The rule under an objective's name is lighter than the number's: there are five or six of
# them to a section against one number, and at the number's weight they would be the loudest
# thing on the page. \uline draws its rule \ULdepth below the baseline and \ULthickness
# thick, so a depth and a thickness of the same length put its top edge on the baseline.
#
# \uline draws a line as a chain of rules, one to a word and one to a space, and ulem laps
# each of them a three-hundredth of an inch over the next so that no seam can open up
# between them on a low-resolution printer. That lap is also a quarter of a point of rule
# before the first thing underlined, which under a heading that opens with a square reads
# as the rule starting before the square rather than with it. Zeroed, the rule begins
# exactly where the square does, and the chain still meets end to end to within a
# thousandth of a point, which is a hundredth of a pixel at any resolution worth printing
# at.
#
# The rule hangs from the baseline in Arabic as well. An Arabic name has no capitals to sit
# above it, and its letters hang below the baseline as a matter of course, so the rule runs
# through the tail of every one of them; setting it under the descenders instead was tried
# and turned down, as the rule then floated well clear of the shorter letters and the two
# languages' headings no longer matched.
OBJECTIVE_RULE = "0.7pt"
UNDERLINE = f"""\\usepackage[normalem]{{ulem}}
\\renewcommand{{\\ULthickness}}{{{OBJECTIVE_RULE}}}
\\renewcommand{{\\ULdepth}}{{{OBJECTIVE_RULE}}}
\\makeatletter\\UL@pixel=\\z@\\makeatother
"""

# The number's shift is written for a page that runs either way: \hspace* before the
# number is a space at its start, which is at the left margin of an English paper and the
# right margin of an Arabic one, and the digits' side bearings are the same on both sides
# (0.070em either way, measured from the face), so one amount serves both.
SECTION_HEAD = f"""\\newcommand{{\\sectionhead}}[2]{{%
{{\\fontsize{{{SECTION_HEAD_SIZE}}}{{{SECTION_HEAD_LEADING}}}\\selectfont%
\\bfseries\\raggedright\\hyphenpenalty=10000\\exhyphenpenalty=10000%
\\noindent{{\\numeralface\\fontsize{{{NUMERAL_SIZE}}}{{{NUMERAL_SIZE}}}\\selectfont%
\\hspace*{{-{NUMERAL_SHIFT}}}\\textcolor{{{NUMERAL_COLOUR}}}{{#1}}}}\\par\\nopagebreak%
{HEAD_LINE}\\nopagebreak%
\\vspace{{{NUMERAL_RULE_GAP}}}\\nopagebreak%
\\noindent{{\\color{{{HEADING_COLOUR}}}{{#2}}}}\\par}}\\nopagebreak%
\\vspace{{{SECTION_HEAD_GAP}}}\\par}}
"""

# The line that runs along the foot of every page: who the paper is from and what it
# is for, and where the reader is in it. Set small and centred in the bottom margin,
# where \footskip puts it clear of the text: the page the paper prints on is untouched
# by it, and so is the arithmetic that plans what goes on that page.
#
# Both numbers in "Page n of m" are the book's, not the section's, because the book is
# what is read. A section knows neither of them while it is being set — its first page
# depends on every section before it — so both are written in as a counter and a macro
# that the stamping pass rewrites once every section has been set and the totals are
# known. Until then they stand at 1, which is what the measuring pass sees and does not
# care about.
#
# Words alone, with no rule under them, and in mixed case: the foot is the one thing on the
# paper a reader is meant to pass over, and a rule or a line of capitals is a mark that asks
# to be noticed.
#
# Set in white, so the line is in the file without being on the page: it prints as nothing,
# and a reader who wants to know where they are drags across the foot and the selection shows
# it. What the words say and where they sit is unchanged, so putting the ink back is a matter
# of naming a grey here — black!30 was the last one tried and read well.
#
# "Page n of m" is said in the book's language; the publisher and the series keep their
# names. The stamping pass rewrites the two numbers by their counters and never the words,
# so the words are free to differ.
PUBLISHER = "NovaStax"
SERIES = "Practice Book"
FOOT_SEPARATOR = "\u2022"
FOOT_SIZE = "9pt"
FOOT_LEADING = "11pt"
FOOT_COLOUR = "white"
PAGE_OF = {
    "en": "Page {page} of {pages}",
    "ar": "صفحة {page} من {pages}",
}


def page_of(profile: Profile) -> str:
    """The 'page n of m' of the foot, its two numbers in the digits the book prints."""
    if profile.indic_digits:
        return PAGE_OF[profile.language_code].format(
            page="\\indic{\\thepage}", pages="\\indic{\\bookpages}"
        )
    return PAGE_OF[profile.language_code].format(page="\\thepage\\", pages="\\bookpages")


def foot_style(profile: Profile) -> str:
    return f"""\\newcommand{{\\footfixed}}{{}}
\\newcommand{{\\bookpages}}{{1}}
\\makeatletter
\\newcommand{{\\ps@paper}}{{%
\\renewcommand{{\\@oddhead}}{{}}\\renewcommand{{\\@evenhead}}{{}}%
\\renewcommand{{\\@oddfoot}}{{\\hfil{{\\fontsize{{{FOOT_SIZE}}}{{{FOOT_LEADING}}}\\selectfont%
\\color{{{FOOT_COLOUR}}}%
\\footfixed\\ {FOOT_SEPARATOR}\\ {page_of(profile)}}}\\hfil}}%
\\renewcommand{{\\@evenfoot}}{{\\@oddfoot}}}}
\\makeatother
\\pagestyle{{paper}}
"""

# The grid, for looking at a page while the layout is being settled. It is no part of
# the paper: it is set as a page of its own, on the paper's own trim size and nothing
# else on it, and whoever wants to see it lays that page under a bound book. So the
# book the grid is read on and the book that is handed out are the same pages twice
# over, and there is no question of the lines having moved anything.
#
# Black marks where a column begins and ends. Only the four-column edges are drawn,
# because a two-column's edges are four of those same eight lines: 19 and 103mm,
# 107 and 191mm. One black therefore states the whole grid, and a figure standing in
# a pair column has its edges marked as surely as a member of a four.
#
# Black closes the grid at the head and the foot as well, across the measure at the
# top and bottom margins, so that what is drawn is the box the text sits in rather
# than eight lines running off the page. The head line is the one a first block
# should sit on and the foot line is the one a page fills to, which is what the
# planning is arithmetic about; drawn, they are what the eye checks that arithmetic
# against.
#
# Red marks where the wording of a column begins, a number's step inside that
# column's reading-start edge: from the left in LTR and from the right in RTL.
# TikZ coordinates are page-absolute, so the RTL step is mirrored explicitly.
GRID_COLUMN_INK = ("black", 0.4)
GRID_STEP_INK = ("red", 0.2)


def grid_lines(profile: Profile = ENGLISH) -> str:
    """The profile's grid as page-absolute strokes from the lower left."""
    bottom = PAGE_MARGIN
    top = PAGE_HEIGHT - PAGE_MARGIN
    column = grid_column(4)
    uprights: dict[float, tuple[str, float]] = {}
    for index in range(4):
        left = PAGE_MARGIN + index * (column + GUTTER)
        for edge in (left, left + column):
            uprights[round(edge, 2)] = GRID_COLUMN_INK
        step = (
            left + column - NUMBER_ROOM
            if profile.rtl
            else left + NUMBER_ROOM
        )
        uprights[round(step, 2)] = GRID_STEP_INK

    colour, width = GRID_COLUMN_INK
    strokes = [
        f"\\draw[{colour},line width={width}pt]"
        f" ({PAGE_MARGIN:.2f}pt,{height:.2f}pt) -- ({PAGE_WIDTH - PAGE_MARGIN:.2f}pt,{height:.2f}pt);"
        for height in (bottom, top)
    ]
    strokes += [
        f"\\draw[{ink},line width={weight}pt]"
        f" ({edge}pt,{bottom:.2f}pt) -- ({edge}pt,{top:.2f}pt);"
        for edge, (ink, weight) in sorted(uprights.items())
    ]
    return "%\n".join(strokes)


def grid_tex(profile: Profile = ENGLISH) -> str:
    """A page of the grid and nothing else, on the trim size the papers are set to.

    Its own document because it is laid under a book that is already bound rather
    than set into it: one page is drawn however long the book runs to, and a book
    of a hundred pages costs one compile to see the grid on.

    eso-pic ships the lines as the page's background, which puts them on the page's
    own coordinates in a single pass — the alternative, a tikz picture that remembers
    where the page is, wants a second run to find out.
    """
    return (
        "\\documentclass{article}\n"
        f"\\usepackage[paperwidth={PAGE_MM[0]:g}mm,paperheight={PAGE_MM[1]:g}mm,"
        "margin=0pt]{geometry}\n"
        "\\usepackage{tikz}\n"
        "\\usepackage{eso-pic}\n"
        "\\pagestyle{empty}\n"
        "\\AddToShipoutPictureBG{%\n\\AtPageLowerLeft{%\n"
        "\\begin{tikzpicture}[overlay]\n"
        f"{grid_lines(profile)}\n"
        "\\end{tikzpicture}}}\n"
        "\\begin{document}\n\\null\n\\end{document}\n"
    )


def build_grid_pdf(tex_path: Path, profile: Profile = ENGLISH) -> Path:
    """Write and compile the one page of grid, returning the PDF it made."""
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    tex_path.write_text(grid_tex(profile), encoding="utf-8")
    compile_pdf(tex_path)
    return tex_path.with_suffix(".pdf")


def bbox_preamble(profile: Profile) -> str:
    """Whatsits that write each question's page position for groups.py.

    \\savepos is recorded at shipout, so the write is delayed. Measuring boxes are
    never shipped and leave nothing to read. A set member's cell width is frozen
    when the mark is placed: \\setcolumn is local to \\setgrid, and a delayed write
    would expand it as 0pt at shipout.
    """
    direction = "R" if profile.rtl else "L"
    return (
        "\\makeatletter\n"
        f"\\newcommand{{\\qiddir}}{{{direction}}}\n"
        "\\newwrite\\bboxwrite\n"
        "\\immediate\\openout\\bboxwrite=\\jobname.bbox\n"
        "\\newcommand{\\qidmark}[3]{%\n"
        "\\savepos\n"
        "\\edef\\qid@write{\\noexpand\\write\\noexpand\\bboxwrite{%\n"
        "#1 \\noexpand\\thepage\\space #2 \\space"
        "\\noexpand\\number\\noexpand\\lastxpos"
        "\\space\\noexpand\\number\\noexpand\\lastypos"
        "\\space\\number\\dimexpr#3\\relax\\space \\qiddir}}%\n"
        "\\qid@write}\n"
        "\\newcommand{\\qidstart}[2]{%\n"
        "\\def\\qid@id{#1}%\n"
        "\\ifx\\qid@id\\empty\\else\\qidmark{B}{#1}{#2}\\fi}\n"
        "\\newcommand{\\qidend}[2]{%\n"
        "\\def\\qid@id{#1}%\n"
        "\\ifx\\qid@id\\empty\\else\\qidmark{E}{#1}{#2}\\fi}\n"
        "\\newcommand{\\qidbox}[2]{%\n"
        "\\def\\qid@id{#1}%\n"
        "\\ifx\\qid@id\\empty #2%\n"
        "\\else\n"
        "\\qidstart{#1}{\\linewidth}#2\\qidend{#1}{\\linewidth}%\n"
        "\\fi}\n"
        "% A stem is one paragraph. The start mark has to be on its baseline, and\n"
        "% the end mark under the line, or the box is only 3pt below the baseline\n"
        "% and the tail of a letter such as yeh falls outside it. A list must not\n"
        "% come through here: \\leavevmode ahead of \\begin{list} opens an empty line.\n"
        "\\newcommand{\\qidline}[2]{%\n"
        "\\def\\qid@id{#1}%\n"
        "\\ifx\\qid@id\\empty #2%\n"
        "\\else\n"
        "\\leavevmode\\qidstart{#1}{\\linewidth}#2%\n"
        "\\ifhmode\\par\\fi\\qidend{#1}{\\linewidth}%\n"
        "\\fi}\n"
        "\\makeatother\n"
    )


def preamble(profile: Profile) -> str:
    """Everything before \\begin{document}, for a paper of one kind of book.

    Most of it is the same for every book. What the profile decides is the text face,
    the maths face and the direction (type_preamble), how a number is labelled and the
    choices lettered (direction_preamble), which edge a question's box is read from
    (bbox_preamble), the face the section number is set in (numeral_face), and the
    two marks on an objective's heading that are cut from a letter of the face
    (objective_head, underline).
    """
    return (
        PAGE_PREAMBLE
        + GRID_PREAMBLE
        + type_preamble(profile)
        + LEADING_PREAMBLE
        + DRAWING_PREAMBLE
        + direction_preamble(profile)
        + LABEL_LENGTH
        + BLANK_RULE
        + tally_preamble(profile)
        + TABLE_PADDING
        + TABLE_FIT
        + ARABVERSE_PREAMBLE
        + OPTIONS_PREAMBLE
        + BESIDE_FIGURE
        + WIDE_FIGURE
        + SET_GRID
        + bbox_preamble(profile)
        + UNBROKEN
        + numeral_face(profile)
        + UNDERLINE
        + foot_style(profile)
        + objective_head(profile)
        + SECTION_HEAD
    )

# The step from an exercise's wording down to the choices it offers, which sets the
# choices off as the answer to the question rather than as more of its wording.
OPTIONS_GAP = "8pt"

# Characters outside maths that the text face has no glyph for: STIX Two Text
# in a Latin book, Noto Naskh Arabic in an Arabic one. LaTeX drops them with
# only a log warning, so they are rewritten as maths to avoid losing content.
# An Arabic book writes the leftward arrow where a Latin book writes the
# rightward one, so both directions are here.
MATH_CHAR_REPLACEMENTS = {
    "→": r"\rightarrow", "←": r"\leftarrow", "↔": r"\leftrightarrow",
    "⇒": r"\Rightarrow", "⇐": r"\Leftarrow",
    "∅": r"\emptyset", "∈": r"\in", "∉": r"\notin", "∏": r"\prod", "∑": r"\sum",
    "√": r"\surd", "∞": r"\infty", "∠": r"\angle", "∥": r"\parallel",
    "∩": r"\cap", "∪": r"\cup", "∫": r"\int", "∴": r"\therefore", "∼": r"\sim",
    "≅": r"\cong", "≈": r"\approx", "≠": r"\neq", "≡": r"\equiv", "≤": r"\leq",
    "≥": r"\geq", "⊂": r"\subset", "⊥": r"\perp", "⋅": r"\times", "·": r"\times",
}

LATEX_SPECIALS = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
    "%": r"\%", "#": r"\#", "_": r"\_", "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
}

MATH_SEGMENT = re.compile(r"\$\$.+?\$\$|\$.+?\$", re.DOTALL)
DISPLAY_TAIL_PUNCT = re.compile(r"^[.,;:!?،؛؟]+")

# A table, wherever the writing happened to put it. The model builds one out of an
# array inside maths, and writes it with the delimiters of a phrase rather than of a
# display, so "the table below:" is followed by the table on the same line as the
# words. A table has a width of its own and no way to break across a line, so set
# there it hangs off the edge of the paper and is cut in half by it. Which
# delimiters it arrived in is not allowed to decide this: a table is a display.
TABLE_SEGMENT = re.compile(r"\\begin\{(?:array|tabular)\*?\}")

# Every environment whose & is a column separator, tables included. Wider than
# TABLE_SEGMENT because cases, a matrix, aligned and split are not tables and must
# not be unframed, but a bare & inside one of them is still the alignment and not
# the stray the model writes at the start of an expression.
ALIGNMENT_TAB = re.compile(
    r"\\begin\{(?:array|tabular|cases|dcases|rcases|"
    r"matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|smallmatrix|subarray|"
    r"aligned|alignedat|split|gathered|"
    r"alignat|flalign|eqnarray|align)\*?\}"
)

# The frame around a table, which the paper takes off. The writing is held to one
# form for a table — every cell ruled, as the textbooks it is transcribing from print
# them — because one form is what can be asked for and checked; how a table looks is
# the paper's to decide, and the paper decides against the box. A ruled edge closes
# the table off from the page and doubles the margin's own work, where the inner grid
# is what the reader actually needs: it says which figure belongs to which heading.
# Taken off here rather than asked for, so that the style is one line to change and
# no book has to be written again to change it.
#
# The opening pattern finds the column spec, whose pipes are taken off either end to
# kill the left and right rules, and swallows the \hline the first row is capped with;
# the closing one swallows the rules and the row break the last row ends with. The
# spec is matched one set of braces deep, which is as far as a column of a fixed width
# or a column with a format attached goes; anything deeper is a table this cannot take
# apart, and such a table keeps its frame whole rather than losing half of it.
TABLE_FRAME_OPEN = re.compile(
    r"(\\begin\{(?:array|tabular)\*?\}(?:\s*\[[^\]]*\])?\s*)"
    r"\{((?:[^{}]|\{[^{}]*\})*)\}(?:\s*\\hline)*"
)
TABLE_FRAME_CLOSE = re.compile(r"(?:\s*(?:\\hline|\\\\))*(\s*\\end\{(?:array|tabular)\*?\})")

# The one command the model writes into its prose, and so the one that survives
# escaping. Everything else it writes outside $...$ is text, backslash and all.
# A leftover TeX control-space (``\blank\ angle`` → ``\blank\.`` after the
# English word is dropped) is part of the blank, not a printed backslash.
BLANK_TOKEN = re.compile(
    r"\\blank\b(?:\{\})?(?:\\(?=[.,;:!?،؛؟\s]|$))?"
)

# An HTML underline the model wrote in prose, as ``<u>ذَا عِلْمٍ</u>``. TeX does
# not read the tags, and ``<`` is not a special, so left alone they print as
# themselves. The rest of the book already underlines as ``$\underline{\text{...}}$``.
HTML_UNDERLINE = re.compile(r"<u>(.*?)</u>", re.IGNORECASE | re.DOTALL)
BARE_UNDERLINE_OPEN = re.compile(r"\\underline\{")

# Typographic double and single quotation marks on the paper.
OPEN_DOUBLE = "\u201c"
CLOSE_DOUBLE = "\u201d"
OPEN_SINGLE = "\u2018"
CLOSE_SINGLE = "\u2019"
OPEN_GUILLEMET = "\u00ab"
CLOSE_GUILLEMET = "\u00bb"

ARROW_ASCII = re.compile(r"->")
MARKDOWN_BOLD = re.compile(r"\*\*\s*(.+?)\s*\*\*", re.DOTALL)
MARKDOWN_ITALIC = re.compile(r"\*\s*(.+?)\s*\*", re.DOTALL)
ARROW_PLACEHOLDER = "\ue000"

# Any of these in prose counts as one double-quote delimiter; output alternates “ and ”.
_DOUBLE_QUOTE = frozenset('"«»\u201c\u201d')
# Single-quote delimiters; output alternates ‘ and ’. Apostrophes inside words are excepted.
_SINGLE_QUOTE = frozenset("'\u2018\u2019`")

# Arabic papers set «» for every quote delimiter. English papers keep “” / ‘’.
_PROSE_GUILLEMETS: ContextVar[bool] = ContextVar("prose_guillemets", default=False)
# Western 0-9 in Arabic prose: STIX via \\foreignlanguage{english}, after escape.
_WESTERN_STIX: ContextVar[bool] = ContextVar("western_stix", default=False)
# Latin books wrap Arabic words in Noto Naskh via \\arabicsnippet{...}.
_LATIN_ARABIC_SNIPPETS: ContextVar[bool] = ContextVar("latin_arabic_snippets", default=False)
# Egyptian books (book id contains ``_eg``) print a unit vector as ``\overrightarrow``.
_EGYPT_UNIT_VECTORS: ContextVar[bool] = ContextVar("egypt_unit_vectors", default=False)

_IPA_LETTER = re.compile(r"[\u0250-\u02AF\u0270-\u027E\u0294-\u02B8]")
_ARABIC_PROSE_RUN = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]+")


def quoting_for(profile: Profile, book_id: str = "") -> tuple[object, object, object]:
    """Arabic language books print guillemets; RTL-western books wrap 0-9 in STIX.

    Egyptian books, named by ``_eg`` in the book id, also rewrite ``\\hat`` in maths
    to ``\\overrightarrow``, so a unit vector uses the same mark as a vector.
    """
    quotes = _PROSE_GUILLEMETS.set(profile.language_code == "ar")
    western = _WESTERN_STIX.set(profile.rtl and not profile.indic_digits)
    snippets = _LATIN_ARABIC_SNIPPETS.set(not profile.rtl)
    _EGYPT_UNIT_VECTORS.set("_eg" in book_id)
    return quotes, western, snippets


def _reset_quoting(quotes: object, western: object, snippets: object) -> None:
    _PROSE_GUILLEMETS.reset(quotes)
    _WESTERN_STIX.reset(western)
    _LATIN_ARABIC_SNIPPETS.reset(snippets)
    _EGYPT_UNIT_VECTORS.set(False)


def _use_guillemets(guillemets: bool | None) -> bool:
    return _PROSE_GUILLEMETS.get() if guillemets is None else guillemets


def _is_apostrophe(text: str, index: int) -> bool:
    char = text[index]
    prev = text[index - 1] if index else ""
    nxt = text[index + 1] if index + 1 < len(text) else ""
    if char == "'":
        return prev.isalpha() and (nxt.isalpha() or nxt in "sS")
    if char == "\u2019":
        return prev.isalpha() and nxt.isalpha()
    return False


def normalize_quotes(text: str, *, guillemets: bool | None = None) -> str:
    """Use typographic quotes with correct open and close glyphs.

    ASCII and curly quotes from the model are walked in order; each delimiter
    becomes an opening or closing mark as appropriate. Wrong pairs such as
    ``‘word‘`` are repaired to ``‘word’``. When ``guillemets`` is true, every
    quote delimiter — ASCII, curly, single or double, including «» already
    written — becomes «»; apostrophes inside words stay apostrophes. When it
    is false, «» count as double quotes and become “”. ``None`` follows the
    paper's language (Arabic books true, English books false).
    """
    use_guillemets = _use_guillemets(guillemets)
    math_spans = [found.span() for found in MATH_SEGMENT.finditer(text)]
    math_i = 0

    def in_math(index: int) -> bool:
        nonlocal math_i
        while math_i < len(math_spans) and math_spans[math_i][1] <= index:
            math_i += 1
        if math_i >= len(math_spans):
            return False
        start, stop = math_spans[math_i]
        return start <= index < stop

    out: list[str] = []
    double_open = True
    single_open = True
    quote_open = True
    for index, char in enumerate(text):
        if in_math(index):
            out.append(char)
            continue
        if use_guillemets:
            if char in _SINGLE_QUOTE and _is_apostrophe(text, index):
                out.append(CLOSE_SINGLE)
                continue
            if char in _DOUBLE_QUOTE or char in _SINGLE_QUOTE:
                out.append(OPEN_GUILLEMET if quote_open else CLOSE_GUILLEMET)
                quote_open = not quote_open
                continue
            out.append(char)
            continue
        if char in _DOUBLE_QUOTE:
            out.append(OPEN_DOUBLE if double_open else CLOSE_DOUBLE)
            double_open = not double_open
            continue
        if char in _SINGLE_QUOTE:
            if _is_apostrophe(text, index):
                out.append(CLOSE_SINGLE)
                continue
            out.append(OPEN_SINGLE if single_open else CLOSE_SINGLE)
            single_open = not single_open
            continue
        out.append(char)
    return "".join(out)


def normalize_quotes_in_text_macros(maths: str, *, guillemets: bool | None = None) -> str:
    """``\\text{...}`` in maths is prose; its quotes and western digits follow the body."""
    return TEXT_IN_MATHS.sub(
        lambda found: (
            "\\text{"
            + maybe_stix_digits(
                normalize_quotes(found.group(1), guillemets=guillemets)
            )
            + "}"
        ),
        maths,
    )


def _escape_markdown_italic(text: str) -> str:
    """Turn leftover ``* italic *`` into ``\\textit`` after bold has been lifted out."""
    pieces: list[str] = []
    last = 0
    for found in MARKDOWN_ITALIC.finditer(text):
        pieces.append(escape_plain(text[last : found.start()]))
        pieces.append(f"\\textit{{{escape_plain(found.group(1))}}}")
        last = found.end()
    pieces.append(escape_plain(text[last:]))
    return "".join(pieces)


def escape_prose(
    text: str, *, guillemets: bool | None = None, quotes: bool = True
) -> str:
    """Escape exercise prose: quotes, ``** bold **``, ``* italic *``, and ``->`` before LaTeX specials."""
    if quotes:
        text = normalize_quotes(text, guillemets=guillemets)
    text = ARROW_ASCII.sub(ARROW_PLACEHOLDER, text)
    pieces: list[str] = []
    last = 0
    for found in MARKDOWN_BOLD.finditer(text):
        pieces.append(_escape_markdown_italic(text[last : found.start()]))
        pieces.append(f"\\textbf{{{escape_plain(found.group(1))}}}")
        last = found.end()
    pieces.append(_escape_markdown_italic(text[last:]))
    return "".join(pieces).replace(ARROW_PLACEHOLDER, r"$\rightarrow$")


def wrap_arabic_prose_runs(text: str) -> str:
    """Wrap Arabic words on a Latin page in ``\\arabicsnippet`` (Noto Naskh).

    Call after ``escape_prose`` so the command name is not escaped again.
    """
    return _ARABIC_PROSE_RUN.sub(
        lambda found: rf"\arabicsnippet{{{found.group(0)}}}", text
    )


def escape_plain(text: str) -> str:
    """Escape LaTeX specials in text that is not inside maths."""
    out = []
    for char in text:
        if char in MATH_CHAR_REPLACEMENTS:
            out.append(f"${MATH_CHAR_REPLACEMENTS[char]}$")
        else:
            out.append(LATEX_SPECIALS.get(char, char))
    return "".join(out)


def as_paragraphs(text: str) -> str:
    """Turn line breaks into LaTeX's own paragraph break, a blank line.

    This is what puts the options of a multiple-choice exercise on lines of their
    own: the model writes them one per line, and each becomes its own paragraph.
    """
    return re.sub(r"[ \t]*\n[ \t\n]*", "\n\n", text)


def one_line(text: str) -> str:
    return re.sub(r"\s*\n\s*", " ", text)


def line_escapes(text: str) -> str:
    """Turn a literal backslash-n the model wrote into the break it meant.

    The fields arrive as JSON, where a line break is a newline, but the model
    occasionally writes the two characters instead. Left alone they print as
    "\\n(a)" ahead of a multiple-choice option and leave every option on one
    line. A real LaTeX command is never followed by a non-letter here, so \\nu
    and \\newline are untouched.
    """
    return re.sub(r"\\n(?![a-zA-Z])", "\n", text)


# A command is one backslash. Two or more in front of its name, as in \\frac, is
# the line break \\ followed by the letters of the command, and inside $...$ that
# break is what stops the paper. A line break that is really one stands before a
# space, a newline, or a single letter, never before a name, so it is left as it is.
DOUBLED_COMMAND = re.compile(r"\\{2,}(?=[A-Za-z]{2})")


def single_commands(text: str) -> str:
    """The text with a command the model escaped twice brought back to one backslash."""
    return DOUBLED_COMMAND.sub(r"\\", text)


def balance_math(text: str) -> tuple[str, bool]:
    """Repair a field where the model wrote only one $ of a pair.

    It regularly emits maths with the opening delimiter missing, as in
    r"\\because 0.73 = \\frac{73}{100} \\therefore 0.73$ is rational". Whether the
    missing delimiter opens or closes is decided by looking for LaTeX commands
    ahead of the first $.
    """
    if text.count("$") % 2 == 0:
        return text, False
    head = text.split("$", 1)[0]
    repaired = f"${text}" if re.search(r"\\[a-zA-Z]", head) else f"{text}$"
    return repaired, True


def math_blanks(segment: str) -> str:
    """Rewrite a run of underscores inside maths as a rule to write an answer on.

    A blank is written as \\blank and needs no help from here. This is for the
    other thing the model does: a row of underscores, which TeX reads inside
    $...$ as a subscript and which fails the build outright.
    """
    return re.sub(r"_{2,}", r"\\blank{}", segment)


def math_stray_ampersands(body: str) -> str:
    """Drop a bare & in simple maths, which TeX reads as an alignment tab.

    The model sometimes writes one at the start of an expression, as in
    ``$&12 \\times 7$``. That is not mathematics and breaks \\mcq, which is laid
    out as a tabular. An & inside an alignment — a table, cases, a matrix,
    aligned, split — is the column separator and is left.
    """
    if ALIGNMENT_TAB.search(body):
        return body
    return re.sub(r"(?<!\\)&", "", body)


# ``\\right) + \\left(`` — a sum of two sized groups. \\binoppenalty=10000
# otherwise keeps the ``+`` from breaking, so a stem wider than the measure
# hangs off the page. Zero-width; unused when the line already fits.
_PLUS_BETWEEN_DELIMS = re.compile(
    r"(\\right(?:[)\]]|\\\}))(\s*\+)(?!\s*\\allowbreak)(\s*)(\\left(?:[(\[]|\\\{))"
)
# ``A \to B \to C`` of labelled points. \\relpenalty=10000 otherwise keeps
# ``\\to`` from breaking, so a star of ten vertices hangs off the page.
_TO_THEN_BREAK = re.compile(r"(\\to|\\rightarrow)(?![A-Za-z])(?!\s*\\allowbreak)")


def _plus_between_delimiters(body: str) -> str:
    return _PLUS_BETWEEN_DELIMS.sub(r"\1\2\\allowbreak\3\4", body)


def _to_allowbreak(body: str) -> str:
    return _TO_THEN_BREAK.sub(r"\1\\allowbreak", body)


def math_commas(body: str) -> str:
    """Let a line end after a comma in a list of values set as one formula.

    TeX offers a break inside a formula after a relation or a binary operator and
    nowhere else, and it decides what counts as a binary operator by what follows
    it: a minus with a comma after it is demoted to an ordinary symbol, which
    offers nothing. So "$0-, 10-, 20-, 30-40$" has exactly one place it can break,
    at the very end, and set anywhere near the width of the measure it hangs off
    the edge of the paper instead. A penalty of zero after each comma gives the
    list the breaks a reader would expect, and costs nothing where the line fits:
    it has no width, so a formula that was never going to break is unchanged.

    Only a comma at the outer level, because that is the only one separating a
    list. The commas of a coordinate pair, a set, or the argument of a function
    are inside their brackets and are left alone: a break there would divide
    something that reads as one thing. ``+`` between two ``\\left…\\right``
    groups is the same kind of list join, and gets the same break. So does
    ``\\to`` between labelled points.
    """
    out: list[str] = []
    depth = 0
    for at, character in enumerate(body):
        out.append(character)
        if character in "({[":
            depth += 1
        elif character in ")}]":
            depth = max(0, depth - 1)
        elif character in (",", ARABIC_COMMA) and depth == 0:
            # ``\,`` is a thin space, not a list comma.
            if character == "," and at and body[at - 1] == "\\":
                continue
            # The name of a command ends at the first character that is not a
            # letter, so one followed by a letter needs the space to end it. Where
            # the list already has a space there, that one will do.
            rest = body[at + 1 :]
            if rest.lstrip().startswith("\\allowbreak"):
                continue
            following = rest[:1]
            out.append("\\allowbreak" if following.isspace() else "\\allowbreak ")
    return _to_allowbreak(_plus_between_delimiters("".join(out)))


# \hline and \cline are \noalign. Each is legal after a row break, after another
# rule, or at the start of the array, and nowhere else. The model writes the rule
# where the row break belongs — "cell \hline cell" — and TeX stops with
# Misplaced \noalign. Read as one row, the ampersands of the later rows also widen
# the preamble, so a two-column table comes out as {c|c||c|c}.
ARRAY_RULE = re.compile(r"\\(?:hline|cline\s*\{[^{}]*\})")
RULE_ALREADY_PLACED = re.compile(
    r"\\\\(?:\s*\*|\s*\[[^\]]*\])?\s*$"
    r"|\\(?:hline|cline\s*\{[^{}]*\})\s*$"
    r"|\\begin\{(?:array|tabular)\*?\}(?:\s*\[[^\]]*\])?\s*"
    r"\{(?:[^{}]|\{[^{}]*\})*\}\s*$"
)


def row_breaks_before_rules(text: str) -> str:
    """A row break in front of a rule the model wrote in its place."""
    pieces: list[str] = []
    last = 0
    for found in ARRAY_RULE.finditer(text):
        before = text[: found.start()]
        opens = len(re.findall(r"\\begin\{(?:array|tabular)\*?\}", before))
        closes = len(re.findall(r"\\end\{(?:array|tabular)\*?\}", before))
        if opens <= closes:
            continue
        if RULE_ALREADY_PLACED.search(before.rstrip()):
            continue
        pieces.append(text[last : found.start()])
        pieces.append("\\\\ ")
        last = found.start()
    if not pieces:
        return text
    pieces.append(text[last:])
    return "".join(pieces)


def unframed(body: str) -> str:
    """A table with the rules around its edge taken off and its inner grid kept.

    Every table in the book goes through here, since every one of them is written as
    an array of fully ruled cells. What is left is the grid between the cells: a rule
    under the heading row, a rule between one column and the next, and the page's own
    white either side of the whole.

    All of a table's edge or none of it: where a spec cannot be read the table is
    handed back as it came, since a box missing one side reads as a mistake where a
    box reads as a style.
    """
    body = row_breaks_before_rules(body)
    opened, found = TABLE_FRAME_OPEN.subn(
        lambda match: f"{match[1]}{{{match[2].strip('|')}}}", body
    )
    if found != len(TABLE_SEGMENT.findall(body)):
        return align_array_columns(body)
    return align_array_columns(TABLE_FRAME_CLOSE.sub(r"\1", opened))


# A whole table, for turning round. The spec is matched one level deep and with no
# braces in it — the plain run of letters and pipes the writing is held to — because
# a spec is turned round by reversing its characters, and a fixed-width column or a
# format attached to one would come out inside out. A table whose spec is anything
# else is left as it came, whole.
TABLE_WHOLE = re.compile(
    r"(\\begin\{array\}(?:\s*\[[^\]]*\])?\s*)\{([|lcr\s]*)\}(.*?)(\\end\{array\})",
    re.DOTALL,
)
# A row's leading rules, which stay where they are when the cells behind them are
# turned round: a rule is a rule across the whole row whichever way it reads.
ROW_LEAD = re.compile(r"\s*(?:\\hline\s*)*")
# The cell separator, an ampersand the model did not escape.
CELL_BREAK = re.compile(r"(?<!\\)&")
STEM_HEADER = re.compile(r"\\text\{\s*(?:الساق|السيقان|Stem(?:s)?)\s*\}", re.I)
LEAF_HEADER = re.compile(r"\\text\{\s*(?:الأوراق|الورقة|Leaves?|Leaf)\s*\}", re.I)
RTL_MIXED_CELL = re.compile(
    r"^(?P<lead>\s*)\\text\{(?P<words>[^{}]*[\u0621-\u064A][^{}]*)\}"
    r"(?P<formula>\s+\S(?:.*?\S)?)(?P<trail>\s*)$",
    re.DOTALL,
)


def array_column_count(spec: str) -> int:
    """How many columns a plain array preamble declares."""
    return len(re.findall(r"[lcr]", spec, re.IGNORECASE))


def array_row_cell_count(row: str) -> int:
    """Cells in one array row, ignoring a leading \\hline."""
    lead = ROW_LEAD.match(row)
    if not lead:
        return 0
    rest = row[lead.end() :].strip()
    if not rest:
        return 0
    return len(CELL_BREAK.split(rest))


def widen_array_spec(spec: str, columns: int) -> str:
    """Append |c columns until the preamble matches the widest row."""
    spec = spec.strip()
    count = array_column_count(spec)
    if count >= columns:
        return spec
    if count == 0:
        return "c" if columns == 1 else "c" + "|c" * (columns - 1)
    return spec.rstrip("|") + "|c" * (columns - count)


def align_one_array(match: re.Match[str]) -> str:
    """One array whose preamble is widened when a row has too many & cells."""
    spec = match.group(2)
    body = match.group(3)
    if "\\begin{" in body or "\\multicolumn" in body:
        return match.group(0)
    if not re.fullmatch(r"[|lcr\s]*", spec, re.IGNORECASE):
        return match.group(0)
    max_cells = 0
    for row in body.split("\\\\"):
        max_cells = max(max_cells, array_row_cell_count(row))
    if max_cells <= array_column_count(spec):
        return match.group(0)
    new_spec = widen_array_spec(spec, max_cells)
    return f"{match.group(1)}{{{new_spec}}}{body}{match.group(4)}"


def align_array_columns(text: str) -> str:
    """Pad array preambles when rows hold more cells than {c|…} declares.

    The model sometimes writes four cells — three x-values and an X or Y row
    label — but declares only three columns, as ``{c|c|c}`` with
    ``4 & -3 & 2 &X``. LaTeX then reports ``Extra alignment tab has been
    changed to \\cr``, and papers.py fails the section. Trailing ``|c`` columns
    are appended to match the widest row; tables with \\multicolumn or a nested
    \\begin are left unchanged.
    """
    return TABLE_WHOLE.sub(align_one_array, text)


def math_cell_to_prose(cell: str) -> str:
    """A math-mode cell as wrapping text: ``\\text{words}`` stay words, the rest is ``$...$``."""
    pieces: list[str] = []
    last = 0
    for found in TEXT_IN_MATHS.finditer(cell):
        pieces.append(_math_chunk_as_inline(cell[last : found.start()]))
        pieces.append(found.group(1))
        last = found.end()
    pieces.append(_math_chunk_as_inline(cell[last:]))
    return "".join(pieces)


def _math_chunk_as_inline(chunk: str) -> str:
    inner = chunk.strip().rstrip("\\")
    if not inner:
        return chunk
    lead = len(chunk) - len(chunk.lstrip())
    trail = len(chunk) - len(chunk.rstrip())
    return f"{chunk[:lead]}${inner}${chunk[len(chunk) - trail :] if trail else ''}"


def _top_level_eq_count(inner: str) -> int:
    """``=`` signs not inside ``{…}`` and not escaped."""
    count = 0
    depth = 0
    index = 0
    while index < len(inner):
        char = inner[index]
        if char == "\\":
            index += 2 if index + 1 < len(inner) else 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "=" and depth == 0:
            count += 1
        index += 1
    return count


def _split_inner_at_eq(inner: str) -> list[str]:
    """Pieces of a formula on either side of top-level ``=``."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    index = 0
    while index < len(inner):
        char = inner[index]
        if char == "\\":
            buf.append(char)
            if index + 1 < len(inner):
                buf.append(inner[index + 1])
                index += 2
                continue
            index += 1
            continue
        if char == "{":
            depth += 1
            buf.append(char)
        elif char == "}":
            depth = max(0, depth - 1)
            buf.append(char)
        elif char == "=" and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(char)
        index += 1
    parts.append("".join(buf).strip())
    return parts


def _cell_is_atomic(cell: str) -> bool:
    """One word, one short ``$…$``, or empty — a column that can stay natural width."""
    text = cell.strip()
    if not text:
        return True
    if text.startswith("$") and text.endswith("$") and text.count("$") == 2:
        return _top_level_eq_count(text[1:-1]) <= 1
    return not any(char.isspace() for char in text)


# ``Standard deviation`` is two words and still fits; CPCT reasons do not.
_WRAP_CELL_CHARS = 40


def _cell_needs_wrap(cell: str) -> bool:
    """True when a cell is long enough that an ``X`` column is worth the stretch."""
    text = cell.strip()
    if not text or _cell_is_atomic(cell):
        return False
    if len(text) >= _WRAP_CELL_CHARS:
        return True
    return (
        text.startswith("$")
        and text.endswith("$")
        and text.count("$") == 2
        and _top_level_eq_count(text[1:-1]) >= 2
    )


def _split_flex_math(cell: str) -> str:
    """``$a=b=c$`` as ``$a$ $=$ $b$ $=$ $c$`` so an X column can wrap."""
    pieces: list[str] = []
    last = 0
    for found in re.finditer(r"\$[^$]*\$", cell):
        pieces.append(cell[last : found.start()])
        chunk = found.group(0)
        inner = chunk[1:-1]
        if _top_level_eq_count(inner) < 2:
            pieces.append(chunk)
        else:
            parts = [part for part in _split_inner_at_eq(inner) if part]
            pieces.append(" $=$ ".join(f"${part}$" for part in parts))
        last = found.end()
    pieces.append(cell[last:])
    return "".join(pieces)


def _x_hsize_factors(weights: list[int]) -> list[float | None]:
    """tabularx ``\\hsize`` multipliers; ``None`` means a plain ``X``."""
    count = len(weights)
    if count <= 1:
        return [None]
    weights = [max(weight, 1) for weight in weights]
    if all(weight == weights[0] for weight in weights):
        return [None] * count
    total = sum(weights)
    factors: list[float | None] = []
    used = 0.0
    for index, weight in enumerate(weights):
        if index < count - 1:
            factor = count * weight / total
            factors.append(factor)
            used += factor
        else:
            factors.append(count - used)
    return factors


def _flex_col_preamble(factor: float | None) -> str:
    if factor is None:
        return r">{\raggedright\arraybackslash}X"
    return f">{{\\hsize={factor:.4f}\\hsize\\raggedright\\arraybackslash}}X"


def _array_begin_to_tabularx(begin: str) -> str:
    pos = re.search(r"\[([^\]]*)\]", begin)
    if pos:
        return f"\\begin{{tabularx}}{{\\linewidth}}[{pos.group(1)}]"
    return r"\begin{tabularx}{\linewidth}"


def _wrap_one_text_array(match: re.Match[str]) -> str:
    """One 2–3 column ``l`` array of ``\\text`` as ``c``/``X`` at body size.

    A column of one-block cells (a word, a short ``$…$``) keeps its ``lcr``
    letter and takes its natural width. Longer columns become ``X`` and share
    what is left of ``\\linewidth``, weighted by the longest cell.
    """
    spec = match.group(2)
    inner = match.group(3)
    if "\\begin{" in inner or "\\multicolumn" in inner:
        return match.group(0)
    cols = array_column_count(spec)
    if (
        cols < 2
        or cols > 3
        or "l" not in spec.lower()
        or r"\text{" not in inner
    ):
        return match.group(0)
    parsed: list[tuple[str, list[str]]] = []
    for row in inner.split("\\\\"):
        lead = ROW_LEAD.match(row)
        prefix = lead.group(0) if lead else ""
        cells = [
            math_cell_to_prose(cell)
            for cell in CELL_BREAK.split(row[len(prefix) :])
        ]
        parsed.append((prefix, cells))
    wrap_col = [False] * cols
    weights = [0] * cols
    for _, cells in parsed:
        for index, cell in enumerate(cells):
            if index >= cols:
                break
            if _cell_needs_wrap(cell):
                wrap_col[index] = True
            weights[index] = max(weights[index], len(cell.strip()))
    if not any(wrap_col):
        return match.group(0)
    flex_weights = [weights[index] for index, wrap in enumerate(wrap_col) if wrap]
    factors = iter(_x_hsize_factors(flex_weights))
    replacements: list[str] = []
    for wrap in wrap_col:
        if wrap:
            replacements.append(_flex_col_preamble(next(factors)))
        else:
            replacements.append("")
    slot = iter(replacements)

    def one_letter(found: re.Match[str]) -> str:
        preamble = next(slot)
        return found.group(0) if not preamble else preamble

    new_spec = re.sub(r"[lcr]", one_letter, spec, flags=re.I)
    row_break = "\\\\"
    rows = []
    for prefix, cells in parsed:
        out = []
        for index, cell in enumerate(cells):
            if index < cols and wrap_col[index]:
                out.append(_split_flex_math(cell))
            else:
                out.append(cell)
        rows.append(prefix + "&".join(out))
    begin = _array_begin_to_tabularx(match.group(1))
    return (
        r"\begingroup\textdir TLT\relax"
        r"\setlength{\tabcolsep}{\arraycolsep}"
        f"{begin}{{{new_spec}}}"
        f"{row_break.join(rows)}"
        r"\end{tabularx}\endgroup"
    )


def wrap_text_table_columns(body: str) -> tuple[str, bool]:
    """Rewrite text-heavy 2–3 column arrays to wrapping tabularx.

    ``\\tablefit`` scales an ``{l|l}`` proof until CPCT reasons fit, taking the
    type down with it. An ``X`` column wraps those words at 12pt; a one-block
    or short column stays ``lcr``. Numeric ``{c|c|c}`` tables and short
    ``{c|l}`` lists are left for ``\\tablefit``.
    """
    wrapped = False

    def one(match: re.Match[str]) -> str:
        nonlocal wrapped
        out = _wrap_one_text_array(match)
        if out != match.group(0):
            wrapped = True
        return out

    return TABLE_WHOLE.sub(one, body), wrapped


def mirrored_table(match: re.Match) -> str:
    """One array with its columns in the other order and its cells untouched."""
    if _preserve_short_division(match[0]):
        return match[0]
    body = match[3]
    if "\\begin{" in body or "\\multicolumn" in body:
        return match[0]
    rows = []
    for row in body.split("\\\\"):
        lead = ROW_LEAD.match(row)[0]
        cells = CELL_BREAK.split(row[len(lead) :])
        rows.append(lead + "&".join(reversed(cells)))
    sep = "\\\\"
    return f"{match[1]}{{{match[2][::-1]}}}{sep.join(rows)}{match[4]}"


def mirrored_tables(text: str) -> str:
    """Every table in a field with its columns read from the right.

    A table is a maths array, and maths is set left to right in a book whose
    notation is Latin — which is right for "$5(X - 4) + 12$" and wrong for the table
    it stands in. A table is a thing the prose reads, not the maths: an Arabic reader
    looks for its first column at the right, under the heading that came first, and
    the textbooks these papers go beside print them that way. The engine cannot be
    told this — an array is built as rows of boxes whose order no direction switch
    reaches, which was tried both ways — so the columns are turned round here, in the
    source, once per paper: each row's cells in the other order, the spec's letters
    and rules with them, and every cell's own contents exactly as written, so the
    maths inside still reads left to right.

    A row's rules stay in front of it, and a table this cannot take apart cleanly —
    one with a table inside it, a cell spanning columns, a spec with a format in it —
    is left as it came rather than half turned.
    """
    return TABLE_WHOLE.sub(mirrored_table, text)


def is_stem_leaf_table(match: re.Match[str]) -> bool:
    """Whether an array's header gives it stem/leaf semantics."""
    body = match[3]
    if "\\begin{" in body or "\\multicolumn" in body:
        return False
    first = body.split("\\\\", 1)[0]
    cells = CELL_BREAK.split(first)
    return (
        len(cells) == 2
        and any(STEM_HEADER.search(cell) for cell in cells)
        and any(LEAF_HEADER.search(cell) for cell in cells)
    )


def _array_depth_before(text: str, index: int) -> int:
    """How many ``\\begin{array}`` are still open before ``index``."""
    return text[:index].count(r"\begin{array}") - text[:index].count(r"\end{array}")


def _rtl_unit_cell(cell: str) -> str | None:
    """A quantity cell as an RTL box whose unit is read after the number.

    ``6\\text{ سم}`` and ``4.8\\text{ سم}^2`` match ``TRAILING_ARABIC_UNIT``.
    A unit-only cell (``\\text{ م}^2``) and a cell already laid out stay as written,
    so a second pass does not move them again. Inside the box the number is the
    first item, which an RTL line reads before the unit.
    """
    stripped = cell.strip()
    if not stripped or "\\rtlmathcell" in stripped or "\\hbox" in stripped:
        return None
    trailing = TRAILING_ARABIC_UNIT.search(stripped)
    if trailing is None:
        return None
    expression = stripped[: trailing.start()].strip()
    if not expression:
        return None
    label = math_delimiters_as_text(trailing.group("unit").strip())
    if not label:
        return None
    power = (trailing.group("power") or "").strip()
    suffix = f"${power}$" if power else ""
    lead_len = len(cell) - len(cell.lstrip())
    trail_len = len(cell) - len(cell.rstrip())
    lead = cell[:lead_len]
    trail = cell[len(cell) - trail_len :] if trail_len else ""
    return (
        f"{lead}\\hbox dir TRT{{\\hbox{{\\textdir TLT\\relax ${expression}$}}"
        f"\\hskip.333em\\hbox{{\\textdir TRT\\relax {label}{suffix}}}}}{trail}"
    )


def rtl_latin_cells(match: re.Match[str]) -> str:
    """Keep Arabic words right of the Western/Latin maths that follows them.

    A cell that is a formula with a trailing Arabic unit is laid out like a
    prose unit, so the unit is read after the number. Nested arrays and
    ``\\multicolumn`` cells are left unchanged.
    """
    if _preserve_short_division(match[0]):
        return match[0]
    body = match[3]
    if "\\begin{" in body or "\\multicolumn" in body:
        return match[0]
    rows = []
    for row in body.split("\\\\"):
        lead = ROW_LEAD.match(row)[0]
        cells = CELL_BREAK.split(row[len(lead) :])
        rewritten = []
        for cell in cells:
            unit_cell = _rtl_unit_cell(cell)
            if unit_cell is not None:
                rewritten.append(unit_cell)
                continue
            found = RTL_MIXED_CELL.fullmatch(cell)
            if found is None:
                rewritten.append(cell)
                continue
            words = found["words"].strip()
            formula = found["formula"].strip()
            rewritten.append(
                f"{found['lead']}\\rtlmathcell{{{words}}}{{{formula}}}"
                f"{found['trail']}"
            )
        rows.append(lead + "&".join(rewritten))
    joined = "\\\\".join(rows)
    return f"{match[1]}{{{match[2]}}}{joined}{match[4]}"


def directional_tables(text: str, profile: Profile) -> str:
    """Set prose tables and semantic stem/leaf tables for this RTL profile."""
    def orient(match: re.Match[str]) -> str:
        if _preserve_short_division(match[0]):
            return match[0]
        if _array_depth_before(match.string, match.start()) > 0:
            return match[0]
        stem_leaf = is_stem_leaf_table(match)
        if profile.notation == "latin":
            oriented = match[0] if stem_leaf else mirrored_table(match)
            rematched = TABLE_WHOLE.fullmatch(oriented)
            return rtl_latin_cells(rematched) if rematched is not None else oriented
        return mirrored_table(match) if stem_leaf else match[0]

    return TABLE_WHOLE.sub(orient, row_breaks_before_rules(text))


# Clock cell body: H:MM, optional trailing ``\text{صباحًا}``. Groups so the
# TLT hbox can wrap only the digits while the period word stays outside.
CLOCK_CELL_PARTS = re.compile(
    r"^([0-9٠-٩]{1,2}:[0-9٠-٩]{2})(\\text\{[^{}]*\})?$"
)


def _clock_cell_ltr(cell: str) -> str:
    """LTR box for H:MM in a clock array cell, or the cell unchanged.

    Only the time is forced TLT. A trailing ``\\text{…}`` (صباحًا) stays
    outside so RTL places the period word on the reading side of the digits.
    """
    stripped = cell.strip()
    parts = CLOCK_CELL_PARTS.match(stripped)
    if not parts:
        return cell
    lead_len = len(cell) - len(cell.lstrip())
    trail_len = len(cell) - len(cell.rstrip())
    lead = cell[:lead_len]
    trail = cell[len(cell) - trail_len :] if trail_len else ""
    time, period = parts.group(1), parts.group(2) or ""
    # Array cells sit under \\mathdir TRT; an hbox with TLT keeps H:MM order
    # and avoids mathrel spacing on the colon.
    return f"{lead}\\hbox{{\\textdir TLT\\relax {time}}}{period}{trail}"


def clock_array_cells(text: str) -> str:
    """Lift bare clock cells inside arrays out of TRT maths into LTR boxes.

    Times arrive as ``٨:١٥\\text{ صباحًا}`` with no surrounding ``$…$``, so
    ``unwrap_clock`` on the whole display never sees them. Spaced ratios
    (``٦ : ١٠``) are not clocks and are left alone.
    """

    def rewrite(match: re.Match[str]) -> str:
        body = match[3]
        if "\\begin{" in body or "\\multicolumn" in body:
            return match[0]
        rows = []
        for row in body.split("\\\\"):
            lead = ROW_LEAD.match(row)[0]
            cells = CELL_BREAK.split(row[len(lead) :])
            rows.append(lead + "&".join(_clock_cell_ltr(cell) for cell in cells))
        sep = "\\\\"
        return f"{match[1]}{{{match[2]}}}{sep.join(rows)}{match[4]}"

    return TABLE_WHOLE.sub(rewrite, text)


# The hyphen Arabic prose is set with. The ASCII hyphen-minus is the one character
# the Arabic face's fallback never reaches: TeX takes it for the face's hyphen
# character and sets it by a route of its own, and the face has no glyph at U+002D, so
# "2025-2026" in a footer or a sentence lost its dash with a line in the log. U+2010,
# the hyphen proper, the face does have, and it is a hyphen to the reader either way.
# Only prose is touched: a minus in the maths is set by the maths font.
HYPHEN = "\u2010"

# Words inside maths, set in the text face and so wanting the text face's hyphen.
# Brace-balanced: nested ``\text{… ($^\circ\text{س}$)}`` must stay one prose island.
TEXT_MACRO_HEAD = re.compile(r"\\text\{")


class TextInMathsMatch:
    """One balanced ``\\text{…}`` span, with the same groups a regex match would have."""

    __slots__ = ("_full", "_body", "_start", "_end")

    def __init__(self, full: str, body: str, start: int, end: int) -> None:
        self._full = full
        self._body = body
        self._start = start
        self._end = end

    def group(self, number: int = 0) -> str:
        if number == 0:
            return self._full
        if number == 1:
            return self._body
        raise IndexError(number)

    def __getitem__(self, number: int) -> str:
        return self.group(number)

    def start(self, number: int = 0) -> int:
        if number == 0:
            return self._start
        if number == 1:
            return self._start + len(r"\text{")
        return self._end - 1

    def end(self, number: int = 0) -> int:
        if number == 0:
            return self._end
        if number == 1:
            return self._end - 1
        return self._end


def _balanced_brace_end(text: str, open_at: int) -> int | None:
    """Index just past the ``}`` that closes the group at ``open_at``, or None."""
    if open_at >= len(text) or text[open_at] != "{":
        return None
    depth = 0
    index = open_at
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def iter_text_in_maths(text: str):
    """Top-level ``\\text{…}`` macros whose body is a balanced brace group.

    Nested ``\\text`` inside an outer one is not yielded on its own: the outer
    match already covers it. Yielding both would make ``sub`` rewrite the
    inner span twice and corrupt headers like
    ``\\text{درجة الحرارة العظمى ($^\\circ\\text{س}$)}``.
    """
    index = 0
    while index < len(text):
        head = TEXT_MACRO_HEAD.search(text, index)
        if head is None:
            return
        open_at = head.end() - 1
        close_at = _balanced_brace_end(text, open_at)
        if close_at is None:
            return
        yield TextInMathsMatch(
            text[head.start() : close_at],
            text[open_at + 1 : close_at - 1],
            head.start(),
            close_at,
        )
        index = close_at


class TextInMathsPattern:
    """``finditer`` / ``sub`` / ``fullmatch`` over balanced ``\\text{…}`` macros."""

    def finditer(self, text: str):
        return iter_text_in_maths(text)

    def sub(self, repl, text: str) -> str:
        pieces: list[str] = []
        cursor = 0
        for found in iter_text_in_maths(text):
            if found.start() < cursor:
                continue
            pieces.append(text[cursor : found.start()])
            pieces.append(repl(found) if callable(repl) else repl)
            cursor = found.end()
        pieces.append(text[cursor:])
        return "".join(pieces)

    def fullmatch(self, text: str) -> TextInMathsMatch | None:
        matches = list(iter_text_in_maths(text))
        if len(matches) == 1 and matches[0].start() == 0 and matches[0].end() == len(text):
            return matches[0]
        return None


TEXT_IN_MATHS = TextInMathsPattern()

# A clock written as maths — ``$7:00$``, ``$٨:٢٠\text{ صباحًا}$`` — is prose:
# hour, colon, minutes, left to right. \mathdir TRT would print the minutes
# first. The whole ``$...$`` must be that clock. A ratio ``$٦ : ١٠$`` has
# spaces around the colon and stays maths.
# A clock colon may be written ``:``, ``{:}`` or ``\colon``. All three unwrap
# to the same prose ``hour:minutes`` so the line, not the RTL formula, orders them.
CLOCK_COLON = r"(?::|\{:\}|\\colon)"
CLOCK_MATH = re.compile(
    rf"^[0-9٠-٩]{{1,2}}{CLOCK_COLON}[0-9٠-٩]{{2}}(?:\\text\{{[^{{}}]*\}})?$"
)
_CLOCK_COLON_TOKEN = re.compile(r"\\colon|\{:\}")

# Tashkeel a vocalised word carries and a math letter never does: fatha through
# sukun, and superscript alef. A sarf weight ``$فَعَلَ$`` is prose; ``$س$`` is not.
ARABIC_TASHKEEL = frozenset(chr(code) for code in range(0x064B, 0x0660)) | {
    "\u0670"
}

# An Arabic-language book (``اللغة العربية``, book id ``arabic_…``), as against
# mathematics written in Arabic. language_code is ar for both.
ARABIC_SUBJECTS = frozenset({"اللغة العربية", "arabic", "Arabic"})

# A number written as maths with an Arabic unit in \\text — ``$3\text{ ساعات}$``,
# ``$60000\text{ دينار}$``. Inside LTR maths the number sits to the left of the
# word; on an RTL line the reader hits the word first. The unit is prose, so the
# number is a maths island and the paragraph puts it on the right.
ARABIC_UNIT_MATH = re.compile(
    r"^("
    r"[0-9٠-٩]+(?:,[0-9٠-٩]+)*(?:\\frac\{[^{}]+\}\{[^{}]+\})?"
    r"|\\frac\{[^{}]+\}\{[^{}]+\}"
    r")\s*\\text\{([^{}]*[\u0600-\u06FF][^{}]*)\}$"
)

# ``\text{درجة الحرارة (}^\circ\text{C)}`` leaves the degree as a maths atom
# between two text runs. Joined, the label is one ``\text`` with ``$^\circ$``
# inside, as a unit is written.
DEGREE_SPLIT_TEXT = re.compile(
    r"\\text\{([^{}]*\()\}\s*\^(?:\\circ|\{\\circ\})\s*\\text\{([CFK])(\)?)\}"
)

# Two slashes as a parallel relation (``AB // CD``), not two tally strokes.
# A longer slash run is a tally and is left for tally_maths; a digit or letter
# on either side is left alone, as is ``///`` or ``////``.
PARALLEL_SLASHES = re.compile(r"(?<![A-Za-z0-9/])//(?![A-Za-z0-9/])")

# Slashes standing for tally strokes, not for division. A lone ``/`` is per or
# divide — ``1/2`` has a digit on the left, ``\text{ميلًا}/\text{ساعة}`` sits
# on a brace — and is left alone. Two or more strokes, or a slash-backslash
# pair, are a tally; ``////\backslash\ ///`` is eight strokes.
TALLY_RUN = re.compile(
    r"(?<![A-Za-z0-9])((?:/{2,4}(?:\\backslash)?|/(?:\\backslash))"
    r"(?:(?:\\ | )+/{1,4}(?:\\backslash)?)*)(?![A-Za-z0-9/])"
)
# Model writes a five-bar gate as \\cancel{||||}. The diagonal is the fifth stroke.
_CANCEL_TALLY = re.compile(r"\\cancel\{([|/]{1,8})\}")
_DEVANAGARI_RUN = re.compile(r"[\u0900-\u097F]+")

# Arabic punctuation the model writes inside maths — "$\{1، 2، 3\}$", a blank
# "$؟$" — that the maths font has no glyph for and would drop. The Latin maths
# face has none of them; NagwaArabicMath has the comma but not the question mark.
# They become the Latin marks the face does have (and arabic_formula then writes
# the comma as the Arabic one).
MATHS_PUNCTUATION = str.maketrans({"،": ",", "؛": ";", "؟": "?"})


def _is_tex_letter(char: str) -> bool:
    """TeX's letters: A–Z a–z, not Unicode isalpha (which includes Arabic)."""
    return "A" <= char <= "Z" or "a" <= char <= "z"


def _is_latin_letter(char: str) -> bool:
    """A Latin letter, including the diacritics Computer Modern maths lacks."""
    if _is_tex_letter(char):
        return True
    code = ord(char)
    return (
        0x00C0 <= code <= 0x00D6
        or 0x00D8 <= code <= 0x00F6
        or 0x00F8 <= code <= 0x024F
    )


def _is_ipa_char(char: str) -> bool:
    # Slash and ASCII apostrophe are maths (1/3, f'), not IPA.
    return bool(_IPA_LETTER.fullmatch(char)) or char in "ˈˌː"


def wrap_ipa_in_maths(body: str) -> str:
    """Set IPA symbols in maths with ``\\text`` so STIX renders them, not cmmi."""
    stripped = body.strip()
    if stripped.startswith("\\text{") and stripped.endswith("}"):
        return body
    # Whole-body wrap is for a phoneme like /eə/, not a formula that happens
    # to contain / or '.
    if _IPA_LETTER.search(body) and "\\" not in body:
        return f"\\text{{{body}}}"
    return body


def wrap_latin_diacritic_words(body: str) -> str:
    """Wrap a Latin word the maths face has no glyph for as ``\\text``.

    Computer Modern math italic has A–Z and a–z, but not é and the rest of
    Latin-1. The model writes ``$archéologie$`` as if it were a formula; left
    in maths the é is dropped and papers.py fails the section. Existing
    ``\\text{...}`` is left as it is; an ASCII run (``logos``, ``Arkhaios``)
    stays maths, because the face has those letters. IPA symbols and slash
    phoneme marks (``$/eə/$``) are set as text for the same reason.
    """
    body = wrap_ipa_in_maths(body)
    body = _outside_textual_math(
        body,
        lambda part: _DEVANAGARI_RUN.sub(
            lambda found: f"\\text{{\\devanagarisnippet{{{found.group(0)}}}}}",
            part,
        ),
    )
    out: list[str] = []
    index = 0
    length = len(body)
    while index < length:
        if body.startswith("\\text{", index):
            end = body.find("}", index + 6)
            if end < 0:
                out.append(body[index:])
                break
            out.append(body[index : end + 1])
            index = end + 1
            continue
        char = body[index]
        if char == "\\":
            next_at = index + 1
            if next_at < length and not _is_tex_letter(body[next_at]):
                out.append(body[index : next_at + 1])
                index = next_at + 1
                continue
            while next_at < length and _is_tex_letter(body[next_at]):
                next_at += 1
            out.append(body[index:next_at])
            index = next_at
            continue
        if _is_latin_letter(char) or _is_ipa_char(char):
            start = index
            index += 1
            while index < length and (
                _is_latin_letter(body[index]) or _is_ipa_char(body[index])
            ):
                index += 1
            run = body[start:index]
            has_letter = any(
                _is_latin_letter(letter) for letter in run
            ) or bool(_IPA_LETTER.search(run))
            if has_letter and (
                any(not _is_tex_letter(letter) for letter in run)
                or _IPA_LETTER.search(run)
            ):
                out.append(f"\\text{{{run}}}")
            else:
                out.append(run)
            continue
        out.append(char)
        index += 1
    return "".join(out)


def maths_for_face(maths: str) -> str:
    """One maths segment with its punctuation the maths font's and its words' hyphens the text face's."""
    # A model occasionally wraps one atom as \(2\) inside an already-open
    # $...$ segment. Nested math delimiters are redundant and make LaTeX fail
    # with "Bad math environment delimiter"; inside a known maths segment they
    # can be removed without changing the expression.
    maths = normalize_quotes_in_text_macros(maths)
    maths = maths.replace(r"\(", "").replace(r"\)", "")
    maths = wrap_latin_diacritic_words(maths)
    words = TEXT_IN_MATHS.sub(lambda found: found.group(0).replace("-", HYPHEN), maths)
    pieces = []
    last = 0
    for found in TEXT_IN_MATHS.finditer(words):
        pieces.append(words[last : found.start()].translate(MATHS_PUNCTUATION))
        pieces.append(found.group(0))
        last = found.end()
    pieces.append(words[last:].translate(MATHS_PUNCTUATION))
    return "".join(pieces)


def prose_hyphens(text: str) -> str:
    """The text with every hyphen the text face sets made a hyphen it has.

    Prose is the text face's, and so are the words inside maths written with \\text;
    the maths itself is left to the maths font, apart from the punctuation above.
    """
    pieces = []
    last = 0
    for found in MATH_SEGMENT.finditer(text):
        pieces.append(text[last : found.start()].replace("-", HYPHEN))
        pieces.append(maths_for_face(found.group(0)))
        last = found.end()
    pieces.append(text[last:].replace("-", HYPHEN))
    return "".join(pieces)


# How a Mashriq book in Arabic notation writes what the model wrote 0-9, with a point
# and a comma. The model writes every number in Western digits whatever the book prints
# (prompts/README.md says why). A book whose numerals are Arabic-Indic prints the
# Arabic-Indic digits, the Arabic decimal mark in place of the point, the Arabic
# thousands mark in place of a comma that groups thousands, and the Arabic per cent
# sign. A Maghreb book keeps 0-9, «.», and `\%`, and still writes a list comma as the
# Arabic comma. A comma groups thousands when exactly three digits follow it, so that
# "12,500" is one number and "(2,3)" is two; a point is a decimal point when digits
# stand either side.
ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
FIGURE_INDEX = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
ARABIC_DECIMAL = "\u066b"
ARABIC_THOUSANDS = "\u066c"
ARABIC_PERCENT = "\u066a"
ARABIC_COMMA = "\u060c"
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# ASCII-only: NUMBER's \\d also matches Arabic-Indic digits.
WESTERN_STIX_RUN = re.compile(
    r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?"
)
# Clock, ratio, or fraction in prose stays one run so RTL does not flip it.
WESTERN_STIX_CLOCK = re.compile(r"[0-9]+(?:[:/][0-9]+)+")
# Noto has U+2010, so a year dash would stay naskh unless it is asked for in STIX.
# An hbox keeps the glyph LTR without joining the two years into one run.
WESTERN_STIX_DASH = re.compile(r"(?<=[0-9])([-‐–])(?=[0-9])")
# Bare Arabic-Indic decimals in prose use the text face's ٫ glyph; wrap them so
# NagwaArabicMath prints the same mark as numbers inside ``$…$``.
ARABIC_DECIMAL_RUN = re.compile(
    r"(?<![٠-٩0-9٫٬$\\])"
    r"[٠-٩]+(?:٬[٠-٩]{3})*٫[٠-٩]+"
    r"(?![٠-٩0-9٫٬])"
)
# ASCII thousands grouping only. TeX classifies a bare comma as punctuation
# and puts a thin space after it, so ``4,400,331`` prints as ``4, 400, 331``.
# ``4{,}400{,}331`` keeps writing order (the digits stay LTR inside the
# formula) and prints as one number. Arabic-Indic grouped numbers use U+066C
# and are left to arabic_formula / arabic_maths.lua.
# Indian lakhs/crores group 3, then 2s (``2,04,300``). Applied first so the
# last ``,300`` is not taken as a Western thousands group leaving ``2,`` loose.
GROUPED_INDIAN_NUMBER = re.compile(
    r"[0-9]{1,2}(?:,[0-9]{2})+,[0-9]{3}(?:\.[0-9]+)?"
)
GROUPED_WESTERN_NUMBER = re.compile(r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?")
# A pipeline token, not a number the reader is meant to see. arabic_prose rewrites
# digits, so the index has to be lifted out and put back in ASCII; the matcher
# still accepts Arabic-Indic digits if one slips through.
FIGURE_TOKEN = re.compile(r"\[figure ([0-9٠-٩]+)\]")
FIGURE_MARKER = re.compile(r"[ \t]*\[figure ([0-9٠-٩]+)\][ \t]*")
# \\widefigure ends a paragraph, so a comma written after the marker would start
# the next line. Same marks DISPLAY_TAIL_PUNCT drops after a display.
FIGURE_THEN_PUNCT = re.compile(r"\[figure ([0-9٠-٩]+)\][ \t]*[.,;:!?،؛؟]*")

# What a digit inside maths is not to be rewritten in: the arguments TeX reads as a
# count or a length rather than prints. The model is not asked to write any of these,
# and one that it does write is left as TeX needs it.
TEX_READS = re.compile(
    r"\\(?:multicolumn|hspace\*?|vspace\*?|rule|raisebox|mskip|hskip|kern)"
    r"(?:\s*\[[^\]]*\])?(?:\s*\{[^{}]*\})*"
    r"|\\\\\s*\[[^\]]*\]"
)


def arabic_number(found: re.Match) -> str:
    return (
        found.group(0)
        .translate(ARABIC_DIGITS)
        .replace(",", ARABIC_THOUSANDS)
        .replace(".", ARABIC_DECIMAL)
    )


def figure_number(digits: str) -> int:
    """The 1-based index in a `[figure N]` token, Western or Arabic-Indic."""
    return int(digits.translate(FIGURE_INDEX))


def arabic_prose(text: str, *, convert_digits: bool = True) -> str:
    """Prose, or the words inside maths, with its numbers and marks the book's.

    `[figure N]` and `[poem N]` are tokens the paper places a drawing or a verse
    block by, not a number to print, so their digits are left ASCII (and
    normalised if they had already been rewritten). Lengths TeX itself reads
    (`\\hspace{1.5cm}`, `\\rule`, …) stay Western too: rewriting those digits
    makes the engine report a missing number. Maghreb arabic-notation books
    keep Western digits and percent signs; the hyphen of the Arabic face is still
    substituted.
    """
    pieces = []
    last = 0
    tokens = [
        *FIGURE_TOKEN.finditer(text),
        *POEM_TOKEN.finditer(text),
        *MATH_SEGMENT.finditer(text),
        *TEX_READS.finditer(text),
    ]
    tokens.sort(key=lambda found: found.start())
    for found in tokens:
        if found.start() < last:
            continue
        pieces.append(
            _arabic_prose_marks(text[last : found.start()], convert_digits=convert_digits)
        )
        raw = found.group(0)
        if raw.startswith("[poem") or raw.startswith("[figure"):
            kind = "poem" if raw.startswith("[poem") else "figure"
            pieces.append(f"[{kind} {figure_number(found.group(1))}]")
        else:
            pieces.append(raw)
        last = found.end()
    pieces.append(_arabic_prose_marks(text[last:], convert_digits=convert_digits))
    return "".join(pieces)


def _arabic_list_commas(text: str) -> str:
    """A Latin list comma in Arabic prose, not a thousands mark and not ``\\,``."""
    return re.sub(r"(?<!\\),", ARABIC_COMMA, text)


def wrap_arabic_decimal_runs(text: str) -> str:
    """Wrap bare Arabic-Indic decimals in ``$…$`` so they use the maths face."""
    return ARABIC_DECIMAL_RUN.sub(lambda found: f"${found.group(0)}$", text)


# H، M، A، T in RTL prose wraps the last letter. One TLT box keeps the list LTR.
LATIN_LETTER_LIST = re.compile(
    r"(?<![A-Za-z])(?:[A-Za-z]\s*[،,]\s*)+[A-Za-z](?![A-Za-z])"
)


def _ltr_latin_letter_lists(text: str) -> str:
    """Keep a Latin letter list (H، M، A، T) in one left-to-right box."""
    if r"\textdir TLT" in text:
        return text
    return LATIN_LETTER_LIST.sub(
        lambda found: r"\hbox{\textdir TLT\relax " + found.group(0) + "}",
        text,
    )


_STIX_WRAP = r"\foreignlanguage{english}{"
_STIX_DIMEN = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:pt|em|ex|mm|cm|in|bp|dd|pc|sp|mu)?"
)
_STIX_MATH = re.compile(
    r"\$\$.+?\$\$|\$.+?\$|\\\[.+?\\\]|\\\(.+?\\\)",
    re.DOTALL,
)


def _stix_skip_command(text: str, index: int) -> int:
    """Index just after a TeX command starting at ``index`` (a backslash)."""
    if index + 1 >= len(text):
        return index + 1
    nxt = text[index + 1]
    if nxt == "\\":
        end = index + 2
    elif "A" <= nxt <= "Z" or "a" <= nxt <= "z":
        end = index + 2
        while end < len(text) and (
            "A" <= text[end] <= "Z" or "a" <= text[end] <= "z"
        ):
            end += 1
    else:
        end = index + 2
    pos = end
    while pos < len(text) and text[pos] in " \t":
        pos += 1
    consumed_group = False
    while pos < len(text) and text[pos] in "[{":
        opener = text[pos]
        closer = "]" if opener == "[" else "}"
        depth = 0
        cursor = pos
        while cursor < len(text):
            if text[cursor] == "\\" and cursor + 1 < len(text):
                cursor += 2
                continue
            if text[cursor] == opener:
                depth += 1
            elif text[cursor] == closer:
                depth -= 1
                if depth == 0:
                    cursor += 1
                    break
            cursor += 1
        else:
            break
        pos = cursor
        consumed_group = True
        while pos < len(text) and text[pos] in " \t":
            pos += 1
    if not consumed_group:
        dimen = _STIX_DIMEN.match(text, pos)
        if dimen:
            pos = dimen.end()
    return pos


def _stix_protected_spans(text: str) -> list[tuple[int, int]]:
    """Math, TeX-command syntax, and pipeline tokens whose digits must not be wrapped."""
    spans = [found.span() for found in _STIX_MATH.finditer(text)]
    spans.extend(found.span() for found in FIGURE_TOKEN.finditer(text))
    spans.extend(found.span() for found in POEM_TOKEN.finditer(text))
    index = 0
    while index < len(text):
        if text[index] == "\\":
            end = _stix_skip_command(text, index)
            spans.append((index, end))
            index = end
            continue
        index += 1
    return spans


def _stix_covered(start: int, stop: int, spans: list[tuple[int, int]]) -> bool:
    return any(
        left <= start < right or left < stop <= right or (start <= left and stop >= right)
        for left, right in spans
    )


def _stix_already_wrapped(text: str, start: int) -> bool:
    return text[max(0, start - len(_STIX_WRAP)) : start] == _STIX_WRAP


def _stix_touching_latin(text: str, start: int, stop: int) -> bool:
    """True when a digit run sits against a Latin letter (``G7``, ``T1``, ``A1B``)."""
    return (start > 0 and _is_tex_letter(text[start - 1])) or (
        stop < len(text) and _is_tex_letter(text[stop])
    )


def _stix_box(inner: str) -> str:
    return r"\hbox{" + _STIX_WRAP + inner + "}}"


def wrap_stix_digits(text: str) -> str:
    """Set western digits (and the dash between them) in STIX. Idempotent."""

    def replace_runs(source: str, pattern: re.Pattern[str]) -> str:
        spans = _stix_protected_spans(source)
        pieces: list[str] = []
        cursor = 0
        for found in pattern.finditer(source):
            start, stop = found.span()
            if (
                _stix_already_wrapped(source, start)
                or _stix_covered(start, stop, spans)
                or _stix_touching_latin(source, start, stop)
            ):
                continue
            pieces.append(source[cursor:start])
            pieces.append(_stix_box(found.group(0)))
            cursor = stop
        pieces.append(source[cursor:])
        return "".join(pieces)

    text = replace_runs(text, WESTERN_STIX_CLOCK)

    def dash(found: re.Match[str]) -> str:
        start = found.start()
        if (
            _stix_already_wrapped(text, start)
            or _stix_covered(start, found.end(), _stix_protected_spans(text))
            or _stix_touching_latin(text, start, found.end())
        ):
            return found.group(0)
        return _stix_box(found.group(1))

    text = WESTERN_STIX_DASH.sub(dash, text)
    return replace_runs(text, WESTERN_STIX_RUN)


def maybe_stix_digits(text: str, profile: Profile | None = None) -> str:
    """wrap_stix_digits when this book keeps 0-9 in Arabic prose."""
    on = (
        (profile.rtl and not profile.indic_digits)
        if profile is not None
        else _WESTERN_STIX.get()
    )
    return wrap_stix_digits(text) if on else text


def _arabic_prose_marks(text: str, *, convert_digits: bool = True) -> str:
    text = normalize_quotes(text, guillemets=True)
    if convert_digits:
        text = NUMBER.sub(arabic_number, text)
        text = text.replace("\\%", ARABIC_PERCENT).replace("%", ARABIC_PERCENT)
        text = _arabic_list_commas(text).replace("-", HYPHEN)
        return wrap_arabic_decimal_runs(text)
    protected: dict[str, str] = {}

    def stash(found: re.Match[str]) -> str:
        token = chr(0xE200 + len(protected))
        protected[token] = found.group(0)
        return token

    stashed = NUMBER.sub(stash, text)
    stashed = _arabic_list_commas(stashed)
    for token, number in protected.items():
        stashed = stashed.replace(token, number)
    return wrap_arabic_decimal_runs(stashed.replace("-", HYPHEN))


def arabic_formula(maths: str, *, convert_digits: bool = True) -> str:
    """The maths proper, between the words: numbers, per cent signs, and list commas."""

    def rewrite(piece: str) -> str:
        if convert_digits:
            piece = NUMBER.sub(arabic_number, piece).replace("\\%", ARABIC_PERCENT)
            return _arabic_list_commas(piece)
        protected: dict[str, str] = {}

        def stash(found: re.Match[str]) -> str:
            token = chr(0xE200 + len(protected))
            protected[token] = found.group(0)
            return token

        stashed = NUMBER.sub(stash, piece)
        stashed = _arabic_list_commas(stashed)
        for token, number in protected.items():
            stashed = stashed.replace(token, number)
        return stashed

    pieces = []
    last = 0
    for found in TEX_READS.finditer(maths):
        pieces.append(rewrite(maths[last : found.start()]))
        pieces.append(found.group(0))
        last = found.end()
    pieces.append(rewrite(maths[last:]))
    return "".join(pieces)


TEXT_MATH_FONT = re.compile(
    r"\\(?:mathit|mathrm|mathbf|textrm|symit|symrm)\{((?:[^{}]|\{[^{}]*\})*)\}"
)
ARRAY_PREAMBLE = re.compile(r"\\begin\{array\}\{[^{}]*\}")


def protect_array_preambles(maths: str) -> tuple[str, dict[str, str]]:
    """Hide TeX column letters while Arabic formula letters are rewritten."""
    protected: dict[str, str] = {}

    def stash(found: re.Match[str]) -> str:
        token = chr(0xE100 + len(protected))
        protected[token] = found.group(0)
        return token

    return ARRAY_PREAMBLE.sub(stash, maths), protected


def restore_array_preambles(maths: str, protected: dict[str, str]) -> str:
    for token, preamble in protected.items():
        maths = maths.replace(token, preamble)
    return maths


def strip_text_math_fonts(maths: str) -> str:
    """Drop text-font wrappers around CLS Arabic letter macros.

    ``\\mathit{«\\waw …»}`` would otherwise set AMS letters through Noto Naskh,
    which has no glyphs at U+1EE00 and papers.py fails the section.
    """
    prev = None
    while prev != maths:
        prev = maths
        maths = TEXT_MATH_FONT.sub(r"\1", maths)
    return maths


# ``\hat{i}``, ``\hat i``, ``\hat{\imath}``, ``\hat {i}``. Not ``\widehat``.
_HAT_CMD = re.compile(r"\\hat(?![A-Za-z])\s*(?:\{([^{}]*)\}|(\S))")
# Compound units are ``N·cm``. A number product ``2 \times 3`` is not this.
_UNIT_TIMES = re.compile(
    r"(\\text\{[^{}]*\})\s*\\times\s*(\\text\{[^{}]*\})"
)


def rewrite_egypt_unit_hats(body: str) -> str:
    """Egyptian papers: a unit vector ``\\hat`` is the same mark as a vector."""
    if not _EGYPT_UNIT_VECTORS.get():
        return body

    def repl(found: re.Match[str]) -> str:
        inner = found.group(1) if found.group(1) is not None else found.group(2)
        return r"\overrightarrow{" + inner + "}"

    return _HAT_CMD.sub(repl, body)


def rewrite_arabic_unit_times(body: str) -> str:
    """``\\text{نيوتن}\\times\\text{سم}`` prints as ``\\cdot``. ``2 \\times 3`` stays."""
    return _UNIT_TIMES.sub(r"\1\\cdot\2", body)


def _prepare_arabic_formula(piece: str) -> str:
    """Alef and number sets, then the letter macros."""
    piece = arabic_math.fold_math_alef(piece)
    piece = arabic_math.replace_number_sets(piece)
    piece = arabic_math.letters_for_tex(piece)
    return arabic_math.replace_real_imag_parts(piece)


def _arabic_formula_piece(piece: str, *, convert_digits: bool) -> str:
    """Digits, then the Arabic-notation formula rewrites."""
    return _prepare_arabic_formula(arabic_formula(piece, convert_digits=convert_digits))


def _unit_letter_text_body(body: str) -> bool:
    """True when ``\\text`` holds one LETTERS letter and an optional trailing tatweel."""
    stripped = body.strip()
    if stripped.endswith(arabic_math.TATWEEL):
        stripped = stripped[: -len(arabic_math.TATWEEL)].rstrip()
    return len(stripped) == 1 and stripped in arabic_math.LETTER_TO_NAME


def _sole_text_body(inner: str) -> str | None:
    """The ``\\text{…}`` body when that macro is the whole of ``inner``, else None."""
    stripped = inner.strip()
    if not stripped.startswith("\\text{"):
        return None
    close_at = _balanced_brace_end(stripped, 5)
    if close_at is None or close_at != len(stripped):
        return None
    return stripped[6 : close_at - 1]


def _try_unwrap_unit_superscript(maths: str, index: int) -> tuple[int, str] | None:
    """If ``maths[index]`` starts ``^{\\text{X}}`` / ``^\\text{X}``, return (length, ``^{X}``)."""
    if index >= len(maths) or maths[index] != "^":
        return None
    pos = index + 1
    while pos < len(maths) and maths[pos] in " \t":
        pos += 1
    if pos < len(maths) and maths[pos] == "{":
        close_at = _balanced_brace_end(maths, pos)
        if close_at is None:
            return None
        body = _sole_text_body(maths[pos + 1 : close_at - 1])
        if body is None or not _unit_letter_text_body(body):
            return None
        return close_at - index, "^{" + body.strip() + "}"
    if maths.startswith("\\text{", pos):
        close_at = _balanced_brace_end(maths, pos + 5)
        if close_at is None:
            return None
        body = maths[pos + 6 : close_at - 1]
        if not _unit_letter_text_body(body):
            return None
        return close_at - index, "^{" + body.strip() + "}"
    return None


def _unwrap_unit_superscripts(maths: str) -> str:
    """Turn a one-letter ``\\text`` unit mark in a superscript into the bare letter."""
    pieces: list[str] = []
    index = 0
    length = len(maths)
    while index < length:
        found = _try_unwrap_unit_superscript(maths, index)
        if found is not None:
            consumed, replacement = found
            pieces.append(replacement)
            index += consumed
            continue
        pieces.append(maths[index])
        index += 1
    return "".join(pieces)


def _rewrite_symbol_texts(maths: str) -> str:
    """``\\text{ن ق}`` (any SYMBOLS key, spaces ignored) becomes that symbol's macro."""
    names = {key: name for key, name, _code in arabic_math.SYMBOLS}
    pieces: list[str] = []
    cursor = 0
    for found in TEXT_IN_MATHS.finditer(maths):
        name = names.get(re.sub(r"\s+", "", found.group(1)))
        if name is None:
            continue
        pieces.append(maths[cursor : found.start()])
        pieces.append(f"\\{name} ")
        cursor = found.end()
    pieces.append(maths[cursor:])
    return "".join(pieces)


def _qaf_text_body(body: str) -> bool:
    """True when ``\\text`` holds ق, optional spaces, and an optional trailing tatweel."""
    stripped = body.strip()
    if stripped.endswith(arabic_math.TATWEEL):
        stripped = stripped[: -len(arabic_math.TATWEEL)].rstrip()
    return stripped == "ق"


def _rewrite_text_qaf_angles(maths: str) -> str:
    """``\\text{ق}`` before ``\\angle`` is the measure, so it becomes ``\\dotlessqaft``."""
    pieces: list[str] = []
    cursor = 0
    for found in TEXT_IN_MATHS.finditer(maths):
        if not _qaf_text_body(found.group(1)):
            continue
        if not arabic_math._qaf_measures_angle(maths, found.end()):
            continue
        pieces.append(maths[cursor : found.start()])
        pieces.append(r"\dotlessqaft ")
        cursor = found.end()
    pieces.append(maths[cursor:])
    return "".join(pieces)


def rewrite_arabic_notation_text(maths: str) -> str:
    """Unit superscripts, SYMBOLS ``\\text``, and angle-measure ``\\text{ق}``, before the split."""
    return _rewrite_text_qaf_angles(
        _rewrite_symbol_texts(_unwrap_unit_superscripts(maths))
    )


def arabic_maths(maths: str, *, convert_digits: bool = True) -> str:
    """One maths segment as a book in Arabic notation prints it.

    The words in it, written with \\text, are prose and are rewritten as prose; the
    rest is the formula. A Quranic quotation is wrapped as \\text first
    (``﴿...﴾``), since the verse is words and the maths face has no glyph for the
    ornate parentheses. A run of Arabic letters ending in taa marbuta is wrapped
    as \\text next (``المساحة``), then the remaining letters become CLS macros
    (``أب`` → ``\\alef \\beh``, ``ج`` → ``\\jeemi``), so they print as isolated
    mathematical forms rather than a joined word. The order is reading order: the
    engine lays the formula out from the right and arabic_maths.lua keeps each
    number reading from the left inside it. Digits become Arabic-Indic only when
    ``convert_digits`` is true.
    """
    maths, array_preambles = protect_array_preambles(maths)
    maths = arabic_math.replace_real_imag_parts(maths)
    maths = strip_text_math_fonts(maths)
    maths = arabic_math.wrap_quran_quotes(maths)
    maths = arabic_math.wrap_taa_marbuta_words(maths)
    maths = wrap_latin_diacritic_words(maths)
    maths = rewrite_arabic_notation_text(maths)
    maths = rewrite_arabic_unit_times(maths)
    pieces = []
    last = 0
    for found in TEXT_IN_MATHS.finditer(maths):
        pieces.append(_arabic_formula_piece(
            maths[last : found.start()].translate(MATHS_PUNCTUATION),
            convert_digits=convert_digits,
        ))
        pieces.append(arabic_prose(found.group(0), convert_digits=convert_digits))
        last = found.end()
    pieces.append(_arabic_formula_piece(
        maths[last:].translate(MATHS_PUNCTUATION),
        convert_digits=convert_digits,
    ))
    return restore_array_preambles("".join(pieces), array_preambles)


def arabic_maths_letters(maths: str) -> str:
    """Arabic letters in one maths segment, leaving digits as TikZ wrote them.

    The words in ``\\text`` stay words. A Quranic quotation is wrapped as
    ``\\text`` first, the same way ``arabic_maths`` does. Digits are not
    rewritten: a figure's numbers are often computed, and arabic_maths.lua
    reprints the ones that print. A paper's own wording goes through
    arabic_maths, which does both.
    """
    maths, array_preambles = protect_array_preambles(maths)
    maths = arabic_math.replace_real_imag_parts(maths)
    maths = strip_text_math_fonts(maths)
    maths = arabic_math.wrap_quran_quotes(maths)
    maths = arabic_math.wrap_taa_marbuta_words(maths)
    maths = wrap_latin_diacritic_words(maths)
    maths = rewrite_arabic_notation_text(maths)
    maths = rewrite_arabic_unit_times(maths)
    pieces = []
    last = 0
    for found in TEXT_IN_MATHS.finditer(maths):
        pieces.append(_prepare_arabic_formula(
            maths[last : found.start()].translate(MATHS_PUNCTUATION)
        ))
        pieces.append(found.group(0))
        last = found.end()
    pieces.append(_prepare_arabic_formula(maths[last:].translate(MATHS_PUNCTUATION)))
    return restore_array_preambles("".join(pieces), array_preambles)


def math_body(segment: str) -> str:
    """The inside of a ``$...$`` or ``$$...$$`` segment."""
    if segment.startswith("$$") and segment.endswith("$$"):
        return segment[2:-2]
    return segment[1:-1]


def unwrap_clock(body: str) -> str | None:
    """Prose for a clock-only maths body, or None if it is not a clock.

    ``\\text{...}`` after the minutes is stripped to its inner words so it is
    not left as a maths command in prose.
    """
    stripped = body.strip()
    if not CLOCK_MATH.match(stripped):
        return None
    plain = _CLOCK_COLON_TOKEN.sub(":", stripped)
    return TEXT_IN_MATHS.sub(lambda found: found.group(1), plain)


def is_arabic_subject(document: dict) -> bool:
    """Whether this paper is Arabic language, not mathematics in Arabic.

    The table of contents writes the subject ``اللغة العربية``; the book id is
    ``arabic_…``. ``language_code`` is ar for a mathematics book as well.
    """
    book_id = str(document.get("book_id", ""))
    if book_id.split("_", 1)[0] == "arabic":
        return True
    return str(document.get("subject", "")).strip() in ARABIC_SUBJECTS


def has_arabic_letter(text: str) -> bool:
    """An Arabic letter the model writes, not an Arabic-Indic digit."""
    return any(
        char in arabic_math.WORD_LETTERS or "\u0621" <= char <= "\u064A"
        for char in text
    )


def unwrap_arabic_math(body: str, *, language_book: bool = False) -> str | None:
    """Prose for a maths body that is really Arabic wording, or None.

    In an Arabic-language book every Arabic letter in maths is wording: a sarf
    weight, a verse, an unvocalised word. ``\\text{...}`` is already words (a
    unit, an underline) and is left in maths.     In a mathematics book only a
    vocalised word with no TeX is unwrapped, so ``$س$`` stays a variable.
    Spaced letters (``$سَ صَ عـَ لَ$``) and a single letter with optional
    tatweel or tashkeel stay maths: they are vertices, not a word.
    """
    stripped = body.strip()
    if not stripped:
        return None
    if language_book:
        leftover = TEXT_IN_MATHS.sub("", stripped)
        if not has_arabic_letter(leftover):
            if TEXT_IN_MATHS.fullmatch(stripped) and has_arabic_letter(stripped):
                return TEXT_IN_MATHS.sub(lambda found: found.group(1), stripped)
            return None
        if "\\" in leftover:
            return None
        return TEXT_IN_MATHS.sub(lambda found: found.group(1), stripped)
    if "\\" in stripped:
        return None
    has_letter = False
    has_mark = False
    for char in stripped:
        if char in arabic_math.WORD_LETTERS:
            has_letter = True
        elif char in ARABIC_TASHKEEL:
            has_mark = True
        elif not char.isspace():
            return None
    if not has_letter or not has_mark:
        return None
    # سَ صَ عـَ لَ is four vertices, not one vocalised word like فَعَلَ.
    if _spaced_math_letters(stripped) or _single_math_letter(stripped):
        return None
    return stripped


def _is_math_letter_token(token: str) -> bool:
    """One WORD_LETTERS letter, optional tatweel, optional tashkeel."""
    letters = [
        char
        for char in token
        if char in arabic_math.WORD_LETTERS and char != arabic_math.TATWEEL
    ]
    if len(letters) != 1:
        return False
    for char in token:
        if (
            char == letters[0]
            or char == arabic_math.TATWEEL
            or char in ARABIC_TASHKEEL
        ):
            continue
        return False
    return True


def _spaced_math_letters(body: str) -> bool:
    """True when the body is two or more spaced letters (optional tatweel / tashkeel)."""
    tokens = body.split()
    return len(tokens) >= 2 and all(_is_math_letter_token(token) for token in tokens)


def _single_math_letter(body: str) -> bool:
    return _is_math_letter_token(body.strip())


def unwrap_arabic_unit(body: str) -> tuple[str, str] | None:
    """A number and its Arabic unit, if that is all this maths body is."""
    found = ARABIC_UNIT_MATH.match(body.strip())
    if not found:
        return None
    return found.group(1), found.group(2).strip()


TRAILING_ARABIC_UNIT = re.compile(
    r"\\(?:text|mathrm|operatorname)\{\s+(?P<unit>[^{}]*[\u0600-\u06FF][^{}]*)\}"
    r"(?P<power>\s*\^\s*(?:\{[^{}]+\}|[-+]?[0-9]+))?\s*$"
)
ARABIC_IN_TEXT = re.compile(r"[\u0600-\u06FF]")
_LEFT_RIGHT_CMD = re.compile(r"\\(?:left|right)(?![A-Za-z])\s*")


def math_delimiters_as_text(piece: str) -> str:
    """Plain ``( )`` when a math snippet is moved into text.

    ``\\left`` / ``\\right`` are math-mode only. A peeled unit such as
    ``\\left(كم\\right)`` would otherwise sit in an RTL text hbox and TeX
    would insert ``$``.
    """
    return _LEFT_RIGHT_CMD.sub("", piece)
TOP_RELATIONS = (
    (r"\leq", r"\geq"),
    (r"\geq", r"\leq"),
    (r"\le", r"\ge"),
    (r"\ge", r"\le"),
    (r"\neq", r"\neq"),
    ("=", "="),
    ("<", ">"),
    (">", "<"),
)


def _top_relations(body: str) -> list[tuple[int, int, str, str]]:
    """Top-level relations and their inverses, from left to right."""
    depth = {"{": 0, "(": 0, "[": 0}
    closing = {"}": "{", ")": "(", "]": "["}
    found: list[tuple[int, int, str, str]] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char in depth:
            depth[char] += 1
            index += 1
            continue
        if char in closing:
            opener = closing[char]
            depth[opener] = max(0, depth[opener] - 1)
            index += 1
            continue
        if not any(depth.values()):
            for token, inverse in TOP_RELATIONS:
                if body.startswith(token, index):
                    # A shorter command token must not be the prefix of a longer one.
                    stop = index + len(token)
                    if token.startswith("\\") and stop < len(body) and body[stop].isalpha():
                        continue
                    found.append((index, stop, token, inverse))
                    index = stop
                    break
            else:
                index += 1
            continue
        index += 1
    return found


def _reverse_top_level_relations(body: str) -> str:
    if _preserve_short_division(body):
        return body
    relations = _top_relations(body)
    if not relations:
        return body
    operands: list[str] = []
    inverses: list[str] = []
    cursor = 0
    for start, stop, token, inverse in relations:
        operand = body[cursor:start].strip()
        if not operand:
            rest = body[stop:].strip()
            if len(relations) == 1 and rest:
                # ``$= 4\text{ سم}$``: the subject is in prose. Put the
                # relation on the right of the quantity without inverting,
                # so an RTL reader hits ``=`` before ``4``.
                return f"{rest} {token}"
            return body
        operands.append(operand)
        inverses.append(inverse)
        cursor = stop
    last = body[cursor:].strip()
    if not last:
        return body
    operands.append(last)
    pieces = [operands[-1]]
    for inverse, operand in zip(reversed(inverses), reversed(operands[:-1])):
        pieces.extend((inverse, operand))
    return " ".join(pieces)


def _closing_brace(body: str, opened: int) -> int:
    """The index of the ``}`` that closes the ``{`` at ``opened``, or -1."""
    depth = 0
    for index in range(opened, len(body)):
        char = body[index]
        if char == "{" and (index == 0 or body[index - 1] != "\\"):
            depth += 1
        elif char == "}" and (index == 0 or body[index - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return index
    return -1


def _is_arabic_text_operand(operand: str) -> bool:
    """Whether a relation's side is one ``\\text{…}`` of Arabic words and nothing else."""
    operand = operand.strip()
    if not operand.startswith("\\text{"):
        return False
    close = _closing_brace(operand, len("\\text"))
    return close == len(operand) - 1 and bool(ARABIC_IN_TEXT.search(operand))


def _top_operands(body: str) -> list[str]:
    operands: list[str] = []
    cursor = 0
    for start, stop, _token, _inverse in _top_relations(body):
        operands.append(body[cursor:start].strip())
        cursor = stop
    operands.append(body[cursor:].strip())
    return operands


def _has_top_level_list(body: str) -> bool:
    """``a = 1,\\allowbreak b = 2`` is a list of relations, not one chain to turn round."""
    depth = 0
    for char in body:
        if char in "{([":
            depth += 1
        elif char in "})]":
            depth = max(0, depth - 1)
        elif char in ",;" and depth == 0:
            return True
    return "\\allowbreak" in body


def _has_arabic_text_operand(body: str) -> bool:
    if not _top_relations(body) or _has_top_level_list(body):
        return False
    return any(_is_arabic_text_operand(operand) for operand in _top_operands(body))


def _has_inner_arabic_text(head: str) -> bool:
    """Arabic words inside the maths before a unit, other than a side of its own.

    ``$\\text{المساحة} = 2x + y\\text{ سم}^2$`` names its quantity in a side of its
    own and is turned round with the unit peeled. ``$600\\text{ جنيه} < 900\\text{ جنيه}$``
    or ``$C = \\text{مجموعة … لها } \\frac{8}{2} \\text{ أضلاع}$`` is not a
    quantity with one unit: peeling the last word prints ``900 > 600\\text{ جنيه}`` and
    a stray ``جنيه`` after it.
    """
    whole = {
        operand for operand in _top_operands(head) if _is_arabic_text_operand(operand)
    }
    return any(
        ARABIC_IN_TEXT.search(found.group(0)) and found.group(0) not in whole
        for found in TEXTUAL_MATH_RUN.finditer(head)
    )


def rtl_latin_unit_layout(text: str) -> str:
    """Lay out Arabic unit suffixes and Arabic-worded relations on an RTL Latin-math line.

    A formula is an LTR box on an RTL line, so its right end is read first. A
    relation that carries Arabic words — a trailing unit, or a side that is all
    Arabic wording — is turned round once so it reads in the order it was
    written: ``$\\text{النسبة} = \\frac{3x}{y}$`` as ``$\\frac{3x}{y} = \\text{النسبة}$``.
    Turning round is not idempotent; the paper reads each field once.
    """

    def rewrite(found: re.Match[str]) -> str:
        segment = found.group(0)
        if _preserve_short_division(segment):
            return segment
        opened = 2 if segment.startswith("$$") else 1
        body = segment[opened:-opened]
        if TABLE_SEGMENT.search(body):
            return segment
        trailing = TRAILING_ARABIC_UNIT.search(body)
        if trailing is None or _has_inner_arabic_text(body[: trailing.start()]):
            if _has_arabic_text_operand(body):
                reversed_body = _reverse_top_level_relations(body.strip())
                return f"{segment[:opened]}{reversed_body}{segment[-opened:]}"
            return segment
        if opened == 2:
            raise RuntimeError(
                f"display mathematics with a trailing Arabic unit is unsupported: {segment!r}"
            )
        expression = body[: trailing.start()].strip()
        if not expression:
            # A unit-only island such as $\text{ كم/س}$ or $\text{ م}^3$
            # must stay inside \text{}. Unwrapping it emits `$$`, which LaTeX
            # reads as display math and then corrupts the next formula.
            return segment
        expression = _reverse_top_level_relations(expression)
        label = math_delimiters_as_text(trailing.group("unit").strip())
        if not label:
            raise RuntimeError("Arabic unit layout has an empty label")
        power = (trailing.group("power") or "").strip()
        suffix = f"${power}$" if power else ""
        return f"${expression}$ {label}{suffix}"

    return MATH_SEGMENT.sub(rewrite, text)


POLYGON_THEN_LATIN = re.compile(r"\\text\{المضلع \}\s*([A-Za-z]+)")


def rtl_latin_polygon_layout(text: str) -> str:
    """``\\text{المضلع } STUV`` as ``STUV\\ \\text{المضلع}`` on an RTL Latin-math line.

    The trailing space inside ``\\text`` is the English ``\\text{polygon }``
    habit. On an RTL page that space sits on the left of the word and the
    Latin name on its right, so the two glue. The name is written first in
    LTR maths and the word follows it, with a math skip between them.
    """
    return POLYGON_THEN_LATIN.sub(
        lambda found: rf"{found.group(1)}\ \text{{المضلع}}",
        text,
    )


ARABIC_TEXT_THEN_MATH = re.compile(
    r"\\text\{([^{}]*[\u0600-\u06FF][^{}]*?)\s+\}\s*"
    r"(?=(?:\\blank|\\frac|[0-9٠-٩A-Za-z]|\\[A-Za-z]+|\\\{))"
)


def rtl_latin_text_math_space(text: str) -> str:
    """Move a trailing space out of ``\\text{arabic }`` when maths follows.

    On an RTL page that space sits on the left of the word — the wrong side of
    the number or letter that follows — so the two glue. A math skip after the
    closing brace is LTR, keeps them apart, and does not reverse the number.
    """
    if _preserve_short_division(text):
        return text
    return ARABIC_TEXT_THEN_MATH.sub(r"\\text{\1}\\ ", text)


ARABIC_PLAIN_TEXT = re.compile(r"\\text\{[^{}]*\}")
# What an Arabic phrase may take into itself: a number (grouped thousands and
# decimals allowed) or one Latin letter, with a prime or a subscript.
PHRASE_ATOM = (
    r"(?:[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?"
    r"|[A-Za-z](?:'+|_(?:\{[A-Za-z0-9]+\}|[A-Za-z0-9]))?)"
)
PHRASE_RELATION = (
    r"\\(?:leq|geq|le|ge|neq|in|notin|ni|subset|subseteq|supset|supseteq"
    r"|equiv|approx|blank)(?![A-Za-z])"
)
# Only a letter is taken in from the left: a number before Arabic words is a
# quantity and its unit (``30\text{ وحدة مربعة}``), which goes to prose instead.
ATOM_BEFORE_PHRASE = re.compile(
    r"(?P<atom>[A-Za-z](?:'+|_(?:\{[A-Za-z0-9]+\}|[A-Za-z0-9]))?)\s*$"
)
BOUNDARY_BEFORE_ATOM = re.compile(
    rf"(?:^|[:,=<>(\[{{&]|\\text\{{\s*[،؛]\s*\}}|{PHRASE_RELATION})$"
)
TRAILING_MATH_SPACE = re.compile(r"(?:\\ |\s)+$")
ATOM_AFTER_PHRASE = re.compile(
    rf"\s*(?P<atom>{PHRASE_ATOM})"
    rf"(?=\s*(?:$|[:,=<>)\]}}&]|\\[{{}}]|\\text\{{|{PHRASE_RELATION}))"
)
CHAINED_PHRASE = re.compile(r"\s*\\text\{(\s[^{}]*)\}")
# A Latin word written as ``\text{EGYPT}`` is an atom of the Arabic phrase, not
# a second ``\text`` beside it: ``\text{$x$ حرف في كلمة $\text{EGYPT}$}``.
LATIN_TEXT_ATOM = re.compile(
    r"\s*\\text\{(?P<body>\s*[A-Za-z][A-Za-z0-9 .'-]*\s*)\}"
)
MATH_AFTER_PHRASE = re.compile(
    r"\s*(?:\\blank|\\frac|[0-9A-Za-z]|\\[A-Za-z]+|\\\{)"
)
# و before a letter starts a new condition (``… \text{ و } x \le 16``); before a
# number it is inside the phrase (``\text{ بين } 10 \text{ و } 20``) and stays.
SET_BUILDER_AND = re.compile(r"\s*\\text\{\s*و\s*\}\s*(?=[A-Za-z](?![A-Za-z]))")
# Maths in this profile uses Latin punctuation. A ``\text{،}`` is a translated
# list comma, not an Arabic word or unit.
ARABIC_PUNCT_TEXT = re.compile(
    r"\s*\\text\{\s*(?P<mark>[،؛])\s*\}(?:\\ )?\s*"
)
ENSUREMATH = re.compile(r"\\ensuremath\{((?:[^{}]|\{[^{}]*\})*)\}")


def _phrase_atom(atom: str) -> str:
    """An atom held as ``\\ensuremath`` inside ``\\text`` until the paper writes it as ``$…$``.

    A nested ``$`` would split the formula wherever the maths is cut out of the
    field by its dollars, so the atom keeps a brace form until ``to_latex``.
    """
    return f"\\ensuremath{{{_brace_grouped_commas(atom)}}}"


def set_builder_commas(body: str) -> str:
    """``\\{x : x \\in \\mathbb{N} \\text{ و } …\\}`` with a comma between the conditions.

    Only after the colon of a set-builder; و elsewhere (``\\overline{AC}\\text{ و}…``)
    joins names in a sentence and stays a word.
    """
    out: list[str] = []
    conditions: list[bool] = []
    index = 0
    while index < len(body):
        if body.startswith("\\{", index):
            conditions.append(False)
            out.append("\\{")
            index += 2
            continue
        if body.startswith("\\}", index):
            if conditions:
                conditions.pop()
            out.append("\\}")
            index += 2
            continue
        if conditions and body[index] == ":":
            conditions[-1] = True
        if conditions and conditions[-1]:
            found = SET_BUILDER_AND.match(body, index)
            if found:
                out.append(", ")
                index = found.end()
                continue
        out.append(body[index])
        index += 1
    return "".join(out)


def latin_math_punctuation(body: str) -> str:
    """``\\text{،}`` as ``, ``, so set-builder lists use the Latin comma.

    Arabic in an RTL Latin formula is words and units. A punctuation-only
    ``\\text`` is the English list mark written in Arabic script; the numbers
    and notation around it are Latin, so the comma is too.
    """

    def replace(found: re.Match[str]) -> str:
        return ", " if found.group("mark") == "،" else "; "

    return ARABIC_PUNCT_TEXT.sub(replace, body)


def fold_arabic_phrases(body: str) -> str:
    """An Arabic phrase and the numbers or letters inside it as one ``\\text``.

    ``x \\text{ عدد أولي أقل من } 15`` is one sentence in Arabic. Set as maths
    and words in turn, the LTR formula puts ``x`` on the left and ``15`` on the
    right, so an RTL reader meets them at the wrong ends of the phrase. Inside
    one ``\\text`` the words run RTL and each atom sits where it is read:
    ``\\text{$x$ عدد أولي أقل من $15$}``. A phrase opens toward an atom with
    the space the English habit leaves inside ``\\text``; only phrases of two or
    more words are folded, so a unit such as ``5\\text{ سم}`` is not.
    """
    position = 0
    while True:
        found = ARABIC_PLAIN_TEXT.search(body, position)
        if found is None:
            return body
        content = found.group(0)[len("\\text{") : -1]
        if not ARABIC_IN_TEXT.search(content) or len(content.split()) < 2:
            position = found.end()
            continue
        start, end = found.start(), found.end()
        before: list[str] = []
        if content[:1].isspace():
            atom = ATOM_BEFORE_PHRASE.search(body[:start])
            lead = TRAILING_MATH_SPACE.sub("", body[: atom.start()]) if atom else ""
            if atom and BOUNDARY_BEFORE_ATOM.search(lead):
                before.append(_phrase_atom(atom.group("atom")))
                start = atom.start()
        after: list[str] = []
        tail = content
        while tail[-1:].isspace():
            atom = ATOM_AFTER_PHRASE.match(body, end)
            if atom is not None:
                after.append(_phrase_atom(atom.group("atom")))
                end = atom.end()
                chained = CHAINED_PHRASE.match(body, end)
                if chained is None or not ARABIC_IN_TEXT.search(chained.group(1)):
                    tail = ""
                    break
                tail = chained.group(1)
                after.append(tail.strip())
                end = chained.end()
                continue
            latin = LATIN_TEXT_ATOM.match(body, end)
            if latin is None or ARABIC_IN_TEXT.search(latin.group("body")):
                break
            after.append(_phrase_atom(rf"\text{{{latin.group('body').strip()}}}"))
            end = latin.end()
            tail = ""
            break
        if not before and not after:
            position = found.end()
            continue
        first = content.lstrip() if before else content
        if after:
            first = first.rstrip()
        words = " ".join([*before, first, *after]).rstrip()
        folded = f"\\text{{{words}}}"
        if tail[-1:].isspace() and MATH_AFTER_PHRASE.match(body, end):
            folded += "\\ "
            while end < len(body) and body[end].isspace():
                end += 1
        body = body[:start] + folded + body[end:]
        position = start + len(folded)


def rtl_latin_phrase_layout(text: str) -> str:
    """Set-builder commas and folded Arabic phrases in each formula of a field."""

    def rewrite(found: re.Match[str]) -> str:
        segment = found.group(0)
        if _preserve_short_division(segment):
            return segment
        opened = 2 if segment.startswith("$$") else 1
        body = segment[opened:-opened]
        if TABLE_SEGMENT.search(body) or not ARABIC_IN_TEXT.search(body):
            return segment
        body = latin_math_punctuation(set_builder_commas(body))
        trailing = TRAILING_ARABIC_UNIT.search(body)
        if trailing is not None and not _has_inner_arabic_text(body[: trailing.start()]):
            # The unit is the unit layout's to peel; only what comes before it folds.
            body = fold_arabic_phrases(body[: trailing.start()]) + body[trailing.start() :]
        else:
            body = fold_arabic_phrases(body)
        return f"{segment[:opened]}{body}{segment[-opened:]}"

    return MATH_SEGMENT.sub(rewrite, text)


def text_math_dollars(body: str) -> str:
    """``\\ensuremath{…}`` inside a ``\\text`` written as the ``$…$`` the paper prints."""
    if "\\ensuremath{" not in body:
        return body
    pieces: list[str] = []
    cursor = 0
    while True:
        opened = body.find("\\text{", cursor)
        if opened < 0:
            break
        close = _closing_brace(body, opened + len("\\text"))
        if close < 0:
            break
        pieces.append(body[cursor:opened])
        pieces.append(ENSUREMATH.sub(r"$\1$", body[opened : close + 1]))
        cursor = close + 1
    pieces.append(body[cursor:])
    return "".join(pieces)


def rtl_latin_layout(text: str) -> str:
    """Phrases, unit suffixes, relations, polygon names, and ``\\text`` gaps for RTL Latin maths."""
    if _preserve_short_division(text):
        return text
    return rtl_latin_text_math_space(
        rtl_latin_polygon_layout(rtl_latin_unit_layout(rtl_latin_phrase_layout(text)))
    )


def join_degree_text(body: str) -> str:
    """One ``\\text`` for a heading that split ``(`` from ``°C)``."""
    return DEGREE_SPLIT_TEXT.sub(
        lambda found: (
            f"\\text{{{found.group(1)}$^\\circ${found.group(2)}{found.group(3)}}}"
        ),
        body,
    )


def tally_count(run: str) -> int:
    """How many strokes a slash-and-backslash tally names."""
    token = run.replace(r"\backslash", "B").replace(r"\ ", "")
    return token.count("/") + token.count("B")


TEXTUAL_MATH_RUN = re.compile(
    r"\\(?:text|mathrm|operatorname)\{[^{}]*\}"
)


def _outside_textual_math(body: str, rewrite: Callable[[str], str]) -> str:
    pieces: list[str] = []
    cursor = 0
    for found in TEXTUAL_MATH_RUN.finditer(body):
        pieces.append(rewrite(body[cursor : found.start()]))
        pieces.append(found.group(0))
        cursor = found.end()
    pieces.append(rewrite(body[cursor:]))
    return "".join(pieces)


def _brace_grouped_commas(part: str) -> str:
    """Brace Indian lakh commas, then Western thousands commas."""
    part = GROUPED_INDIAN_NUMBER.sub(
        lambda found: found.group(0).replace(",", "{,}"), part
    )
    return GROUPED_WESTERN_NUMBER.sub(
        lambda found: found.group(0).replace(",", "{,}"), part
    )


def tight_thousands(body: str) -> str:
    """``4,400,331`` as ``4{,}400{,}331``, so the comma is not math punctuation.

    A punctuation comma gets a thin space after it. Bracing it keeps the digits
    in writing order and prints them as one number. Indian grouping
    (``2,04,300``) is braced first, so a lakh is not split into a list comma
    plus a Western thousands tail. List commas (``10, 20``) and coordinate
    commas (``(2,3)``) are not thousands grouping and stay as they are.
    Already-braced marks are left alone.
    """
    return _outside_textual_math(body, _brace_grouped_commas)


def parallel_slashes(body: str) -> str:
    """``//`` between atoms as ``\\parallel``, before slash runs become tallies."""
    return _outside_textual_math(
        body, lambda part: PARALLEL_SLASHES.sub(r"\\parallel ", part)
    )


# Table cells keep the 15bp CLS strokes. Inline math is also displaystyle
# (\\everymath), so shrink it in Python rather than \\mathchoice.
INLINE_TALLY_SCALE = "0.8"


def _tally_command(count: int, *, table: bool) -> str:
    cmd = f"\\CounterTallyMarks{{{count}}}"
    if table:
        return cmd
    return f"\\vcenter{{\\hbox{{\\scalebox{{{INLINE_TALLY_SCALE}}}{{{cmd}}}}}}}"


def tally_maths(body: str) -> str:
    """Slash runs that are tally marks, as ``\\CounterTallyMarks``."""
    table = bool(TABLE_SEGMENT.search(body))

    def replace(found: re.Match[str]) -> str:
        count = tally_count(found.group(1))
        if count <= 0:
            return found.group(0)
        return _tally_command(count, table=table)

    def cancel_tally(found: re.Match[str]) -> str:
        return _tally_command(len(found.group(1)) + 1, table=table)

    rewritten = _CANCEL_TALLY.sub(cancel_tally, body)
    return _outside_textual_math(
        rewritten, lambda part: TALLY_RUN.sub(replace, part)
    )


# Axis names the figure drawer emits in Latin; Arabic-notation papers use CLS letters.
AXIS_LATIN_MATH = re.compile(r"\$([xy])\$")


def arabic_axis_letters(tikz: str) -> str:
    """Map lone axis ``$x$`` / ``$y$`` labels to ``\\seen`` / ``\\sad``."""

    def one(found: re.Match[str]) -> str:
        return r"$\seen $" if found.group(1) == "x" else r"$\sad $"

    return AXIS_LATIN_MATH.sub(one, tikz)


# ``$0.5*(P1) + 0.5*(P3)$`` is calc, not a printed label. ``$(A)!0.5!(B)$``
# already starts with ``(``; this catches a coefficient times a point.
_TIKZ_CALC_BODY = re.compile(r"\*\s*\(|\+\s*\(|-\s*\(|!\s*\(")


def _is_tikz_calc(body: str) -> bool:
    """True when a ``$...$`` is a TikZ calc coordinate, not printed maths."""
    if body.lstrip().startswith("("):
        return True
    return _TIKZ_CALC_BODY.search(body) is not None


def arabic_tikz(
    tikz: str, *, language_book: bool = False, convert_digits: bool = True
) -> str:
    """A drawing's printed maths labels and numerals, without touching coordinates.

    Calc coordinates are written ``$(PL1)!0.75!(V1)$`` / ``$(\\i,0)+(0.08,-0.08)$``
    / ``$0.5*(P1) + 0.5*(P3)$``: those dollars are not maths, and rewriting their
    digits or commas makes PGF reject the path. A ``$...$`` whose body starts with
    ``(`` or is a coefficient times a point is left as it is. A clock ``$١:٠٠$``
    is prose, not a formula. Arabic wording wrapped as maths is prose as well.
    Every other ``$...$`` is a printed label and has its letters rewritten. Lone
    axis ``$x$`` / ``$y$`` become ``\\seen`` / ``\\sad``.
    """
    tikz = arabic_axis_letters(tikz)

    # TikZ calc coordinates delimit nested expressions with ``$(`` and ``$)``.
    # Mask every balanced pair before the ordinary $...$ scanner runs; otherwise
    # nested calc dollars are paired as maths and dimensions such as ``1.5mm``
    # are rewritten as Arabic-Indic digits that PGF cannot parse.
    sentinel = "\ue000"
    characters = list(tikz)
    stack: list[int] = []
    paired: set[int] = set()
    for token in re.finditer(r"\$\(|\$\)", tikz):
        if token.group(0) == "$(":
            stack.append(token.start())
        elif stack:
            paired.add(stack.pop())
            paired.add(token.start())
    for index in paired:
        characters[index] = sentinel
    protected = "".join(characters)

    def rewrite(found: re.Match) -> str:
        segment = found.group(0)
        body = math_body(segment)
        if _is_tikz_calc(body):
            return segment
        clock = unwrap_clock(body)
        if clock is not None:
            return arabic_prose(clock, convert_digits=convert_digits)
        word = unwrap_arabic_math(body, language_book=language_book)
        if word is not None:
            return arabic_prose(word, convert_digits=convert_digits)
        return arabic_maths(segment, convert_digits=convert_digits)

    return MATH_SEGMENT.sub(rewrite, protected).replace(sentinel, "$")


TIKZ_INTERVAL_NODE = re.compile(
    r"((?<![A-Za-z])node\b[^;]*?\{)"
    r"([0-9٠-٩]+(?:[.,٫][0-9٠-٩]+)?[-‐–]"
    r"(?:[0-9٠-٩]+(?:[.,٫][0-9٠-٩]+)?)?)"
    r"(\}\s*;)"
)
NODE_MATH_PARENS = re.compile(
    r"^(?P<before>.*?)\(\s*(?P<math>\$[^$]+\$)\s*\)(?P<after>.*)$",
    re.DOTALL,
)

# A caption on a row of related drawings: "الشكل 1", "شكل ٢", "Figure 3".
FIGURE_SEQUENCE_CAPTION = re.compile(
    r"\\node\b[^;]*?\bat\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)\s*\{"
    r"\s*((?:ال)?شكل|Figure)\s*([0-9٠-٩]+)\s*\}",
    re.IGNORECASE,
)
TIKZ_COORD_PAIR = re.compile(r"\((-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\)")


def _tikz_num(value: float) -> str:
    """A coordinate written without trailing zeros."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def arabic_tikz_nodes(tikz: str, *, convert_digits: bool = True) -> str:
    """Printed node bodies, never geometry: digits and marks as the book prints them."""

    def rewrite(found: re.Match[str]) -> str:
        return (
            found.group(1)
            + arabic_prose(found.group(2), convert_digits=convert_digits)
            + found.group(3)
        )

    return TIKZ_NODE.sub(rewrite, tikz)


def sequence_figure_captions(tikz: str) -> list[tuple[int, float, float]]:
    """Numbered figure captions in a drawing, as (n, x, y)."""
    found: list[tuple[int, float, float]] = []
    for match in FIGURE_SEQUENCE_CAPTION.finditer(tikz):
        number = int(match.group(4).translate(FIGURE_INDEX))
        found.append((number, float(match.group(1)), float(match.group(2))))
    found.sort(key=lambda row: row[0])
    return found


def rtl_sequence_figures(tikz: str) -> str:
    """Put الشكل 1 / Figure 1 on the right of a side-by-side sequence.

    A coordinate grid is left alone: only a picture that captions two or more
    numbered figures is reflected, and only if those captions still read left
    to right.
    """
    captions = sequence_figure_captions(tikz)
    if len(captions) < 2:
        return tikz
    xs = [row[1] for row in captions]
    if all(left > right for left, right in zip(xs, xs[1:])):
        return tikz
    pairs = [
        float(match.group(1))
        for match in TIKZ_COORD_PAIR.finditer(tikz)
    ]
    if len(pairs) < 2:
        return tikz
    low, high = min(pairs), max(pairs)
    if high - low < 1e-9:
        return tikz

    def flip(found: re.Match[str]) -> str:
        x = low + high - float(found.group(1))
        return f"({_tikz_num(x)},{found.group(2)})"

    return TIKZ_COORD_PAIR.sub(flip, tikz)


def profile_tikz(
    tikz: str, profile: Profile, *, language_book: bool = False
) -> str:
    """A drawing's labels under both its notation and page-direction profile."""
    if profile.notation == "arabic":
        tikz = arabic_tikz(
            tikz,
            language_book=language_book,
            convert_digits=profile.indic_digits,
        )
    elif (
        profile.language_code,
        profile.numerals,
        profile.notation,
    ) == ("ar", "western", "latin"):
        tikz = rtl_latin_layout(tikz)
    if profile.indic_digits:
        tikz = arabic_tikz_nodes(tikz, convert_digits=True)
    if not profile.rtl:
        return tikz

    def isolate(found: re.Match[str]) -> str:
        label = re.sub(r"[-‐–]", HYPHEN, found[2])
        return (
            found[1]
            + r"\hbox{\foreignlanguage{english}{"
            + label
            + "}}"
            + found[3]
        )

    tikz = TIKZ_INTERVAL_NODE.sub(isolate, tikz)
    tikz = rtl_isolate_tagged_node_math(tikz)
    return rtl_sequence_figures(tikz)


def rtl_isolate_tagged_node_math(tikz: str) -> str:
    """Keep ``($B$)`` LTR in mixed word/math node labels so RTL cannot wrap it."""

    def rewrite(found: re.Match[str]) -> str:
        prefix, body, suffix = found.group(1), found.group(2), found.group(3)
        if r"\textdir TLT" in body:
            return found.group(0)
        tagged = NODE_MATH_PARENS.match(body.strip())
        if tagged is None:
            return found.group(0)
        isolated = (
            r"\hbox{\textdir TLT\relax (" + tagged.group("math") + ")}"
        )
        parts: list[str] = []
        before = tagged.group("before").rstrip()
        after = tagged.group("after").lstrip()
        if before:
            parts.append(before)
        parts.append(isolated)
        if after:
            parts.append(after)
        return prefix + " ".join(parts) + suffix

    return TIKZ_NODE.sub(rewrite, tikz)


def notation_drawings(
    drawings: Drawings, profile: Profile, *, language_book: bool = False
) -> Drawings:
    """The library as this book's maths prints it; the descriptions are the keys and stay."""
    rewritten: Drawings = {}
    for description, row in drawings.items():
        tikz = row.get("tikz")
        if not tikz:
            rewritten[description] = row
            continue
        copied = dict(row)
        copied["tikz"] = profile_tikz(
            tikz, profile, language_book=language_book
        )
        rewritten[description] = copied
    return rewritten


def arabic_notation(
    text: str, *, language_book: bool = False, convert_digits: bool = True
) -> str:
    """A field as a book in Arabic notation prints it: prose and maths each rewritten."""
    text = clock_array_cells(text)
    pieces = []
    last = 0
    for found in MATH_SEGMENT.finditer(text):
        pieces.append(arabic_prose(text[last : found.start()], convert_digits=convert_digits))
        body = math_body(found.group(0))
        clock = unwrap_clock(body)
        if clock is not None:
            pieces.append(arabic_prose(clock, convert_digits=convert_digits))
        else:
            word = unwrap_arabic_math(body, language_book=language_book)
            if word is not None:
                pieces.append(arabic_prose(word, convert_digits=convert_digits))
            else:
                pieces.append(arabic_maths(found.group(0), convert_digits=convert_digits))
        last = found.end()
    pieces.append(arabic_prose(text[last:], convert_digits=convert_digits))
    return "".join(pieces)


def unwrap_arabic_math_field(text: str, *, language_book: bool) -> str:
    """The same unwrap as arabic_notation, without rewriting digits or letters."""
    pieces = []
    last = 0
    for found in MATH_SEGMENT.finditer(text):
        pieces.append(text[last : found.start()])
        body = math_body(found.group(0))
        clock = unwrap_clock(body)
        if clock is not None:
            pieces.append(clock)
        else:
            word = unwrap_arabic_math(body, language_book=language_book)
            pieces.append(word if word is not None else found.group(0))
        last = found.end()
    pieces.append(text[last:])
    return "".join(pieces)


def written_for(text: str, profile: Profile) -> str:
    """A line of the paper's own — a title, a heading, the foot — as this book prints it.

    In the book's digits where they are Arabic-Indic, and with the hyphen the Arabic
    face has where the book is Arabic; an English book's is printed as it came.
    """
    if profile.indic_digits:
        return arabic_prose(text)
    return text.replace("-", HYPHEN) if profile.rtl else text


# A bare caret in a module title, as the XML writes it: x^k, or an Arabic variable
# and exponent with optional spaces around the caret. Not matched inside $...$.
BARE_HEADING_EXPONENT = re.compile(
    r"(?<![\\$])"
    r"(?P<base>[A-Za-z\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+)"
    r"\s*\^\s*"
    r"(?P<exp>[A-Za-z0-9\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+)"
)


def _wrap_bare_exponents_plain(text: str) -> str:
    def repl(found: re.Match[str]) -> str:
        base, exp = found.group("base"), found.group("exp")
        if len(exp) == 1:
            return f"${base}^{exp}$"
        return f"${base}^{{{exp}}}$"

    return BARE_HEADING_EXPONENT.sub(repl, text)


def wrap_bare_exponents(text: str) -> str:
    """Turn x^k-style tokens in prose into $...$ segments."""
    pieces: list[str] = []
    last = 0
    for found in MATH_SEGMENT.finditer(text):
        pieces.append(_wrap_bare_exponents_plain(text[last : found.start()]))
        pieces.append(found.group(0))
        last = found.end()
    pieces.append(_wrap_bare_exponents_plain(text[last:]))
    return "".join(pieces)


def _uppercase_latin(text: str) -> str:
    """Capitals for a heading, leaving π and other non-Latin letters as they are.

    str.upper() maps π to Π, which is a different symbol, not a capital pi the
    student should read in a section title.
    """
    return re.sub(r"[a-z]", lambda found: found.group(0).upper(), text)


def _uppercase_prose_outside_math(text: str) -> str:
    pieces: list[str] = []
    last = 0
    for found in MATH_SEGMENT.finditer(text):
        pieces.append(_uppercase_latin(text[last : found.start()]))
        pieces.append(found.group(0))
        last = found.end()
    pieces.append(_uppercase_latin(text[last:]))
    return "".join(pieces)


def _heading_math_latex(segment: str) -> str:
    body = one_line(math_body(segment))
    body = join_degree_text(tally_maths(parallel_slashes(body)))
    return f"${math_commas(body)}$"


def heading_tex(text: str, profile: Profile, *, uppercase: bool = False) -> str:
    """A module title for the TOC or a section head, with bare exponents typeset."""
    text = written_for(text, profile)
    text = wrap_bare_exponents(text)
    if uppercase:
        text = _uppercase_prose_outside_math(text)
    pieces: list[str] = []
    last = 0
    for found in MATH_SEGMENT.finditer(text):
        chunk = text[last : found.start()]
        if chunk:
            pieces.append(maybe_stix_digits(escape_plain(chunk), profile))
        pieces.append(_heading_math_latex(found.group(0)))
        last = found.end()
    chunk = text[last:]
    if chunk:
        pieces.append(maybe_stix_digits(escape_plain(chunk), profile))
    return "".join(pieces)


# The fields of an exercise that hold what the reader reads, and so the ones a table
# can stand in. A figure's description is not among them: it is the key a drawing is
# filed under, and rewriting it would lose the drawing.
READ_FIELDS = ("stem", "text", "options")


def arabic_section_number(number: str | int) -> str:
    """A section's number as a book in Arabic-Indic digits reads it: from the right.

    5.3 is read chapter first there as anywhere, and the reading runs right to left,
    so the 5 stands at the right of the point and the 3 at its left. The bidirectional
    algorithm sets digits with a point between them as one number, left to right,
    whatever way the paragraph runs, so the parts are written in the other order and
    the algorithm turns them round on the page. An Arabic book in Western digits does
    not come here: its 5.3 stands as the English book's does.
    """
    parts = str(number).split(".")
    return ".".join(reversed(parts)).translate(ARABIC_DIGITS)


def distribute_marked_group_figures(group: dict) -> None:
    """Move uniquely referenced shared figures onto the member that places them.

    Legacy/generated sets can store several figures on the group while members
    say ``[figure 1]``, ``[figure 2]`` and so on. Marker indices are local to the
    field that prints them, so leaving that shape untouched prints the marker
    literally. A shared figure referenced once by one member is that member's
    figure: move it there and renumber the member's local markers from 1.
    """
    shared = list(group.get("figures") or [])
    if not shared:
        return
    exercises = list(group.get("exercises") or [])
    stem_claims = marked_indices(str(group.get("stem") or ""))
    references: dict[int, int] = {}
    for exercise in exercises:
        if exercise.get("figures"):
            continue
        for index in marked_indices(str(exercise.get("text") or "")):
            references[index] = references.get(index, 0) + 1

    moved: set[int] = set()
    for exercise in exercises:
        if exercise.get("figures"):
            continue
        text = str(exercise.get("text") or "")
        wanted = [
            index
            for index in sorted(marked_indices(text))
            if 1 <= index <= len(shared)
            and index not in stem_claims
            and references.get(index) == 1
        ]
        if not wanted:
            continue
        local = {old: new for new, old in enumerate(wanted, start=1)}
        exercise["text"] = FIGURE_TOKEN.sub(
            lambda found: (
                f"[figure {local[figure_number(found.group(1))]}]"
                if figure_number(found.group(1)) in local
                else found.group(0)
            ),
            text,
        )
        exercise["figures"] = [shared[index - 1] for index in wanted]
        moved.update(wanted)

    if moved:
        group["figures"] = [
            figure
            for index, figure in enumerate(shared, start=1)
            if index not in moved
        ]


def distribute_marked_group_poems(group: dict) -> None:
    """Move uniquely referenced shared poems onto the member that places them."""
    shared = list(group.get("poems") or [])
    if not shared:
        return
    exercises = list(group.get("exercises") or [])
    stem_claims = marked_poem_indices(str(group.get("stem") or ""))
    references: dict[int, int] = {}
    for exercise in exercises:
        if exercise.get("poems"):
            continue
        for index in marked_poem_indices(str(exercise.get("text") or "")):
            references[index] = references.get(index, 0) + 1

    moved: set[int] = set()
    for exercise in exercises:
        if exercise.get("poems"):
            continue
        text = str(exercise.get("text") or "")
        wanted = [
            index
            for index in sorted(marked_poem_indices(text))
            if 1 <= index <= len(shared)
            and index not in stem_claims
            and references.get(index) == 1
        ]
        if not wanted:
            continue
        local = {old: new for new, old in enumerate(wanted, start=1)}
        exercise["text"] = POEM_TOKEN.sub(
            lambda found: (
                f"[poem {local[poem_number(found.group(1))]}]"
                if poem_number(found.group(1)) in local
                else found.group(0)
            ),
            text,
        )
        exercise["poems"] = [shared[index - 1] for index in wanted]
        moved.update(wanted)

    if moved:
        group["poems"] = [
            poem
            for index, poem in enumerate(shared, start=1)
            if index not in moved
        ]


def for_direction(document: dict, profile: Profile) -> dict:
    """The document as a book that runs this way has it.

    The same for an English book. For an Arabic book with Latin maths, its tables are
    mirrored and its prose given the hyphen the face has; for one in Arabic notation,
    letters become CLS macros and tables are left alone, because a table is maths and
    the maths is laid out from the right already. Mashriq books also rewrite digits
    and marks as Arabic-Indic; Maghreb books keep 0-9.

    A copy, because the exercises are the writing stage's file and this is the
    paper's reading of it. Applied once, on the way in, since a table turned round
    twice is a table the right way round again.
    """
    copied = json.loads(json.dumps(document))
    for group in section_groups(copied):
        distribute_marked_group_figures(group)
        distribute_marked_group_poems(group)
    if not profile.rtl:
        return copied
    language_book = is_arabic_subject(copied)

    def read(text: str) -> str:
        if profile.notation == "arabic":
            return arabic_notation(
                directional_tables(text, profile),
                language_book=language_book,
                convert_digits=profile.indic_digits,
            )
        text = directional_tables(text, profile)
        if (
            profile.language_code,
            profile.numerals,
            profile.notation,
        ) == ("ar", "western", "latin"):
            text = rtl_latin_layout(text)
        if language_book:
            text = unwrap_arabic_math_field(text, language_book=True)
        return prose_hyphens(text)

    def verse(text: str) -> str:
        return arabic_prose(text, convert_digits=profile.indic_digits)

    def rewrite_poems(row: dict) -> None:
        for poem in row.get("poems") or []:
            if not isinstance(poem, dict):
                continue
            for line in poem.get("rows") or []:
                line["shatrs"] = [verse(part) for part in line.get("shatrs") or []]

    # The section's own number and title are the paper's lines rather than the
    # writing's, and go the way a heading goes; the number keeps its point.
    copied["title"] = written_for(copied["title"], profile)
    if profile.indic_digits:
        copied["number"] = arabic_section_number(copied["number"])
    for group in section_groups(copied):
        rewrite_poems(group)
        if group.get("stem"):
            group["stem"] = read(group["stem"])
        for exercise in group["exercises"]:
            rewrite_poems(exercise)
            for name in READ_FIELDS:
                value = exercise.get(name)
                if isinstance(value, str):
                    exercise[name] = read(value)
                elif isinstance(value, list):
                    exercise[name] = [read(item) if isinstance(item, str) else item for item in value]
    return copied


def math_comments(segment: str) -> str:
    """Escape a bare % inside maths, which TeX reads as a comment.

    The model writes a unit as it would say it, "(in $%$)", and that percent sign
    comments out the rest of the line, closing delimiter and all.
    """
    return re.sub(r"(?<!\\)%", r"\\%", segment)


def html_underlines(text: str) -> str:
    """Turn a prose ``<u>…</u>`` into the underline the rest of the book writes.

    Only the spans between maths are rewritten, so a tag inside ``$...$`` is
    left alone rather than nesting dollars. Inner text that already holds a
    dollar, a brace, or a backslash is left as HTML rather than broken maths.
    """

    def one(found: re.Match[str]) -> str:
        inner = found.group(1)
        if any(char in inner for char in "${}\\"):
            return found.group(0)
        return f"$\\underline{{\\text{{{inner}}}}}$"

    pieces = []
    cursor = 0
    for match in MATH_SEGMENT.finditer(text):
        pieces.append(HTML_UNDERLINE.sub(one, text[cursor : match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(HTML_UNDERLINE.sub(one, text[cursor:]))
    return "".join(pieces)


def wrap_bare_underlines(text: str) -> str:
    """Wrap ``\\underline{...}`` that sits outside ``$...$`` in maths delimiters.

    Arabic grammar items underline a word in the sentence; the model often writes
    the command without dollars even though the paper sets it as
    ``$\\underline{\\text{...}}$``.
    """

    def wrap_segment(segment: str) -> str:
        out: list[str] = []
        index = 0
        while index < len(segment):
            found = BARE_UNDERLINE_OPEN.search(segment, index)
            if not found:
                out.append(segment[index:])
                break
            out.append(segment[index : found.start()])
            cursor = found.end()
            depth = 1
            while cursor < len(segment) and depth:
                char = segment[cursor]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                cursor += 1
            command = segment[found.start() : cursor]
            out.append(f"${command}$")
            index = cursor
        return "".join(out)

    pieces: list[str] = []
    cursor = 0
    for match in MATH_SEGMENT.finditer(text):
        pieces.append(wrap_segment(text[cursor : match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(wrap_segment(text[cursor:]))
    return "".join(pieces)


SHORT_DIV_ARRAY = re.compile(
    r"\\begin\{array\}\{r\}(.*?)\\end\{array\}",
    re.DOTALL,
)
SHORT_DIV_HOUSE = re.compile(
    r"^(?P<div>.*?)\\overline\{\s*\)\s*(?P<num>.*)\}\s*$",
    re.DOTALL,
)
SHORT_DIV_CARRY = re.compile(r"\^\s*\{\(\s*(.*?)\s*\)\}")
SHORT_DIV_SQUARE = re.compile(r"\\(?:square|Box)\b")
SHORT_DIV_DIGIT = re.compile(r"[0-9٠-٩]")
_SHORT_DIV_LETTER_MACRO = "|".join(
    sorted({name for name, _code in arabic_math.letter_macros()} | {"arrad"})
)
SHORT_DIV_LETTER = re.compile(
    rf"(?:\\(?:{_SHORT_DIV_LETTER_MACRO})\b\s*"
    rf"|[{re.escape(''.join(arabic_math.LETTER_TO_NAME))}])"
)


def _preserve_short_division(text: str) -> bool:
    """True when the field is a short-division house or already ``\\divform``."""
    if r"\divform" in text:
        return True
    return any(
        r"\overline{" in found.group(1) for found in SHORT_DIV_ARRAY.finditer(text)
    )


def short_division_cells(row: str, decimal: str = ".") -> list[str]:
    """Digit, decimal, blank, and carry cells of one short-division row."""
    cells: list[str] = []
    index = 0
    length = len(row)
    while index < length:
        if row[index].isspace():
            index += 1
            continue
        if row[index] in ".,٫،":
            cells.append(decimal if decimal.startswith("\\") else f"${decimal}$")
            index += 1
            continue
        carry = SHORT_DIV_CARRY.match(row, index)
        if carry:
            mark = _short_div_carry(carry.group(1))
            index = carry.end()
            while index < length and row[index].isspace():
                index += 1
            token, index = _short_div_token(row, index)
            if token is None:
                token = r"\phantom{0}"
            cells.append(f"\\cir{{{mark}}}{{${token}$}}")
            continue
        token, index = _short_div_token(row, index)
        if token is None:
            index += 1
            continue
        cells.append(f"${token}$")
    return cells


def _short_div_carry(inner: str) -> str:
    """A blank carry is an empty circle, not a circled \\blank rule."""
    inner = inner.strip()
    if not inner or SHORT_DIV_SQUARE.fullmatch(inner) or BLANK_TOKEN.fullmatch(inner):
        return ""
    return inner


def _short_div_token(row: str, index: int) -> tuple[str | None, int]:
    found = SHORT_DIV_SQUARE.match(row, index) or BLANK_TOKEN.match(row, index)
    if found:
        return r"\square", found.end()
    found = SHORT_DIV_DIGIT.match(row, index)
    if found:
        return found.group(0), found.end()
    found = SHORT_DIV_LETTER.match(row, index)
    if found:
        return found.group(0).rstrip(), found.end()
    return None, index


def rewrite_short_division(text: str) -> str:
    """A two-row `{r}` array with ``\\overline{ )`` as ``\\divform``."""

    def one(found: re.Match[str]) -> str:
        inner = found.group(1)
        if r"\overline{" not in inner:
            return found.group(0)
        rows = re.split(r"\s*\\\\\s*", inner.strip(), maxsplit=1)
        if len(rows) != 2:
            return found.group(0)
        house = SHORT_DIV_HOUSE.match(rows[1].strip())
        if house is None:
            return found.group(0)
        divisor = BLANK_TOKEN.sub(lambda _: r"\square", house.group("div").strip())
        divisor = SHORT_DIV_SQUARE.sub(lambda _: r"\square", divisor)
        decimal = r"\divdec" if re.search("[٠-٩]", inner) else "."
        quotient = short_division_cells(rows[0], decimal)
        dividend = short_division_cells(house.group("num"), decimal)
        if not divisor or not quotient or not dividend:
            return found.group(0)
        if not divisor.startswith("$"):
            divisor = f"${divisor}$"
        return rf"\divform{{{divisor}}}{{{'/'.join(quotient)}}}{{{'/'.join(dividend)}}}"

    return SHORT_DIV_ARRAY.sub(one, text)


def _house_has_gap(inner: str) -> bool:
    """Whether a short-division house leaves a digit, divisor or carry blank."""
    without = SHORT_DIV_CARRY.sub("", inner)
    if BLANK_TOKEN.search(without) or SHORT_DIV_SQUARE.search(without):
        return True
    return any(
        not mark.strip()
        or BLANK_TOKEN.fullmatch(mark.strip())
        or SHORT_DIV_SQUARE.fullmatch(mark.strip())
        for mark in (m.group(1) for m in SHORT_DIV_CARRY.finditer(inner))
    )


def short_div_house_mark(text: str) -> str | None:
    """The mark a house's missing spot prints as: a box, or an empty circle.

    None when the field has no house, or the house has no missing spot.
    """
    for found in SHORT_DIV_ARRAY.finditer(text):
        inner = found.group(1)
        if r"\overline{" not in inner or not _house_has_gap(inner):
            continue
        without = SHORT_DIV_CARRY.sub("", inner)
        if BLANK_TOKEN.search(without) or SHORT_DIV_SQUARE.search(without):
            return r"$\square$"
        return r"$\cirmark{}$"
    return None


def house_wording_square_problems(text: str, where: str) -> list[str]:
    """``\\square`` in the wording must point at a missing spot in a house."""
    wording = SHORT_DIV_ARRAY.sub(
        lambda found: "" if r"\overline{" in found.group(0) else found.group(0),
        text,
    )
    if not SHORT_DIV_SQUARE.search(wording):
        return []
    houses = [
        found.group(1)
        for found in SHORT_DIV_ARRAY.finditer(text)
        if r"\overline{" in found.group(1)
    ]
    if not houses:
        return [
            f"{where} writes \\square in the wording but has no short-division "
            "house to point at"
        ]
    if not any(_house_has_gap(inner) for inner in houses):
        return [
            f"{where} writes \\square in the wording but the short-division "
            "house has no missing digit or carry"
        ]
    return []


_TEXT_MATH_MACROS = frozenset({"text", "mathrm", "operatorname", "textrm", "textbf", "textit"})
_SKIP_BRACE_MACROS = frozenset({"begin", "end", "label", "ref", "eqref", "cite", "pageref"})
_BOX_MACROS = frozenset({"hbox", "vbox", "vtop", "mbox"})
_PICTURE_ENVS = frozenset({"tikzpicture", "pgfpicture"})
# $...$, $$...$$, \[...\], and \(...\). The first alternative wins, so a \[
# inside $ stays inside that segment. \[ and \( themselves are delimiters.
_PAPER_MATH = re.compile(
    r"\$\$.+?\$\$|\$.+?\$|\\\[.+?\\\]|\\\(.+?\\\)",
    re.DOTALL,
)
_SIZING_BASES = frozenset({"big", "Big", "bigg", "Bigg"})
_TIKZ_COMMANDS = frozenset({
    "draw", "fill", "filldraw", "path", "node", "coordinate", "clip",
    "shade", "pattern", "plot", "graph", "matrix", "useasboundingbox", "pic",
})
_CLOSE_KIND = {")": "(", "]": "[", "\\}": "\\{"}


def _control_word(body: str, index: int) -> tuple[int, str]:
    """Past a backslash at ``index``. The second value is the command name, ``\\\\``, or ``symbol``."""
    if index + 1 >= len(body):
        return index + 1, "symbol"
    char = body[index + 1]
    if char == "\\":
        return index + 2, "\\\\"
    if not ("A" <= char <= "Z" or "a" <= char <= "z"):
        return index + 2, "symbol"
    end = index + 2
    while end < len(body) and ("A" <= body[end] <= "Z" or "a" <= body[end] <= "z"):
        end += 1
    return end, body[index + 1 : end]


def _after_box_prefix(body: str, end: int) -> int:
    """Past optional ``dir TRT`` / ``to 10pt`` / ``spread`` after ``\\hbox``."""
    index = end
    while index < len(body) and body[index] in " \t":
        index += 1
    rest = body[index:]
    if rest.startswith("dir"):
        index += 3
        while index < len(body) and body[index] in " \t":
            index += 1
        while index < len(body) and (
            "A" <= body[index] <= "Z" or "a" <= body[index] <= "z"
        ):
            index += 1
        while index < len(body) and body[index] in " \t":
            index += 1
        return index
    if rest.startswith("to") or rest.startswith("spread"):
        brace = body.find("{", index)
        return brace if brace >= 0 else index
    return index


def _brace_group_end(body: str, open_at: int) -> int | None:
    if open_at >= len(body) or body[open_at] != "{":
        return None
    depth = 0
    index = open_at
    while index < len(body):
        if body[index] == "\\" and index + 1 < len(body):
            index += 2
            continue
        if body[index] == "{":
            depth += 1
        elif body[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _is_sizing_command(name: str) -> bool:
    """\\left, \\bigl, \\Biggr, \\middle and the other delimiter-sizing commands."""
    if name in {"left", "right", "middle"}:
        return True
    base = name[:-1] if len(name) > 1 and name[-1] in "lrm" else name
    return base in _SIZING_BASES


def _picture_end(body: str, index: int) -> int | None:
    """Index just after ``\\end{tikzpicture}`` when ``index`` is its ``\\begin``."""
    end, name = _control_word(body, index)
    if name != "begin" or end >= len(body) or body[end] != "{":
        return None
    close = _brace_group_end(body, end)
    if close is None:
        return None
    env = body[end + 1 : close - 1].strip()
    if env not in _PICTURE_ENVS:
        return None
    needle = "\\end{" + env + "}"
    found = body.find(needle, close)
    if found < 0:
        return len(body)
    return found + len(needle)


def _sized_delimiter_end(body: str, after_command: int) -> int | None:
    """Index just after the delimiter a sizing command already applies to."""
    index = after_command
    while index < len(body) and body[index] == " ":
        index += 1
    if index >= len(body):
        return None
    if body[index] in "()[]":
        return index + 1
    if body.startswith("\\{", index) or body.startswith("\\}", index):
        return index + 2
    if body[index] in ".|/":
        return index + 1
    if body[index] == "\\" and index + 1 < len(body):
        end, _name = _control_word(body, index)
        return end
    return None


def _optional_square_end(body: str, index: int) -> int | None:
    """Index just after an optional ``[...]`` argument, if one starts here."""
    while index < len(body) and body[index] == " ":
        index += 1
    if index >= len(body) or body[index] != "[":
        return None
    depth = 0
    braces = 0
    while index < len(body):
        if body[index] == "\\" and index + 1 < len(body):
            index += 2
            continue
        char = body[index]
        if char == "{":
            braces += 1
        elif char == "}":
            braces = max(0, braces - 1)
        elif braces == 0 and char == "[":
            depth += 1
        elif braces == 0 and char == "]":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _tikz_statement_end(body: str, index: int) -> int | None:
    """Index just after the ``;`` that ends a TikZ path, if this statement has one."""
    braces = 0
    while index < len(body):
        if body[index] == "\\" and index + 1 < len(body):
            index += 2
            continue
        char = body[index]
        if char == "{":
            braces += 1
        elif char == "}":
            braces = max(0, braces - 1)
        elif char == ";" and braces == 0:
            return index + 1
        index += 1
    return None


def _one_comma_interval(body: str, start: int, end: int) -> bool:
    """True when ``body[start:end]`` is two endpoints and one comma.

    ``\\allowbreak`` may sit beside the comma. A second comma, an alignment
    tab, or another square bracket means this is not one interval.
    """
    commas = 0
    left = False
    right = False
    braces = 0
    index = start
    while index < end:
        if body[index] == "\\" and index + 1 < len(body):
            stop, name = _control_word(body, index)
            if name == "\\\\" and braces == 0:
                return False
            if name != "allowbreak":
                if commas:
                    right = True
                else:
                    left = True
            index = stop
            continue
        char = body[index]
        if char == "{":
            braces += 1
        elif char == "}":
            if braces == 0:
                return False
            braces -= 1
        elif char == "&" and braces == 0:
            return False
        elif char in "[]" and braces == 0:
            return False
        elif char in (",", ARABIC_COMMA) and braces == 0:
            commas += 1
            if commas > 1:
                return False
        elif not char.isspace():
            if commas:
                right = True
            else:
                left = True
        index += 1
    return commas == 1 and left and right and braces == 0


def _reversed_intervals(
    body: str,
    squares: list[tuple[int, str, int]],
    used: set[int],
) -> list[tuple[int, str, int, str]]:
    """Open and half-open intervals whose brackets face outward.

    ``]a، b[``, ``[a، b[``, and ``]a، b]``. Each entry is the left index, the
    left replacement, the right index, and the right replacement.
    """
    by_depth: dict[int, list[tuple[int, str]]] = {}
    for index, char, depth in squares:
        if index not in used:
            by_depth.setdefault(depth, []).append((index, char))
    found: list[tuple[int, str, int, str]] = []
    shapes = {("]", "["), ("[", "["), ("]", "]")}
    for items in by_depth.values():
        cursor = 0
        while cursor < len(items) - 1:
            left_at, left = items[cursor]
            right_at, right = items[cursor + 1]
            if (left, right) in shapes and _one_comma_interval(body, left_at + 1, right_at):
                found.append((left_at, r"\left" + left, right_at, r"\right" + right))
                cursor += 2
                continue
            cursor += 1
    return found


def _merge_bracket_findings(body: str, spans: list[tuple[int, int]]) -> list[str]:
    """One snippet per mixed or unmatched bracket, joining ``]a[`` into a single interval."""
    ordered = sorted(set(spans))
    snippets: list[str] = []
    index = 0
    while index < len(ordered):
        start, stop = ordered[index]
        token = body[start:stop]
        if (
            index + 1 < len(ordered)
            and token in {")", "]", "\\}"}
            and stop - start == len(token)
        ):
            nxt, nxt_stop = ordered[index + 1]
            between = body[stop:nxt]
            opener = body[nxt:nxt_stop]
            if (
                opener in {"(", "[", "\\{"}
                and not any(mark in between for mark in ("(", ")", "[", "]", "\\{", "\\}", "&", "\\\\"))
            ):
                snippets.append(body[start:nxt_stop])
                index += 2
                continue
        snippets.append(body[start:stop])
        index += 1
    return snippets


def autosize_brackets(body: str, found: list[str] | None = None) -> str:
    """Size matched brackets in one maths body: ``( )``, ``[ ]``, and ``\\{ \\}``.

    Already-sized delimiters, optional arguments, brackets inside ``\\text`` /
    ``\\mathrm`` / ``\\operatorname``, TikZ code, ``\\begin`` arguments, text
    boxes (``\\hbox`` / ``\\mbox`` / ``\\vbox``), and a
    pair split by ``&`` or ``\\\\`` are left as written. A reversed interval
    with one comma, ``]a، b[``, ``[a، b[``, or ``]a، b]``, is sized too
    (``\\allowbreak`` may follow the comma). Anything else with no closer of
    its own type, such as ``(a, b]``, is left and, when ``found`` is given,
    appended there. Plain grouping braces and the delimiters ``\\( \\)`` and
    ``\\[ \\]`` are not brackets. Running this twice changes nothing the
    second time.
    """
    pairs: list[tuple[int, int, str]] = []
    spans: list[tuple[int, int]] = []
    stack: list[tuple[str, int, int]] = []
    squares: list[tuple[int, str, int]] = []
    # Closers left behind by a pair that & or \\ split. They stay literal and
    # are not mixed intervals.
    split_closers: dict[int, int] = {}
    braces = 0
    text_boxes: set[int] = set()
    inner_math = False
    index = 0

    def in_math() -> bool:
        return not text_boxes or inner_math

    def abandon(depth: int) -> None:
        count = 0
        while stack and stack[-1][2] == depth:
            stack.pop()
            count += 1
        if count:
            split_closers[depth] = split_closers.get(depth, 0) + count

    def close_delimiter(kind: str, at: int, length: int) -> None:
        if kind == "]":
            squares.append((at, "]", braces))
        opener = _CLOSE_KIND[kind]
        if stack and stack[-1][0] == opener and stack[-1][2] == braces:
            _open_kind, open_at, _depth = stack.pop()
            pairs.append((open_at, at, opener))
            return
        if stack and stack[-1][2] == braces:
            _open_kind, open_at, _depth = stack.pop()
            spans.append((open_at, at + length))
            return
        if split_closers.get(braces, 0) > 0:
            split_closers[braces] -= 1
            return
        spans.append((at, at + length))

    while index < len(body):
        char = body[index]
        if char == "\\":
            picture = _picture_end(body, index)
            if picture is not None:
                index = picture
                continue
            end, name = _control_word(body, index)
            if name == "\\\\":
                if braces == 0:
                    abandon(0)
                optional = _optional_square_end(body, end)
                index = optional if optional is not None else end
                continue
            if name in _TEXT_MATH_MACROS and end < len(body) and body[end] == "{":
                close = _brace_group_end(body, end)
                index = close if close is not None else len(body)
                continue
            if name in _BOX_MACROS:
                index = _after_box_prefix(body, end)
                if index < len(body) and body[index] == "{":
                    braces += 1
                    text_boxes.add(braces)
                    index += 1
                    continue
                continue
            if name in _SKIP_BRACE_MACROS and end < len(body) and body[end] == "{":
                close = _brace_group_end(body, end)
                index = close if close is not None else len(body)
                if name == "begin":
                    optional = _optional_square_end(body, index)
                    if optional is not None:
                        index = optional
                continue
            if _is_sizing_command(name):
                skipped = _sized_delimiter_end(body, end)
                index = skipped if skipped is not None else end
                continue
            if name in _TIKZ_COMMANDS:
                statement = _tikz_statement_end(body, end)
                if statement is not None:
                    index = statement
                    continue
            if body.startswith("\\{", index):
                if in_math():
                    stack.append(("\\{", index, braces))
                index += 2
                continue
            if body.startswith("\\}", index):
                if in_math():
                    close_delimiter("\\}", index, 2)
                index += 2
                continue
            optional = _optional_square_end(body, end)
            if optional is not None:
                index = optional
                continue
            index = end
            continue
        if char == "{":
            braces += 1
            index += 1
            continue
        if char == "}":
            while stack and stack[-1][2] == braces:
                _kind, at, _depth = stack.pop()
                length = 2 if _kind == "\\{" else 1
                spans.append((at, at + length))
            if braces in text_boxes:
                text_boxes.discard(braces)
                if not text_boxes:
                    inner_math = False
            braces = max(0, braces - 1)
            index += 1
            continue
        if char == "$" and text_boxes:
            inner_math = not inner_math
            index += 1
            continue
        if char == "&" and braces == 0:
            abandon(0)
            index += 1
            continue
        if char in "([":
            if not in_math():
                index += 1
                continue
            if char == "[":
                squares.append((index, "[", braces))
            stack.append((char, index, braces))
            index += 1
            continue
        if char in ")]":
            if not in_math():
                index += 1
                continue
            close_delimiter(char, index, 1)
            index += 1
            continue
        index += 1

    used = {at for open_at, close_at, _kind in pairs for at in (open_at, close_at)}
    intervals = _reversed_intervals(body, squares, used)
    interval_at = {at for left_at, _left, right_at, _right in intervals for at in (left_at, right_at)}
    spans = [(start, stop) for start, stop in spans if start not in interval_at]
    for kind, at, _depth in stack:
        if at in interval_at:
            continue
        length = 2 if kind == "\\{" else 1
        spans.append((at, at + length))
    if found is not None:
        for snippet in _merge_bracket_findings(body, spans):
            if snippet and snippet not in found:
                found.append(snippet)
    if not pairs and not intervals:
        return body
    replacement: dict[int, tuple[int, str]] = {}
    for open_at, close_at, kind in pairs:
        if kind == "(":
            replacement[open_at] = (1, r"\left(")
            replacement[close_at] = (1, r"\right)")
        elif kind == "[":
            replacement[open_at] = (1, r"\left[")
            replacement[close_at] = (1, r"\right]")
        else:
            replacement[open_at] = (2, r"\left\{")
            replacement[close_at] = (2, r"\right\}")
    for left_at, left_tex, right_at, right_tex in intervals:
        replacement[left_at] = (1, left_tex)
        replacement[right_at] = (1, right_tex)
    pieces: list[str] = []
    cursor = 0
    while cursor < len(body):
        if cursor in replacement:
            length, text = replacement[cursor]
            pieces.append(text)
            cursor += length
        else:
            pieces.append(body[cursor])
            cursor += 1
    return "".join(pieces)


def autosize_round_parens(body: str) -> str:
    """The round-bracket part of :func:`autosize_brackets`. Kept for callers."""
    return autosize_brackets(body)



def to_latex(text: str, *, inline: bool = False, guillemets: bool | None = None) -> str:
    """Convert model output to LaTeX, leaving its own maths untouched.

    The model writes maths as $...$ and $$...$$, so only the prose between those
    segments may be escaped. An inline field is kept to a single paragraph, since
    it is rendered inside a command argument that a \\par would break. A command
    written with two backslashes is brought back to one before any of that, since
    the second backslash is a line break and a line break inside the maths stops
    the paper.
    """
    text = single_commands(text)
    text, _ = balance_math(text)
    text = html_underlines(text)
    # Quotes are paired on the whole field so a closer after a maths island is
    # not read as a new opener.
    text = normalize_quotes(text, guillemets=guillemets)
    split_lines = one_line if inline else as_paragraphs
    # \\blank is the answer line. \\square in the wording points at the house
    # and takes the same mark the grid uses (box or empty circle).
    house_mark = short_div_house_mark(text)
    box_latin_lists = has_arabic_letter(text)

    def prose(chunk: str) -> str:
        """Escape prose, holding back the blank the model was asked to write.

        The blanks come marked, so nothing here has to guess which dots stood for
        one. \\blank is lifted out before escaping, which would otherwise print it
        as its own name, and put back as the command it is.
        """
        def one(part: str) -> str:
            escaped = escape_prose(
                line_escapes(part), guillemets=guillemets, quotes=False
            )
            if _LATIN_ARABIC_SNIPPETS.get():
                escaped = wrap_arabic_prose_runs(escaped)
            if box_latin_lists:
                escaped = _ltr_latin_letter_lists(escaped)
            return split_lines(escaped)

        return maybe_stix_digits(
            "\\blank{}".join(one(part) for part in BLANK_TOKEN.split(chunk))
        )

    pieces = []
    cursor = 0
    after_display = False

    def take_prose(chunk: str) -> str:
        nonlocal after_display
        if after_display:
            chunk = DISPLAY_TAIL_PUNCT.sub("", chunk)
            after_display = False
        return prose(chunk)

    for match in _PAPER_MATH.finditer(text):
        pieces.append(take_prose(text[cursor : match.start()]))
        segment = math_comments(math_blanks(match.group(0)))
        if house_mark and r"\overline{" not in segment:
            mark = house_mark.strip("$")

            def wording_carry(found: re.Match[str]) -> str:
                inner = found.group(1).strip()
                if (
                    not inner
                    or SHORT_DIV_SQUARE.fullmatch(inner)
                    or BLANK_TOKEN.fullmatch(inner)
                ):
                    return mark
                return found.group(0)

            # $^{(\square)}$ is house-carry syntax; wording prints the mark
            # without the ^{()} wrapper, so it is ○ not (○).
            segment = SHORT_DIV_CARRY.sub(wording_carry, segment)
            if mark == r"\cirmark{}":
                segment = SHORT_DIV_SQUARE.sub(lambda _: mark, segment)
        # A blank line inside maths is an error, and the model's delimiters do
        # sometimes span lines, so maths is always flattened onto one line.
        display = segment.startswith("$$")
        bracketed = segment.startswith(r"\[")
        delimited = segment.startswith(r"\(")
        opened = 2 if display or bracketed or delimited else 1
        body = one_line(segment[opened:-opened])
        body = body.replace(r"\(", "").replace(r"\)", "")
        body = text_math_dollars(body)
        body = normalize_quotes_in_text_macros(body, guillemets=guillemets)
        body = math_stray_ampersands(body)
        body = join_degree_text(tally_maths(parallel_slashes(body)))
        body = rewrite_short_division(body)
        body = wrap_latin_diacritic_words(body)
        body = tight_thousands(body)
        body = autosize_brackets(body)
        body = rewrite_egypt_unit_hats(body)
        unit = unwrap_arabic_unit(body)
        if unit is not None:
            number, words = unit
            pieces.append(
                f"${math_commas(number)}$ {prose(math_delimiters_as_text(words))}"
            )
            cursor = match.end()
            continue
        table = bool(TABLE_SEGMENT.search(body))
        wrapped = False
        if table:
            body = unframed(body)
            body, wrapped = wrap_text_table_columns(body)
        # An inline field is set inside a command argument, where a display is not
        # allowed to go, so there a table is measured as the long line it would be.
        # That is the answer wanted of it: a member of a set carrying a table needs
        # the whole width, and reporting the table's own width is what says so.
        if wrapped:
            pieces.append(f"\\par\\noindent{body}\\par")
            after_display = True
        elif not inline and (display or bracketed or table):
            fitted = f"\\tablefit{{{body}}}" if table else body
            pieces.append(f"\\[{fitted}\\]")
            after_display = True
        elif bracketed:
            pieces.append(f"\\[{math_commas(body)}\\]")
        elif delimited:
            pieces.append(f"\\({math_commas(body)}\\)")
        else:
            pieces.append(f"${math_commas(body)}$")
        cursor = match.end()
    pieces.append(take_prose(text[cursor:]))
    result = "".join(pieces)

    # Last resort: never emit a document that leaves maths open, even if that
    # means printing the field's LaTeX literally.
    if len(re.findall(r"(?<!\\)\$", result)) % 2:
        result = prose(text)
    return result.strip()


def standard_lines(tikz: str) -> str:
    """Bend every line width a drawing names to one of the two the paper keeps.

    The named weights are bent in the preamble, where a style can be redefined, but a
    width written out cannot be reached that way, and sixty figures wrote one out. The
    width is not removed, which would leave an empty key in the options, but rewritten:
    what a figure asked to be a hairline stays the lighter of the two, since it is a
    grid line or a line of construction, and everything else becomes the weight the
    book draws with. A width in millimetres or centimetres is measured in points first,
    so that `line width=0.3mm` is read as the 0.85pt it is.
    """

    def bend(found: re.Match[str]) -> str:
        # Lowered before it is looked up, because the pattern matches a unit in any
        # case and the table is keyed in one: `line width=0.3MM` read as 0.3pt would
        # be bent the wrong way, a hairline where the figure asked for the full weight.
        size = float(found["size"]) * POINTS_PER.get((found["unit"] or "pt").lower(), 1.0)
        return f"line width={FIGURE_LIGHT if size < HAIRLINE else FIGURE_RULE}"

    tikz = LINE_WIDTH.sub(bend, tikz)
    tikz = canonical_tikz_arrows(tikz)
    return tikz_range_label_hyphens(tikz)


_NAMED_ARROW_TIP = r"(?:Stealth|Latex|latex|stealth|To|to)"
_REWRITE_ARROW_TIP = r"(?:Stealth|Latex|latex|stealth)"
_ARROW_TIP_OPTS = r"(?:\[[^\]]*\])?"


def canonical_tikz_arrows(tikz: str) -> str:
    """One arrow head for decorations, path ends, and ``>=`` tip keys: ``To``."""
    tikz = re.sub(r"\\arrow\s*\{[^{}]*\}", r"\\arrow{To}", tikz)
    tikz = re.sub(
        rf"\{{\s*{_NAMED_ARROW_TIP}{_ARROW_TIP_OPTS}\s*\}}",
        "{To}",
        tikz,
        flags=re.IGNORECASE,
    )
    tikz = re.sub(
        rf">=\s*{_NAMED_ARROW_TIP}{_ARROW_TIP_OPTS}",
        ">={To}",
        tikz,
        flags=re.IGNORECASE,
    )
    # Unbraced path tips: ``-Stealth``, ``Stealth-``, ``Latex-Stealth``.
    tikz = re.sub(
        rf"(?<=-)\s*{_REWRITE_ARROW_TIP}{_ARROW_TIP_OPTS}\b",
        "{To}",
        tikz,
        flags=re.IGNORECASE,
    )
    tikz = re.sub(
        rf"\b{_REWRITE_ARROW_TIP}{_ARROW_TIP_OPTS}(?=-)",
        "{To}",
        tikz,
        flags=re.IGNORECASE,
    )
    return tikz


# Histogram and axis bucket labels like ``{0-5}`` are set in the Arabic text face
# inside TikZ nodes. ASCII hyphen-minus has no glyph there (same as prose HYPHEN).
TIKZ_RANGE_LABEL = re.compile(
    r"\{([0-9٠-٩]+(?:-[0-9٠-٩]+)+|[0-9٠-٩]+-)\}"
)


def tikz_range_label_hyphens(tikz: str) -> str:
    """Turn ASCII hyphens in numeric range node labels into U+2010."""

    def fix(found: re.Match[str]) -> str:
        return "{" + found.group(1).replace("-", HYPHEN) + "}"

    return TIKZ_RANGE_LABEL.sub(fix, tikz)


# How much of a two-across choice is kept for the letter and the words beside the
# drawing — "(أ) الشكل " — so the drawing is capped to what is left of the pair
# cell. The cap is \\linewidth of that cell (a minipage of half the list),
# evaluated when the choice is set, not the page's \\gridpair.
OPTION_FIGURE_LABEL = "5em"


def figure_to_latex(
    description: str,
    drawings: Drawings,
    *,
    alone: bool = True,
    air: bool = True,
    option: bool = False,
) -> str:
    """Set a figure: the drawing where one has been made, the description where none has.

    A drawing is held inside the width it is given, so a picture that came out
    wider is shrunk rather than run into the margin. A description with no
    drawing is still set, in italics, because a figure the paper cannot show is
    better named than dropped.

    A figure standing on a line of its own is centred there, given air above and
    below, and asks not to be parted from the wording above it. One standing
    beside its wording is none of the three: it is placed in a column that is
    already centring it, the wording beside it holds the white it needs, and the
    column cannot be broken away from what it stands beside.

    A figure standing in a choice is a fourth case. It shares the line with the
    option's own words, so it is centred on that line rather than sitting on its
    baseline, and it is capped to the pair cell less the letter and those words.
    A tie holds the words to the drawing so TeX does not break the line between
    them. The cap is the cell's own \\linewidth, which \\mcqpair sets to half the
    list.

    `air` is what the figure with no wording at all turns off, because there is
    nothing there for the white to part it from and it would only stand the
    drawing away from its own number.
    """
    drawing = standard_lines((drawings.get(description) or {}).get("tikz", "").strip())
    if not drawing:
        return f"\\emph{{[figure: {to_latex(description, inline=True)}]}}"
    if option:
        return (
            "~\\adjustbox{valign=c, max width="
            f"\\dimexpr\\linewidth-{OPTION_FIGURE_LABEL}\\relax"
            "}{%\n"
            f"{drawing}%\n"
            "}"
        )
    held = f"\\adjustbox{{max width=\\linewidth}}{{%\n{drawing}%\n}}"
    if not alone:
        return held
    room = "" if air else "[0pt]"
    return f"\\widefigure{room}{{%\n{held}%\n}}"


def figure_height(row: dict, measure: float = TEXT_WIDTH) -> float:
    """How tall a drawing will stand in a column `measure` wide, in points.

    A picture wider than its column is shrunk to fit by adjustbox, which takes
    its height down in the same proportion, so the height on the page is not the
    height the figure was compiled at. A drawing that was never measured returns
    zero, which asks for no room at all rather than guessing wrongly.
    """
    height = float(row.get("height") or 0)
    width = float(row.get("width") or 0)
    if height <= 0:
        return 0.0
    return height * min(1.0, measure / width) if width > 0 else height


def reserved(body: str, room: float) -> str:
    """Ask for `room` on the page before setting the block, or don't ask at all.

    Three lines are added for the wording that comes with whatever needed the
    room, and the request is capped well under the height of the page, so a tall
    figure moves down rather than forcing a blank page.

    \\reserve asks through \\Needspace, not \\needspace: the lower-case one asks by
    making a page break cheap and leaving the rest to TeX's own arithmetic, which on
    a paper like this reads as a break wanted rather than a break offered. It sent
    every figure to the top of a fresh page and left a third of the one before it
    empty. The capital one measures what is left against what is asked for and
    breaks only when the room is genuinely not there. Either way the request stands
    down on a paper whose pages were planned, which is what \\reserve is for.
    """
    if room <= 0:
        return body
    return f"\\reserve{{{min(room + 36, 0.7 * TEXT_HEIGHT):.0f}pt}}%\n{body}"


def asked_figures(row: dict) -> list[str]:
    """The figures a group or an exercise asks for, as the descriptions they are.

    A written section may hold its figures in either of two shapes: a section written
    before the type of a drawing was asked for holds a bare description, and one
    written since holds the type alongside it. Everything from
    here on names a drawing by its description — that is what the library is keyed by
    and what a set is grouped by — so this is where the two shapes become one, and the
    only place that needs to know there were ever two.
    """
    return [
        figure["description"] if isinstance(figure, dict) else figure
        for figure in row.get("figures") or []
    ]


def option_claimed(exercise: dict) -> set[int]:
    """The 1-based figure indices the choices of `exercise` name with `[figure N]`."""
    claimed: set[int] = set()
    for option in exercise.get("options") or []:
        claimed |= marked_indices(option)
    return claimed


def held_together(
    body: str,
    figures: list[str],
    drawings: Drawings,
    claimed: set[int] | None = None,
) -> str:
    """Ask for enough room to set a block and its pictures on the same page.

    The room is asked for before the wording, because that is the only point at
    which TeX can still move the whole exercise down: reserving at the picture
    itself would leave the wording behind on the page above.

    `claimed` are indices a choice already owns. Those drawings are set in the
    pair cell, two-by-two, so the room they ask for is two rows of that column,
    not four copies of the full measure.
    """
    owned = claimed or set()
    column = grid_column(2) - NUMBER_ROOM
    stem = 0.0
    option_heights: list[float] = []
    for index, description in enumerate(figures, start=1):
        row = drawings.get(description) or {}
        if index in owned:
            option_heights.append(figure_height(row, column))
        else:
            stem += figure_height(row)
    rows = (len(option_heights) + 1) // 2
    option = rows * max(option_heights) if option_heights else 0.0
    return reserved(body, stem + option)


def stands_beside(figures: list[str], drawings: Drawings, measure: float) -> bool:
    """Whether the one figure here fits the column it would stand in beside wording.

    `measure` is the line the wording is set on, which is what TeX calls \\linewidth
    at that point, and the column the figure gets follows from it exactly as
    \\besidefigure works it out: a whole grid column where the line is long enough to
    leave the wording a fair share, and half of what there is where it is not. Only a
    figure that fits at, or near, the size it was drawn may stand there; the
    alternative is a drawing shrunk until its labels cannot be read. Two figures do
    not go beside wording at all, since the second would have to stand under the first
    in a column narrower than either was drawn for.
    """
    if len(figures) != 1:
        return False
    width = float((drawings.get(figures[0]) or {}).get("width") or 0)
    column = grid_column(2)
    if measure - GUTTER - column < 0.5 * column:
        column = (measure - GUTTER) / 2
    return width > 0 and column / width >= SET_FIGURE_SHRINK


def marked_indices(text: str) -> set[int]:
    """The 1-based figure indices a field names with `[figure N]`."""
    return {figure_number(found.group(1)) for found in FIGURE_TOKEN.finditer(text)}


def has_marker(text: str, index: int) -> bool:
    """Whether `text` still carries the token for figure `index`."""
    return index in marked_indices(text)


def replace_marker(text: str, index: int, replacement: str) -> str:
    """`text` with figure `index`'s token swapped for `replacement`.

    Punctuation immediately after the marker is dropped: the figure is a
    paragraph of its own, and a comma there would print as the next line's first
    character.
    """
    return FIGURE_THEN_PUNCT.sub(
        lambda found: replacement if figure_number(found.group(1)) == index else found.group(0),
        text,
    )


def without_marker(body: str, index: int) -> str:
    """`body` with figure `index`'s marker taken out and the gap it leaves closed.

    A marker alone on its line was a paragraph break in the writing's own layout,
    so it leaves one behind; one inside a sentence leaves a single space, since the
    words either side of it were meant to run on. Either way the wording is what it
    would have been had the figure never been placed there, which is what the
    figure standing beside it needs.
    """
    arabic = str(index).translate(ARABIC_DIGITS)
    alone = re.compile(
        rf"\n[ \t]*\[figure (?:{index}|{re.escape(arabic)})\][ \t]*\n"
    )
    body = alone.sub("\n\n", body)
    return FIGURE_MARKER.sub(
        lambda found: " " if figure_number(found.group(1)) == index else found.group(0),
        body,
    ).strip()


def peel_fitted_tables(wording: str) -> tuple[str, str]:
    """Cut \\tablefit displays off the stem so they can use the full measure.

    A table inside \\besidefigure is scaled to the wording minipage, half the
    line, so the type shrinks with the array. The stem can still sit beside the
    figure; the table goes under both.
    """
    start = wording.find(r"\[\tablefit{")
    if start < 0:
        return wording, ""
    return wording[:start].rstrip(), wording[start:]


def body_to_latex(
    text: str,
    figures: list[str],
    drawings: Drawings,
    measure: float = TEXT_WIDTH,
    lead: str = "",
    claimed: set[int] | None = None,
    *,
    alone: bool = True,
    poems: list | None = None,
) -> str:
    """Render one text field with its figures and verse blocks.

    A `lead` is set at the head of the wording, and is how a set's range comes to
    stand in front of its stem. It is passed in rather than put there afterwards
    because where the wording goes decides where the range goes: with a figure
    beside it, the range belongs inside the column the wording is in, and a range
    set before that column would be a line of its own with two columns under it.

    A figure the model placed with a `[figure N]` marker is substituted at that
    spot; the markers survive escaping because they hold no LaTeX specials. Any
    figure left unmarked follows the text as its own paragraph, so a description
    is never silently dropped — and where there is no text at all, because the
    figure under a stem is the whole exercise, it follows nothing and is set
    without the air a figure is otherwise given: the air is there to part a
    drawing from wording, and there is no wording here to part it from.

    A `[poem N]` marker is the same for a reprinted verse block: kashida and
    arabverse are built here, and an unmarked poem trails the wording.

    `claimed` are indices a choice already owns. Those drawings are not trailed
    here: they belong in the option that named them, not under the stem.

    `alone` is whether a figure may take a line of its own (\\widefigure). A member
    of a set is already in a cell, so it is false there: wrapping a number line in
    \\widefigure is what sent a two-column set of them across the measure.

    The exception is the small figure with wording to keep it company, which goes
    beside that wording instead of beneath it. The width matters to that choice
    and to nothing else here, which is why it is only ever passed in: what fits
    beside an exercise set across the page does not fit beside one set in a
    column with three others.

    That exception outranks the marker. Whether a figure fits beside its wording is
    a question about the measure, which a marker written before the drawing was made
    cannot answer; a marked figure narrow enough for the column beside it was being
    set centred across the page instead, splitting its own question in two and
    leaving the column beside it empty. So the fit is tested first, and the marker
    for the figure that passes is dropped rather than honoured.

    A table in that wording is cut out of the column and set under both, because
    \\tablefit scales to \\linewidth and the column is half the line.
    """
    owned = claimed or set()
    verse = poems or []
    body = to_latex(text)
    if body and 1 not in owned and not verse and stands_beside(figures, drawings, measure):
        wording = without_marker(body, 1)
        stem, table = peel_fitted_tables(wording)
        if stem:
            beside = figure_to_latex(figures[0], drawings, alone=False)
            laid = f"\\besidefigure{{{lead}{stem}}}{{{beside}}}"
            return f"{laid}\n{table}" if table else laid

    trailing = []
    for index, description in enumerate(figures, start=1):
        if has_marker(body, index):
            body = replace_marker(
                body, index, figure_to_latex(description, drawings, alone=alone)
            )
        elif index not in owned:
            trailing.append(description)

    trailing_poems = []
    for index, poem in enumerate(verse, start=1):
        if index in marked_poem_indices(body):
            body = replace_poem_marker(body, index, format_arabverse(poem))
        else:
            trailing_poems.append(poem)

    body = f"{lead}{body}".rstrip()
    blocks = [body] if body else []
    for description in trailing:
        rendered = figure_to_latex(
            description, drawings, alone=alone, air=bool(body)
        )
        blocks.append(f"  \\par {rendered}" if blocks else rendered)
    for poem in trailing_poems:
        rendered = format_arabverse(poem)
        blocks.append(f"  \\par {rendered}" if blocks else rendered)
    assembled = "\n".join(blocks).strip()
    if body or not assembled:
        return assembled
    # With no wording ahead of it, the figure is what the exercise's number has to
    # align with, and a drawing is no use as a baseline: see \topaligned. The box is
    # what gives the number a top edge to sit against, since \topaligned on its own
    # only works inside one.
    return f"\\parbox[t]{{\\linewidth}}{{\\topaligned\n{assembled}}}"


def options_to_latex(
    options: list[str], figures: list[str], drawings: Drawings
) -> str:
    """The choices of a multiple-choice exercise, for TeX to label and lay out.

    Four is the count the writing stage enforces and the count the layout is built
    for. Any other number is still set, one choice to a line, because a paper that
    prints an odd exercise plainly is better than one that fails to build.

    A `[figure N]` in a choice is that choice's drawing, set in the option rather
    than under the stem. The drawing is not a display: it has to live inside \\mcq's
    argument. Four such choices are handed to \\mcqfig, which lays them two-by-two
    without measuring the unscaled tikzpicture.
    """
    rendered = []
    figured = False
    for option in options:
        line = to_latex(option, inline=True)
        for index, description in enumerate(figures, start=1):
            if has_marker(line, index):
                figured = True
                figure = figure_to_latex(description, drawings, option=True)
                line = FIGURE_MARKER.sub(
                    lambda found, put=figure, want=index: (
                        put if figure_number(found.group(1)) == want else found.group(0)
                    ),
                    line,
                )
        rendered.append(line)
    if len(rendered) == MCQ_OPTIONS:
        macro = "\\mcqfig" if figured else "\\mcq"
        return macro + "".join(f"{{{option}}}" for option in rendered)
    return "\n\n".join(
        f"\\choice{{{index}}}{{{option}}}" for index, option in enumerate(rendered, start=1)
    )


def exercise_body(
    exercise: dict,
    figures: list[str],
    drawings: Drawings,
    measure: float = TEXT_WIDTH,
    *,
    alone: bool = True,
) -> str:
    """One exercise: its wording, the figures it needs, and its choices if any."""
    options = exercise.get("options") or []
    claimed = option_claimed(exercise)
    body = body_to_latex(
        exercise["text"],
        figures,
        drawings,
        measure,
        claimed=claimed,
        alone=alone,
        poems=asked_poems(exercise),
    )
    if not options:
        return body
    # The gap goes in here rather than inside \mcq, so that the choices stand the
    # same distance below the wording however they end up laid out.
    return (
        f"{body}\n\n\\vspace{{{OPTIONS_GAP}}}\\nopagebreak\n"
        f"{options_to_latex(options, figures, drawings)}"
    )


def label_number(number: int) -> str:
    """One exercise's number as the paper labels it, for TeX to put the dot on.

    Left to TeX because which side of the digits the dot goes depends on which way
    the page runs, and that is the preamble's to know: see direction_preamble().
    """
    return f"\\labelnumber{{{number}}}"


def set_range(first: int, last: int) -> str:
    """The range of numbers a set covers, to stand in front of its stem."""
    return f"\\exerciselabel{{\\labelrange{{{first}}}{{{last}}}}} "


def set_cell(exercise: dict, number: int, drawings: Drawings, column: float) -> str:
    """One member of a set: its number, its wording to measure, and how it is set.

    How it is set is two renderings, not one, because the column a member lands in
    is what TeX is being asked to choose and only TeX can choose it. The first is
    the member in a column `column` wide, shared with the members beside it. The
    second is the member with a whole line to itself, and it is handed over because
    a figure that has to stand under its wording in half a line stands beside it in
    a whole one — the difference between a member four inches tall and one two
    inches tall, and the reason a set of figures ran to a page and a half.

    Where the two come out the same the second is dropped: repeating a member that
    does not depend on the width would only make the source harder to read, and no
    member without a figure depends on it.

    The copy that is measured has every `[figure N]` taken out first. That copy is
    measured as letters, and a marker left in it is both the wrong width and a
    figure the audit reads as never placed. The printed copies are the other two,
    and they carry the drawing.
    """
    figures = asked_figures(exercise)
    wording = exercise["text"]
    for index in marked_indices(wording):
        wording = without_marker(wording, index)
    shared = exercise_body(exercise, figures, drawings, column, alone=False)
    whole = exercise_body(exercise, figures, drawings, NUMBERED_WIDTH, alone=False)
    return (
        f"\\setitem{{{label_number(number)}}}{{{to_latex(wording, inline=True)}}}"
        f"{{{shared}}}{{{'' if whole == shared else whole}}}{{{tex_id(exercise.get('id'))}}}"
    )


def shares_a_line(exercise: dict, drawings: Drawings, column: float) -> bool:
    """Whether this member of a set could stand beside another in a `column`.

    Ruled out is what needs the whole width whatever its wording: choices to lay
    out beneath it, wording the writing broke into parts, and a displayed
    equation, which is a line by definition. How wide the wording itself turns
    out to be is TeX's to measure, not this module's to guess.

    A figure is allowed in a column it fits at, or nearly at, the size it was
    drawn. Shrinking one takes its labels down with it, and a figure whose labels
    cannot be read is not worth the room it saves; a figure that was never drawn
    is a description set as a line of prose, and is no shorter than one. A member
    that is only a drawing is held to the same test: one picture drawn to the
    wide measure sends the set one member to a line, where that picture is not
    scaled back into the cell.
    """
    if exercise.get("options"):
        return False
    text = exercise["text"].strip()
    if "\n" in text or "$$" in text or exercise.get("poems") or marked_poem_indices(text):
        return False
    figures = asked_figures(exercise)
    widths = [
        float((drawings.get(description) or {}).get("width") or 0)
        for description in figures
    ]
    return all(width > 0 and column / width >= SET_FIGURE_SHRINK for width in widths)


def figures_room(exercise: dict, drawings: Drawings, column: float) -> float:
    """How much of a `column` wide cell this member's drawings fill, in points."""
    return sum(
        figure_height(drawings.get(description) or {}, column)
        for description in asked_figures(exercise)
    )


def paired_by_height(
    exercises: list[dict], drawings: Drawings, column: float, span: int
) -> list[dict]:
    """The members of a set reordered so that the drawings in a row are of a height.

    A row is as tall as the tallest thing in it, so a row that sets the tallest
    drawing of a set beside the shortest pays for the tall one twice: once for
    itself and again as white beside its neighbour. Sorting the members by the
    height of their drawings and taking them a row at a time leaves the sum of the
    rows as small as any ordering can, which is the whole of the saving — nothing
    is shrunk, and nothing is sent to another page to get it.

    The rows then go down the page in the order their first member was written in,
    and each row's members in the order they were written, so the writing's own
    sequence is disturbed no further than the pairing needs. The numbers do not
    travel with the members: a set is numbered down the page as it is printed, so
    whichever drawing comes first takes the set's first number.

    A set whose members carry no drawings comes back exactly as it went in, since
    the only heights this knows about are the drawings' and a set without them has
    nothing here to sort by.
    """
    if span < 2 or len(exercises) <= span:
        return exercises
    room = [figures_room(exercise, drawings, column) for exercise in exercises]
    by_height = sorted(range(len(exercises)), key=lambda member: (room[member], member))
    rows = [sorted(by_height[at : at + span]) for at in range(0, len(by_height), span)]
    rows.sort(key=lambda row: row[0])
    return [exercises[member] for row in rows for member in row]


def tex_id(raw: object) -> str:
    """An exercise or group id safe to write through TeX and to key a hover box by."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "", str(raw or "").strip())


def qidbox_tex(qid: str, body: str) -> str:
    """Wrap a block so groups.py can mark it, or leave it if there is no id."""
    if not qid or not (body or "").strip():
        return body
    return f"\\qidbox{{{qid}}}{{{body}}}"


def qidline_tex(qid: str, body: str) -> str:
    """Wrap one paragraph, with the end mark under the line's depth.

    A list goes through qidbox_tex instead. \\qidline leaves vertical mode before
    the start mark, which ahead of a list would open a blank line.
    """
    if not qid or not (body or "").strip():
        return body
    return f"\\qidline{{{qid}}}{{{body}}}"


def indented_block(body: str, label: str, qid: str = "") -> str:
    """One exercise, its number in the margin and everything else in a column.

    Set as a one-item list rather than with \\hangindent, for two reasons. An
    exercise is often several paragraphs — every option of a multiple-choice
    question is one — and a hanging indent would only hold the first of them. And
    a list narrows the text width itself, so a figure asked to fit the line fits
    the column the exercise is set in instead of running into the margin.

    The label is set flush right in a box as wide as the longest number, so the
    dots line up down the page and each dot stands one gap from its own wording,
    as a textbook's do. All of the list's own spacing is taken out, since the gap
    between one exercise and the next is set by whatever stacks them.

    `qid` wraps the list in a zero-size mark so groups.py can find the exercise.
    """
    listed = (
        "\\begin{list}{}{%\n"
        "\\setlength{\\labelwidth}{\\exercisenumber}"
        "\\setlength{\\labelsep}{\\labelgap}%\n"
        "\\setlength{\\leftmargin}{\\numberroom}%\n"
        "\\setlength{\\itemindent}{0pt}\\setlength{\\listparindent}{0pt}%\n"
        "\\setlength{\\topsep}{0pt}\\setlength{\\partopsep}{0pt}%\n"
        "\\setlength{\\itemsep}{0pt}\\setlength{\\parsep}{0pt}%\n"
        "\\def\\makelabel#1{\\hfill#1}}%\n"
        f"\\item[\\exerciselabel{{{label}}}] {body}\n"
        "\\end{list}"
    )
    return qidbox_tex(qid, listed)


def stack(blocks: list[str], gap: float) -> str:
    """Set blocks as consecutive paragraphs, holding them apart by `gap` points."""
    return f"\n\n\\vspace{{{gap:.0f}pt}}\n\n".join(blocks)


def group_pieces(
    group: dict, first: int, drawings: Drawings, apart: bool = False
) -> list[str]:
    """One group as the pieces it can be set in, stem first.

    A group of one is a single piece, since there is nothing in it to divide. A set
    is its stem and then either its grid, which is one piece because the grid is
    what decides its own rows, or its members one to a piece, which is the form a
    set takes when its wording is too long for two of them to share a line.

    `apart` asks for that second form whatever the wording measures, and there is
    one reason to ask: a set no page will hold whole. Set one member to a line it
    can be laid down page by page, which is the only way to print a set of four
    figures that stands taller than the paper.
    """
    exercises = group["exercises"]
    group_figures = asked_figures(group)
    # A stem is set across the page; everything numbered is set in the column the
    # numbers leave, which is that much narrower.
    indented = NUMBERED_WIDTH

    if len(exercises) == 1:
        # With no stem there is nothing for the group's figures to hang off, so they
        # trail the exercise instead of being printed ahead of its wording.
        only = exercises[0]
        figures = (asked_figures(only)) + group_figures
        claimed = option_claimed(only)
        if not (group.get("stem") or "").strip():
            return [held_together(
                indented_block(
                    exercise_body(only, figures, drawings, indented),
                    label_number(first),
                    tex_id(only.get("id")),
                ),
                figures,
                drawings,
                claimed,
            )]
        # A group of one has no set for its stem to head, so the stem and the
        # exercise are one exercise and are set as one.
        own = asked_figures(only)
        return [held_together(
            indented_block(
                f"{body_to_latex(group['stem'], group_figures, drawings, indented, poems=asked_poems(group))}\n\n"
                f"{exercise_body(only, own, drawings, indented)}",
                label_number(first),
                tex_id(only.get("id")),
            ),
            figures,
            drawings,
            claimed,
        )]

    last = first + len(exercises) - 1
    stem = qidline_tex(
        tex_id(group.get("id")),
        body_to_latex(
            group.get("stem") or "",
            group_figures,
            drawings,
            lead=set_range(first, last),
            poems=asked_poems(group),
        ),
    )
    # A set of figures is held to two across however short its wording is, since a
    # four-column is narrower than most figures are drawn.
    figured = any(exercise.get("figures") for exercise in exercises)
    span = SET_FIGURE_SPAN if figured else SET_SPAN
    column = grid_column(span) - NUMBER_ROOM
    if not apart and all(shares_a_line(exercise, drawings, column) for exercise in exercises):
        # Handed over whole, so that the widest member decides the layout for all of
        # them: a set laid out member by member would put four on one line and two
        # on the next as the wording happened to run.
        cells = "%\n".join(
            set_cell(exercise, number, drawings, column)
            for number, exercise in enumerate(
                paired_by_height(exercises, drawings, column, span), start=first
            )
        )
        # The stem asks for room for the tallest of the figures that follow it — one
        # row's worth — so that it is not left at the foot of a page with its own set
        # overleaf. Asking for every row would ask for more than a page.
        room = sum(
            figure_height(drawings.get(description) or {}) for description in group_figures
        ) + max(
            (
                figure_height(drawings.get(description) or {}, column)
                for exercise in exercises
                for description in asked_figures(exercise)
            ),
            default=0.0,
        )
        return [reserved(stem, room), f"\\setgrid{{{span}}}{{%\n{cells}}}"]

    return [
        held_together(stem, group_figures, drawings),
        *(
            held_together(
                indented_block(
                    exercise_body(
                        exercise, asked_figures(exercise), drawings, indented
                    ),
                    label_number(number),
                    tex_id(exercise.get("id")),
                ),
                asked_figures(exercise),
                drawings,
                option_claimed(exercise),
            )
            for number, exercise in enumerate(exercises, start=first)
        ),
    ]


def group_to_latex(group: dict, first: int, drawings: Drawings) -> str:
    """Render one group whole, numbering its exercises from `first`.

    A set of exercises is numbered with the range it spans, as in 17-21., and its
    exercises carry their own numbers below it. A group of one with no stem is a
    standalone exercise and is set as one.
    """
    return stack(group_pieces(group, first, drawings), SET_GAP)


def section_groups(document: dict) -> list[dict]:
    """The groups of the section, which is the only part of a document typeset."""
    return document["exercises"]["groups"]


def figures_in(document: dict) -> list[str]:
    """Every figure description the section prints, in the order it prints them.

    Kept here because this is the module that decides what reaches the page: a
    figure this does not return is a figure the paper never asks for, and drawing
    it would be work spent on nothing.
    """
    found: list[str] = []
    for group in section_groups(document):
        found += asked_figures(group)
        for exercise in group["exercises"]:
            found += asked_figures(exercise)
    return found


def count_repairs(document: dict) -> int:
    """How many fields needed a missing maths delimiter added.

    The answers are left out of the count because they are left off the paper: a
    broken delimiter in an answer is for whatever typesets the answers to report.
    """
    fields = []
    for group in section_groups(document):
        fields.append(group.get("stem") or "")
        fields += asked_figures(group)
        for exercise in group["exercises"]:
            fields.append(exercise["text"])
            fields += asked_figures(exercise)
    return sum(balance_math(field)[1] for field in fields)


def gap_above(kind: str, previous: str) -> float:
    """How far a block of `kind` stands off the one of `previous` above it.

    Space is all the paper has to show what belongs with what, so a set is opened
    and closed by a gap wider than the one between single exercises: its own
    exercises are held tight under its stem, and the wider gap on either side is
    what marks where that stem starts applying and where it stops.

    An objective's heading takes the widest gap above it and none below, since the
    heading carries its own. Whatever it introduces, set or single exercise, opens
    directly under it: a gap there would leave the heading floating between the
    two objectives rather than belonging to the one it names. Under the section
    title it takes the narrow gap instead, being stacked under the title rather
    than divided from the objective before it.

    Nothing stands off the top of a page, which `previous` empty asks for: glue at
    the top of a page is glue TeX throws away, and a gap the arithmetic counted and
    the page did not is a page planned wrong.
    """
    if not previous or previous == "objective":
        return 0.0
    if kind == "objective":
        return TITLE_GAP if previous == "section" else OBJECTIVE_GAP
    if kind == "set" or previous == "set":
        return SET_MARGIN
    return EXERCISE_GAP


def academic_year(stored: str) -> str:
    """An academic year as the foot of a page gives it, from the way a book stores it.

    A book keeps "25-26", and a reader wants both centuries spelled out. Anything not
    of that shape is passed through untouched: a book that stores its year some other
    way is better printed as it stored it than guessed at.
    """
    halves = stored.split("-")
    if len(halves) == 2 and all(half.isdigit() and len(half) == 2 for half in halves):
        return "-".join(f"20{half}" for half in halves)
    return stored


def foot_fixed(document: dict) -> str:
    """The part of the footer that does not change from page to page.

    Everything but the numbers, which are the book's and are not known while a section
    is being set. Whatever a book has not recorded is left out rather than printed
    empty, so a book with no term to its name does not carry a stray separator.
    """
    grade, term = document.get("grade", ""), document.get("term", "")
    parts = [
        PUBLISHER,
        document.get("series", SERIES),
        document.get("subject", ""),
        document.get("country", ""),
        academic_year(document.get("academic_year", "")),
        f"G{grade}" if grade else "",
        f"T{term}" if term else "",
    ]
    return f" {FOOT_SEPARATOR} ".join(escape_plain(part) for part in parts if part)


def foot_tex(document: dict, first: int = 1, total: int = 1, profile: Profile = ENGLISH) -> str:
    """What a paper says at the top of its document about the foot of its pages.

    The two numbers are written as a counter and a macro so that the stamping pass can
    rewrite them without the paper being planned again: they are the only thing about a
    paper that depends on the rest of the book.
    """
    fixed = maybe_stix_digits(written_for(foot_fixed(document), profile), profile)
    return (
        f"\\renewcommand{{\\footfixed}}{{{fixed}}}\n"
        f"\\setcounter{{page}}{{{first}}}\n"
        f"\\renewcommand{{\\bookpages}}{{{total}}}\n"
    )


def spaced(blocks: list[tuple[str, str]]) -> str:
    """Stack a section's blocks, spacing each of them off the one before."""
    pieces: list[str] = []
    previous = ""
    for body, kind in blocks:
        if gap := gap_above(kind, previous):
            pieces.append(f"\\vspace{{{gap:.0f}pt}}")
        pieces.append(body)
        previous = kind
    return "\n\n".join(pieces)


# unicode-math's stretchy over-arrows are combining accents. Under \mathdir TRT the
# head of \overrightarrow sits in the middle of an Arabic name, and the shaft does
# not grow with the letters. After \begin{document} — once unicode-math has defined
# the commands — an Arabic-notation paper replaces them with a TikZ shaft the width
# of the letters. Rays are a full barb (east to west); a line keeps both heads; a
# vector is a harpoon, never a ray. JSON still writes \overrightarrow for a ray
# and \vec for a vector; only the typesetting changes.
#
# \to / \rightarrow is the same story for a limit. TRT lays س \to جـ out as
# جـ, arrow, س; NagwaArabicMath rtlm does not mirror that arrow, so it still
# points at س. JSON keeps \to. The paper prints \leftarrow so the head sits
# on جـ. TikZ drawing arrows ([-{Stealth}]) are not these commands.
#
# Each shaft is its own picture. Inside a figure node that picture would inherit
# the drawing's scale and unit vectors, so the mark slides off the letter. Interrupt
# the outer picture, then wrap the inner one in an hbox: a node on a path still
# leaks the shaft beside the letter unless the inner picture is one TeX box.
# Stem and figure then use the same ruler.
ARABIC_OVER_ARROWS = r"""\makeatletter
\newcommand{\NagwaOverLeft}[1]{\mathpalette\NagwaOverLeft@{#1}}
\def\NagwaOverLeft@#1#2{%
  \hbox{%
    \pgfinterruptpicture
    \tikz[baseline=(n.base), inner sep=0pt, outer sep=0pt]{%
      \node[inner sep=0.1em] (n) {$\m@th#1#2$};
      \draw[line width=0.6pt, line cap=round,
            -{Straight Barb[length=2pt, width=2pt]}]
        ([yshift=0.1em]n.north east)
        -- ([yshift=0.1em]n.north west);
    }%
    \endpgfinterruptpicture
  }%
}
\newcommand{\NagwaOverLeftRight}[1]{\mathpalette\NagwaOverLeftRight@{#1}}
\def\NagwaOverLeftRight@#1#2{%
  \hbox{%
    \pgfinterruptpicture
    \tikz[baseline=(n.base), inner sep=0pt, outer sep=0pt]{%
      \node[inner sep=0.1em] (n) {$\m@th#1#2$};
      \draw[line width=0.6pt, line cap=round,
            {Straight Barb[length=2pt, width=2pt]}-{Straight Barb[length=2pt, width=2pt]}]
        ([yshift=0.1em]n.north west)
        -- ([yshift=0.1em]n.north east);
    }%
    \endpgfinterruptpicture
  }%
}
\newcommand{\NagwaOverLeftHarp}[1]{\mathpalette\NagwaOverLeftHarp@{#1}}
\def\NagwaOverLeftHarp@#1#2{%
  \hbox{%
    \pgfinterruptpicture
    \tikz[baseline=(n.base), inner sep=0pt, outer sep=0pt]{%
      \node[inner sep=0.1em] (n) {$\m@th#1#2$};
      \draw[line width=0.6pt, line cap=round,
            -{Straight Barb[right, length=2pt, width=2pt]}]
        ([yshift=0.1em]n.north east)
        -- ([yshift=0.1em]n.north west);
    }%
    \endpgfinterruptpicture
  }%
}
\newcommand{\NagwaOverRightHarp}[1]{\mathpalette\NagwaOverRightHarp@{#1}}
\def\NagwaOverRightHarp@#1#2{%
  \hbox{%
    \pgfinterruptpicture
    \tikz[baseline=(n.base), inner sep=0pt, outer sep=0pt]{%
      \node[inner sep=0.1em] (n) {$\m@th#1#2$};
      \draw[line width=0.6pt, line cap=round,
            -{Straight Barb[left, length=2pt, width=2pt]}]
        ([yshift=0.1em]n.north west)
        -- ([yshift=0.1em]n.north east);
    }%
    \endpgfinterruptpicture
  }%
}
\renewcommand{\overleftarrow}{\NagwaOverLeft}
\let\overrightarrow\overleftarrow
\renewcommand{\overleftrightarrow}{\NagwaOverLeftRight}
\renewcommand{\overleftharpoon}{\NagwaOverLeftHarp}
\renewcommand{\overrightharpoon}{\NagwaOverRightHarp}
\renewcommand{\vec}{\overleftharpoon}
\let\rightarrow\leftarrow
\let\to\leftarrow
\makeatother
"""

# Latin-notation papers keep unicode-math's stretchy rays. \vec is the Egyptian
# vector mark, a harpoon, not unicode-math's short accent. After \begin{document}
# because that is when unicode-math defines \vec.
LATIN_VEC_HARPOON = "\\renewcommand{\\vec}{\\overrightharpoon}\n"


def begin_document(profile: Profile, *, planned: bool = False) -> str:
    """The opening of a paper: \\begin{document}, then the Arabic over-arrows.

    Planned papers set \\plannedtrue here so \\unbroken does not ask for room. The
    over-arrow macros belong after \\begin{document}, because unicode-math defines
    \\overrightarrow and \\vec at that hook and a redefinition in the preamble would lose.
    Latin-notation papers keep unicode-math's rays and print \\vec as a harpoon.
    """
    parts = ["\\begin{document}\n"]
    if profile.notation == "arabic":
        parts.append(ARABIC_OVER_ARROWS)
    else:
        parts.append(LATIN_VEC_HARPOON)
    if planned:
        parts.append("\\plannedtrue\n")
    parts.append("\\raggedright\n")
    return "".join(parts)


def section_tex(
    document: dict,
    drawings: Drawings | None = None,
    labels: dict[str, str] | None = None,
    profile: Profile = ENGLISH,
) -> str:
    """Build a standalone A4 document listing the section's exercises.

    Exercises are numbered straight through the section in the order the groups
    appear, so a number on the paper is the nth exercise the section asks however
    the writing chose to group them. The numbering runs through the headings too:
    a heading divides the paper into parts, it does not start it again.

    `drawings` maps a figure's description to the TikZ that draws it. A section
    can be set before anything has been drawn, and again once it has, which is
    what lets the drawing stage rebuild a paper without the writing stage running
    a second time.

    `labels` maps an objective's id to the short name the paper heads its
    exercises with. An objective with no name there is set without a heading, so
    a paper can be set before anything has been named.

    `profile` is the kind of book the section is from, which decides the face and
    the direction the paper is set in. It is the book's to say, not the section's:
    the caller reads it off the table of contents once and hands it to every paper.
    """
    drawings = notation_drawings(
        drawings or {}, profile, language_book=is_arabic_subject(document)
    )
    labels = labels or {}
    document = for_direction(document, profile)
    numeral = escape_plain(str(document["number"]))
    heading = heading_tex(str(document["title"]), profile, uppercase=True)
    quotes, western, snippets = quoting_for(profile, str(document.get("book_id", "")))

    blocks: list[tuple[str, str]] = []
    number = 1
    headed = ""
    try:
        for group in section_groups(document):
            exercises = group["exercises"]
            objective = group.get("objective") or ""
            if objective != headed and (name := labels.get(objective)):
                name = maybe_stix_digits(
                    escape_prose(written_for(name, profile)), profile
                )
                blocks.append((f"\\objectivehead{{{name}}}", "objective"))
                headed = objective
            blocks.append(
                (group_to_latex(group, number, drawings), "set" if len(exercises) > 1 else "exercise")
            )
            number += len(exercises)
    finally:
        _reset_quoting(quotes, western, snippets)

    return (
        preamble(profile)
        + "\n"
        + begin_document(profile)
        + foot_tex(document, profile=profile)
        + f"\\sectionhead{{{numeral}}}{{{heading}}}\n\n"
        + spaced(blocks)
        + "\n\\end{document}\n"
    )


@dataclass
class Block:
    """One thing the paper sets whole, and what it measured when it was set alone.

    `height` and `depth` are TeX's own two: the distance from the top of the block
    down to its last baseline, and from that baseline to the bottom of it. Both are
    kept, because what a block costs a page depends on the depth of whatever stands
    above it, and a page is filled to the point.

    `parts` says which pieces of its group the block sets, and empty means all of
    them. `pieces` are the blocks the same group can be laid down in when no page
    would hold it whole or a strict bilingual order can use a member boundary to
    avoid stranding most of the preceding page.
    """

    key: str
    kind: str
    objective: str = ""
    name: str = ""
    numeral: str = ""
    group: dict | None = None
    group_key: str = ""
    parts: tuple[int, ...] = ()
    pieces: list[Block] = field(default_factory=list)
    height: float = 0.0
    depth: float = 0.0
    loose: bool = False


@dataclass
class Paper:
    """How a paper came out, for the stage that has to report it.

    `slack` is the room left at the foot of each page in the order the pages run,
    which is the measure of the work: a paper of full pages is a short paper, and
    the last page of a section is the only one entitled to be empty.
    """

    pages: int
    slack: list[float] = field(default_factory=list)
    divided: int = 0
    loose: int = 0
    tries: int = 1
    planned: bool = True
    selection: Selection | None = None


def set_pieces(group: dict, key: str, objective: str) -> list[Block]:
    """A set as the blocks it can be laid down in at member boundaries.

    The stem and the first member are one block, because a stem alone at the foot
    of a page announces an instruction and then withholds it, and the paper reads
    as though a page were missing. Every member after the first is a block of its
    own, free to start a page.
    """
    return [
        Block(
            key=f"{key}p{part}",
            kind="member",
            objective=objective,
            group=group,
            group_key=key,
            parts=(0, 1) if part == 0 else (part + 1,),
        )
        for part in range(len(group["exercises"]))
    ]


def section_blocks(
    document: dict, labels: dict[str, str] | None = None, profile: Profile = ENGLISH
) -> list[Block]:
    """Everything the paper sets, in the order the writing put it.

    The section's own heading first, then, objective by objective, the heading that
    names it where it has a name and the groups that belong to it.

    Every set carries the pieces it could be laid down in as well as itself. Whether
    it needs them is not known until it has been measured, and measuring them at the
    same time costs part of one compile against a second compile of the whole paper.

    `document` has been through for_direction already; the objectives' names have
    not, and are written for the book here.
    """
    labels = labels or {}
    quotes, western, snippets = quoting_for(profile, str(document.get("book_id", "")))
    blocks = [Block(
        key="section",
        kind="section",
        name=heading_tex(str(document["title"]), profile, uppercase=True),
        numeral=escape_plain(str(document["number"])),
    )]
    headed = ""
    try:
        for index, group in enumerate(section_groups(document), start=1):
            objective = group.get("objective") or ""
            if objective != headed and (name := labels.get(objective)):
                blocks.append(Block(
                    key=f"head-{objective}",
                    kind="objective",
                    objective=objective,
                    name=maybe_stix_digits(
                        escape_prose(written_for(name, profile)), profile
                    ),
                ))
                headed = objective
            members = len(group["exercises"])
            key = f"g{index}"
            blocks.append(Block(
                key=key,
                kind="set" if members > 1 else "exercise",
                objective=objective,
                group=group,
                group_key=key,
                pieces=set_pieces(group, key, objective) if members > 1 else [],
            ))
    finally:
        _reset_quoting(quotes, western, snippets)
    return blocks


def every_block(blocks: list[Block]) -> list[Block]:
    """Every block and every piece of one, which is everything to be measured."""
    return [row for block in blocks for row in (block, *block.pieces)]


def numbering(pages: list[list[Block]]) -> dict[str, int]:
    """The number the first exercise of each group takes, in the order they are set.

    The numbers run straight through the paper, so a number is the nth exercise the
    section asks however the pages were planned; the blocks of one set share the
    number its first exercise took, since they are one group however it was divided.
    """
    firsts: dict[str, int] = {}
    number = 1
    for page in pages:
        for block in page:
            if block.group is None or block.group_key in firsts:
                continue
            firsts[block.group_key] = number
            number += len(block.group["exercises"])
    return firsts


def block_tex(block: Block, first: int, drawings: Drawings) -> str:
    """The LaTeX of one block, numbering the group it belongs to from `first`."""
    if block.kind == "section":
        return f"\\sectionhead{{{block.numeral}}}{{{block.name}}}"
    if block.kind == "objective":
        return f"\\objectivehead{{{block.name}}}"
    pieces = group_pieces(block.group or {}, first, drawings, apart=bool(block.parts))
    return stack([pieces[part] for part in block.parts] if block.parts else pieces, SET_GAP)


def block_room(block: Block, above: Block | None) -> float:
    """How much of a page's height `block` takes, set under `above`.

    A block is a box, and the space TeX leaves above a box is the leading less the
    depth of what stands above it and less the box's own height, or the line skip
    where that arithmetic leaves too little. At the top of a page there is none of
    it: the glue there is glue TeX discards, and the box starts at the top of the
    text.
    """
    if above is None:
        return block.height + block.depth
    return (
        gap_above(block.kind, above.kind)
        + max(BODY_LEADING - above.depth - block.height, LINE_OPENING)
        + block.height
        + block.depth
    )


def page_fill(page: list[Block]) -> float:
    """How much of the page's height the blocks on it take."""
    used = 0.0
    above: Block | None = None
    for block in page:
        used += block_room(block, above)
        above = block
    return used


def objective_runs(blocks: list[Block]) -> list[tuple[Block | None, list[Block]]]:
    """The paper as its objectives: each one's heading, where it has one, and its
    questions.

    The writing keeps an objective's groups together, so an objective is a run of
    consecutive blocks and the runs are the order the section itself sets out.
    """
    runs: list[tuple[Block | None, list[Block]]] = []
    for _, rows in groupby(blocks, key=lambda block: block.objective):
        rows = list(rows)
        head = rows[0] if rows[0].kind == "objective" else None
        runs.append((head, [row for row in rows if row.kind != "objective"]))
    return runs


def best_fill(pending: list[Block], room: float, above: Block | None) -> list[Block]:
    """The questions that together fill the room left on a page fullest.

    Returned in the order the writing put them, since which questions share a page
    is the page's business and the order they are read in is not.

    A page is filled by choosing what goes on it together rather than one question
    at a time. Taking the tallest that fits, and then the tallest of what is left,
    spends the tall questions early and strands the page when everything remaining
    is taller than the room: an objective of ten questions between 197 and 401pt
    put two on a page and left 206pt of it standing empty, where a different three
    of them fill it to within 30pt. So every combination is weighed, which is a
    subset sum over the room the page has left, and the fullest is taken. Room left
    at the end of an objective is not waste in the way room left in the middle of
    one is: the next heading and the questions under it carry on down the same page.

    What a block costs the page depends on what stands above it, and what stands
    above it is not known until the choice is made, so the choice is made against a
    stand-in with nothing hanging below it, and in whole points. Both are a little
    pessimistic, which is why the laying that follows does the real arithmetic and
    hands back anything that then does not fit.
    """
    limit = int(room)
    if limit <= 0:
        return []
    stand_in = above or Block(key="", kind="exercise")
    costs = [ceil(block_room(block, stand_in)) for block in pending]
    # How each total the blocks can add up to was arrived at: the block that
    # completed it and the total standing before that block, which is the trail the
    # walk back at the end follows. A total reached once is not reached again, so the
    # first way to any total is the way kept, and the blocks are offered in the
    # writing's order.
    reached: list[tuple[int, int] | None] = [None] * (limit + 1)
    found = [False] * (limit + 1)
    found[0] = True
    for index, cost in enumerate(costs):
        for total in range(limit, cost - 1, -1):
            if not found[total] and found[total - cost]:
                found[total], reached[total] = True, (index, total - cost)
    chosen: list[int] = []
    total = max(amount for amount, reachable in enumerate(found) if reachable)
    while trail := reached[total]:
        index, total = trail
        chosen.append(index)
    return [pending[index] for index in sorted(chosen)]


def tallest_first(pending: list[Block], room: float, above: Block | None) -> list[Block]:
    """The tallest question the room will take, and nothing decided beyond it.

    A tall question is what strands a page, so it is spent while there is still room
    for it and the short ones are kept back to finish a page off. Taken one at a time
    this fills the early pages of an objective well and can strand a later one; it is
    also, question for question, the best way there is of getting an objective onto
    as few pages as it will go, which is why the paper is planned this way as well as
    by filling each page fullest and the better of the two plans is the one kept.
    """
    fitting = [block for block in pending if block_room(block, above) <= room]
    return [max(fitting, key=lambda block: block.height + block.depth)] if fitting else []


def tallest_then_fill(pending: list[Block], room: float, above: Block | None) -> list[Block]:
    """The tallest question the room will take, and then the best fill of what is left.

    Both rules at once, and over the sample book the one that wastes least: the tall
    question is spent while there is room for it, as the rule above spends it, and the
    rest of the page is then filled as full as the questions left will fill it. It has
    the weakness of both as well, which is why it is one of the plans tried and not the
    plan: the tallest question is taken whether or not the page is the place for it.
    """
    picked = tallest_first(pending, room, above)
    if not picked:
        return []
    rest = [block for block in pending if block is not picked[0]]
    return picked + best_fill(rest, room - block_room(picked[0], above), picked[0])


def lay_out(
    blocks: list[Block],
    holdback: float,
    choose: Callable[[list[Block], float, Block | None], list[Block]],
    strict_order: bool = False,
) -> list[list[Block]]:
    """Which questions go on which page, `choose` deciding what shares each one.

    The objectives keep the order the writing gave them, because a heading is a
    division of the section and the divisions are not the page's to rearrange.
    Within an objective the questions are free to move. Where the objective's climb
    from the routine to the demanding is disturbed, it is disturbed within the
    objective and nowhere else.

    Every question, and every set's stem with its first member, stays whole on one
    page. A strict bilingual order may divide a set between later members to use
    space without skipping ahead. A set no page would hold is likewise laid down
    member by member; a single question taller than the paper is set loose for TeX
    to break because there is nothing else left to do with it.
    """
    capacity = TEXT_HEIGHT - holdback
    pages: list[list[Block]] = [[]]
    used = 0.0
    above: Block | None = None
    # The blocks are the caller's and may have been planned once already, with more
    # held back this time, so nothing decided by the last attempt is carried over.
    for block in every_block(blocks):
        block.loose = False

    def lay(block: Block) -> None:
        nonlocal used, above
        used += block_room(block, above)
        pages[-1].append(block)
        above = block

    def turn() -> None:
        nonlocal used, above
        carried: list[Block] = []
        while (
            pages[-1]
            and len(pages[-1]) > 1
            and pages[-1][-1].kind in {"objective", "section"}
        ):
            carried.append(pages[-1].pop())
        carried.reverse()
        pages.append([])
        used, above = 0.0, None
        for block in carried:
            lay(block)

    def fits(block: Block) -> bool:
        return used + block_room(block, above) <= capacity

    def lay_loose(block: Block) -> None:
        """Set a block no page will hold, and let TeX break it where it must."""
        block.loose = True
        lay(block)

    for block in blocks:
        if block.kind == "section":
            lay(block)

    for head, pending in objective_runs([b for b in blocks if b.kind != "section"]):
        if head is not None:
            if not pending:
                lay(head)
                continue
            # A heading may not end a page. Free order reserves the shortest
            # question it introduces; a locked twin reserves the next block that
            # will actually be laid (or that set's first piece).
            if strict_order:
                follower = (
                    pending[0].pieces[0] if pending[0].pieces else pending[0]
                )
                room = block_room(head, above) + block_room(follower, head)
            else:
                room = block_room(head, above) + min(
                    block_room(block, head) for block in pending
                )
            if pages[-1] and used + room > capacity:
                turn()
            lay(head)
        while pending:
            laid = False
            for block in choose(pending, capacity - used, above):
                if not fits(block):
                    break
                pending.remove(block)
                lay(block)
                laid = True
            if laid:
                # What is left is offered the room that is left, which is how a page
                # takes anything the rounding of the choice kept back from it.
                continue
            # A locked bilingual paper cannot skip to a later group, but it can
            # divide the next set at a member boundary. Use that measured prefix
            # when it fits the room already open instead of stranding most of a
            # page, then continue the same set in order over following pages.
            if (
                strict_order
                and pages[-1]
                and pending[0].pieces
                and fits(pending[0].pieces[0])
            ):
                picked = pending.pop(0)
                for piece in picked.pieces:
                    if not fits(piece) and pages[-1]:
                        turn()
                    lay(piece) if fits(piece) else lay_loose(piece)
                continue
            # A locked twin must not skip ahead to a shorter later question.
            if not strict_order:
                if fitting := [block for block in pending if fits(block)]:
                    picked = max(fitting, key=lambda block: block.height + block.depth)
                    pending.remove(picked)
                    lay(picked)
                    continue
            if pages[-1]:
                turn()
            else:
                # A page of its own and it still does not fit, so the shortest
                # question left is taller than the paper itself.
                picked = min(pending, key=lambda block: block.height + block.depth)
                pending.remove(picked)
                if not picked.pieces:
                    lay_loose(picked)
                    continue
                for piece in picked.pieces:
                    if not fits(piece) and pages[-1]:
                        turn()
                    lay(piece) if fits(piece) else lay_loose(piece)
    return _carry_trailing_headings(pages)


def _carry_trailing_headings(pages: list[list[Block]]) -> list[list[Block]]:
    """Move a heading that ended a page onto the next page that has content."""
    index = 0
    while index < len(pages) - 1:
        if pages[index] and pages[index][-1].kind in {"objective", "section"}:
            heading = pages[index].pop()
            pages[index + 1].insert(0, heading)
            if not pages[index]:
                pages.pop(index)
            continue
        index += 1
    return pages


def plan_cost(pages: list[list[Block]], holdback: float) -> tuple[int, float]:
    """What a plan is worth, lower being better: pages first, then how it wastes.

    Pages come first because a page saved is paper saved, and nothing else about a
    plan is worth a page. Between plans of the same length what separates them is
    where the waste falls, and the room left at the foot of each page is squared
    before it is added up, so that a plan leaving 40pt on three pages is preferred to
    one leaving 10pt on two and 200pt on a third. The last page of a section is left
    out of it: a section ends where it ends, and the room after the last question is
    not a page half used.
    """
    room = TEXT_HEIGHT - holdback
    return (
        len(pages),
        sum((room - page_fill(page)) ** 2 for page in pages[:-1]),
    )


# The ways of settling what shares a page. None of the three is the best of them on
# every paper, and which wins is a matter of what questions a section happens to have:
# over the sample book the tallest with the page filled under it takes eight of the
# seventeen papers, the tallest alone six and filling fullest three, and on every one
# of the seventeen the winner is strictly better than the other two rather than tied
# with them. Dropping any of the three would cost some paper of the book.
PAGE_PLANS = (best_fill, tallest_first, tallest_then_fill)


def fit_pages(blocks: list[Block], holdback: float = PAGE_HOLDBACK) -> list[list[Block]]:
    """The best plan for the pages of the paper, of the ways there are of making one.

    Filling every page as full as it will go and spending the tallest question first
    are both good rules and neither is the better rule. Filling each page fullest
    evens the waste out and now and then costs a page, since a page filled to the
    last point can leave what follows it packing badly; spending the tallest first
    keeps an objective on as few pages as it will go and now and then strands one,
    206pt of a page standing empty because everything left to put there was taller
    than that. All of them are cheap next to the compile that follows, so the paper is
    planned every way and the plan that comes out best is the one set.
    """
    plans = [(lay_out(blocks, holdback, choose), choose) for choose in PAGE_PLANS]
    _, choose = min(plans, key=lambda plan: plan_cost(plan[0], holdback))
    # The planning marks the blocks it could not hold whole, and the blocks belong to
    # the caller rather than to a plan, so the winning plan is laid out again to leave
    # those marks agreeing with the pages that are returned.
    return lay_out(blocks, holdback, choose)


# How full pages should be before the fit stops pulling from the +8 pool. Interior
# pages are filled as full as measurements allow; only the last page may stop at
# LAST_PAGE_FILL_TARGET. The writing gives every objective more exercises than the
# paper prints, so which of them are printed can be chosen to land the paper well.
INTERIOR_PAGE_FILL_TARGET = 1.0
LAST_PAGE_FILL_TARGET = 0.6
LAST_PAGE_FILL = LAST_PAGE_FILL_TARGET
PAGE_FILL_TARGET = LAST_PAGE_FILL_TARGET  # older name; last page only

# How many turns of choosing again the fit is given before the paper is set as it
# stands. Each turn replans the pages, which is cheap next to a compile, but the
# choice is greedy and a paper that has not landed in this many is not going to.
FIT_TURNS = 60


@dataclass
class Selection:
    """Which of the exercises written for a section its paper prints, and why.

    `budget` is what the objectives were budgeted between them and `written` what
    they were written to; `printed` is the count the paper has, which is the budget
    unless the last page could not be filled without more. `printed_ids` is the
    linear reading order of those exercises. `left_out` names the exercises not
    printed, group by group, so the choice can be read and, if need be, argued
    with. `interior_fill` is the sparsest interior page; `fill` is the last page.
    """

    budget: int = 0
    written: int = 0
    printed: int = 0
    printed_ids: list[str] = field(default_factory=list)
    left_out: dict[str, list[str]] = field(default_factory=dict)
    interior_fill: float = 1.0
    fill: float = 1.0

    @property
    def added(self) -> int:
        """How many exercises over the budget the fit had to print."""
        return max(self.printed - self.budget, 0)


def group_size(block: Block) -> int:
    return len(block.group["exercises"]) if block.group else 0


def block_cost(block: Block) -> float:
    """What a block is worth to a page, roughly: its box and a line's leading above."""
    return block_room(block, Block(key="", kind="exercise"))


def spread_out(candidates: list[Block], need: int) -> list[Block]:
    """The blocks to leave out of an objective to bring it down to its budget.

    The exercises of an objective are written to climb from the routine to the
    demanding, so the ones left out are spread along that climb rather than taken
    from either end of it: the nth of k to go is the group nearest the nth of k
    evenly spaced points, among the groups still small enough to fit what is left to
    drop. Exercises standing alone go before sets, because a set is a piece of
    design and an exercise is not; a set goes only when nothing else will bring the
    count down. A count no combination reaches exactly is left a little over rather
    than cut a little under.
    """
    out: list[Block] = []
    remaining = need
    positions = {block.key: index for index, block in enumerate(candidates)}
    for prefer_singles in (True, False):
        targets = [(index + 0.5) * len(candidates) / max(need, 1) for index in range(need)]
        for target in targets:
            if remaining <= 0:
                break
            pool = [
                block for block in candidates
                if block not in out and group_size(block) <= remaining
                and (group_size(block) == 1 or not prefer_singles)
            ]
            if not pool:
                continue
            pick = min(pool, key=lambda block: abs(positions[block.key] - target))
            out.append(pick)
            remaining -= group_size(pick)
    return out


def page_capacity(holdback: float) -> float:
    return TEXT_HEIGHT - holdback


def min_page_fill(pages: list[list[Block]], holdback: float) -> float:
    """The fill ratio of the sparsest page, or 1 when there are no pages."""
    room = page_capacity(holdback)
    if not pages or room <= 0:
        return 1.0
    return min(page_fill(page) / room for page in pages)


def last_fill(pages: list[list[Block]], holdback: float) -> float:
    room = page_capacity(holdback)
    return page_fill(pages[-1]) / room if pages and room > 0 else 1.0


def interior_min_fill(pages: list[list[Block]], holdback: float) -> float:
    """The fill ratio of the sparsest page before the last, or 1 when there is only one page."""
    if len(pages) <= 1:
        return 1.0
    return min_page_fill(pages[:-1], holdback)


def fill_score(pages: list[list[Block]], holdback: float) -> tuple[float, float]:
    """How well the paper fills, for comparing fits: interior pages first, then the last."""
    return (interior_min_fill(pages, holdback), last_fill(pages, holdback))


def fill_needs_work(pages: list[list[Block]], holdback: float) -> bool:
    """Whether the +8 pool should still be swapped or added from."""
    if interior_min_fill(pages, holdback) < INTERIOR_PAGE_FILL_TARGET:
        return True
    return last_fill(pages, holdback) < LAST_PAGE_FILL_TARGET


def locked_left_out_from(path: Path | None) -> dict[str, list[str]] | None:
    """The exercises a locked selection leaves out, or None when nothing is locked.

    None is the native fit, not a lock. Once a source lock is requested, a
    missing or malformed file is an error rather than permission to diverge.
    """
    if path is None:
        return None
    if not path.is_file():
        raise RuntimeError(f"selection lock does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read selection lock {path}: {error}") from error
    left = data.get("left_out")
    if not isinstance(left, dict):
        raise RuntimeError(f"selection lock {path} has no left_out mapping")
    return {
        str(objective): [str(item) for item in ids]
        for objective, ids in left.items()
    }


def locked_printed_ids_from(path: Path | None) -> list[str] | None:
    """The reading order a locked selection recorded, or None when nothing is locked."""
    if path is None:
        return None
    if not path.is_file():
        raise RuntimeError(f"selection lock does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read selection lock {path}: {error}") from error
    ids = data.get("printed_ids")
    if not isinstance(ids, list) or not ids:
        raise RuntimeError(f"selection lock {path} has no printed_ids list")
    return [str(item) for item in ids]


@dataclass(frozen=True)
class MembershipLock:
    printed_ids: tuple[str, ...]
    left_out: dict[str, list[str]]


def membership_lock_from(
    path: Path | None, *, require_printed: bool = True
) -> MembershipLock | None:
    """A strict selected-ID set and its objective-addressed complement."""
    if path is None:
        return None
    if not path.is_file():
        raise RuntimeError(f"membership lock does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read membership lock {path}: {error}") from error
    printed = data.get("printed_ids")
    left = data.get("left_out")
    if require_printed and (not isinstance(printed, list) or not printed):
        raise RuntimeError(f"membership lock {path} has no printed_ids list")
    if printed is None and not require_printed:
        printed = []
    if not isinstance(printed, list):
        raise RuntimeError(f"membership lock {path} has invalid printed_ids")
    if not isinstance(left, dict):
        raise RuntimeError(f"membership lock {path} has no left_out mapping")
    printed_ids = tuple(str(item) for item in printed)
    omitted = {
        str(objective): [str(item) for item in ids]
        for objective, ids in left.items()
        if isinstance(ids, list)
    }
    if len(omitted) != len(left):
        raise RuntimeError(f"membership lock {path} has a non-list left_out row")
    flat_left = [item for ids in omitted.values() for item in ids]
    if len(set(printed_ids)) != len(printed_ids):
        raise RuntimeError(f"membership lock {path} repeats a printed id")
    if len(set(flat_left)) != len(flat_left):
        raise RuntimeError(f"membership lock {path} repeats a left-out id")
    overlap = set(printed_ids) & set(flat_left)
    if overlap:
        raise RuntimeError(
            f"membership lock {path} both prints and omits {sorted(overlap)[0]}"
        )
    return MembershipLock(printed_ids, omitted)


def document_exercise_ids(document: dict) -> set[str]:
    """Every exercise id the paper can print."""
    ids: set[str] = set()
    for group in section_groups(document):
        for exercise in group.get("exercises") or []:
            exercise_id = str(exercise.get("id") or "")
            if exercise_id:
                ids.add(exercise_id)
    return ids


def selection_lock_splits_groups(document: dict, lock: MembershipLock) -> bool:
    """True when the lock prints some exercises in a set but omits others."""
    printed = set(lock.printed_ids)
    omitted = {
        exercise_id
        for ids in lock.left_out.values()
        for exercise_id in ids
    }
    for group in section_groups(document):
        exercise_ids = [
            str(exercise.get("id") or "")
            for exercise in group.get("exercises") or []
        ]
        exercise_ids = [item for item in exercise_ids if item]
        if len(exercise_ids) <= 1:
            continue
        hits_printed = bool(set(exercise_ids) & printed)
        hits_omitted = bool(set(exercise_ids) & omitted)
        if hits_printed and hits_omitted:
            return True
    return False


def reusable_selection(tex_path: Path, document: dict) -> MembershipLock | None:
    """Reuse a stored selection when it still partitions this section's exercises.

    A later measurement can prefer a different subset of the same exercises.
    The stored file is the selection those exercises were published with, so it
    is kept until an exercise is added or removed, or exercises are regrouped
    into sets that the lock would split.
    """
    path = tex_path.with_suffix(".selection.json")
    if not path.is_file():
        return None
    try:
        lock = membership_lock_from(path, require_printed=False)
    except RuntimeError:
        return None
    if lock is None:
        return None
    stored = set(lock.printed_ids)
    for ids in lock.left_out.values():
        stored.update(ids)
    if stored != document_exercise_ids(document):
        return None
    if selection_lock_splits_groups(document, lock):
        return None
    return lock


def apply_stored_selection(
    tex_path: Path,
    document: dict,
    locked_left_out: dict[str, list[str]] | None,
    locked_printed_ids: list[str] | None,
    expected_printed_ids: list[str] | tuple[str, ...] | None,
) -> tuple[dict[str, list[str]] | None, list[str] | None, list[str] | tuple[str, ...] | None]:
    """Keep a stored print order when it still matches this section.

    An explicit printed-id order is left as given. A membership lock keeps its
    set, and the stored file supplies the order when that set is the same.
    With no lock at all, the stored file supplies the set and the order.
    """
    if locked_printed_ids is not None:
        return locked_left_out, locked_printed_ids, expected_printed_ids
    reused = reusable_selection(tex_path, document)
    if reused is None:
        return locked_left_out, locked_printed_ids, expected_printed_ids
    expected = set(expected_printed_ids or ())
    if expected and set(reused.printed_ids) != expected:
        return locked_left_out, locked_printed_ids, expected_printed_ids
    printed = list(reused.printed_ids)
    if locked_left_out is None and expected_printed_ids is None:
        return reused.left_out, printed, printed
    return locked_left_out, printed, expected_printed_ids


def printed_ids_from_pages(pages: list[list[Block]]) -> list[str]:
    """Exercise ids in the order the planned pages set them."""
    ids: list[str] = []
    seen: set[str] = set()
    for page in pages:
        for block in page:
            group = block.group
            if not group:
                continue
            for exercise in group.get("exercises") or []:
                name = str(exercise.get("id") or "")
                if name and name not in seen:
                    seen.add(name)
                    ids.append(name)
    return ids


def next_in_order(pending: list[Block], room: float, above: Block | None) -> list[Block]:
    """The next written question if it fits, and nothing chosen beyond it."""
    if pending and block_room(pending[0], above) <= room:
        return [pending[0]]
    return []


def order_blocks(blocks: list[Block], printed_ids: list[str]) -> list[Block]:
    """The same blocks, questions within each objective in `printed_ids` order."""
    rank = {exercise_id: index for index, exercise_id in enumerate(printed_ids)}

    def block_rank(block: Block) -> int:
        group = block.group or {}
        found = [
            rank[str(exercise.get("id") or "")]
            for exercise in group.get("exercises") or []
            if str(exercise.get("id") or "") in rank
        ]
        return min(found) if found else len(rank)

    ordered: list[Block] = [block for block in blocks if block.kind == "section"]
    for head, pending in objective_runs(
        [block for block in blocks if block.kind != "section"]
    ):
        if head is not None:
            ordered.append(head)
        ordered.extend(sorted(pending, key=block_rank))
    return ordered


def plan_pages(
    blocks: list[Block],
    holdback: float,
    locked_printed_ids: list[str] | None = None,
) -> list[list[Block]]:
    """Plan pages; when a printed order is locked, keep that order and pack in sequence."""
    if locked_printed_ids:
        return lay_out(
            order_blocks(blocks, locked_printed_ids),
            holdback,
            next_in_order,
            strict_order=True,
        )
    return fit_pages(blocks, holdback)


def select_blocks(
    blocks: list[Block],
    budgets: dict[str, int],
    holdback: float = PAGE_HOLDBACK,
    locked_left_out: dict[str, list[str]] | None = None,
    expected_printed_ids: list[str] | tuple[str, ...] | None = None,
) -> tuple[list[Block], Selection]:
    """Choose which of the written exercises the paper prints, to the budget and the page.

    Every objective is written to more exercises than it was budgeted. This brings
    each back to its budget, then looks at the paper those choices make: interior
    pages should reach INTERIOR_PAGE_FILL_TARGET and the last page at least
    LAST_PAGE_FILL_TARGET. If not, the choice is turned, one exchange at a time,
    towards a better fill_score — interior pages first, then the last — and the
    pages planned again after each: an exercise that is printed is exchanged for one
    that is not, of the same objective and the same size, so the budget holds and the
    objective's order holds, and only the height of the paper moves.

    Only when no exchange will do it are exercises added over the budget, and then
    one at a time from the pool, choosing whichever most improves fill_score without
    opening a new page. Whatever the fit reaches in FIT_TURNS is what is set.

    A section with no budget recorded — one written before the budgets carried the
    extras — keeps every exercise, and comes out as it always did.
    """
    questions = [block for block in blocks if block.group is not None]
    by_objective: dict[str, list[Block]] = {}
    for block in questions:
        by_objective.setdefault(block.objective, []).append(block)

    left: dict[str, list[Block]] = {}
    for objective, rows in by_objective.items():
        written = sum(map(group_size, rows))
        budget = budgets.get(objective, written)
        left[objective] = spread_out(rows, written - budget) if written > budget else []

    if locked_left_out is not None:
        all_ids: dict[str, tuple[str, str]] = {}
        for objective, rows in by_objective.items():
            for block in rows:
                group = block.group or {}
                group_id = str(group.get("id") or block.key)
                for exercise in group.get("exercises") or []:
                    exercise_id = str(exercise.get("id") or "")
                    if not exercise_id or exercise_id in all_ids:
                        raise RuntimeError(
                            f"paper has a missing or duplicate exercise id {exercise_id!r}"
                        )
                    all_ids[exercise_id] = (objective, group_id)
        locked_ids = {
            str(exercise_id)
            for ids in locked_left_out.values()
            for exercise_id in ids
        }
        expected = set(expected_printed_ids or ()) or (set(all_ids) - locked_ids)
        unknown = (locked_ids | expected) - set(all_ids)
        if unknown:
            raise RuntimeError(f"selection lock names unknown id {sorted(unknown)[0]}")
        if expected & locked_ids or expected | locked_ids != set(all_ids):
            raise RuntimeError("selection lock does not partition every exercise exactly once")
        for objective, ids in locked_left_out.items():
            for exercise_id in ids:
                actual = all_ids[str(exercise_id)][0]
                if objective != actual:
                    raise RuntimeError(
                        f"selection lock files {exercise_id} under {objective}, "
                        f"but it belongs to {actual}"
                    )
        dropped_groups: set[str] = set()
        for rows in by_objective.values():
            for block in rows:
                group = block.group or {}
                exercise_ids = [
                    str(exercise.get("id", ""))
                    for exercise in group.get("exercises") or []
                ]
                selected = set(exercise_ids) & expected
                omitted = set(exercise_ids) & locked_ids
                if selected and omitted:
                    raise RuntimeError(
                        f"selection lock splits group {group.get('id') or block.key}"
                    )
                if exercise_ids and set(exercise_ids) <= locked_ids:
                    group_id = str(group.get("id") or "")
                    if group_id:
                        dropped_groups.add(group_id)
        left = {
            objective: [
                block
                for block in rows
                if str((block.group or {}).get("id") or "") in dropped_groups
            ]
            for objective, rows in by_objective.items()
        }

    def kept(objective: str) -> list[Block]:
        return [row for row in by_objective[objective] if row not in left[objective]]

    def chosen() -> list[Block]:
        return [
            block for block in blocks
            if block.group is None or block not in left[block.objective]
        ]

    def swap(objective: str, printed: Block, out: Block) -> None:
        left[objective].remove(out)
        left[objective].append(printed)

    def same_size_swaps() -> list[tuple[str, Block, Block]]:
        """Every swap of a printed block for a left-out one of the same size."""
        found: list[tuple[str, Block, Block]] = []
        for objective in by_objective:
            for out in left[objective]:
                for printed in kept(objective):
                    if group_size(printed) == group_size(out):
                        found.append((objective, printed, out))
        return found

    pages = fit_pages(chosen(), holdback)
    score = fill_score(pages, holdback)
    turns = 0
    while locked_left_out is None and fill_needs_work(pages, holdback) and turns < FIT_TURNS:
        turns += 1
        best: tuple[str, Block, Block] | None = None
        best_score = score
        best_pages = pages
        for objective, printed, out in same_size_swaps():
            swap(objective, printed, out)
            trial = fit_pages(chosen(), holdback)
            trial_score = fill_score(trial, holdback)
            swap(objective, out, printed)
            if trial_score > best_score:
                best_score = trial_score
                best = (objective, printed, out)
                best_pages = trial
        if best is None:
            break
        swap(*best)
        pages, score = best_pages, best_score

    # Nothing to exchange, or exchanging did not do it: add from the pool, one block at
    # a time, whichever most improves interior fill then the last page without opening a
    # new page.
    turns = 0
    while locked_left_out is None and fill_needs_work(pages, holdback) and turns < FIT_TURNS:
        turns += 1
        best_candidate: tuple[str, Block] | None = None
        best_score = score
        best_pages = pages
        for objective in by_objective:
            pool = left[objective]
            if not pool:
                continue
            singles = [block for block in pool if group_size(block) == 1] or pool
            for candidate in singles:
                left[objective].remove(candidate)
                trial = fit_pages(chosen(), holdback)
                if len(trial) <= len(pages):
                    trial_score = fill_score(trial, holdback)
                    if trial_score > best_score:
                        best_score = trial_score
                        best_candidate = (objective, candidate)
                        best_pages = trial
                left[objective].append(candidate)
        if best_candidate is None:
            break
        objective, candidate = best_candidate
        left[objective].remove(candidate)
        pages, score = best_pages, best_score
        if not fill_needs_work(pages, holdback):
            break

    selection = Selection(
        budget=sum(budgets.get(o, sum(map(group_size, rows))) for o, rows in by_objective.items()),
        written=sum(map(group_size, questions)),
        printed=sum(group_size(block) for block in chosen() if block.group is not None),
        left_out={
            objective: [
                exercise["id"] for block in by_objective[objective] if block in rows
                for exercise in block.group["exercises"]
            ]
            for objective, rows in left.items() if rows
        },
        interior_fill=interior_min_fill(pages, holdback),
        fill=last_fill(pages, holdback),
    )
    if expected_printed_ids is not None:
        selected_ids = {
            str(exercise.get("id") or "")
            for block in chosen()
            if block.group is not None
            for exercise in ((block.group or {}).get("exercises") or [])
        }
        if selected_ids != set(expected_printed_ids):
            raise RuntimeError("selected exercise membership differs from the requested lock")
    return chosen(), selection


def objective_budgets(document: dict) -> dict[str, int]:
    """What each objective was budgeted, from the exercises document; empty if unrecorded."""
    rows = document.get("exercises", {}).get("objectives", [])
    return {row["id"]: row["budget"] for row in rows if "budget" in row}


def measure_tex(
    blocks: list[Block],
    drawings: Drawings,
    profile: Profile = ENGLISH,
    *,
    language_book: bool = False,
    book_id: str = "",
) -> str:
    """A document that measures every block the paper might set and prints none of them.

    The blocks are numbered as the writing's own order numbers them, which is not
    the numbering the planned paper will use. Only a set's range is set in the text
    rather than in a box of a fixed width, so all a renumbering can change is a few
    characters at the head of a stem; where that turns out to matter, the plan is
    caught not holding and made again.
    """
    drawings = notation_drawings(drawings, profile, language_book=language_book)
    firsts = numbering([blocks])
    quotes, western, snippets = quoting_for(profile, book_id)
    try:
        measured = "\n".join(
            f"\\measure{{{block.key}}}{{%\n"
            f"{block_tex(block, firsts.get(block.group_key, 1), drawings)}%\n}}"
            for block in every_block(blocks)
        )
    finally:
        _reset_quoting(quotes, western, snippets)
    return (
        preamble(profile)
        + MEASURE_BLOCKS
        + "\n"
        + begin_document(profile, planned=True)
        + measured
        + "\n\\null\n\\immediate\\closeout\\measurements\n\\end{document}\n"
    )


def read_measurements(path: Path) -> dict[str, tuple[float, float]]:
    """The height and depth of every block the measuring document set, in points."""
    found: dict[str, tuple[float, float]] = {}
    for line in path.read_text(errors="replace").splitlines():
        key, _, rest = line.partition(" ")
        sizes = [word.strip(",").removesuffix("pt") for word in rest.split()]
        if not (key and len(sizes) == 2):
            continue
        try:
            found[key] = (float(sizes[0]), float(sizes[1]))
        except ValueError:
            continue
    return found


def measure_blocks(
    blocks: list[Block],
    drawings: Drawings,
    tex_path: Path,
    profile: Profile = ENGLISH,
    *,
    language_book: bool = False,
    book_id: str = "",
) -> list[str]:
    """Measure every block by compiling a document that sets them all and prints none.

    Returns the keys nothing came back for, which is the caller's cue that this
    paper cannot be planned and has to be set the way TeX alone would set it. The
    measuring document is left behind, as the paper's own .tex is, because it is the
    account of what the plan was made from; the page it has to produce to be a
    document at all is not worth keeping.
    """
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    tex_path.write_text(
        measure_tex(
            blocks, drawings, profile, language_book=language_book, book_id=book_id
        ),
        encoding="utf-8",
    )
    compile_pdf(tex_path)
    tex_path.with_suffix(".pdf").unlink(missing_ok=True)
    sizes_path = tex_path.with_suffix(".sizes")
    sizes = read_measurements(sizes_path) if sizes_path.is_file() else {}
    missing = []
    for block in every_block(blocks):
        if block.key in sizes:
            block.height, block.depth = sizes[block.key]
        else:
            missing.append(block.key)
    return missing


def paper_tex(
    document: dict,
    pages: list[list[Block]],
    drawings: Drawings,
    profile: Profile = ENGLISH,
) -> str:
    """The paper as it was planned: every block set whole, a break where a page ends.

    Nothing is left for TeX to decide. The blocks are laid down in the order the
    plan puts them, each in a box of its own so that none can break across a page,
    and \\newpage says where every page ends.
    """
    drawings = notation_drawings(
        drawings, profile, language_book=is_arabic_subject(document)
    )
    firsts = numbering(pages)
    quotes, western, snippets = quoting_for(profile, str(document.get("book_id", "")))
    body: list[str] = []
    try:
        for index, page in enumerate(pages):
            if index:
                body.append("\\newpage")
            above: Block | None = None
            for block in page:
                if gap := gap_above(block.kind, above.kind if above else ""):
                    body.append(f"\\vspace{{{gap:.0f}pt}}")
                set_out = block_tex(block, firsts.get(block.group_key, 1), drawings)
                body.append(set_out if block.loose else f"\\unbroken{{%\n{set_out}%\n}}")
                above = block
    finally:
        _reset_quoting(quotes, western, snippets)
    return (
        preamble(profile)
        + "\n"
        + begin_document(profile, planned=True)
        + foot_tex(document, profile=profile)
        + "\n"
        + "\n\n".join(body)
        + "\n\\end{document}\n"
    )


# A figure compiled on its own for an Arabic book is set on a page that runs left to
# right. standalone cuts its page to the picture by measuring the box the picture makes
# and shipping out a page that size, and with babel's body direction set the other way
# the box is laid leftwards from the page's edge, so the page showed the right half of
# the picture and nothing else. The page and the body are turned back; the paragraph
# and the text inside the picture's nodes are left as babel set them, so a label that
# reads "الكتلة (kg)" comes out the same here as it does on the paper, where the picture
# stands as a line of a paragraph and the page's direction never reaches it.
STANDALONE_DIRECTION = "\\AtBeginDocument{\\pagedir TLT\\bodydir TLT}\n"


def drawing_preamble(profile: Profile = ENGLISH) -> str:
    """The type setup a figure is compiled with on its own.

    The paper's own, so a label that will be too small to read on the page is
    already too small to read here, and a figure for an Arabic book is set in the
    Arabic face with its labels reordered right to left as they will be in the paper.
    """
    return (
        type_preamble(profile)
        + LEADING_PREAMBLE
        + DRAWING_PREAMBLE
        + (STANDALONE_DIRECTION if profile.rtl else "")
    )


def drawing_tex(tikz: str, border: str = FIGURE_BORDER, profile: Profile = ENGLISH) -> str:
    """One figure as a document of its own, cropped to the picture.

    Compiled this way while it is being drawn, so a picture that will not build
    is caught as itself rather than as a paper that failed somewhere.
    """
    tikz = profile_tikz(tikz, profile)
    return (
        f"\\documentclass[{BODY_SIZE}pt,border={border}]{{standalone}}\n"
        + drawing_preamble(profile)
        + begin_document(profile)
        + standard_lines(tikz.strip())
        + "\n\\end{document}\n"
    )


def build_drawing_pdf(tikz: str, tex_path: Path, profile: Profile = ENGLISH) -> Path:
    """Write and compile one figure on its own, returning the PDF it made."""
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    tex_path.write_text(drawing_tex(tikz, profile=profile), encoding="utf-8")
    compile_pdf(tex_path)
    return tex_path.with_suffix(".pdf")


def compile_pdf(tex_path: Path) -> None:
    """Run the LaTeX engine, raising with the first real error if it fails."""
    log_path = tex_path.with_suffix(".log")
    pdf_path = tex_path.with_suffix(".pdf")
    # LaTeX still writes a PDF for a document it reported errors on, so clear any
    # previous output first: a stale file must never look like a fresh success.
    pdf_path.unlink(missing_ok=True)

    result = subprocess.run(
        [LATEX_ENGINE, "-interaction=nonstopmode", tex_path.name],
        cwd=tex_path.parent,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0 or not pdf_path.is_file():
        pdf_path.unlink(missing_ok=True)
        detail = ""
        if log_path.is_file():
            errors = [line for line in log_path.read_text(errors="replace").splitlines()
                      if line.startswith("!")]
            detail = f": {errors[0]}" if errors else ""
        raise RuntimeError(f"{LATEX_ENGINE} failed on {tex_path.name}{detail} (see {log_path})")

    missing = 0
    if log_path.is_file():
        missing = log_path.read_text(errors="replace").count("Missing character")
    tex_path.with_suffix(".aux").unlink(missing_ok=True)
    if missing:
        pdf_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{missing} character(s) had no glyph in {tex_path.name} (see {log_path})"
        )
    log_path.unlink(missing_ok=True)


def page_stamp(tex_path: Path) -> tuple[int, int] | None:
    """The page numbers a paper was last stamped with, if it carries any.

    Read so that a paper already stamped with the numbers the book now wants is not
    sent to the engine again for nothing: setting one section leaves the papers before
    it exactly where they were.
    """
    if not tex_path.is_file():
        return None
    source = tex_path.read_text(encoding="utf-8")
    first = re.search(r"\\setcounter\{page\}\{(\d+)\}", source)
    total = re.search(r"\\renewcommand\{\\bookpages\}\{(\d+)\}", source)
    if not first or not total:
        return None
    return int(first.group(1)), int(total.group(1))


def stamp_pages(tex_path: Path, first: int, total: int) -> int:
    """Set a section's pages again with the book's page numbers in the foot.

    A section is set before the book is bound, so while it is being set it knows
    neither the page it starts on nor how long the book runs to. Both are written into
    its .tex as a counter and a macro standing at 1; this rewrites the two of them and
    sends the paper back to the engine.

    Nothing else about the paper is touched. The foot sits in the margin, outside the
    text the plan was made from, so the pages come back as they were planned and the
    count is returned only to say so.
    """
    source = tex_path.read_text(encoding="utf-8")
    stamped, counters = re.subn(
        r"\\setcounter\{page\}\{\d+\}", f"\\\\setcounter{{page}}{{{first}}}", source
    )
    stamped, totals = re.subn(
        r"\\renewcommand\{\\bookpages\}\{\d+\}",
        f"\\\\renewcommand{{\\\\bookpages}}{{{total}}}",
        stamped,
    )
    if counters != 1 or totals != 1:
        raise RuntimeError(
            f"{tex_path.name} has no page numbers to stamp; set the section again"
        )
    tex_path.write_text(stamped, encoding="utf-8")
    compile_pdf(tex_path)
    return len(PdfReader(tex_path.with_suffix(".pdf")).pages)


def require_latex() -> None:
    if shutil.which(LATEX_ENGINE) is None:
        raise RuntimeError(f"{LATEX_ENGINE} not found; install a TeX distribution")
    # Checked here rather than left to the engine, because a numeral face that is not
    # there is not one paper going wrong: it is every paper, each reporting a fontspec
    # error from the middle of its own log, for a file whose name is on one line of this
    # module. Said once, before the compiles, and naming the line to change.
    numeral = NUMERAL_DIR / NUMERAL_FONT
    if not numeral.is_file():
        raise RuntimeError(
            f"the numeral face {NUMERAL_FACE_NAME!r} is not at {numeral}; "
            f"check NUMERAL_FACE_NAME and NUMERAL_FACES in {Path(__file__).name}"
        )
    arabic_numeral = FONT_DIR / ARABIC_NUMERAL_FONT
    if not arabic_numeral.is_file():
        raise RuntimeError(
            f"the Arabic numeral face is not at {arabic_numeral}; "
            f"check ARABIC_NUMERAL_FONT in {Path(__file__).name}"
        )


def build_section_pdf(
    document: dict,
    tex_path: Path,
    drawings: Drawings | None = None,
    labels: dict[str, str] | None = None,
    profile: Profile = ENGLISH,
    locked_left_out: dict[str, list[str]] | None = None,
    locked_printed_ids: list[str] | None = None,
    expected_printed_ids: list[str] | tuple[str, ...] | None = None,
) -> Paper:
    """Write and compile one section's paper, and say how it came out.

    The paper is measured before it is set. Every block it might print is compiled
    once in a box of its own to see what it stands to; which of them it does print is
    then chosen, to the objectives' budgets and to the page (select_blocks); the
    pages are planned from those measurements, and the paper is then set to the plan:
    a question goes where it was put, and no page breaks anywhere but where it was
    told to.

    The plan is checked against the paper. Its own arithmetic says how many pages
    the paper runs to, and if the engine disagrees then something was measured a
    fraction short and a page spilled, so the pages are planned again with more held
    back. Three tries, and if none of them holds the paper stands as it came out and
    says so, because a paper set imperfectly is worth more than no paper.

    A section whose blocks cannot be measured is set the way it always was, with the
    page breaks left to TeX. Both .tex files are left behind, the paper's beside its
    PDF and the measuring one under `fitting`, because they are the only account of
    what was sent to the engine and what the plan was made from.
    """
    drawings = drawings or {}
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    # section_tex turns the tables round for itself, so it is handed the document as
    # written; the blocks are made from the turned one.
    blocks = section_blocks(for_direction(document, profile), labels, profile)
    missing = measure_blocks(
        blocks,
        drawings,
        tex_path.parent / "fitting" / tex_path.name,
        profile,
        language_book=is_arabic_subject(document),
        book_id=str(document.get("book_id", "")),
    )

    if missing:
        tex_path.write_text(
            section_tex(document, drawings, labels, profile), encoding="utf-8"
        )
        compile_pdf(tex_path)
        return Paper(
            pages=len(PdfReader(tex_path.with_suffix(".pdf")).pages), planned=False
        )

    # Every block was measured; which of them the paper prints is settled once, from
    # the budgets and the page, and the pages are then planned from those.
    # A stored selection is reused when the accepted exercises are unchanged.
    locked_left_out, locked_printed_ids, expected_printed_ids = apply_stored_selection(
        tex_path,
        document,
        locked_left_out,
        locked_printed_ids,
        expected_printed_ids,
    )
    blocks, selection = select_blocks(
        blocks,
        objective_budgets(document),
        locked_left_out=locked_left_out,
        expected_printed_ids=expected_printed_ids,
    )

    for attempt in range(PLANNING_TRIES):
        pages = plan_pages(
            blocks,
            PAGE_HOLDBACK + attempt * HOLDBACK_STEP,
            locked_printed_ids,
        )
        tex_path.write_text(
            paper_tex(document, pages, drawings, profile), encoding="utf-8"
        )
        compile_pdf(tex_path)
        set_to = len(PdfReader(tex_path.with_suffix(".pdf")).pages)
        selection.printed_ids = printed_ids_from_pages(pages)
        holdback = PAGE_HOLDBACK + attempt * HOLDBACK_STEP
        selection.interior_fill = interior_min_fill(pages, holdback)
        selection.fill = last_fill(pages, holdback)
        paper = Paper(
            pages=set_to,
            slack=[TEXT_HEIGHT - page_fill(page) for page in pages],
            divided=len({
                block.group_key for page in pages for block in page
                if block.kind == "member"
            }),
            loose=sum(1 for page in pages for block in page if block.loose),
            tries=attempt + 1,
            selection=selection,
        )
        if set_to == len(pages):
            return paper
    return paper
