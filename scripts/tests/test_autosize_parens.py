"""Matched round brackets in maths become \\left( \\right) when the paper is written."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.latex import (  # noqa: E402
    arabic_notation,
    autosize_brackets,
    autosize_round_parens,
    begin_document,
    compile_pdf,
    preamble,
    to_latex,
)
from src.prompting import Profile  # noqa: E402


ENGLISH = Profile("en", "western", "latin")


class AutosizeRoundParensTests(unittest.TestCase):
    def test_fraction_function_and_product_examples(self) -> None:
        self.assertEqual(
            to_latex(r"$(\frac{1}{6})$"),
            r"$\left(\frac{1}{6}\right)$",
        )
        self.assertEqual(
            to_latex(r"$\text{د}(\theta)$"),
            r"$\text{د}\left(\theta\right)$",
        )
        self.assertEqual(
            to_latex(r"$(س+2)(س-3)$"),
            r"$\left(س+2\right)\left(س-3\right)$",
        )

    def test_nested_pairs_are_both_sized(self) -> None:
        self.assertEqual(
            to_latex(r"$((a+b)c)$"),
            r"$\left(\left(a+b\right)c\right)$",
        )

    def test_already_sized_delimiters_stay(self) -> None:
        already = r"$\left(\frac{1}{6}\right)$"
        self.assertEqual(to_latex(already), already)
        big = r"$\bigl(a\bigr)\Bigl(b\Bigr)\biggl(c\biggr)\Biggl(d\Biggr)$"
        self.assertEqual(to_latex(big), big)
        self.assertEqual(
            autosize_round_parens(r"\left (x\right )"),
            r"\left (x\right )",
        )

    def test_tikz_coordinates_and_options_stay(self) -> None:
        picture = r"\begin{tikzpicture}\draw (0,0) circle (1.5cm);\end{tikzpicture}"
        self.assertEqual(autosize_round_parens(picture), picture)
        rendered = to_latex(picture)
        self.assertIn("(0,0)", rendered)
        self.assertIn("(1.5cm)", rendered)
        self.assertNotIn(r"\left", rendered)
        inside = to_latex(
            r"$x+\begin{tikzpicture}\draw (0,0) circle (1.5cm);\end{tikzpicture}+(a)$"
        )
        self.assertIn("(0,0)", inside)
        self.assertIn("(1.5cm)", inside)
        self.assertIn(r"\left(a\right)", inside)
        self.assertNotIn(r"\left(0,0)", inside)
        self.assertNotIn(r"\left(1.5cm)", inside)

    def test_text_mathrm_and_operatorname_keep_their_brackets(self) -> None:
        self.assertEqual(to_latex(r"$\text{(أ)}$"), r"$\text{(أ)}$")
        self.assertEqual(to_latex(r"$\mathrm{(x)}$"), r"$\mathrm{(x)}$")
        self.assertEqual(
            to_latex(r"$\operatorname{lcm}(n)$"),
            r"$\operatorname{lcm}\left(n\right)$",
        )

    def test_parens_in_rtl_text_hbox_are_not_sized(self) -> None:
        text_unit = r"\hbox{\textdir TRT\relax (كم)}"
        self.assertEqual(autosize_brackets(text_unit), text_unit)
        nested = (
            r"\hbox dir TRT{\hbox{\textdir TLT\relax $\text{المسافة}\ x$}"
            r"\hskip.333em\hbox{\textdir TRT\relax (كم)}}"
        )
        self.assertEqual(autosize_brackets(nested), nested)
        inner_math = r"\hbox{\textdir TLT\relax $(a+b)$}"
        self.assertEqual(
            autosize_brackets(inner_math),
            r"\hbox{\textdir TLT\relax $\left(a+b\right)$}",
        )

    def test_mixed_intervals_stay(self) -> None:
        self.assertEqual(autosize_round_parens(r"(a, b]"), r"(a, b]")
        self.assertEqual(autosize_round_parens(r"[a, b)"), r"[a, b)")
        self.assertEqual(autosize_brackets(r"]a، b، c["), r"]a، b، c[")
        self.assertEqual(autosize_brackets(r"]ab["), r"]ab[")
        for source in (r"$(a, b]$", r"$[a, b)$"):
            rendered = to_latex(source)
            self.assertNotIn(r"\left", rendered)
            self.assertNotIn(r"\right", rendered)

    def test_square_brackets_and_set_braces_are_sized(self) -> None:
        self.assertEqual(
            to_latex(r"$[\frac{\pi}{٢}، \frac{٣\pi}{٢}]$"),
            r"$\left[\frac{\pi}{٢}، \frac{٣\pi}{٢}\right]$",
        )
        self.assertEqual(
            to_latex(r"$\{١، ٢، ٣\}$"),
            r"$\left\{١، ٢، ٣\right\}$",
        )
        self.assertEqual(
            to_latex(r"$[\left(x\right)]$"),
            r"$\left[\left(x\right)\right]$",
        )

    def test_delimiters_grouping_and_optional_arguments_stay(self) -> None:
        self.assertEqual(autosize_brackets(r"\[ x \]"), r"\[ x \]")
        self.assertEqual(autosize_brackets(r"\( x \)"), r"\( x \)")
        self.assertEqual(to_latex(r"\[ x \]"), r"\[ x \]")
        self.assertEqual(to_latex(r"\( x \)"), r"\( x \)")
        self.assertEqual(autosize_brackets(r"{a}"), r"{a}")
        self.assertEqual(to_latex(r"$\sqrt[3]{8}$"), r"$\sqrt[3]{8}$")
        self.assertEqual(autosize_brackets(r"a\\[2pt]b"), r"a\\[2pt]b")
        self.assertNotIn(r"\left", to_latex(r"$a\\[2pt]b$"))

    def test_tikz_options_stay(self) -> None:
        picture = r"\begin{tikzpicture}[thick]\draw (0,0) -- (1,0);\end{tikzpicture}"
        self.assertEqual(autosize_brackets(picture), picture)
        self.assertEqual(autosize_brackets(r"\draw[thick] (0,0);"), r"\draw[thick] (0,0);")

    def test_mixed_intervals_are_reported(self) -> None:
        half: list[str] = []
        self.assertEqual(autosize_brackets(r"(a، b]", half), r"(a، b]")
        self.assertIn("(a، b]", half)
        other: list[str] = []
        self.assertEqual(autosize_brackets(r"[a، b)", other), r"[a، b)")
        self.assertIn("[a، b)", other)
        split: list[str] = []
        self.assertEqual(autosize_brackets(r"(a & b)", split), r"(a & b)")
        self.assertEqual(split, [])

    def test_pass_is_idempotent_and_never_doubles_sizing(self) -> None:
        samples = (
            r"$(\frac{1}{6})$",
            r"$[\frac{\pi}{٢}، \frac{٣\pi}{٢}]$",
            r"$\{١، ٢، ٣\}$",
            r"$((a+b)c)$",
            r"$[\left(x\right)]$",
            r"$\left(\frac{1}{6}\right)$",
            r"$\sqrt[3]{8}$",
            r"$]-٦،\allowbreak ٦[$",
            r"$[a، b[$",
            r"$]a، b]$",
        )
        for source in samples:
            once = to_latex(source)
            self.assertEqual(autosize_brackets(once), once)
            self.assertEqual(to_latex(once), once)
            self.assertNotRegex(once, r"\\left\\left|\\right\\right|\\left\\bigl")

    def test_pair_split_by_alignment_stays(self) -> None:
        self.assertEqual(autosize_round_parens(r"(a & b)"), r"(a & b)")
        self.assertEqual(autosize_round_parens(r"(a \\ b)"), r"(a \\ b)")
        rendered = to_latex(r"$$\begin{array}{cc}(a & b) \\ (c+d) & e\end{array}$$")
        self.assertIn("(a", rendered)
        self.assertIn("& b)", rendered)
        self.assertNotIn(r"\left(a", rendered)
        self.assertIn(r"\left(c+d\right)", rendered)

    def test_escaped_delimiters_are_not_themselves_sized(self) -> None:
        self.assertEqual(
            to_latex(r"\((\frac{1}{6})\)"),
            r"\(\left(\frac{1}{6}\right)\)",
        )

    def test_pass_follows_arabic_letter_macros(self) -> None:
        laid_out = arabic_notation(
            r"$(\frac{1}{6})$ and $\text{د}(\theta)$ and $(س+2)(س-3)$",
            convert_digits=False,
        )
        rendered = to_latex(laid_out)
        self.assertIn(r"\left(\frac{1}{6}\right)", rendered)
        self.assertIn(r"\text{د}\left(\theta\right)", rendered)
        self.assertIn(r"\left(", rendered)
        self.assertNotIn("(س+2)", rendered)
        self.assertNotIn("(س-3)", rendered)

    @unittest.skipUnless(shutil.which("lualatex"), "autosize compile needs lualatex")
    def test_reversed_intervals_are_sized(self) -> None:
        self.assertEqual(
            to_latex(r"$]-٦،\allowbreak ٦[$"),
            r"$\left]-٦،\allowbreak ٦\right[$",
        )
        self.assertEqual(autosize_brackets(r"]a، b["), r"\left]a، b\right[")
        self.assertEqual(autosize_brackets(r"[a، b["), r"\left[a، b\right[")
        self.assertEqual(autosize_brackets(r"]a، b]"), r"\left]a، b\right]")
        self.assertEqual(autosize_brackets(r"]a, b["), r"\left]a, b\right[")
        already = r"\left]a، b\right["
        self.assertEqual(autosize_brackets(already), already)
        found: list[str] = []
        self.assertEqual(autosize_brackets(r"]a، b[", found), r"\left]a، b\right[")
        self.assertEqual(found, [])
        once = to_latex(r"$]-٦،\allowbreak ٦[$")
        self.assertEqual(autosize_brackets(once), once)
        self.assertEqual(to_latex(once), once)
        self.assertNotRegex(once, r"\\left\\left|\\right\\right")

    def test_section_04_05_open_intervals(self) -> None:
        open_ends = r"]-\infty،\allowbreak \infty["
        sized_open = r"\left]-\infty،\allowbreak \infty\right["
        for question in (25, 27, 33, 34):
            with self.subTest(question=question):
                self.assertEqual(autosize_brackets(open_ends), sized_open)
        self.assertEqual(
            to_latex(r"$" + open_ends + r"$"),
            r"$" + sized_open + r"$",
        )
        half_open = {
            33: (r"[٠، \infty[", r"\left[٠، \infty\right["),
            75: (r"]-٣،\allowbreak ٣[", r"\left]-٣،\allowbreak ٣\right["),
            78: (r"]-٦،\allowbreak ٦[", r"\left]-٦،\allowbreak ٦\right["),
            79: (r"]-٤،\allowbreak ٤[", r"\left]-٤،\allowbreak ٤\right["),
        }
        for question, (source, want) in half_open.items():
            with self.subTest(question=question):
                self.assertEqual(autosize_brackets(source), want)
                self.assertEqual(to_latex(f"${source}$"), f"${want}$")
                self.assertEqual(autosize_brackets(want), want)
                self.assertNotRegex(want, r"\\left\\left|\\right\\right")

    def test_sized_fraction_compiles(self) -> None:
        body = to_latex(
            r"$(\frac{1}{6})$ and $[\frac{1}{2}]$ and $\{1, 2, 3\}$ and $((a+b)c)$"
        )
        with tempfile.TemporaryDirectory() as folder:
            tex = Path(folder) / "autosize.tex"
            tex.write_text(
                preamble(ENGLISH) + begin_document(ENGLISH) + body + "\n\\end{document}\n",
                encoding="utf-8",
            )
            compile_pdf(tex)
            self.assertTrue(tex.with_suffix(".pdf").is_file())
