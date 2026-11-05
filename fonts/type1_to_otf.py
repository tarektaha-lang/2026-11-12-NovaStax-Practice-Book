"""Make an OpenType copy of a Type 1 font, which is the only form the book can use.

The display faces this repository keeps are Type 1: a PFB of outlines with an AFM of
widths beside it, the shape a font took when it came on a disk. LuaTeX will read that
pair, but not safely. luaotfload has no cache for a Type 1 face, so every run parses it
again, and a book is set as seventeen papers compiling at once: run that way the engine
segfaults on about a third of them, in no fixed place, leaving a log that stops in the
middle of the preamble and a section missing from the book. An OpenType face is cached
like every other face here, and seventeen at once is seventeen clean papers.

There is a second reason, met first and worth writing down. fontspec's Path option is not
consulted for a Type 1 lookup: hand it one and the font is reported as simply not found,
whatever the folder is called. A Type 1 face can only be found by putting its folder on
the engine's own search path, by running it with TEXFONTS set to "<folder>//:" — and
naming the AFM rather than the PFB, because the AFM is the file carrying the metrics.
Naming the PFB is the quiet mistake: the file is found and the log says "metric data not
found or bad". All of that works, and still segfaults under a parallel build, which is
why the conversion exists rather than the environment variable.

So a face is converted once, by hand, and the OTF committed in fonts/ beside the vendor
folder the PFB came from. This script is what makes it, kept so that each OTF is a thing
that can be made again rather than a binary nobody can account for:

    python3 fonts/type1_to_otf.py <the PFB> fonts/<PostScript name>.otf

The OTF is named after the font's own PostScript name, which the AFM beside the PFB gives
as FontName, so that the file in fonts/ and the face inside it answer to the same thing.
The DOS-era PFB names do not survive the trip, and are worth writing down, since a folder
of EU*.PFB says very little about which cut is which. Every OTF in fonts/ came from one of
these, and remaking one is the line beside it:

    fonts/AntiqueOlive-Nord.otf         "fonts/Antique Olive 2/AQNR____.PFB"
    fonts/Eurostile-Bold.otf            "fonts/Eurostile 1/EUB_____.PFB"
    fonts/Eurostile-BoldExtendedTwo.otf "fonts/Eurostile 2/EUBEX___.PFB"

Each vendor folder has a .txt from the foundry listing its PostScript names against its
DOS filenames, which is where those pairings come from rather than from guessing.

It needs fontTools, which the book itself does not, so it is not in requirements.txt.
Any Python with `pip install fonttools` will do.

The outlines are carried over exactly: each Type 1 charstring is drawn into a Type 2 one
through a pen, so the shapes and the widths are the font's own. What is dropped is the
hinting, which matters at the sizes a screen renders text at and not at the 72pt a section
number is set at, and the foundry's kerning, which in none of these faces touches a digit
or a full stop and so could not have moved anything in "3.1" or "12.10" anyway.

Two things then go back in on top of that faithful pass, and both are the book's own rather
than the foundry's: kerning for pairs the section number makes and the foundry never did
(KERN_PAIRS), and glyphs the book has redrawn (REDRAWN_GLYPHS). Each is keyed by the face
it was made for, and each says at its own definition what it changes and why.

They live in the face rather than in LaTeX so that they belong to the face. The book picks
its display font by name from a list in latex.py, on the promise that changing it is one
line; a correction drawn for Eurostile's figures has no business following the numbers to a
font whose figures are shaped differently.
"""

from __future__ import annotations

import sys
from pathlib import Path
from statistics import median

from fontTools.agl import AGL2UV
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.t1Lib import T1Font


# What a Type 1 font calls its weight, as the OpenType number for it. A Type 1 font
# says "Nord" or "Demi" where OpenType wants a number between 100 and 900, and there is
# no rule that turns one into the other: these are the words the Adobe packages in this
# folder actually use, ordered by the stem widths those same fonts declare. Antique Olive
# runs Light 72, Roman 112, Bold 150, Black 194, Compact 254, Nord 339, so the two
# display cuts sit above Black rather than beside it. A word not here is left as Regular,
# which is wrong only in what a font menu would call the face; nothing in the book
# selects a face by weight, because every face here is asked for by filename.
WEIGHT_CLASSES = {
    "thin": 100, "extralight": 200, "ultralight": 200, "light": 300,
    "regular": 400, "normal": 400, "roman": 400, "book": 400,
    "medium": 500, "demi": 600, "demibold": 600, "semibold": 600,
    "bold": 700, "black": 800, "heavy": 800, "extrabold": 800,
    "compact": 900, "nord": 900,
}


