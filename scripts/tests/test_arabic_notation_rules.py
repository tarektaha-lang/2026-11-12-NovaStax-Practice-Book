"""Arabic-notation typesetting rules: functions, sets, clocks, products, alef."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from src.arabic_math import (  # noqa: E402
    FONT_MATH_CODEPOINTS,
    LETTERS,
    NUMBER_SETS,
    STYLE_STARTS,
    letter_macros,
    letters_for_tex,
    macros_tex,
)
from src.latex import arabic_maths, arabic_notation, compile_pdf, preamble  # noqa: E402
from src.prompting import Profile  # noqa: E402
from src.translation_quality import text_problems  # noqa: E402


ARABIC = {"language_code": "ar", "notation": "arabic", "numerals": "western"}
RTL_ARABIC = Profile("ar", "arabic", "arabic")
PROMPTS = ROOT / "prompts" / "translate"


class ArabicNotationRuleTests(unittest.TestCase):
    def test_prompts_state_the_rules(self) -> None:
        notation = (PROMPTS / "notation" / "arabic.md").read_text(encoding="utf-8")
        self.assertIn(r"\text{د}(\theta)", notation)
        self.assertIn(r"\mathbb{R}", notation)
        self.assertIn(r"\times", notation)
        self.assertIn(r"\parallel", notation)
        self.assertIn(r"\overline{ا ب}", notation)
        self.assertNotIn(r"\overline{أب}", notation)
        self.assertIn(r"\ln", notation)
        self.assertIn(r"\text{لو}_هـ", notation)
        self.assertIn(r"\operatorname{Re}", notation)
        self.assertIn(r"\text{ح}", notation)
        self.assertIn(r"\text{تخ}", notation)
        self.assertIn(r"\text{سعة}", notation)
        self.assertIn(r"\text{لو}", notation)
        self.assertIn(r"{}^n ق_r", notation)
        self.assertIn(r"\blank", notation)
        self.assertIn("Never drop", notation)
        self.assertIn(r"\setminus", notation)
        self.assertIn("Do not replace `\\cdot` with `\\times`", notation)
        for name in ("exercises.md", "specs.md", "tikz.md"):
            text = (PROMPTS / name).read_text(encoding="utf-8")
            self.assertIn(r"\times", text, name)
            self.assertIn(r"\parallel", text, name)
        self.assertIn("one space", (PROMPTS / "glossary_merge.md").read_text(encoding="utf-8"))
        self.assertIn("ا ب", (PROMPTS / "terminology.md").read_text(encoding="utf-8"))

    def test_text_command_is_not_rewritten_and_a_bare_letter_is(self) -> None:
        kept = arabic_maths(r"$\text{د}(\theta)$")
        self.assertIn(r"\text{د}", kept)
        self.assertNotIn(r"\dal", kept)
        bare = arabic_maths("$د$")
        self.assertIn(r"\dal", bare)
        self.assertNotIn("أ", arabic_maths("$أ$"))
        self.assertIn(r"\alef", arabic_maths("$أ$"))

    def test_real_and_imaginary_parts_become_text_abbreviations(self) -> None:
        real = arabic_maths(r"$\operatorname{Re}(z) = -3$")
        self.assertIn(r"\text{ح}", real)
        self.assertNotIn("Re", real)
        imag = arabic_maths(r"$\operatorname{Im}\left(z\right) = 4$")
        self.assertIn(r"\text{تخ}", imag)
        self.assertNotIn("Im", imag)
        self.assertIn(r"\text{ح}", arabic_maths(r"$\Re(z)$"))
        self.assertIn(r"\text{تخ}", arabic_maths(r"$\mathrm{Im}(z)$"))
        named = arabic_maths(r"$(\operatorname{حقيقي}(ع))^2 - (\operatorname{تخيلي}(ع))^2$")
        self.assertIn(r"\text{ح}", named)
        self.assertIn(r"\text{تخ}", named)
        self.assertNotIn(r"\operatorname", named)
        arg = arabic_maths(r"$\operatorname{Arg}(z) + \operatorname{سعة}(ع)$")
        self.assertIn(r"\text{سعة}", arg)
        self.assertNotIn(r"\operatorname", arg)

    def test_number_sets_become_the_font_letters(self) -> None:
        self.assertEqual(NUMBER_SETS["N"], "tah")
        self.assertEqual(NUMBER_SETS["C"], "kaf")
        result = arabic_maths(r"$\theta \in \mathbb{R}^+$")
        self.assertIn(r"\haht^+", result)
        self.assertNotIn(r"\mathbb", result)
        self.assertIn(r"\sadt", arabic_maths(r"$\mathbb{Z}$"))
        self.assertIn(r"\noont", arabic_maths(r"$\mathbb{Q}$"))
        self.assertIn(r"\tah", arabic_maths(r"$\mathbb{N}$"))
        self.assertIn(r"\kaf", arabic_maths(r"$\mathbb{C}$"))

    def test_macro_table_matches_the_font_cmap(self) -> None:
        from fontTools.ttLib import TTFont

        font = TTFont(ROOT / "fonts" / "NagwaArabicMath-12-25.otf")
        cmap = set(font.getBestCmap())
        expected = {
            (f"{name}{suffix}", start + index)
            for suffix, start in STYLE_STARTS
            for index, name in enumerate(LETTERS)
            if (start + index) in cmap
        }
        self.assertEqual(set(letter_macros()), expected)
        self.assertEqual(
            {code for _name, code in letter_macros()},
            {code for code in FONT_MATH_CODEPOINTS if any(
                code == start + index
                for _suffix, start in STYLE_STARTS
                for index in range(len(LETTERS))
            )},
        )
        names = {name for name, _code in letter_macros()}
        for missing in ("alefi", "dali", "wawi", "zaini", "tahi", "rehi", "thali", "zahi",
                        "dotlessbehi", "dotlessnooni", "dotlessfehi", "dotlessqafi"):
            self.assertNotIn(missing, names)
        tex = macros_tex()
        self.assertIn(r"\let\heh=\hehi", tex)
        self.assertIn(r"\let\jeem=\jeemi", tex)
        self.assertIn(r"\Umathchardef\arrad", tex)
        self.assertIn(r"\haht", tex)
        joined = letters_for_tex("اـ")
        self.assertIn(r"\alef", joined)
        self.assertNotIn(r"\alefi", joined)
        self.assertIn(r"\ain ", letters_for_tex("عـ"))
        self.assertNotIn(r"\aini", letters_for_tex("عـ"))
        self.assertIn(r"\ain '", letters_for_tex("عـَ"))
        self.assertIn(r"\seen '", letters_for_tex("سَ"))
        self.assertIn(r"\hehi ", letters_for_tex("هـو"))
        self.assertIn(r"\waw ", letters_for_tex("هـو"))
        self.assertIn(r"\jeemi ", letters_for_tex("جـ"))
        self.assertIn(r"\hehi ", letters_for_tex("هـ"))
        self.assertEqual(letters_for_tex(r"\text{سَ}"), r"\text{سَ}")

    def test_validators_reject_a_bare_function_a_bare_set_cdot_and_hamza(self) -> None:
        bare_function = text_problems(
            r"$f(\theta) = \sin\theta$",
            r"$د(\theta) = \text{جا}\,\theta$",
            "stem",
            ARABIC,
        )
        self.assertTrue(any("function name" in item for item in bare_function))
        named = text_problems(
            r"$f(\theta) = \sin\theta$",
            r"$\text{د}(\theta) = \text{جتا}\,\theta$",
            "stem",
            ARABIC,
        )
        self.assertFalse(any("function name" in item for item in named))
        defined = text_problems(
            r"$L(t) = 3$ and $L(2)$",
            r"$\text{ر}(ت) = 3$ and $ر(2)$",
            "stem",
            ARABIC,
        )
        self.assertTrue(any("function name" in item for item in defined))
        bare_set = text_problems(
            r"$n \in \mathbb{R}^+$",
            r"$ن \in ح^+$",
            "stem",
            ARABIC,
        )
        self.assertTrue(any("mathbb" in item for item in bare_set))
        kept_set = text_problems(
            r"$n \in \mathbb{R}^+$",
            r"$ن \in \mathbb{R}^+$",
            "stem",
            ARABIC,
        )
        self.assertFalse(any("mathbb" in item for item in kept_set))
        product = text_problems(r"$2 \cdot 3$", r"$2 \cdot 3$", "stem", ARABIC)
        self.assertFalse(any("times" in item for item in product), product)
        invented = text_problems(r"$2 \times 3$", r"$2 \cdot 3$", "stem", ARABIC)
        self.assertTrue(any("times" in item for item in invented), invented)
        rewritten = text_problems(r"$2 \cdot 3$", r"$2 \times 3$", "stem", ARABIC)
        self.assertTrue(any("changed beyond words" in item for item in rewritten), rewritten)
        vectors = text_problems(
            r"$\vec{a} \cdot \vec{b}$",
            r"$\vec{ا} \cdot \vec{ب}$",
            "stem",
            ARABIC,
        )
        self.assertFalse(any("times" in item for item in vectors))
        hamza = text_problems(r"$\overline{AB}$", r"$\overline{أب}$", "stem", ARABIC)
        self.assertTrue(any("أ" in item for item in hamza))
        choice = text_problems("Choose (A).", "اختر (أ).", "answer", ARABIC)
        self.assertFalse(any("أ إ آ" in item for item in choice))

    def test_products_stay_and_clocks_are_rewritten(self) -> None:
        product = arabic_maths(r"$2 \cdot 3$")
        self.assertIn(r"\cdot", product)
        self.assertNotIn(r"\times", product)
        dotted = arabic_maths(r"$\vec{a} \cdot \vec{b}$")
        self.assertIn(r"\cdot", dotted)
        units = arabic_maths(r"$100\text{ kg}\cdot\text{m/sec}$")
        self.assertIn(r"\cdot", units)
        self.assertNotIn(r"\times", units)
        folded = arabic_maths(r"$180\text{ نيوتن}\times\text{سم}$")
        self.assertIn(r"\cdot", folded)
        self.assertNotIn(r"\times", folded)
        numbers = arabic_maths(r"$15 \times 60$")
        self.assertIn(r"\times", numbers)
        sci = arabic_maths(r"$2 \times 10^7\text{ جول}$")
        self.assertIn(r"\times", sci)
        cross = arabic_maths(r"$\vec{ق} \times \vec{ف}$")
        self.assertIn(r"\times", cross)
        limit = arabic_maths(r"$\text{نها}_{س \to جـ} د(س)$")
        self.assertIn(r"\to", limit)
        self.assertNotIn(r"\leftarrow", limit)
        clock = arabic_notation(r"$3{:}00\text{ a.m.}$")
        self.assertNotIn("{:}", clock)
        self.assertIn("٣:٠٠", clock)
        self.assertIn("a.m.", clock)
        self.assertNotIn(r"\text", clock)

    @unittest.skipUnless(shutil.which("lualatex") and shutil.which("pdftotext"), "needs lualatex")
    def test_a_clock_inside_a_formula_reads_hour_then_minutes(self) -> None:
        body = (
            preamble(RTL_ARABIC)
            + "\n\\begin{document}\n"
            + arabic_notation(r"$٣{:}٠٠$")
            + "\n\n"
            + arabic_notation(r"$س = ٣{:}٠٠$")
            + "\n\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            tex = Path(folder) / "clock.tex"
            tex.write_text(body, encoding="utf-8")
            compile_pdf(tex)
            xml = subprocess.run(
                ["pdftotext", "-bbox", str(tex.with_suffix(".pdf")), "-"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout
        words = [
            (node.text or "", float(node.attrib["xMin"]), float(node.attrib["yMin"]))
            for node in ET.fromstring(xml).iter()
            if node.tag.endswith("word")
        ]

        def is_hour(text: str) -> bool:
            return text.startswith("٣") or text.startswith("۳")

        def is_minutes(text: str) -> bool:
            return "٠٠" in text or text == "٠"

        prose = [row for row in words if is_hour(row[0]) and (":" in row[0] or "∶" in row[0])]
        self.assertTrue(prose, words)
        formula_hours = [row for row in words if is_hour(row[0]) and row not in prose]
        formula_minutes = [
            row for row in words
            if is_minutes(row[0]) and not is_hour(row[0]) and row[2] > 60
        ]
        self.assertTrue(formula_hours, words)
        self.assertTrue(formula_minutes, words)
        self.assertLess(min(row[1] for row in formula_hours), min(row[1] for row in formula_minutes))

    @unittest.skipUnless(shutil.which("lualatex") and shutil.which("pdftotext"), "needs lualatex")
    def test_a_thousands_thin_space_keeps_the_number_ltr(self) -> None:
        body = (
            preamble(RTL_ARABIC)
            + "\n\\begin{document}\n"
            + arabic_notation(r"$100\,000$")
            + "\n\n"
            + arabic_notation(r"$60\,000$")
            + "\n\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            tex = Path(folder) / "thousands.tex"
            tex.write_text(body, encoding="utf-8")
            compile_pdf(tex)
            xml = subprocess.run(
                ["pdftotext", "-bbox", str(tex.with_suffix(".pdf")), "-"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout
        words = [
            (node.text or "", float(node.attrib["xMin"]), float(node.attrib["yMin"]))
            for node in ET.fromstring(xml).iter()
            if node.tag.endswith("word")
        ]
        digits = [
            row for row in words
            if row[0] and all(ch in "٠١٢٣٤٥٦٧٨٩0123456789 ٬," for ch in row[0])
        ]
        self.assertTrue(digits, words)
        top = min(row[2] for row in digits)
        bottom = max(row[2] for row in digits)

        def compact(band: float) -> str:
            parts = sorted(
                (row for row in digits if abs(row[2] - band) < 8),
                key=lambda row: row[1],
            )
            return "".join(row[0] for row in parts).replace(" ", "").replace("٬", "").replace(",", "")

        self.assertEqual(compact(top), "١٠٠٠٠٠", words)
        self.assertEqual(compact(bottom), "٦٠٠٠٠", words)


if __name__ == "__main__":
    unittest.main()
