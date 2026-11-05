from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from new_questions import source_artifact_problems  # noqa: E402
from src.arabic_math import QURAN_CLOSE, QURAN_OPEN  # noqa: E402
from src.print_digits import normalize_print_digits  # noqa: E402
from src.legacy_translation_checks import (  # noqa: E402
    MATH_SEGMENT,
    bump_lengthmark_offsets,
    excessive_tashkeel_problems,
    group_problems,
    map_mcq_answer,
    mark_placement_problems,
    option_text,
    merge_group,
    merge_spec,
    normalize_arabic_notation,
    legacy_normalize_profile_translation as normalize_profile_translation,
    normalize_translation,
    repair_excessive_tashkeel,
    repair_mark_placement,
    repair_stored_controls,
    split_english_gloss_problems,
    legacy_tikz_problems as tikz_problems,
    legacy_text_problems as text_problems,
    target_document_problems,
    spec_problems,
    symbolic_notation,
    translatable_spec,
    translatable_group,
    validate_group,
)


EN_META = {"language_code": "en", "notation": "latin", "numerals": "western"}
AR_META = {"language_code": "ar", "notation": "latin", "numerals": "western"}
ARABIC_MATH_META = {
    "language_code": "ar",
    "notation": "arabic",
    "numerals": "arabic",
}


def english_group() -> dict:
    return {
        "id": "group_7",
        "objective": "objective_1",
        "stem": (
            "The table below shows the scores. "
            "$$\\begin{array}{|c|c|}\\hline "
            "\\text{Upper limit} & \\text{Frequency} \\\\ \\hline 10 & 6 "
            "\\\\ \\hline\\end{array}$$ [figure 1]"
        ),
        "figures": [
            {
                "description": "An ascending cumulative-frequency graph.",
                "figure_type": "line graph",
                "graph": {"x_min": 0, "x_max": 10},
            }
        ],
        "exercises": [
            {
                "id": "exercise_14",
                "text": "The frequency is $\\blank$.",
                "options": [],
                "answer": "$6$.",
                "objectives": ["objective_1"],
                "word_problem": True,
            },
            {
                "id": "exercise_15",
                "text": "Which value equals $x + 2$?",
                "options": ["$3$", "$4$", "$5$", "$6$"],
                "answer": "B",
                "figures": [],
                "objectives": ["objective_1"],
                "word_problem": False,
            },
        ],
    }


def arabic_candidate() -> dict:
    return {
        "id": "group_7",
        "stem": (
            "يوضح الجدول التالي الدرجات. "
            "$$\\begin{array}{|c|c|}\\hline "
            "\\text{الحد الأعلى} & \\text{التكرار} \\\\ \\hline 10 & 6 "
            "\\\\ \\hline\\end{array}$$ [figure 1]"
        ),
        "figures": [{"description": "منحنى تكراري متجمع صاعد."}],
        "exercises": [
            {
                "id": "exercise_14",
                "text": "التكرار هو $\\blank$.",
                "options": [],
                "answer": "$6$.",
                "figures": [],
            },
            {
                "id": "exercise_15",
                "text": "أي قيمة تساوي $x + 2$؟",
                "options": ["$3$", "$4$", "$5$", "$6$"],
                "answer": "ب",
                "figures": [],
            },
        ],
    }


