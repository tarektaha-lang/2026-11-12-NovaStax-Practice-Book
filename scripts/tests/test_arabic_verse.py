from __future__ import annotations

import sys
import shutil
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.arabic_verse import (  # noqa: E402
    TATWEEL,
    Poem,
    PoemRow,
    apply_kashida_to_lines,
    format_arabverse,
    insert_kashidas,
    poem_linear_text,
    poem_problems,
)
from src.latex import body_to_latex, begin_document, compile_pdf, preamble  # noqa: E402
from src.prompting import Profile  # noqa: E402
from specs import Excerpt, SpecReply, SectionSpec  # noqa: E402


VERTICAL = Poem(
    rows=[
        PoemRow(shatrs=["يا ليت كل مؤمن بعزها"]),
        PoemRow(shatrs=["يحبها حبي لها"]),
        PoemRow(shatrs=["نحبها من روحنا"]),
    ]
)
SPLIT = Poem(
    rows=[
        PoemRow(shatrs=["مصر التي في خاطري وفي فمي", "أحبّها من كل روحي ودمي"]),
        PoemRow(shatrs=["عيشوا كراما تحت ظل العلم", "تحيا لنا عزيزة في الأمم"]),
    ]
)
TRIANGLE = Poem(
    rows=[
        PoemRow(shatrs=["بني الحمى والوطن", "من منكم يحبها مثلي أنا"]),
        PoemRow(shatrs=["نحبها من روحنا"]),
        PoemRow(shatrs=["ونفتديها بالعزيز الأكرم", "من عمرنا وجهدنا"]),
    ]
)
FREE = Poem(
    kashida=False,
    rows=[
        PoemRow(shatrs=["سطر قصير"]),
        PoemRow(shatrs=["سطر أطول من سابقه بلا مد"]),
        PoemRow(shatrs=["سطر"]),
    ],
)


def _minimal_spec(**excerpt_kwargs) -> SectionSpec:
    return SectionSpec(
        role="teach",
        overview="arc",
        objectives=[{"text": "skill"}],
        prerequisites=[{"text": "prior"}],
        out_of_scope=[{"text": "later", "reason": "later"}],
        excerpts=[Excerpt(**excerpt_kwargs)],
    )


class VerseLayoutTests(unittest.TestCase):
    def test_vertical_has_no_ampersand(self) -> None:
        tex = format_arabverse(VERTICAL)
        self.assertIn(r"\begin{arabverse}{traditional}", tex)
        self.assertNotIn("&", tex)
        self.assertIn(r"\\", tex)
        self.assertIn("يا ليت كل مؤمن بعزها", tex)

    def test_vertical_split_pairs_with_ampersand(self) -> None:
        tex = format_arabverse(SPLIT)
        plain = tex.replace(TATWEEL, "")
        self.assertIn("مصر التي في خاطري وفي فمي", plain)
        self.assertIn("&", tex)
        self.assertIn("أحبّها من كل روحي ودمي", plain)
        self.assertNotIn(r"\multicolumn", tex)

    def test_triangle_centers_the_odd_shatr(self) -> None:
        tex = format_arabverse(TRIANGLE)
        plain = tex.replace(TATWEEL, "")
        self.assertIn(r"\multicolumn{2}{c}{", tex)
        self.assertIn("نحبها من روحنا", plain)
        self.assertIn("بني الحمى والوطن", plain)
        self.assertIn("&", tex)

    def test_free_verse_skips_tatweel(self) -> None:
        tex = format_arabverse(FREE)
        self.assertNotIn(TATWEEL, tex)
        self.assertNotIn("&", tex)
        self.assertIn("سطر قصير", tex)

    def test_kashida_inserts_into_allowed_slots(self) -> None:
        stretched = insert_kashidas("يحبها حبي لها", 4)
        self.assertIn(TATWEEL, stretched)
        self.assertEqual(stretched.count(TATWEEL), 4)
        self.assertTrue(stretched.replace(TATWEEL, "").startswith("يحبها") or "يحبها" in stretched)

    def test_kashida_equalizes_when_font_is_available(self) -> None:
        lines = apply_kashida_to_lines(
            ["يا ليت كل مؤمن بعزها", "يحبها حبي لها", "نحبها من روحنا"]
        )
        if all(TATWEEL not in line for line in lines):
            self.skipTest("kashida font not available")
        self.assertTrue(any(TATWEEL in line for line in lines[1:]))
        self.assertNotIn(TATWEEL, lines[0])