# Kerning the book asks for, by the font's PostScript name, as the space to take out of a
# pair in units of the em's thousandth. Not the font's own kerning, which the conversion
# drops, but space removed from a pair the section number puts together and the foundry
# never did.
#
# Empty, which is not the same as the font being unkerned. Eurostile's figures are tabular —
# every one of them 988 units wide, which is what lets a column of them line up — and the 1
# was 572 units of ink sitting off-centre in that slot, 170 units of air to its left and 246
# to its right where every other figure has matching air either side. That put 388 units in
# "1." and 312 in ".1" against 207 for the same pairs on any other figure, and this table
# once held the two amounts that closed them. Redrawing the 1 (below) took that cause away:
# it is spaced there like a figure of the face, so "1." needs nothing taken out of it at all,
# and the one pair still wanting a kern — a stop before the 1, which has to be set to the
# upright rather than to the flag hanging over it — is worked out by the redrawing from the
# shape it just drew, and returned with it. Better there than here, because the amount
# follows the flag's length and a number written down would go stale the moment it changed.
#
# So this is for a pair whose amount is a judgement rather than a measurement, and it is
# still the last word: what it says here beats anything a redrawing worked out. A pair added
# should be one a section number can actually produce; kerning anything else in is dead
# weight in the font and a puzzle to whoever reads this next.
KERN_PAIRS: dict[str, dict[tuple[str, str], int]] = {}


# The figures a face spaces its digits by, which is all of them but the 1: in a tabular
# face the 1 is the one figure whose air is not the figure's own, being a narrow shape
# padded out to keep a wide one's place.
SPACING_FIGURES = ("zero", "two", "three", "four", "five", "six", "seven", "eight", "nine")


def figure_bearing(glyphs) -> int:
    """The air this face keeps at the side of a figure, in units of the em's thousandth.

    Measured rather than chosen, and measured as a median so that one wide-shouldered
    figure cannot speak for the rest: Eurostile's run from the 0's 47 to the 7's 79, and
    70 is the middle of them. A glyph redrawn here is then spaced by what the face already
    does, and stays right if the face is ever a different one.
    """
    bearings = []
    for name in SPACING_FIGURES:
        if name in glyphs:
            bounds = BoundsPen(glyphs)
            glyphs[name].draw(bounds)
            if bounds.bounds:
                bearings.append(bounds.bounds[0])
    return round(median(bearings))


# How much of the 1's flag is kept, as a fraction of the reach the face draws. The knob
# for the shape: 1 is the flag as Novarese drew it, 0 takes it off and leaves the upright
# bare. This is the only number to turn, and the spacing follows it on its own — the glyph
# is measured after the flag is cut and given a figure's air either side, so a shorter flag
# makes a narrower 1 rather than a 1 with a hole in front of it.
FLAG_KEPT = 0.60


def shortened_flag(name: str, glyph, bearing: int) -> tuple[object, tuple[int, int], dict]:
    """Redraw a figure with a shorter version of the flag that leans off its head.

    Eurostile's 1 is an upright with a wedge running down-left from the top, the flat
    diagonal that face uses where an older one would put a serif. Drawn full length it
    reaches 368 units off the stem, most of the glyph's width, and it is what made the 1
    both wide and lopsided. Shortened it still reads as the same 1, because the two long
    edges keep the exact angles the face gave them: the tip is drawn in along those edges
    rather than the wedge being scaled, so nothing about the flag changes except how far
    it goes.

    The shape is read out of the outline rather than typed in, so this survives the face
    being reconverted, and refuses the job rather than mangling a glyph it does not
    recognise. The 1 is one contour of seven points, and knowing two of them locates the
    rest: the foot is the upright's foot, since the flag never reaches the baseline, so the
    points standing on it give the upright its sides. Walking the contour from there gives
    the corner the flag leaves the head at, the two points of its tip, and the point where
    it rejoins the upright.

    The result is then moved to sit at `bearing` with an advance of its width plus that air
    twice, because where the glyph sat cannot be kept. The upright is at the right-hand end
    of a 988-unit tabular slot, sized for figures three times the width of a 1, and the
    flag is what used to fill some of the space in front of it; a shortened flag leaves the
    rest as emptiness the heading would show as an indent. Spaced like a figure of this
    face instead, the 1 is proportional among tabular ones, which is right on a display
    line and would be wrong in a column of figures.

    That spacing measures to the flag, which is right for the glyph on its own and wrong
    after a full stop. The flag is up at the head and the stop is down on the baseline, so
    they cannot touch whatever the distance, and the space the eye reads in ".1" is not the
    one to the flag's tip but the one to the upright below it — set to the flag, the stop is
    left in a hole a quarter of an em wide. So a kern for that pair goes back with the
    glyph, and it puts the upright where a figure's own air would have put it: the stop is
    spaced to the 1 as if the flag were not there, and the flag overhangs it. The kern is
    returned rather than written down because it follows the flag's length — shorten the
    flag and the upright moves, and a number typed in here would be a number quietly wrong
    the next time FLAG_KEPT is touched.
    """
    record = RecordingPen()
    glyph.draw(record)
    if sum(1 for op, _ in record.value if op == "moveTo") != 1:
        raise ValueError("expected a 1 drawn as a single contour; this face differs")
    points = [point for _, args in record.value for point in args]
    foot = sorted(point for point in points if point[1] == 0)
    if len(foot) != 2 or len(points) != 7:
        raise ValueError(
            f"expected a 1 of seven points standing on two of them, found {len(points)} "
            f"and {len(foot)}; this face draws its 1 differently and needs a look by hand"
        )

    # Turned so the contour starts at the near corner of the foot, which puts its points in
    # a known order: the foot, the far top corner, then the flag — where it leaves the head,
    # the two points of its tip, and where it comes back to the upright.
    start = points.index(foot[0])
    ring = points[start:] + points[:start]
    head, tip, join = ring[3], ring[4:6], ring[6]

    draw = lambda anchor, point: (round(anchor[0] + FLAG_KEPT * (point[0] - anchor[0])),
                                  round(anchor[1] + FLAG_KEPT * (point[1] - anchor[1])))
    flag = [draw(head, tip[0]), draw(join, tip[1])]

    shape = [ring[0], ring[1], ring[2], head, *flag, join]
    shift = bearing - min(x for x, _ in shape)
    shape = [(round(x + shift), round(y)) for x, y in shape]
    advance = max(x for x, _ in shape) + bearing

    pen = T2CharStringPen(advance, None)
    pen.moveTo(shape[0])
    for point in shape[1:]:
        pen.lineTo(point)
    pen.closePath()
    upright = shape[0][0]
    return pen.getCharString(), (advance, bearing), {("period", name): bearing - upright}


