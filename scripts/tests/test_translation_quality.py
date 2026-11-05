from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.arabic_math import QURAN_CLOSE, QURAN_OPEN  # noqa: E402
from new_questions import naked_math  # noqa: E402
from src.translation_quality import (  # noqa: E402
    bump_lengthmark_offsets,
    excessive_tashkeel_problems,
    group_problems,
    map_mcq_answer,
    mark_placement_problems,
    merge_group,
    merge_spec,
    normalize_profile_translation,
    repair_excessive_tashkeel,
    repair_json_escapes,
    repair_mark_placement,
    split_english_gloss_problems,
    tikz_problems,
    text_problems,
    target_document_problems,
    SPEC_LIST_FIELDS,
    spec_problems,
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












    def test_newline_before_lowercase_sentence_is_not_lost_ni(self) -> None:
        text = (
            "Rewrite the sentence using correct punctuation:\n"
            "i write back to my friend in london."
        )
        repaired = repair_json_escapes(text)
        self.assertIn("\n", repaired)
        self.assertNotIn("\\ni", repaired)
        self.assertEqual(naked_math(repaired), "")

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








class TextProblemsV2Tests(unittest.TestCase):
    def test_text_problems_ignores_book_map(self) -> None:
        problems = text_problems(
            "It took 3 hours",
            "استغرق $3$ ساعات",
            "stem",
            AR_META,
            book_map={"entries": [{"kind": "unit", "math_text": "ساعة"}]},
        )
        self.assertFalse(any("unit" in problem.casefold() for problem in problems))

    def test_inflected_unit_accepted(self) -> None:
        problems = text_problems(
            "It took 3 hours",
            "استغرق $3$ ساعات",
            "stem",
            AR_META,
        )
        self.assertFalse(any("unit" in problem.casefold() for problem in problems))

    def test_normalize_profile_translation_no_unit_rewrite(self) -> None:
        source = r"$3\text{ hours}$"
        target = r"$3\text{ ساعات}$"
        self.assertEqual(
            normalize_profile_translation(source, target, AR_META, {"entries": []}),
            target,
        )

    def test_bare_digit_reported(self) -> None:
        problems = text_problems("Ali has 12 pens", "لدى علي 12 قلماً", "stem", AR_META)
        self.assertTrue(any("digit" in problem for problem in problems))

    def test_english_prose_left_reported(self) -> None:
        problems = text_problems("Find the area", "Find the area", "stem", AR_META)
        self.assertTrue(any("English prose" in problem for problem in problems))

    def test_bidi_mark_reported(self) -> None:
        problems = text_problems("Find $x$.", "أوجد \u200e$x$.", "stem", AR_META)
        self.assertTrue(any("bidi" in problem for problem in problems))

    def test_ratio_good_translation_passes(self) -> None:
        problems = text_problems(
            "scale 1 : 2,500",
            "مقياس $1 : 2,500$",
            "stem",
            AR_META,
        )
        self.assertEqual(problems, [])

    def test_excerpt_prose_allows_naked_command_and_bare_digits(self) -> None:
        source = {field: [] for field in SPEC_LIST_FIELDS}
        source["overview"] = ""
        source["excerpts"] = [
            {
                "id": "excerpt_1",
                "text": r"The mean = \frac{\text{sum}}{\text{count}}",
                "kashida": True,
                "rows": [],
            }
        ]
        source["teaching_points"] = [
            {
                "id": "teaching_point_1",
                "title": "Mean",
                "notes": "Notes",
                "figure": {
                    "purpose": "plot",
                    "description": "x-axis from -3 to 3",
                },
            }
        ]
        target = copy.deepcopy(source)
        target["excerpts"][0]["text"] = r"الوسط = \frac{\text{المجموع}}{\text{العدد}}"
        target["teaching_points"][0]["title"] = "الوسط"
        target["teaching_points"][0]["notes"] = "ملاحظات"
        target["teaching_points"][0]["figure"]["purpose"] = "رسم"
        target["teaching_points"][0]["figure"]["description"] = "محور السينات من -3 إلى 3"
        problems = spec_problems(source, target, AR_META)
        self.assertEqual(problems, [])
        target["excerpts"][0]["text"] = r"الوسط = \frac{\text{المجموع}}{\text{2}}"
        problems = spec_problems(source, target, AR_META)
        self.assertTrue(any("excerpt_1.text" in problem and "number" in problem for problem in problems))

    def test_dropped_number_allowed(self) -> None:
        problems = text_problems(
            "It took 2 minutes",
            "استغرق دقيقتين",
            "stem",
            AR_META,
            dropped_numbers=1,
        )
        self.assertFalse(any("number(s) missing" in problem for problem in problems))


class SplitEnglishGlossProblemsTests(unittest.TestCase):
    def test_splits_gloss_rows_from_other_audit_failures(self) -> None:
        gloss = "exercise_1.text: English prose remains in Arabic translation: logos"
        other = "exercise_2.text: some other problem"
        blocking, reported = split_english_gloss_problems([gloss, other])
        self.assertEqual(reported, [gloss])
        self.assertEqual(blocking, [other])


if __name__ == "__main__":
    unittest.main()