class VerseSchemaTests(unittest.TestCase):
    def test_old_poem_excerpt_without_rows_still_loads(self) -> None:
        excerpt = Excerpt.model_validate(
            {
                "type": "poem",
                "reuse": "as_is",
                "text": "مصر التي في خاطري وفي فمي :: أحبها من كل روحي ودمي",
            }
        )
        self.assertEqual(excerpt.rows, [])
        self.assertTrue(excerpt.kashida)

    def test_new_spec_poem_without_rows_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            SpecReply.model_validate(
                {
                    "status": "ok",
                    "abort_reason": "",
                    "spec": _minimal_spec(
                        type="poem",
                        reuse="as_is",
                        text="بيت",
                    ).model_dump(),
                }
            )

    def test_shatr_rejects_double_colon_and_tatweel(self) -> None:
        with self.assertRaises(ValidationError):
            PoemRow(shatrs=["صدر :: عجز"])
        with self.assertRaises(ValidationError):
            PoemRow(shatrs=["يحب" + TATWEEL + "ها"])

    def test_linear_text_joins_bayt_with_a_space(self) -> None:
        self.assertEqual(
            poem_linear_text(SPLIT.rows),
            "مصر التي في خاطري وفي فمي أحبّها من كل روحي ودمي\n"
            "عيشوا كراما تحت ظل العلم تحيا لنا عزيزة في الأمم",
        )

    def test_poem_marker_out_of_range_is_rejected(self) -> None:
        problems = poem_problems("[poem 2]", [VERTICAL], "group 1")
        self.assertTrue(any("poem 2" in problem for problem in problems))

    def test_poem_in_options_is_rejected(self) -> None:
        problems = poem_problems("اقرأ", [VERTICAL], "exercise", options=["[poem 1]"])
        self.assertTrue(any("choice" in problem for problem in problems))


class VerseLatexTests(unittest.TestCase):
    def test_poem_marker_is_substituted(self) -> None:
        tex = body_to_latex(
            "اقرأ الأبيات الآتية:\n[poem 1]",
            [],
            {},
            poems=[SPLIT.model_dump()],
        )
        self.assertIn(r"\begin{arabverse}{traditional}", tex)
        self.assertNotIn("[poem 1]", tex)
        self.assertIn("&", tex)

    def test_unmarked_poem_trails_the_wording(self) -> None:
        tex = body_to_latex(
            "اقرأ الأبيات الآتية:",
            [],
            {},
            poems=[FREE.model_dump()],
        )
        self.assertIn("اقرأ الأبيات الآتية", tex)
        self.assertIn(r"\begin{arabverse}{traditional}", tex)
        self.assertNotIn(TATWEEL, tex)

    @unittest.skipUnless(shutil.which("lualatex"), "arabverse compile needs lualatex")
    def test_arabverse_compiles_inside_qidline(self) -> None:
        profile = Profile("ar", "arabic", "arabic")
        verse = format_arabverse(TRIANGLE)
        body = (
            r"\qidline{group_1}{اقرأ الأبيات الآتية:"
            "\n"
            + verse
            + "}"
        )
        with tempfile.TemporaryDirectory() as folder:
            tex = Path(folder) / "poem.tex"
            tex.write_text(
                preamble(profile) + begin_document(profile) + body + "\n\\end{document}\n",
                encoding="utf-8",
            )
            compile_pdf(tex)
            self.assertTrue(tex.with_suffix(".pdf").is_file())