# Glyphs the book has redrawn, by the font's PostScript name, as the function that replaces
# each. A redrawing is the book's own design and not the foundry's, so it is named here
# against the one face it was drawn for and cannot follow the numbers to another: the point
# of the list in latex.py is that the display face can be changed by a line, and a face
# arriving with someone else's 1 in it would make that untrue.
REDRAWN_GLYPHS = {
    "Eurostile-BoldExtendedTwo": {"one": shortened_flag},
}


def afm_header(pfb_path: Path) -> dict[str, str]:
    """What the AFM beside a PFB says about the font, down to its first character.

    The outlines carry almost nothing about the face as a whole: the cap height, the
    ascender, the descender and the notice all live in the AFM, and a font converted
    from the PFB alone gets a guess where it could have had the number. Read only as far
    as StartCharMetrics, because everything after that is the widths, which are taken
    from the charstrings themselves.
    """
    afm_path = pfb_path.with_suffix(".AFM")
    if not afm_path.is_file():
        afm_path = pfb_path.with_suffix(".afm")
    if not afm_path.is_file():
        return {}
    header: dict[str, str] = {}
    for line in afm_path.read_text(encoding="latin-1").splitlines():
        if line.startswith("StartCharMetrics"):
            break
        key, _, value = line.partition(" ")
        if key and value:
            header[key] = value.strip()
    return header


