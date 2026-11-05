#!/usr/bin/env python3
"""Set a book's front matter: its cover, its title page, its copyright page and its contents.

The first stage, and one that reads nothing any other stage wrote: everything on
these pages is on the <book> element of the table of contents and the modules under
it, or is the cover illustration filed beside it, so they can be set the moment a
book's table of contents is in. Nothing here calls a model, so the pages can be set
again as often as the look of them is being worked on, for the cost of a render and
three compiles.

The cover and the title page are drawn by cover.py, as SVG rendered to PDF; the two
pages after them are set in LaTeX, in the faces and on the page the papers themselves
are set on — STIX Two for an English book, Noto Naskh Arabic for an Arabic one, A4 with
the papers' margin all round — so that a reader turning from the contents to a paper
sees one book. latex.py owns the page and the type; this stage says only what goes on
them.

The copyright page is the notice and the permission line, in the book's language,
standing at the foot of the page above the margin, where a copyright page keeps them.
The year is the wording's, in the book's digits.

The table of contents is the modules of the table of contents as a list: each chapter
in bold, its sections indented a step under it, and any deeper module a step further,
each with its number and its title as the paper prints them. There are no page numbers
on it: this stage runs before the papers are set and cannot know where in the book a
section will fall, and a book read on a screen is reached through its bookmarks. It
runs to as many pages as it needs.

The front matter is the front of the book, and its pages are numbered as such: the
cover is page 1, the title page 2, the copyright page 3 and the contents page 4 and
on, and the first paper follows. Every page but the cover carries the papers' foot —
the line in white that names the book and says "Page n of m" — so a reader who drags
across the foot of any page inside the cover learns where they are. The title page is
drawn without it and has it laid over afterwards, as a page of LaTeX with nothing on
it but the foot, so that the foot is real text in the book's own face and not an
outline of it. How long the book runs to is not known until the papers are set, so
these pages are stamped with the book's total the way the papers are: papers.py counts
the book through, front matter first, and sends whichever pages carry the wrong
numbers back to the engine.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import cover
    import src.latex as latex
    from cover import ARABIC_DIGITS, FRONTMATTER_STAGE, read_wording, wording_of
    from src.prompting import Profile
    from pypdf import PdfReader, PdfWriter
    from sources import OUTPUT_DIR, PROJECT_ROOT, book_metadata, book_root
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# The pages, in the order they stand in the book, by the name each is filed under:
# <book_id>_<name>.pdf in the stage folder. The cover is the one page with no foot;
# the title page's foot is a document of its own laid over the drawn page.
COVER = "cover"
TITLE_PAGE = "title_page"
TITLE_FOOT = "title_foot"
COPYRIGHT = "copyright"
CONTENTS = "contents"
# The pages whose numbers papers.py may have to stamp again, in the book's order.
NUMBERED = (TITLE_FOOT, COPYRIGHT, CONTENTS)

# The copyright notice is set as the body of the book is, at its size and on its
# leading, rather than as small print. Set ragged, against the margin the book's
# language starts from.
NOTICE_SIZE = latex.BODY_SIZE
NOTICE_LEADING = round(latex.BODY_LEADING)
# The gap between the notice and the permission line, one line.
NOTICE_GAP = f"{NOTICE_LEADING}pt"

# The contents' heading is set as a paper's title is, at the section head's size and
# weight and in capitals, with the section head's gap under it, so the contents open
# as a section does.
CONTENTS_HEAD_SIZE = latex.SECTION_HEAD_SIZE
CONTENTS_HEAD_LEADING = latex.SECTION_HEAD_LEADING
CONTENTS_HEAD_GAP = latex.SECTION_HEAD_GAP

# How far each level of the contents stands in from the one above it: the step the
# papers' grid gives a number, so the sections under a chapter start where the wording
# of an exercise does. The air above a chapter, and none between its sections, is what
# lets the eye take a chapter and its sections as one group.
CONTENTS_STEP_MM = latex.NUMBER_ROOM_MM
CHAPTER_GAP = f"{latex.BODY_LEADING / 2:g}pt"

# An entry is a list of one item, because a list is what babel turns round for an
# Arabic book: its margin is on the side the book starts from, and a title that wraps
# returns to that margin and not to the page's. Every space a list would add above,
# below and between items is taken out, so an entry costs its lines and nothing else;
# the chapter's air is added by hand where it is wanted.
CONTENTS_PREAMBLE = f"""\\newlength{{\\contentsstep}}
\\setlength{{\\contentsstep}}{{{CONTENTS_STEP_MM:g}mm}}
\\newcommand{{\\contentsentry}}[4]{{%
\\settowidth{{\\labelwidth}}{{#4\\labelnumber{{#2}}}}%
\\begin{{list}}{{}}{{\\setlength{{\\labelsep}}{{0.33em}}%
\\setlength{{\\leftmargin}}{{\\dimexpr #1\\contentsstep+\\labelwidth+\\labelsep\\relax}}%
\\setlength{{\\itemindent}}{{0pt}}%
\\setlength{{\\topsep}}{{0pt}}\\setlength{{\\partopsep}}{{0pt}}\\setlength{{\\parsep}}{{0pt}}%
\\setlength{{\\itemsep}}{{0pt}}\\setlength{{\\listparindent}}{{0pt}}%
\\def\\makelabel##1{{\\hfill##1}}}}%
\\item[#4\\labelnumber{{#2}}] #3\\end{{list}}}}
\\newcommand{{\\contentschapter}}[2]{{\\Needspace{{3\\baselineskip}}\\addvspace{{{CHAPTER_GAP}}}%
\\contentsentry{{0}}{{#1}}{{\\bfseries #2}}{{\\bfseries}}}}
\\newcommand{{\\contentssection}}[3]{{\\contentsentry{{#1}}{{#2}}{{#3}}{{}}}}
\\newcommand{{\\contentshead}}[1]{{\\noindent{{\\fontsize{{{CONTENTS_HEAD_SIZE}}}{{{CONTENTS_HEAD_LEADING}}}\\selectfont%
\\bfseries\\MakeUppercase{{#1}}}}\\par\\nopagebreak\\vspace{{{CONTENTS_HEAD_GAP}}}}}
"""


@dataclass(frozen=True)
class Entry:
    """One module of the table of contents: how deep it is, its number and its title."""

    depth: int
    number: str
    title: str


@dataclass(frozen=True)
class Numbered:
    """One LaTeX page or pages of the front matter, and where in the book it stands."""

    name: str
    tex: Path
    first: int
    pages: int


@dataclass(frozen=True)
class FrontMatter:
    """A book's front matter as it stands on disk: its pages, in the book's order."""

    book_id: str
    cover: Path
    title_page: Path
    copyright: Path
    contents: Path
    contents_title: str
    numbered: tuple[Numbered, ...]

    @property
    def pages(self) -> int:
        """How many pages the front matter runs to, the cover counted."""
        return 1 + sum(page.pages for page in self.numbered)

    @property
    def bound(self) -> list[tuple[Path, str]]:
        """The PDFs in the order they are bound, each with its bookmark if it has one."""
        return [
            (self.cover, ""),
            (self.title_page, ""),
            (self.copyright, ""),
            (self.contents, self.contents_title),
        ]


# --- What the pages say ------------------------------------------------------------


def stage_dir(book_id: str) -> Path:
    return OUTPUT_DIR / book_id / FRONTMATTER_STAGE


def page_path(book_id: str, name: str, suffix: str = ".pdf") -> Path:
    return stage_dir(book_id) / f"{book_id}_{name}{suffix}"


def entries(root: ET.Element) -> list[Entry]:
    """Every module of the table of contents in the book's order, with its full number.

    A module's number is its own under its parent's, as the papers print it: 1.3 is
    the third section of the first chapter. Depth counts from the chapters at zero.
    """
    found: list[Entry] = []

    def walk(parent: ET.Element, above: list[str], depth: int) -> None:
        for module in parent:
            if module.tag != "module":
                continue
            numbers = above + [module.get("number", "?")]
            found.append(Entry(depth, ".".join(numbers), module.get("title", "").strip()))
            walk(module, numbers, depth + 1)

    walk(root, [], 0)
    return found


def contents_heading(profile: Profile, wording: dict) -> str:
    lang = profile.language_code
    return wording_of(wording, ("table_of_contents", lang), f"contents heading in {lang}")


def preamble(profile: Profile) -> str:
    """The page, the type and the foot of the papers, and the contents' own commands.

    The foot is the papers' own, from latex.py, so the line reads the same on every
    page inside the cover; xcolor is what paints it white, and the direction preamble
    is what teaches an Arabic-digit book to print the page numbers in its digits.
    """
    return (
        latex.PAGE_PREAMBLE
        + latex.type_preamble(profile)
        + latex.LEADING_PREAMBLE
        + "\\usepackage{xcolor}\n"
        + latex.direction_preamble(profile)
        + latex.foot_style(profile)
        + CONTENTS_PREAMBLE
    )


def document(profile: Profile, meta: dict[str, str], first: int, total: int, body: str) -> str:
    """One page or pages of the front matter, numbered from `first` of `total`."""
    return (
        preamble(profile)
        + latex.foot_tex(meta, first, total, profile)
        + "\\begin{document}\n"
        + body
        + "\\end{document}\n"
    )


def number_tex(number: str, profile: Profile) -> str:
    """A module's number as \\labelnumber prints it, without the closing point."""
    if profile.indic_digits:
        return latex.arabic_section_number(number)
    return latex.escape_plain(number)


def prose_tex(text: str, profile: Profile) -> str:
    return latex.maybe_stix_digits(
        latex.escape_plain(latex.written_for(text, profile)), profile
    )


def title_foot_body() -> str:
    """A page with nothing on it but the foot, to lay over the drawn title page."""
    return "\\null\n"


def copyright_body(profile: Profile, wording: dict) -> str:
    """The copyright page: the notice and the permission line at the foot of the page."""
    lang = profile.language_code
    year = wording_of(wording, ("copyright_year",), "copyright year")
    if profile.indic_digits:
        year = year.translate(ARABIC_DIGITS)
    notice = wording_of(wording, ("copyright_notice", lang), f"copyright notice in {lang}")
    permission = wording_of(wording, ("copyright_permission", lang), f"permission line in {lang}")
    # \raggedright in either direction: in a right-to-left paragraph LuaTeX's left is the
    # side the line starts from, so the one command leaves the end of the line ragged
    # whichever way the book reads, and \raggedleft would set an Arabic page flush left.
    return (
        "\\null\\vfill\n"
        f"{{\\fontsize{{{NOTICE_SIZE}pt}}{{{NOTICE_LEADING}pt}}\\selectfont\\raggedright\n"
        f"{prose_tex(notice.replace('{year}', year), profile)}\\par\n"
        f"\\vspace{{{NOTICE_GAP}}}\n"
        f"{prose_tex(permission, profile)}\\par}}\n"
    )


def contents_body(root: ET.Element, profile: Profile, wording: dict) -> tuple[str, int]:
    """The table of contents, and how many entries it lists."""
    lines = [f"\\contentshead{{{latex.escape_plain(contents_heading(profile, wording))}}}"]
    listed = entries(root)
    last = len(listed) - 1
    for index, entry in enumerate(listed):
        number, title = number_tex(entry.number, profile), latex.heading_tex(
            entry.title, profile, uppercase=False
        )
        if index == last - 1:
            lines.append(r"\Needspace{2\baselineskip}")
        if index == last:
            lines.append(r"\nopagebreak")
        if entry.depth == 0:
            lines.append(f"\\contentschapter{{{number}}}{{{title}}}")
        else:
            lines.append(f"\\contentssection{{{entry.depth}}}{{{number}}}{{{title}}}")
    return "\n".join(lines) + "\n", len(listed)


# --- The title page's foot ------------------------------------------------------------


def lay_foot(book_id: str) -> Path:
    """The title page with the book's foot laid over it.

    The page is rendered again from its SVG, which never has a foot, and the foot's
    page put over it; so the same call after the foot has been stamped with new numbers
    gives the page one foot and not two.
    """
    plain = cover.render(page_path(book_id, TITLE_PAGE, ".svg"))
    foot = PdfReader(page_path(book_id, TITLE_FOOT)).pages[0]
    writer = PdfWriter(clone_from=plain)
    writer.pages[0].merge_page(foot, over=True)
    fresh = plain.with_suffix(".footed.pdf")
    writer.write(fresh)
    writer.close()
    os.replace(fresh, plain)
    return plain


# --- What is on disk, for the stage that numbers the book -------------------------------


def front_matter(book_id: str) -> FrontMatter | None:
    """A book's front matter as it stands, or None if it has not been set.

    Read by papers.py, which counts the book through from here: the page a paper
    starts on is the page after the front matter, and the total every foot says is
    the front matter's pages and the papers'. Page counts are read from the PDFs, so
    they are the bound book's.
    """
    paths = {name: page_path(book_id, name) for name in (COVER, TITLE_PAGE, COPYRIGHT, CONTENTS)}
    if not all(path.is_file() for path in paths.values()):
        return None
    if not all(page_path(book_id, name, ".tex").is_file() for name in NUMBERED):
        return None
    profile = Profile.of(book_metadata(book_id))
    heading = contents_heading(profile, read_wording())
    numbered = []
    first = 2
    for name in NUMBERED:
        pdf = paths[TITLE_PAGE] if name == TITLE_FOOT else paths[name]
        pages = len(PdfReader(pdf).pages)
        numbered.append(Numbered(name, page_path(book_id, name, ".tex"), first, pages))
        first += pages
    return FrontMatter(
        book_id, paths[COVER], paths[TITLE_PAGE], paths[COPYRIGHT], paths[CONTENTS],
        heading, tuple(numbered),
    )


def stamp(front: FrontMatter, total: int) -> list[str]:
    """Put the book's total in the foot of every front-matter page that says otherwise.

    Returns a line for each page that would not come back from the engine, and never
    raises: the book is still bound.
    """
    failures = []
    for page in front.numbered:
        if latex.page_stamp(page.tex) == (page.first, total):
            continue
        try:
            latex.stamp_pages(page.tex, page.first, total)
            if page.name == TITLE_FOOT:
                lay_foot(front.book_id)
        except RuntimeError as error:
            failures.append(f"  {page.name}: {error}")
    return failures


# --- The stage -----------------------------------------------------------------------


def set_front_matter(book_id: str) -> tuple[FrontMatter, cover.Cover, int]:
    """Set a book's front matter, saying what came out and how many modules it lists."""
    print("looking for the LaTeX engine", flush=True)
    latex.require_latex()
    meta = book_metadata(book_id)
    profile = Profile.of(meta)
    wording = read_wording()
    root = book_root(book_id)

    print(f"drawing the cover and the title page of {book_id}", flush=True)
    drawn = cover.draw_cover(book_id)

    # The contents are set first because how many pages they run to is what the other
    # pages' "of m" depends on until the papers are in; until then m is the front
    # matter's own count.
    body, count = contents_body(root, profile, wording)
    print(f"setting the table of contents, {count} entries", flush=True)
    contents_tex = page_path(book_id, CONTENTS, ".tex")
    contents_tex.write_text(document(profile, meta, 4, 4, body), encoding="utf-8")
    latex.compile_pdf(contents_tex)
    total = 3 + len(PdfReader(contents_tex.with_suffix(".pdf")).pages)
    if total != 4:
        latex.stamp_pages(contents_tex, 4, total)

    print("setting the copyright page", flush=True)
    copyright_tex = page_path(book_id, COPYRIGHT, ".tex")
    copyright_tex.write_text(
        document(profile, meta, 3, total, copyright_body(profile, wording)), encoding="utf-8"
    )
    latex.compile_pdf(copyright_tex)

    print("laying the foot over the title page", flush=True)
    foot_tex = page_path(book_id, TITLE_FOOT, ".tex")
    foot_tex.write_text(document(profile, meta, 2, total, title_foot_body()), encoding="utf-8")
    latex.compile_pdf(foot_tex)
    lay_foot(book_id)

    front = front_matter(book_id)
    if front is None:  # pragma: no cover - everything above has just been written
        raise RuntimeError("the front matter did not all come out")
    return front, drawn, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. eg_mathematics_en_g7_t1")
    args = parser.parse_args()

    try:
        front, drawn, count = set_front_matter(args.book_id)
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1

    contents_pages = front.numbered[-1].pages
    print(f"OK: {front.pages} pages of front matter under {stage_dir(args.book_id).relative_to(PROJECT_ROOT)}")
    print(f"    cover, painted in {', '.join(drawn.palette)}, reading:")
    for role, words in drawn.lines.items():
        print(f"      {role:<8} {words}")
    print(f"    title page, with the foot laid over it; the cover on its guides as {drawn.guides.name}")
    print("    copyright page")
    print(f"    table of contents, {count} entries on {contents_pages} page(s)")
    print(f"    numbered 1 to {front.pages}; the first paper is page {front.pages + 1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