class TranslationQualityTests(unittest.TestCase):
    def test_actual_partial_group_7_reply_is_rejected(self) -> None:
        fixture = (
            Path(__file__).parent
            / "fixtures"
            / "translation"
            / "group_7_partial_reply.json"
        )
        reply = json.loads(fixture.read_text(encoding="utf-8"))
        problems = group_problems(english_group(), reply["group"], AR_META)
        said = " ".join(problems)
        self.assertIn("stem", said)
        self.assertIn("text", said)
        self.assertIn("id", said)

    def test_valid_arabic_group_passes_and_keeps_structure(self) -> None:
        source = english_group()
        candidate = arabic_candidate()
        validate_group(source, candidate, AR_META)
        merged = merge_group(source, candidate, AR_META)
        self.assertEqual(merged["objective"], "objective_1")
        self.assertEqual(
            merged["figures"][0]["graph"], {"x_min": 0, "x_max": 10}
        )
        self.assertEqual(
            merged["exercises"][0]["objectives"], ["objective_1"]
        )
        self.assertEqual(merged["exercises"][1]["answer"], "ب")

    def test_projection_contains_only_translatable_fields(self) -> None:
        projected = translatable_group(english_group())
        self.assertEqual(
            set(projected),
            {"id", "stem", "figures", "poems", "exercises"},
        )
        self.assertEqual(
            set(projected["exercises"][0]),
            {"id", "text", "options", "answer", "figures", "poems"},
        )
        self.assertNotIn("graph", projected["figures"][0])

    def test_english_prose_and_model_junk_are_rejected(self) -> None:
        candidate = arabic_candidate()
        candidate["exercises"][0]["text"] = (
            "$0$، clarity in thoughts and critical thinking."
        )
        problems = group_problems(english_group(), candidate, AR_META)
        self.assertTrue(any("English prose" in problem for problem in problems))

    def test_source_proper_names_and_ui_labels_may_stay_latin(self) -> None:
        source = (
            "Create pie charts using spreadsheet software such as Microsoft Excel. "
            "Go to the Insert menu, click Pie, and select the 2D icon."
        )
        target = (
            "أنشئ مخططات القطاعات باستخدام برامج الجداول مثل Microsoft Excel. "
            "اذهب إلى قائمة إدراج Insert، ثم انقر على دائري Pie، واختر الشكل ثنائي الأبعاد."
        )
        self.assertFalse(text_problems(source, target, "overview", AR_META))
        self.assertTrue(
            text_problems(
                "Minimization",
                "Minimization",
                "term_2.term",
                AR_META,
            )
        )

    def test_latin_arabic_comma_in_unit_list_matches_source_comma(self) -> None:
        units = {
            "entries": [
                {"kind": "unit", "source_math": "km", "math_text": "كم"},
                {"kind": "unit", "source_math": "m", "math_text": "م"},
            ]
        }
        self.assertFalse(
            text_problems(
                r"$\text{km}, \text{m}$",
                r"$\text{كم}، \text{م}$",
                "figure.description",
                AR_META,
                book_map=units,
            )
        )

    def test_trailing_blank_stays_prose(self) -> None:
        source = (
            "Complete the following statement: The product of two integers "
            r"with opposite signs is always a \blank integer."
        )
        target = (
            r"أكمل العبارة الآتية: حاصل ضرب عددين صحيحين مختلفي الإشارة "
            r"يكون دائمًا عددًا صحيحًا \blank."
        )
        self.assertEqual(normalize_translation(source, target), target)
        self.assertFalse(text_problems(source, target, "exercise_74.text", AR_META))

    def test_english_trailing_blank_is_not_a_math_island(self) -> None:
        prose = "A pentagon and a hexagon cannot be similar because they do not have the same \\blank."
        self.assertEqual(normalize_translation(prose, prose), prose)
        self.assertFalse(MATH_SEGMENT.findall(prose))
        self.assertFalse(text_problems(prose, prose, "exercise_3.text", EN_META))
        after_math = (
            "If a square and a rhombus each have a side length of "
            r"$7\text{ cm}$, the condition of similarity that fails is "
            r"that their corresponding angles are not \blank."
        )
        self.assertEqual(normalize_translation(after_math, after_math), after_math)
        self.assertEqual(MATH_SEGMENT.findall(after_math), [r"$7\text{ cm}$"])
        self.assertFalse(
            text_problems(after_math, after_math, "exercise_9.text", EN_META)
        )
        self.assertFalse(
            target_document_problems(
                {
                    "exercises": {
                        "groups": [
                            {
                                "id": "group_3",
                                "exercises": [
                                    {
                                        "id": "exercise_3",
                                        "text": prose,
                                        "answer": "number of sides",
                                        "options": [],
                                    }
                                ],
                            }
                        ]
                    }
                },
                EN_META,
            )
        )

    def test_ordinal_superscript_is_wording_not_math_structure(self) -> None:
        self.assertFalse(
            text_problems(
                r"The point is in the $3^{\text{rd}}$ quadrant.",
                r"النقطة في الربع $3$.",
                "exercise_37.text",
                AR_META,
            )
        )
        self.assertFalse(
            text_problems(
                r"The point is in the $3^{\text{rd}}$ quadrant.",
                r"النقطة في الربع $3^{\text{rd}}$.",
                "exercise_37.text",
                AR_META,
            )
        )
        problems = text_problems(
            r"The point is in the $3^{\text{rd}}$ quadrant.",
            r"النقطة في الربع $A$.",
            "exercise_37.text",
            AR_META,
        )
        self.assertTrue(any("mathematics" in row for row in problems))
        self.assertFalse(
            text_problems(
                r"$1^{\text{st}}\text{ quadrant}$",
                r"$\text{الربع الأول}$",
                "exercise_33.options[0]",
                AR_META,
            )
        )
        self.assertFalse(
            text_problems(
                r"$1^{\text{st}}\text{ quadrant}$",
                "الربع الأول",
                "exercise_33.options[0]",
                AR_META,
            )
        )
        mismatch = text_problems(
            r"$1^{\text{st}}\text{ quadrant}$",
            r"$\text{الربع الثاني}$",
            "exercise_33.options[0]",
            AR_META,
        )
        self.assertTrue(any("mathematics differs" in row for row in mismatch))
        self.assertFalse(
            text_problems(
                "Which point lies in the third quadrant of the plane?",
                "أي نقطة تقع في الربع الثالث من المستوى؟",
                "exercise_13.text",
                AR_META,
            )
        )
        self.assertFalse(
            text_problems(
                "First quadrant",
                "الربع الأول",
                "exercise_23.options[0]",
                AR_META,
            )
        )
        still_q = text_problems(
            r"The point is in the $3^{\text{rd}}$ quadrant.",
            r"النقطة في $Q3$.",
            "exercise_108.text",
            AR_META,
        )
        self.assertTrue(any("mathematics" in row for row in still_q))

    def test_equivalent_notation_is_the_same_mathematics(self) -> None:
        def passes(source: str, target: str, meta: dict = ARABIC_MATH_META, **kw) -> None:
            self.assertFalse(text_problems(source, target, "x", meta, **kw), (source, target))

        def fails(source: str, target: str, meta: dict = ARABIC_MATH_META, **kw) -> None:
            self.assertTrue(text_problems(source, target, "x", meta, **kw), (source, target))

        passes(
            r"$AD^2 = BD \cdot CD \implies 6^2 = 4x$",
            r"$(ا د)^2 = ب د \times جـ د \implies 6^2 = 4 س$",
        )
        fails(
            r"$\vec{a} \cdot \vec{b} = 6$",
            r"$\vec{أ} \times \vec{ب} = 6$",
        )
        passes(
            r"$\overleftrightarrow{AX} \parallel \overleftrightarrow{BY}$",
            r"$\overleftrightarrow{أ س} // \overleftrightarrow{ب ص}$",
        )
        passes(r"$\frac{\pi}{3}\text{ rad}$", r"$\frac{\pi}{3}^د$")
        passes(r"$\theta^{\text{rad}} = \frac{22}{7}\text{ rad}$", r"$\theta^د = \frac{22}{7}^د$")
        fails(r"$\frac{\pi}{3}\text{ rad}$", r"$\frac{\pi}{4}^د$")
        passes(
            r"$$\begin{array}{|c|c|} \text{Step 1} & \frac{5}{1 + 2i} \\ \end{array}$$",
            r"$$\begin{array}{|c|c|} \text{الخطوة } 1 & \frac{5}{1 + 2ت} \\ \end{array}$$",
        )
        fails(
            r"$$\begin{array}{|c|c|} \text{Step 1} & \frac{5}{1 + 2i} \\ \end{array}$$",
            r"$$\begin{array}{|c|c|} \text{الخطوة } 2 & \frac{5}{1 + 2ت} \\ \end{array}$$",
        )
        passes(
            r"$5\text{ cm}$, since common angle $C$ gives $\triangle CAD \sim \triangle CBA$",
            r"$5\text{ سم}$، و$\angle جـ$ زاوية مشتركة، فإن $\triangle جـ أ د \sim \triangle جـ ب أ$",
        )
        passes(
            r"then the radian measure of $\angle C$ is $\blank$.",
            r"فإن القياس الدائري للزاوية $ج$ هو $\blank$.",
        )
        fails(r"with common angle $C$", r"و$\angle C = 30^\circ$ زاوية مشتركة", AR_META)
        passes(
            r"$AB \times BD = R^2 - r^2 = 10^2 - 6^2 = 64$",
            r"$أ ب \times ب د = (نق_1)^2 - (نق_2)^2 = 10^2 - 6^2 = 64$",
        )
        fails(
            r"$AB \times BD = R^2 - r^2 = 10^2 - 6^2 = 64$",
            r"$أ ب \times ب د = (نق_1)^2 - (نق_2)^2 = 10^2 - 5^2 = 64$",
        )
        fails(r"$x_1 + x_2 = 5$", r"$س_1 + س_3 = 5$")

    def test_latin_unit_side_and_split_lists_are_layout(self) -> None:
        money = {
            "entries": [
                {"kind": "unit", "source_math": "EGP", "math_text": "جنيه", "plain_lemma": "جنيه"}
            ]
        }
        source = r"changes by $-\text{LE } 450$ on Monday; the net is $\text{LE } 0$."
        self.assertFalse(
            text_problems(
                source,
                r"تغير بمقدار $-450\text{ جنيه}$ يوم الإثنين؛ والصافي $0\text{ جنيه}$.",
                "exercise_58.text",
                AR_META,
                book_map=money,
            )
        )
        self.assertTrue(
            text_problems(
                source,
                r"تغير بمقدار $-540\text{ جنيه}$ يوم الإثنين؛ والصافي $0\text{ جنيه}$.",
                "exercise_58.text",
                AR_META,
                book_map=money,
            )
        )
        rays = (
            r"rays $\overrightarrow{MA}, \overrightarrow{MB}, \overrightarrow{MC}, "
            r"\overrightarrow{MD}$ meet at point $M$."
        )
        self.assertFalse(
            text_problems(
                rays,
                r"تتلاقى الأشعة $\overrightarrow{MA}$، $\overrightarrow{MB}$، "
                r"$\overrightarrow{MC}$، $\overrightarrow{MD}$ في النقطة $M$.",
                "exercise_168.text",
                AR_META,
            )
        )
        self.assertTrue(
            text_problems(
                rays,
                r"تتلاقى الأشعة $\overrightarrow{MA}$، $\overrightarrow{MB}$، "
                r"$\overrightarrow{MD}$ في النقطة $M$.",
                "exercise_168.text",
                AR_META,
            )
        )

    def test_clock_time_and_leaked_option_label_keep_source_shape(self) -> None:
        source = (
            "At 5:00, the smaller angle between the hands measures $150^\\circ$."
        )
        self.assertEqual(
            normalize_print_digits("At 5:00 p.m. and 12:345"),
            "At $5:00$ p.m. and $12:345$",
        )
        for target in (
            r"عند الساعة $5:00$، يكون قياس الزاوية الصغرى $150^\circ$.",
            r"عند الساعة $5$:$00$، يكون قياس الزاوية الصغرى $150^\circ$.",
        ):
            self.assertFalse(text_problems(source, target, "exercise_31.text", AR_META), target)
        self.assertTrue(
            text_problems(
                source,
                r"عند الساعة $6:00$، يكون قياس الزاوية الصغرى $150^\circ$.",
                "exercise_31.text",
                AR_META,
            )
        )
        self.assertEqual(option_text("Option D: $]-2, 2[$"), "$]-2, 2[$")
        self.assertEqual(option_text("Choice b) $x = 3$"), "$x = 3$")
        self.assertEqual(option_text("Option D:"), "Option D:")
        self.assertEqual(option_text("Optional $x$"), "Optional $x$")
        group = {
            "id": "group_5",
            "stem": "",
            "figures": [],
            "exercises": [
                {
                    "id": "exercise_8",
                    "text": "Solve $x^2 < 4$.",
                    "options": ["$]-2, 2[$", "Option D: $[-2, 2]$"],
                    "answer": "A",
                    "figures": [],
                }
            ],
        }
        self.assertEqual(
            translatable_group(group)["exercises"][0]["options"],
            ["$]-2, 2[$", "$[-2, 2]$"],
        )
        reply = copy.deepcopy(group)
        reply["exercises"][0].update(
            text="حل $س^2 < 4$.", options=["$]-2 ، 2[$", "$[-2 ، 2]$"], answer="أ"
        )
        self.assertFalse(group_problems(group, reply, ARABIC_MATH_META))
        reply["exercises"][0]["options"][1] = "$[-2 ، 3]$"
        self.assertTrue(group_problems(group, reply, ARABIC_MATH_META))
        self.assertFalse(
            text_problems(
                r"$m^{\text{rad}}\angle A = \frac{\pi}{3}$",
                r"$ق^د(\angle أ) = \frac{\pi}{3}$",
                "x",
                ARABIC_MATH_META,
            )
        )

    def test_lost_json_backslash_is_restored_and_controls_rejected(self) -> None:
        source = r"$L(t) = 1.5\cos(15t)^\circ + 14$"
        broken = "$ل(ن) = 1.5 \text{جتا }(15 ن)^\\circ + 14$"
        self.assertIn("\t", broken)
        self.assertEqual(
            normalize_translation(source, broken),
            r"$ل(ن) = 1.5 \text{جتا }(15 ن)^\circ + 14$",
        )
        self.assertFalse(text_problems(source, broken, "x", ARABIC_MATH_META))
        self.assertEqual(
            normalize_translation(r"$\frac{1}{2}$", "$\frac{1}{2}$"), r"$\frac{1}{2}$"
        )
        stray = text_problems("Find $x$.", "أوجد\x0b $س$.", "x", ARABIC_MATH_META)
        self.assertTrue(any("control character" in row for row in stray))
        stored = {
            "groups": [
                {
                    "stem": "line one\nline two",
                    "exercises": [
                        {"text": "إذا كان $2 \text{قا} \\theta - 4 = 0$", "options": ["$\x0crac{1}{2}$"]}
                    ],
                }
            ]
        }
        fixed, count = repair_stored_controls(stored)
        self.assertEqual(count, 2)
        row = fixed["groups"][0]
        self.assertEqual(row["stem"], "line one\nline two")
        self.assertEqual(row["exercises"][0]["text"], r"إذا كان $2 \text{قا} \theta - 4 = 0$")
        self.assertEqual(row["exercises"][0]["options"], [r"$\frac{1}{2}$"])
        self.assertEqual(repair_stored_controls(fixed), (fixed, 0))

    def test_quadrant_names_are_wording_in_either_language(self) -> None:
        for meta in (AR_META, ARABIC_MATH_META):
            for source, target in (
                ("Quadrant III", "الربع الثالث"),
                (
                    r"III, since $\cot \theta > 0$ in Quadrants I and III",
                    r"الثالث، بما أن $\cot \theta > 0$ في الربعين الأول والثالث",
                ),
                (
                    r"If the point $(p, q)$ lies in the $2^{\text{nd}}$ quadrant.",
                    r"إذا كانت النقطة $(p, q)$ تقع في الربع $2^{\text{nd}}$.",
                ),
                (
                    r"in the $1^{\text{st}}$ and $3^{\text{rd}}$ quadrants",
                    "في الربعين الأول والثالث",
                ),
                (r"$4^{\text{th}}\text{ quadrant}$", r"$4^{\text{th}}\text{ الربع}$"),
            ):
                with self.subTest(meta=meta["notation"], source=source):
                    self.assertFalse(
                        text_problems(source, target, "exercise_69.options[0]", meta)
                    )
        swapped = text_problems(
            r"$4^{\text{th}}\text{ quadrant}$",
            r"$3^{\text{rd}}\text{ الربع}$",
            "exercise_42.options[3]",
            AR_META,
        )
        self.assertTrue(any("quadrant labels" in row for row in swapped))
        lost = text_problems(
            r"The point $D(2, -5)$ is in the $4^{\text{th}}$ quadrant.",
            r"النقطة في الربع الرابع.",
            "exercise_9.answer",
            AR_META,
        )
        self.assertTrue(any("mathematics" in row for row in lost))

    def test_set_builder_text_wording_and_arabic_comma_are_not_math(self) -> None:
        source = (
            r"Which set is $B = \{x : x \in \mathbb{N}, "
            r"x \text{ is a prime number } < 10\}$?"
        )
        target = (
            r"أي مجموعة هي $B = \{x : x \in \mathbb{N} \text{، } "
            r"x \text{ عدد أولي } < 10\}$؟"
        )
        self.assertFalse(text_problems(source, target, "exercise_47.text", AR_META))
        leftover = text_problems(
            source,
            r"أي مجموعة هي $B = \{x : x \in \mathbb{N}, "
            r"x \text{ is a prime number } < 10\}$؟",
            "exercise_47.text",
            AR_META,
        )
        self.assertTrue(any("English prose" in row for row in leftover))

    def test_digits_inside_wording_text_stay_in_the_latin_skeleton(self) -> None:
        source = (
            r"$$\begin{array}{|c|c|} \hline "
            r"\text{Worker 1} & 3200 \\ \text{Worker 2} & 3000 \\ \hline \end{array}$$"
        )
        target = (
            r"$$\begin{array}{|c|c|} \hline "
            r"\text{العامل } 1 & 3200 \\ \text{العامل } 2 & 3000 \\ \hline \end{array}$$"
        )
        self.assertFalse(text_problems(source, target, "exercise_178.text", AR_META))
        dropped = (
            r"$$\begin{array}{|c|c|} \hline "
            r"\text{العامل} & 3200 \\ \text{العامل} & 3000 \\ \hline \end{array}$$"
        )
        problems = text_problems(source, dropped, "exercise_178.text", AR_META)
        self.assertTrue(any("mathematics differs" in row for row in problems))

    def test_prose_textbf_excerpt_is_not_naked_math(self) -> None:
        self.assertFalse(
            text_problems(
                r"\textbf{Statistics} is a branch of mathematics.",
                r"\textbf{الإحصاء} هو علم من علوم الرياضيات.",
                "excerpt_3.text",
                AR_META,
                math_policy="prose",
            )
        )
        self.assertTrue(
            any(
                "English prose" in problem
                for problem in text_problems(
                    "Minimization",
                    "Minimization",
                    "term_2.term",
                    AR_META,
                )
            )
        )

    def test_naked_math_is_repaired_but_changed_math_is_rejected(self) -> None:
        candidate = arabic_candidate()
        candidate["exercises"][1]["options"][0] = r"\frac{1}{5}"
        problems = group_problems(english_group(), candidate, AR_META)
        self.assertTrue(any("mathematics differs" in problem for problem in problems))

    def test_changed_figure_marker_is_rejected(self) -> None:
        candidate = arabic_candidate()
        candidate["stem"] = candidate["stem"].replace(
            "[figure 1]", "[الشكل 1]"
        )
        problems = group_problems(english_group(), candidate, AR_META)
        self.assertTrue(any("figure marker" in problem for problem in problems))

    def test_poem_marker_is_not_english_prose(self) -> None:
        self.assertFalse(
            text_problems(
                "اقرأ الأبيات الآتية:\n[poem 1]",
                "اقرأ الأبيات الآتية:\n[poem 1]",
                "group.stem",
                AR_META,
            )
        )
        self.assertFalse(
            target_document_problems(
                {
                    "exercises": {
                        "groups": [
                            {
                                "id": "group_1",
                                "stem": "اقرأ الأبيات الآتية:\n[poem 1]",
                                "exercises": [],
                            }
                        ]
                    }
                },
                AR_META,
            )
        )

    def test_changed_math_is_rejected(self) -> None:
        candidate = arabic_candidate()
        candidate["exercises"][1]["text"] = candidate["exercises"][1][
            "text"
        ].replace("$x + 2$", "$x + 3$")
        problems = group_problems(english_group(), candidate, AR_META)
        self.assertTrue(any("mathematics differs" in problem for problem in problems))

    def test_latin_profile_allows_identifier_and_digit_promotions(self) -> None:
        self.assertFalse(
            text_problems(
                (
                    r"$26$, from the x-coordinate of the intersection of "
                    r"$\overline{AB}$ and $\overline{CD}$."
                ),
                (
                    r"$26$، من الإحداثي $x$ لنقطة تقاطع "
                    r"$\overline{AB}$ و$\overline{CD}$."
                ),
                "answer",
                AR_META,
            )
        )
        self.assertFalse(
            text_problems(
                (
                    r"Distribution A has $\sum (f \cdot X_m) = 1000$ "
                    r"with total frequency $40$."
                ),
                (
                    r"التوزيع $A$ له $\sum (f \cdot X_m) = 1000$ "
                    r"بمجموع تكرارات $40$."
                ),
                "text",
                AR_META,
            )
        )
        problems = text_problems(
            r"Which value equals $x + 2$?",
            r"أي قيمة تساوي $x + 3$؟",
            "text",
            AR_META,
        )
        self.assertTrue(any("mathematics differs" in row for row in problems))

    def test_lengthmark_offset_bump_keeps_later_options(self) -> None:
        self.assertEqual(
            bump_lengthmark_offsets(
                r"\lengthmark[][sloped]{B}{E}{A}{$7\text{ cm}$}"
            ),
            r"\lengthmark[0.37cm][sloped]{B}{E}{A}{$7\text{ cm}$}",
        )

    def test_tashkeel_stack_is_valid_but_orphan_is_not(self) -> None:
        candidate = arabic_candidate()
        candidate["exercises"][0]["text"] = "مثَّل التكرار $\\blank$."
        self.assertFalse(group_problems(english_group(), candidate, AR_META))
        candidate["exercises"][0]["text"] = "ًمثّل التكرار $\\blank$."
        problems = group_problems(english_group(), candidate, AR_META)
        self.assertTrue(any("Arabic mark" in problem for problem in problems))

    def test_excessive_tashkeel_allows_bare_and_sparse(self) -> None:
        self.assertFalse(excessive_tashkeel_problems("حدد العصر التاريخي مما يأتي.", "bare"))
        self.assertFalse(
            excessive_tashkeel_problems("تولّى محمد علي حكم مصر.", "sparse")
        )
        self.assertFalse(
            excessive_tashkeel_problems("يُقسم العصور الحجرية إلى ثلاثة عصور.", "sparse")
        )

    def test_excessive_tashkeel_rejects_full_vocalization(self) -> None:
        full = (
            "تُرْجِعُ الدِّرَاسَاتُ التَّارِيخِيَّةُ أَصْلَ مُصْطَلَحَاتِ "
            "العُصُورِ الحَجَرِيَّةِ إِلَى اللُّغَةِ."
        )
        problems = excessive_tashkeel_problems(full, "full")
        self.assertTrue(any("fully vocalised" in problem for problem in problems))

    def test_excessive_tashkeel_ignores_quran_spans(self) -> None:
        verse = (
            f"{QURAN_OPEN}بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ{QURAN_CLOSE}"
        )
        self.assertFalse(
            excessive_tashkeel_problems(
                f"اقرأ الآية الآتية ثم أجب: {verse} ما المعنى المقصود؟",
                "quran",
            )
        )
        mixed = (
            f"تُرْجِعُ الدِّرَاسَاتُ التَّارِيخِيَّةُ أَصْلَ المُصْطَلَحِ "
            f"إِلَى اللُّغَةِ. {verse}"
        )
        problems = excessive_tashkeel_problems(mixed, "mixed")
        self.assertTrue(any("fully vocalised" in problem for problem in problems))

    def test_excessive_tashkeel_ignores_guillemet_spans(self) -> None:
        quoted = "«وَنَفْتَدِيهَا بِالْعَزِيزِ الأَكْرَمِ»"
        self.assertFalse(
            excessive_tashkeel_problems(
                f"مضاد كلمة «الْعَزِيز» في قوله: {quoted}:",
                "guillemet",
            )
        )
        mixed = (
            "تُرْجِعُ الدِّرَاسَاتُ التَّارِيخِيَّةُ أَصْلَ المُصْطَلَحِ "
            f"إِلَى اللُّغَةِ. {quoted}"
        )
        problems = excessive_tashkeel_problems(mixed, "mixed")
        self.assertTrue(any("fully vocalised" in problem for problem in problems))

    def test_repair_excessive_tashkeel_strips_unwrapped_poem_stem(self) -> None:
        stem = (
            "اقرأ الأبيات الآتية ثم أجب:\n"
            "مِصْرُ الَّتِي فِي خَاطِرِي وَفِي فَمِي :: أُحِبُّهَا مِنْ كُلِّ رُوحِي وَدَمِي"
        )
        self.assertTrue(excessive_tashkeel_problems(stem, "stem"))
        fixed = repair_excessive_tashkeel(stem)
        self.assertFalse(excessive_tashkeel_problems(fixed, "stem"))
        self.assertIn("مصر التي في خاطري", fixed)
        self.assertNotIn("مِصْرُ", fixed)

    def test_repair_excessive_tashkeel_keeps_guillemet_vocalisation(self) -> None:
        text = "مضاد كلمة «الْعَزِيز» في «وَنَفْتَدِيهَا بِالْعَزِيزِ»."
        full_prose = (
            "تُرْجِعُ الدِّرَاسَاتُ التَّارِيخِيَّةُ أَصْلَ المُصْطَلَحِ إِلَى اللُّغَةِ."
        )
        mixed = f"{full_prose} {text}"
        fixed = repair_excessive_tashkeel(mixed)
        self.assertIn("«الْعَزِيز»", fixed)
        self.assertFalse(excessive_tashkeel_problems(fixed, "mixed"))

    def test_repair_excessive_tashkeel_leaves_sparse_contextual(self) -> None:
        sparse = "تولّى محمد علي حكم مصر."
        self.assertEqual(repair_excessive_tashkeel(sparse), sparse)

    def test_mark_placement_rejects_bare_shadda(self) -> None:
        cases = ("بيّن", "خلوّ", "تمكّن", "أيّ", "حدّد", "قصّة")
        for word in cases:
            with self.subTest(word=word):
                problems = mark_placement_problems(f"{word} معنى الجملة.", "stem")
                self.assertTrue(
                    any("bare shadda" in problem for problem in problems),
                    problems,
                )

    def test_mark_placement_accepts_shadda_with_vowel(self) -> None:
        cases = ("بيِّن", "تولَّى", "حدِّد", "يُمثِّل")
        for word in cases:
            with self.subTest(word=word):
                self.assertFalse(
                    mark_placement_problems(f"{word} معنى الجملة.", "stem")
                )

    def test_mark_placement_rejects_tanween_on_alif(self) -> None:
        cases = ("مقالاً", "سبباً", "اعتراضاً", "نعيماً", "تماماً", "دائماً")
        for word in cases:
            with self.subTest(word=word):
                problems = mark_placement_problems(f"كان ذلك {word}.", "text")
                self.assertTrue(
                    any("tanween on alif" in problem for problem in problems),
                    problems,
                )

    def test_mark_placement_accepts_fathatan_before_alif(self) -> None:
        cases = ("مقالًا", "سببًا", "دائمًا", "تمامًا")
        for word in cases:
            with self.subTest(word=word):
                self.assertFalse(
                    mark_placement_problems(f"كان ذلك {word}.", "text")
                )

    def test_mark_placement_accepts_fathatan_on_hamza_alif(self) -> None:
        self.assertFalse(mark_placement_problems("هذا مبتدأً في الجملة.", "text"))

    def test_mark_placement_ignores_quran_and_as_is(self) -> None:
        verse = f"{QURAN_OPEN}إِنَّا رَادُّوهُ إِلَيْكِ{QURAN_CLOSE}"
        self.assertFalse(
            mark_placement_problems(f"اقرأ: {verse} ثم أجب.", "quran")
        )
        mushaf_word = "رَادُّوهُ"
        self.assertFalse(
            mark_placement_problems(
                f"المعنى هو {mushaf_word}.",
                "as_is",
                as_is=(mushaf_word,),
            )
        )
        self.assertFalse(mark_placement_problems(f"المعنى هو {mushaf_word}.", "ok"))

    def test_mark_placement_as_is_can_hide_tanween_on_alif(self) -> None:
        excerpt = "دائماً كما في الكتاب"
        problems = mark_placement_problems(excerpt, "copy", as_is=(excerpt,))
        self.assertFalse(problems)
        self.assertTrue(
            any(
                "tanween on alif" in problem
                for problem in mark_placement_problems(excerpt, "copy")
            )
        )

    def test_repair_mark_placement_fixes_bare_shadda_and_tanween(self) -> None:
        self.assertEqual(repair_mark_placement("بيّن معنى الكلمة."), "بيِّن معنى الكلمة.")
        self.assertEqual(repair_mark_placement("كان سبباً."), "كان سببًا.")
        self.assertEqual(repair_mark_placement("حدّد دائماً."), "حدِّد دائمًا.")
        self.assertFalse(mark_placement_problems(repair_mark_placement("حدّد دائماً."), "x"))

    def test_repair_mark_placement_leaves_quran_untouched(self) -> None:
        verse = f"{QURAN_OPEN}إِنَّا رَادُّوهُ{QURAN_CLOSE}"
        source = f"بيّن معنى: {verse}"
        repaired = repair_mark_placement(source)
        self.assertIn(verse, repaired)
        self.assertTrue(repaired.startswith("بيِّن"))

    def test_imperative_bayyin_upgraded_but_preposition_left(self) -> None:
        self.assertEqual(
            repair_mark_placement("بين الحكمة الإلهية من جعل الطفل."),
            "بيِّن الحكمة الإلهية من جعل الطفل.",
        )
        self.assertEqual(
            repair_mark_placement("وضح كيف جمع التدبير بين إرجاع موسى إلى أمه."),
            "وضح كيف جمع التدبير بين إرجاع موسى إلى أمه.",
        )
        problems = mark_placement_problems("بين الحكمة الإلهية من جعل الطفل.", "stem")
        self.assertTrue(any("بيِّن" in problem for problem in problems), problems)

    def test_missing_adverb_tanween_is_repaired(self) -> None:
        self.assertEqual(
            repair_mark_placement("تسقط همزة الوصل نطقا في كلمة."),
            "تسقط همزة الوصل نطقًا في كلمة.",
        )
        self.assertEqual(repair_mark_placement("نطقاً عند القراءة."), "نطقًا عند القراءة.")
        problems = mark_placement_problems("تسقط همزة الوصل نطقا في كلمة.", "stem")
        self.assertTrue(any("نطقًا" in problem for problem in problems), problems)

    def test_nisba_ya_tanween_gets_shadda(self) -> None:
        self.assertEqual(
            repair_mark_placement("جعلنا ذلك حرامًا شرعيًا"),
            "جعلنا ذلك حرامًا شرعيًّا",
        )
        self.assertEqual(repair_mark_placement("وعدًا إلهيًا"), "وعدًا إلهيًّا")
        self.assertEqual(repair_mark_placement("كان خاليًا من المعنى."), "كان خاليًا من المعنى.")
        self.assertEqual(repair_mark_placement("جوابًا كافيًا."), "جوابًا كافيًا.")
        self.assertFalse(mark_placement_problems("كان خاليًا من المعنى.", "ok"))
        problems = mark_placement_problems("جعلنا ذلك حرامًا شرعيًا", "choice")
        self.assertTrue(any("شرعيًّا" in problem for problem in problems), problems)

    def test_choice_mapping_is_limited_to_mcq(self) -> None:
        self.assertEqual(map_mcq_answer("B", ["$1$", "$2$", "$3$", "$4$"]), "ب")
        self.assertEqual(map_mcq_answer("A = 4", []), "A = 4")

    def test_required_empty_fields_must_be_explicit(self) -> None:
        candidate = arabic_candidate()
        del candidate["exercises"][0]["options"]
        problems = group_problems(english_group(), candidate, AR_META)
        self.assertTrue(any("options is missing" in problem for problem in problems))

    def test_latin_units_and_preserved_geometry_labels_are_not_english_leaks(self) -> None:
        self.assertFalse(
            text_problems(
                r"$5\text{ cm}$",
                r"$5\text{ cm}$",
                "unit",
                AR_META,
            )
        )
        self.assertFalse(
            text_problems(
                "Quadrilateral ABCD has diagonals AC and BD.",
                "للشكل الرباعي ABCD قطران AC وBD.",
                "figure",
                AR_META,
                math_policy="prose",
            )
        )
        problems = text_problems(
            "Area of ABCD.",
            "Area الشكل ABCD.",
            "figure",
            AR_META,
            math_policy="prose",
        )
        self.assertTrue(any("English prose" in row for row in problems))
        self.assertFalse(
            text_problems(
                "Regions are labelled $ax$, $ay$, $bx$, and $by$; the first is 'ax'.",
                "المناطق تحمل التسميات ax وay وbx وby.",
                "figure",
                AR_META,
                math_policy="prose",
            )
        )

    def test_prose_math_policy_allows_markup_but_preserves_numbers(self) -> None:
        self.assertFalse(
            text_problems(
                "Angle ABC is 40 degrees.",
                r"قياس الزاوية $\angle ABC = 40^\circ$.",
                "figure",
                AR_META,
                math_policy="prose",
            )
        )
        problems = text_problems(
            "Angle ABC is 40 degrees.",
            r"قياس الزاوية $\angle ABC = 45^\circ$.",
            "figure",
            AR_META,
            math_policy="prose",
        )
        self.assertTrue(any("quantities or numbers differ" in row for row in problems))

    def test_prose_quantities_preserve_sign_unit_and_power(self) -> None:
        self.assertFalse(
            text_problems(
                "Area is 5 cm^2 in a 3D diagram.",
                "المساحة 5 سم² في شكل ثلاثي الأبعاد.",
                "figure",
                AR_META,
                math_policy="prose",
            )
        )
        problems = text_problems(
            "Length is 5 cm.",
            "الطول -5 كجم.",
            "figure",
            AR_META,
            math_policy="prose",
        )
        self.assertTrue(any("quantities or numbers differ" in row for row in problems))

    def test_degree_triangle_hyphens_are_separators_not_negative_signs(self) -> None:
        self.assertFalse(
            text_problems(
                "A 30°-60°-90° triangle.",
                "مثلث $30$°-$60$°-$90$°.",
                "term",
                AR_META,
                math_policy="prose",
            )
        )
        self.assertFalse(
            text_problems(
                "In a 30°-60°-90° triangle, the 30° side is shortest.",
                "في المثلث الثلاثيني الستيني، ضلع $30^\\circ$ هو الأقصر.",
                "excerpt",
                AR_META,
                math_policy="prose",
            )
        )

    def test_latin_math_units_cannot_change_inside_text(self) -> None:
        self.assertFalse(
            text_problems(
                r"$2^n\text{ meters}$",
                r"$2^n\text{ m}$",
                "option",
                AR_META,
            )
        )
        self.assertFalse(
            text_problems(
                r"$2(x + 3)\text{ square metres}$",
                r"$2(x + 3)\text{ m}^2$",
                "option",
                AR_META,
            )
        )
        problems = text_problems(
            r"$5\text{ cm}$",
            r"$5\text{ kg}$",
            "option",
            AR_META,
        )
        self.assertTrue(any("mathematics differs" in row for row in problems))
        verbose = text_problems(
            r"$2^n\text{ meters}$",
            r"$2^n\text{ meters}$",
            "option",
            AR_META,
        )
        self.assertTrue(any("English prose" in row for row in verbose))
        self.assertFalse(
            text_problems(
                r"$ABCD \equiv \text{polygon } PQRS$",
                r"$\text{المضلع } ABCD \equiv PQRS$",
                "stem",
                AR_META,
            )
        )

    def test_tikz_geometry_and_language_are_checked(self) -> None:
        source = (
            r"\begin{tikzpicture}"
            r"\draw (0,0) -- (2,0);"
            r"\node at (1,1) {Frequency};"
            r"\end{tikzpicture}"
        )
        valid = source.replace("Frequency", "التكرار")
        self.assertFalse(tikz_problems(source, valid, AR_META))
        changed = valid.replace("(2,0)", "(3,0)")
        self.assertTrue(
            any("numeric/coordinate" in row for row in tikz_problems(source, changed, AR_META))
        )
        self.assertTrue(
            any("English prose" in row for row in tikz_problems(source, source, AR_META))
        )
        moved_label = valid.replace("at (1,1)", "at (1.5,1.2)")
        self.assertFalse(tikz_problems(source, moved_label, AR_META))
        mark_source = (
            r"\begin{tikzpicture}\draw (0,0)--(1,0);"
            r"\lengthmark[][sloped]{A}{B}{C}{$5$}"
            r"\end{tikzpicture}"
        )
        shifted_mark = mark_source.replace(
            "[sloped]", "[sloped,yshift=4pt]"
        )
        self.assertFalse(tikz_problems(mark_source, shifted_mark, ARABIC_MATH_META))
        offset_mark = mark_source.replace(
            "[][sloped]",
            "[0.4cm][sloped,pos=0.8,inner sep=0pt]",
        )
        self.assertFalse(tikz_problems(mark_source, offset_mark, ARABIC_MATH_META))
        path = (
            r"\begin{tikzpicture}"
            r"\fill (-3,5) circle (2pt) node [above=3pt] {Playground ($B$)};"
            r"\end{tikzpicture}"
        )
        self.assertTrue(
            any("English prose" in row for row in tikz_problems(path, path, AR_META))
        )
        translated_path = path.replace("Playground", "ملعب")
        self.assertFalse(tikz_problems(path, translated_path, AR_META))

    def test_arabic_notation_allows_native_letters_sets_and_commas(self) -> None:
        pairs = [
            (r"$n \in \mathbb{R}^+$", r"$ن \in \mathbb{R}^+$"),
            (r"$\{1, -1, i, -i\}$", r"$\{1، -1، ت، -ت\}$"),
            (r"$x^2 + 2x + 1$", r"$س^2 + 2س + 1$"),
            (r"$i^2$ ثم $-1$", r"$-1$ ثم $ت^2$"),
            (r"$\frac{5\pi}{12}^\circ$", r"$(\frac{5ط}{12})^\circ$"),
        ]
        for source, target in pairs:
            self.assertFalse(
                text_problems(source, target, "math", ARABIC_MATH_META),
                (source, target),
            )

    def test_arabic_notation_still_rejects_changed_numbers_and_operators(self) -> None:
        problems = text_problems(
            r"$x^2 + 2x + 1$",
            r"$س^2 - 3س + 1$",
            "math",
            ARABIC_MATH_META,
        )
        self.assertTrue(any("mathematics differs" in row for row in problems))
        reordered = text_problems(
            r"$2 + 3 = 5$",
            r"$3 + 2 = 5$",
            "math",
            ARABIC_MATH_META,
        )
        self.assertTrue(any("mathematics differs" in row for row in reordered))

    def test_arabic_notation_keeps_source_uppercase_acronyms(self) -> None:
        source = (
            "It introduces the AA similarity postulate, then the SSS and SAS "
            "similarity theorems."
        )
        target = (
            "يقدم مسلمة التشابه بزاويتين (AA)، ثم نظريتي التشابه SSS و SAS."
        )
        self.assertFalse(
            text_problems(source, target, "overview", ARABIC_MATH_META)
        )
        leftover = text_problems(
            source,
            "يقدم مسلمة التشابه similarity.",
            "overview",
            ARABIC_MATH_META,
        )
        self.assertTrue(any("English prose" in row for row in leftover))
        invented = text_problems(
            "Apply the AA similarity postulate.",
            "طبق مسلمة التشابه HL.",
            "objective_1.text",
            ARABIC_MATH_META,
        )
        self.assertTrue(any("English prose" in row for row in invented))

    def test_arabic_notation_accepts_calculator_keys_and_native_math_labels(self) -> None:
        pairs = [
            (
                r"$\text{SHIFT } \tan (3.5)^{-1} =$ then $\circ$",
                r"$\mathrm{SHIFT}\ \tan (3.5)^{-1} =$ ثم $\circ$",
            ),
            (
                r"$$\theta^{\text{rad}} = \frac{l}{r}$$",
                r"$$هـ^د = \frac{ل}{نق}$$",
            ),
        ]
        for source, target in pairs:
            self.assertFalse(
                text_problems(source, target, "math", ARABIC_MATH_META),
                (source, target),
            )

    def test_arabic_geometry_allows_cosmetic_segment_and_angle_parentheses(self) -> None:
        source = (
            r"$AX \perp \overline{BC}$ and "
            r"$m\angle D = \frac{1}{2}m\angle A$."
        )
        target = (
            r"$\overline{أ س} \perp \overline{ب ج}$ و"
            r"$ق(\angle د) = \frac{1}{2}ق(\angle أ)$."
        )
        self.assertFalse(
            text_problems(source, target, "geometry", ARABIC_MATH_META)
        )

    def test_arabic_coordinates_allow_punctuation_inside_math_text(self) -> None:
        pairs = [
            (
                r"$B\left(-\frac{3}{5}, y\right)$, where $y > 0$",
                r"$ب\left(-\frac{3}{5}\text{، } ص\right)$، حيث $ص > 0$",
            ),
            (
                r"$B(-3k, 4k)$, where $k > 0$",
                r"$ب(-3ك\text{، } 4ك)$، حيث $ك > 0$",
            ),
        ]
        for source, target in pairs:
            self.assertFalse(
                text_problems(source, target, "coordinate", ARABIC_MATH_META),
                (source, target),
            )

    def test_source_shaped_repairs_wrap_math_and_restore_missing_opener(self) -> None:
        self.assertEqual(
            normalize_translation(r"$-\frac{\sqrt{5}}{2}$", r"-\frac{\sqrt{5}}{2}"),
            r"$-\frac{\sqrt{5}}{2}$",
        )
        repaired = normalize_translation(
            (
                r"$B=\left(-\frac{1}{\sqrt{2}},-\frac{1}{\sqrt{2}}\right)$ "
                r"and $\left(-\frac{1}{\sqrt{2}}\right)^2=\frac12$"
            ),
            (
                r"$ب=\left(-\frac{1}{\sqrt{2}}\text{،}-\frac{1}{\sqrt{2}}\right)$ "
                r"وإذن \left(-\frac{1}{\sqrt{2}}\right)^2=\frac12$"
            ),
        )
        self.assertIn(r"وإذن $\left", repaired)
        self.assertEqual(
            normalize_translation(
                (
                    r"measure of a central angle = \frac{\text{frequency}}"
                    r"{\text{sum of frequencies}} \times 360^\circ"
                ),
                (
                    r"قياس الزاوية المركزية = \frac{\text{التكرار}}"
                    r"{\text{مجموع التكرارات}} \times 360^\circ"
                ),
            ),
            (
                r"قياس الزاوية المركزية = $\frac{\text{التكرار}}"
                r"{\text{مجموع التكرارات}} \times 360^\circ$"
            ),
        )
        self.assertFalse(
            text_problems(
                (
                    r"measure of a central angle = \frac{\text{frequency}}"
                    r"{\text{sum of frequencies}} \times 360^\circ"
                ),
                (
                    r"قياس الزاوية المركزية = \frac{\text{التكرار}}"
                    r"{\text{مجموع التكرارات}} \times 360^\circ"
                ),
                "excerpt_3.text",
                AR_META,
                math_policy="prose",
            )
        )

    def test_arabic_notation_normalizes_trig_commands_variables_and_angle_measure(self) -> None:
        normalized = normalize_arabic_notation(
            r"إذا كان $y=\sin\theta$ فإن $\text{m}(\angle x)=\cot^{-1}y$."
        )
        self.assertEqual(
            normalized,
            (
                r"إذا كان $ص=\text{جا }\theta$ فإن "
                r"$\text{ق}(\angle س)=\text{ظتا }^{-1}ص$."
            ),
        )
        self.assertEqual(
            normalize_arabic_notation(
                r"$$\begin{array}{|c|c|}x&y\end{array}$$"
            ),
            r"$$\begin{array}{|c|c|}س&ص\end{array}$$",
        )

    def test_prose_quantity_repetition_and_minutes_are_not_false_units(self) -> None:
        self.assertFalse(
            text_problems(
                "Angles are 30 degrees, 45 degrees, and 60 degrees.",
                (
                    "الزوايا 30 درجة و45 درجة و60 درجة؛ "
                    "أي 30 درجة و45 درجة و60 درجة."
                ),
                "figure",
                ARABIC_MATH_META,
                math_policy="prose",
            )
        )
        self.assertFalse(
            text_problems(
                "Each degree is subdivided into 60 minutes.",
                "تنقسم كل درجة إلى 60 دقيقة.",
                "definition",
                ARABIC_MATH_META,
                math_policy="prose",
            )
        )
        self.assertFalse(
            text_problems(
                (
                    "A regular polygon has both of the following properties:\n"
                    "1 All its sides are equal in length.\n"
                    "2 All its interior angles are equal in measure."
                ),
                (
                    "المضلع المنتظم هو مضلع تتحقق فيه الخاصيتان التاليتان:\n"
                    "1 جميع أضلاعه متساوية في الطول.\n"
                    "2 جميع زواياه الداخلة متساوية في القياس."
                ),
                "excerpt_4.text",
                AR_META,
                math_policy="prose",
            )
        )
        self.assertFalse(
            text_problems(
                r"A complete angle is $360$ equal degrees.",
                r"الزاوية الكاملة هي $360$ درجة متساوية.",
                "term_1.definition",
                ARABIC_MATH_META,
                math_policy="prose",
            )
        )

    def test_spec_projection_wraps_legacy_bare_math_fields(self) -> None:
        source = {
            "overview": "Geometry.",
            "objectives": [{"id": "objective_1", "text": "Use congruence."}],
            "prerequisites": [],
            "out_of_scope": [],
            "glossary": [],
            "teaching_points": [],
            "excerpts": [],
            "equations": [{"id": "equation_1", "latex": r"AB = CD \iff \overline{AB} \equiv \overline{CD}"}],
            "notations": [
                {"id": "notation_1", "notation": r"\overline{AB}", "meaning": "segment AB"},
                {"id": "notation_2", "notation": "AB", "meaning": "length AB"},
                {"id": "notation_3", "notation": "X : Y : Z", "meaning": "ratio"},
            ],
        }
        projected = translatable_spec(source)
        self.assertEqual(
            projected["equations"][0]["latex"],
            r"$AB = CD \iff \overline{AB} \equiv \overline{CD}$",
        )
        self.assertEqual(
            projected["notations"][0]["notation"],
            r"$\overline{AB}$",
        )
        self.assertEqual(projected["notations"][1]["notation"], "$AB$")
        self.assertTrue(symbolic_notation("X : Y : Z"))
        self.assertTrue(symbolic_notation("]a, b["))
        self.assertTrue(symbolic_notation("[a, b]"))
        self.assertEqual(projected["notations"][2]["notation"], "$X : Y : Z$")
        interval_spec = {
            "overview": "Directed angles.",
            "objectives": [],
            "prerequisites": [],
            "out_of_scope": [],
            "glossary": [],
            "teaching_points": [],
            "excerpts": [],
            "equations": [],
            "notations": [
                {
                    "id": "notation_3",
                    "notation": "]a, b[",
                    "meaning": "open interval",
                }
            ],
        }
        self.assertEqual(
            translatable_spec(interval_spec)["notations"][0]["notation"],
            "$]a, b[$",
        )
        self.assertFalse(
            spec_problems(
                interval_spec,
                {
                    "overview": "الزوايا الموجهة.",
                    "objectives": [],
                    "prerequisites": [],
                    "out_of_scope": [],
                    "glossary": [],
                    "teaching_points": [],
                    "excerpts": [],
                    "equations": [],
                    "notations": [
                        {
                            "id": "notation_3",
                            "notation": r"$]أ ، ب[$",
                            "meaning": "فترة مفتوحة",
                        }
                    ],
                },
                ARABIC_MATH_META,
            )
        )
        self.assertTrue(
            spec_problems(
                interval_spec,
                {
                    "overview": "الزوايا الموجهة.",
                    "objectives": [],
                    "prerequisites": [],
                    "out_of_scope": [],
                    "glossary": [],
                    "teaching_points": [],
                    "excerpts": [],
                    "equations": [],
                    "notations": [
                        {
                            "id": "notation_3",
                            "notation": r"$]1 ، 2[$",
                            "meaning": "فترة مفتوحة",
                        }
                    ],
                },
                ARABIC_MATH_META,
            )
        )
        self.assertFalse(
            text_problems(
                "$X : Y : Z$",
                "X : Y : Z",
                "notation_3.notation",
                AR_META,
                math_policy="strict",
            )
        )
        empty = {
            field: []
            for field in (
                "objectives",
                "prerequisites",
                "out_of_scope",
                "glossary",
                "teaching_points",
                "excerpts",
                "equations",
            )
        }
        self.assertFalse(
            spec_problems(
                {
                    "overview": "Pie charts use ratios.",
                    **empty,
                    "notations": [
                        {
                            "id": "notation_3",
                            "notation": "X : Y : Z",
                            "meaning": "relative shares",
                        }
                    ],
                },
                {
                    "overview": "تستخدم مخططات القطاعات النسب.",
                    **empty,
                    "notations": [
                        {
                            "id": "notation_3",
                            "notation": "X : Y : Z",
                            "meaning": "الحصص النسبية",
                        }
                    ],
                },
                AR_META,
            )
        )

    def test_paper_audit_ignores_nonprinted_figure_description_latex(self) -> None:
        document = {
            "exercises": {
                "groups": [
                    {
                        "id": "group_1",
                        "exercises": [
                            {
                                "id": "exercise_1",
                                "text": "Plot the function.",
                                "answer": "A graph.",
                                "options": [],
                                "figures": [
                                    {
                                        "description": r"Graph of \\sin \\theta and \\sqrt{x}."
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        }
        self.assertFalse(
            target_document_problems(
                document,
                {"language_code": "en", "notation": "latin", "numerals": "western"},
            )
        )

    def test_merge_spec_rewrites_ray_vec_and_matches_overrightarrow(self) -> None:
        empty = {
            field: []
            for field in (
                "objectives",
                "prerequisites",
                "out_of_scope",
                "glossary",
                "teaching_points",
                "excerpts",
                "equations",
            )
        }
        source = {
            "overview": "The ray drawn from a midpoint.",
            **empty,
            "notations": [
                {
                    "id": "notation_1",
                    "notation": r"$\vec{DE}$",
                    "meaning": "Ray originating at point $D$ and passing through point $E$",
                }
            ],
        }
        target = {
            "overview": "الشعاع المرسوم من نقطة منتصف.",
            **empty,
            "notations": [
                {
                    "id": "notation_1",
                    "notation": r"$\overrightarrow{DE}$",
                    "meaning": "الشعاع الذي يبدأ من النقطة $D$ ويمر بالنقطة $E$",
                }
            ],
        }
        merged = merge_spec(source, target, AR_META)
        self.assertEqual(merged["notations"][0]["notation"], r"$\overrightarrow{DE}$")

    def test_normalize_and_merge_keep_true_vectors(self) -> None:
        self.assertEqual(
            normalize_profile_translation(
                r"Find $\vec{a}+\vec{AB}$.",
                r"أوجد $\vec{a}+\vec{AB}$.",
                AR_META,
            ),
            r"أوجد $\vec{a}+\vec{AB}$.",
        )
        source = {
            "id": "group_1",
            "stem": r"Find $\vec{a}+\vec{AB}$.",
            "figures": [],
            "exercises": [
                {
                    "id": "exercise_1",
                    "text": r"Simplify $\vec{a}$.",
                    "options": [],
                    "answer": r"$\vec{a}$.",
                    "figures": [],
                }
            ],
        }
        target = {
            "id": "group_1",
            "stem": r"أوجد $\vec{a}+\vec{AB}$.",
            "figures": [],
            "exercises": [
                {
                    "id": "exercise_1",
                    "text": r"بسّط $\vec{a}$.",
                    "options": [],
                    "answer": r"$\vec{a}$.",
                    "figures": [],
                }
            ],
        }
        merged = merge_group(source, target, AR_META, vector_lesson=True)
        self.assertIn(r"$\vec{a}+\vec{AB}$", merged["stem"])
        unlabeled = merge_group(
            {
                "id": "group_1",
                "stem": r"$\vec{AZ} // \overline{YD}$",
                "figures": [],
                "exercises": [
                    {
                        "id": "exercise_1",
                        "text": r"$H \in \vec{BA}$.",
                        "options": [],
                        "answer": r"$\vec{BA}$.",
                        "figures": [],
                    }
                ],
            },
            {
                "id": "group_1",
                "stem": r"$\overrightarrow{AZ} // \overline{YD}$",
                "figures": [],
                "exercises": [
                    {
                        "id": "exercise_1",
                        "text": r"$H \in \overrightarrow{BA}$.",
                        "options": [],
                        "answer": r"$\overrightarrow{BA}$.",
                        "figures": [],
                    }
                ],
            },
            AR_META,
            vector_lesson=False,
        )
        self.assertEqual(unlabeled["stem"], r"$\overrightarrow{AZ} // \overline{YD}$")
        self.assertEqual(
            unlabeled["exercises"][0]["text"],
            r"$H \in \overrightarrow{BA}$.",
        )


    def test_odd_closer_before_trailing_colon_is_repaired(self) -> None:
        source = (
            r"For each dataset, determine the five-number summary "
            r"$(\text{Minimum}, Q_1, \text{Median}, Q_3, \text{Maximum})$:"
        )
        target = (
            r"لكل مجموعة بيانات، حدد ملخص القيم الخمس "
            r"$(\text{الحد الأدنى}, Q_1, \text{الوسيط}, Q_3, \text{الحد الأقصى}):"
        )
        self.assertFalse(text_problems(source, target, "group_33.stem", AR_META))
        repaired = normalize_profile_translation(source, target, AR_META)
        self.assertTrue(repaired.rstrip().endswith("$:"))
        english = {
            "id": "group_33",
            "stem": source,
            "figures": [],
            "exercises": [
                {
                    "id": "exercise_47",
                    "text": r"$10, 12, 14$",
                    "options": [],
                    "answer": r"$10$",
                    "figures": [],
                }
            ],
        }
        arabic = {
            "id": "group_33",
            "stem": target,
            "figures": [],
            "exercises": [
                {
                    "id": "exercise_47",
                    "text": r"$10, 12, 14$",
                    "options": [],
                    "answer": r"$10$",
                    "figures": [],
                }
            ],
        }
        self.assertTrue(merge_group(english, arabic, AR_META)["stem"].rstrip().endswith("$:"))
        self.assertTrue(
            text_problems(r"Find $a$ and $b$.", r"أوجد $a و $b$.", "text", AR_META)
        )

    def test_number_word_promotion_ratio_angle_and_fail_closed_math(self) -> None:
        self.assertFalse(
            text_problems(
                "There are six students in the row.",
                r"يوجد $6$ طلاب في الصف.",
                "text",
                AR_META,
            )
        )
        self.assertFalse(
            text_problems(
                "1 : 2,500 map scale ratio",
                r"مقياس رسم $1 : 2,500$",
                "exercise_11.options[1]",
                AR_META,
            )
        )
        self.assertFalse(
            text_problems(
                r"$1$ : $2,500$",
                r"$1 : 2,500$",
                "exercise_11.options[1]",
                AR_META,
            )
        )
        self.assertFalse(
            text_problems(
                r"$\angle ABC = 90^\circ$",
                r"$∠ABC = 90^\circ$",
                "text",
                AR_META,
            )
        )
        minutes = {
            "entries": [
                {
                    "kind": "unit",
                    "source_math": "min",
                    "math_text": "دقيقة",
                    "plain_lemma": "دقيقة",
                    "also": ["دقائق"],
                }
            ]
        }
        self.assertFalse(
            text_problems(
                r"The period is $6\text{ min}$.",
                r"الزمن $6\text{ دقائق}$.",
                "options[0]",
                AR_META,
                book_map=minutes,
            )
        )
        self.assertTrue(
            any(
                "unfilled template placeholder" in row
                for row in source_artifact_problems("${d}$", "exercise_126.options[1]")
            )
        )
        self.assertTrue(
            text_problems(
                r"$6$ intervals ($160-$, $165-$, $170-$, $175-$, $180-$, and $185-190$).",
                r"$6$ مجموعات ($160-$، $165-$، $170-$، $175-$، و$185-190$).",
                "exercise_36.answer",
                AR_META,
            )
        )
        self.assertTrue(
            text_problems(
                r"The mass is $5$.",
                r"الكتلة هي $5$ $kg$.",
                "text",
                AR_META,
            )
        )
        self.assertTrue(
            text_problems(
                r"$\angle ABC = 90^\circ$",
                r"$ABC = 90^\circ$",
                "text",
                AR_META,
            )
        )

    def test_model_letters_may_leave_text_in_a_price_table(self) -> None:
        source = (
            r"$$\begin{array}{|c|c|} \hline \text{Car Model} & "
            r"\text{Price (in thousands of LE)} \\ \hline "
            r"\text{Model A} & 650 \\ \text{Model B} & 620 \\ "
            r"\hline \end{array}$$"
        )
        target = (
            r"$$\begin{array}{|c|c|} \hline \text{طراز السيارة} & "
            r"\text{السعر (بآلاف الجنيهات)} \\ \hline "
            r"\text{الطراز } A & 650 \\ \text{الطراز } B & 620 \\ "
            r"\hline \end{array}$$"
        )
        self.assertFalse(text_problems(source, target, "exercise_181.text", AR_META))
        self.assertTrue(
            text_problems(
                source,
                target.replace("650", "651"),
                "exercise_181.text",
                AR_META,
            )
        )

    def test_grade_wording_and_plural_degrees_are_not_math_edits(self) -> None:
        heights = (
            "The heights of six Grade 7 students in centimeters are "
            r"$142$, $148$, $145$, $150$, $141$, and $144$."
        )
        self.assertFalse(
            text_problems(
                heights,
                (
                    r"أطوال ستة طلاب في الصف السابع بالسنتيمتر هي "
                    r"$142$، و$148$، و$145$، و$150$، و$141$، و$144$."
                ),
                "exercise_44.text",
                AR_META,
            )
        )
        self.assertTrue(
            text_problems(
                heights,
                (
                    r"أطوال ستة طلاب في الصف السابع بالسنتيمتر هي "
                    r"$143$، و$148$، و$145$، و$150$، و$141$، و$144$."
                ),
                "exercise_44.text",
                AR_META,
            )
        )
        pie = (
            r"A pie chart shows the grades of $250$ grade 7 students: "
            r"Excellent ($24\%$)."
        )
        self.assertFalse(
            text_problems(
                pie,
                r"يوضح المخطط درجات $250$ طالبًا من الصف السابع: ممتاز ($24\%$).",
                "exercise_132.text",
                AR_META,
            )
        )
        angles = (
            "Angle AGH is labeled 110 degrees and angle GHC is labeled 70 degrees."
        )
        self.assertFalse(
            text_problems(
                angles,
                (
                    r"الزاوية $AGH$ محددة بقياس $110$ درجات "
                    r"والزاوية $GHC$ محددة بقياس $70$ درجة."
                ),
                "exercise_106.figures[0].description",
                AR_META,
                math_policy="prose",
            )
        )
        self.assertTrue(
            text_problems(
                angles,
                r"الزاوية $AGH$ محددة بقياس $70$ درجة.",
                "exercise_106.figures[0].description",
                AR_META,
                math_policy="prose",
            )
        )

    def test_prose_text_labels_may_leave_math(self) -> None:
        source = (
            "A student enters the daily study hours for four subjects:\n"
            r"$$\begin{array}{|c|c|} \hline \text{Subject} & \text{Hours} \\ "
            r"\hline \text{Mathematics} & 2.5 \\ \hline \text{Science} & 1.5 \\ "
            r"\hline \end{array}$$"
            "\nWhat central angle is displayed for "
            r"$\text{Mathematics}$?"
        )
        target = (
            "يدخل طالب ساعات الدراسة اليومية لـ $4$ مواد:\n"
            r"$$\begin{array}{|c|c|} \hline \text{المادة} & \text{الساعات} \\ "
            r"\hline \text{الرياضيات} & 2.5 \\ \hline \text{العلوم} & 1.5 \\ "
            r"\hline \end{array}$$"
            "\nما قياس الزاوية المركزية المعروضة لمادة الرياضيات؟"
        )
        self.assertFalse(text_problems(source, target, "exercise_168.text", AR_META))
        self.assertTrue(
            text_problems(
                source,
                target.replace("2.5", "3.5"),
                "exercise_168.text",
                AR_META,
            )
        )


class SplitEnglishGlossProblemsTests(unittest.TestCase):
    def test_splits_gloss_rows_from_other_audit_failures(self) -> None:
        gloss = "exercise_1.text: English prose remains in Arabic translation: logos"
        other = "exercise_2.text: some other problem"
        blocking, reported = split_english_gloss_problems([gloss, other])
        self.assertEqual(reported, [gloss])
        self.assertEqual(blocking, [other])


if __name__ == "__main__":
    unittest.main()