def convert(pfb_path: Path, otf_path: Path) -> None:
    source = T1Font(str(pfb_path))
    source.parse()
    glyphs = source.getGlyphSet()

    # A Type 1 font states its units in a FontMatrix rather than a number, and the
    # number OpenType wants is that scale inverted: the usual 0.001 is 1000 units.
    matrix = source.font["FontInfo"].get("FontMatrix") or source.font.get("FontMatrix")
    upem = round(1 / matrix[0]) if matrix else 1000

    # The AFM's answer wins wherever it has one, because it is the file the foundry put
    # the face's measurements in; the outlines are only asked where it is silent.
    info = dict(source.font["FontInfo"]) | afm_header(pfb_path)
    ps_name = info.get("FontName", pfb_path.stem)
    family = info.get("FamilyName", ps_name)
    full = info.get("FullName", ps_name)
    weight = info.get("Weight", "Regular")

    # .notdef has to come first and has to be there, because it is glyph 0 and glyph 0
    # is what a reader draws for anything the font turns out not to have.
    order = [".notdef"] + sorted(name for name in glyphs.keys() if name != ".notdef")

    charstrings: dict[str, object] = {}
    metrics: dict[str, tuple[int, int]] = {}
    for name in order:
        if name not in glyphs:
            # A .notdef the font never had: an empty glyph of the font's own width.
            pen = T2CharStringPen(upem // 2, glyphs)
            charstrings[name] = pen.getCharString()
            metrics[name] = (upem // 2, 0)
            continue
        # Drawn twice, because a Type 1 charstring only says how wide it is in the
        # course of being drawn (its hsbw is its first operator), and a Type 2 pen has
        # to be told the width before anything is drawn into it. The first pass is to
        # a recorder, which asks the question, and the second replays the answer.
        glyph = glyphs[name]
        recorder = RecordingPen()
        glyph.draw(recorder)
        pen = T2CharStringPen(glyph.width, glyphs)
        recorder.replay(pen)
        charstrings[name] = pen.getCharString()
        metrics[name] = (glyph.width, 0)

    # Done after the faithful pass rather than instead of it, so that what the conversion
    # carried over and what the book changed are two readable steps and not one. A redrawing
    # may hand back kerning of its own, for a pair its new shape has made wrong; that is
    # merged under the table's own pairs, which stay the last word.
    redrawn = REDRAWN_GLYPHS.get(ps_name, {})
    pairs = {}
    if redrawn:
        bearing = figure_bearing(glyphs)
        for name, redraw in redrawn.items():
            charstrings[name], metrics[name], implied = redraw(name, glyphs[name], bearing)
            pairs |= implied
    pairs |= KERN_PAIRS.get(ps_name, {})

    # Type 1 names its glyphs and says nothing about Unicode, so the cmap is built by
    # looking each name up in the Adobe glyph list. A name that is not in it is a glyph
    # no character can reach, which is right for the likes of .notdef.
    cmap = {AGL2UV[name]: name for name in order if name in AGL2UV}

    builder = FontBuilder(unitsPerEm=upem, isTTF=False)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    builder.setupCFF(ps_name, {"FullName": full, "FamilyName": family, "Weight": weight},
                     charstrings, {})
    builder.setupHorizontalMetrics(metrics)

    ascent = round(float(info.get("Ascender", 0.75 * upem)) or 0.75 * upem)
    descent = round(float(info.get("Descender", -0.25 * upem)) or -0.25 * upem)
    builder.setupHorizontalHeader(ascent=ascent, descent=descent)
    version = str(info.get("Version") or info.get("version") or "001.000")
    names = {
        "familyName": family,
        "styleName": full[len(family):].strip() or "Regular",
        "uniqueFontIdentifier": f"{ps_name};{version};converted from Type 1",
        "fullName": full,
        "psName": ps_name,
        "version": version,
    }
    # The foundry's notice is carried over, because this is a licensed face and a copy of
    # it that does not say whose it is invites being taken for one nobody owns.
    if info.get("Notice"):
        names["copyright"] = info["Notice"]
    builder.setupNameTable(names)
    # Bold and italic are each said twice in OpenType, in OS/2 and again in head, and a
    # face that says one and not the other is a face some readers will embolden or slant
    # for themselves on top of the weight it already has. Only a face whose weight is
    # actually Bold is flagged bold: Nord and Compact are heavier than the family's bold
    # and are still not it, so they are a weight and not a style.
    is_bold = weight.strip().lower() == "bold"
    is_italic = float(info.get("ItalicAngle", 0)) != 0
    selection = 0
    if is_bold:
        selection |= 1 << 5
    if is_italic:
        selection |= 1 << 0
    if not selection:
        selection = 1 << 6
    builder.setupOS2(
        sTypoAscender=ascent,
        sTypoDescender=descent,
        sCapHeight=round(float(info.get("CapHeight", 0.7 * upem))),
        usWeightClass=WEIGHT_CLASSES.get(weight.strip().lower(), 400),
        fsSelection=selection,
    )
    builder.setupPost(italicAngle=float(info.get("ItalicAngle", 0)))
    builder.font["head"].macStyle |= (1 if is_bold else 0) | (2 if is_italic else 0)

    # Written as a feature file because that is the form the pairs have to reach GPOS in,
    # and feaLib is what compiles one. Both language systems are declared because a pair
    # filed only under DFLT is a pair some shapers will not look for in Latin text, and
    # digits are Latin text as far as this question goes; the kern feature itself is on by
    # default in fontspec, so nothing at the LaTeX end has to ask for it.
    if pairs:
        rules = "".join(f"    pos {a} {b} {v};\n" for (a, b), v in pairs.items())
        feature = ("languagesystem DFLT dflt;\nlanguagesystem latn dflt;\n\n"
                   f"feature kern {{\n{rules}}} kern;\n")
        builder.addOpenTypeFeatures(feature)

    builder.save(str(otf_path))
    kerned = ", ".join(f"{a}+{b} {v}" for (a, b), v in pairs.items()) or "none"
    print(f"{otf_path.name}: {len(order)} glyph(s), {upem} units per em, "
          f"from {pfb_path.name}")
    print(f"  redrawn: {', '.join(redrawn) or 'none'}; kerning: {kerned}")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
