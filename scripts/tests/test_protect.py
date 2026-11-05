from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.protect import (  # noqa: E402
    Slots,
    _digit_counter,
    protect,
    protect_group,
    protect_spec,
    restore,
    restore_spec,
    restored_problems,
    set_spec_strings,
    skeleton,
    token_problems,
)


class ProtectTests(unittest.TestCase):
    def test_frozen_math_tokenised_under_latin(self) -> None:
        text, slots = protect("Find $x+2=5$.", notation="latin")
        self.assertEqual(text, "Find ⟦M1⟧.")
        self.assertEqual(slots.frozen, {"M1": "$x+2=5$"})

    def test_text_unit_math_is_editable(self) -> None:
        text, _slots = protect(r"A box of $27\text{ cm}^3$", notation="latin")
        self.assertIn(r"⟦E1⟧$27\text{ cm}^3$⟦/E1⟧", text)

    def test_bare_latin_word_math_is_editable(self) -> None:
        text, slots = protect("$5 cm$", notation="latin")
        self.assertIn("E1", text)
        self.assertIn("E1", slots.editable)

    def test_prose_number_tokenised(self) -> None:
        text, slots = protect("Ali has 12 pens", notation="latin")
        self.assertEqual(text, "Ali has ⟦N1⟧ pens")
        self.assertEqual(slots.numbers, {"N1": "12"})

    def test_ratio_is_one_number(self) -> None:
        _text, slots = protect("map scale 1 : 2,500", notation="latin")
        self.assertEqual(list(slots.numbers), ["N1"])
        self.assertEqual(slots.numbers["N1"], "1 : 2,500")

    def test_figure_marker_not_tokenised(self) -> None:
        text, slots = protect("See [figure 2] and 3 pens", notation="latin")
        self.assertIn("[figure 2]", text)
        self.assertEqual(len(slots.numbers), 1)
        self.assertNotIn("⟦N", text[text.index("[figure") : text.index("]") + 1])

    def test_restore_number_outside_math(self) -> None:
        _text, slots = protect("Ali has 12 pens", notation="latin")
        self.assertEqual(restore("لدى علي ⟦N1⟧ أقلام", slots), "لدى علي $12$ أقلام")

    def test_restore_number_inside_model_math(self) -> None:
        slots = Slots(frozen={}, editable={}, numbers={"N1": "1 : 2,500"})
        self.assertEqual(restore("$⟦N1⟧$", slots), "$1 : 2,500$")

    def test_restore_percent(self) -> None:
        slots = Slots(frozen={}, editable={}, numbers={"N1": "15%"})
        self.assertEqual(restore("⟦N1⟧", slots), r"$15\%$")

    def test_restore_frozen_verbatim(self) -> None:
        _text, slots = protect("Find $x+2=5$.", notation="latin")
        self.assertEqual(restore("أوجد ⟦M1⟧.", slots), "أوجد $x+2=5$.")

    def test_token_missing(self) -> None:
        _text, slots = protect("Find $x+2=5$.", notation="latin")
        problems = token_problems(slots, "أوجد.", "stem")
        self.assertEqual(len(problems), 1)
        self.assertIn("M1", problems[0])

    def test_token_duplicated(self) -> None:
        _text, slots = protect("Find $x+2=5$.", notation="latin")
        problems = token_problems(slots, "⟦M1⟧ ⟦M1⟧", "stem")
        self.assertEqual(len(problems), 1)
        self.assertIn("M1", problems[0])

    def test_unknown_token(self) -> None:
        _text, slots = protect("Find $x+2=5$.", notation="latin")
        problems = token_problems(slots, "⟦M1⟧ ⟦M9⟧", "stem")
        self.assertEqual(len(problems), 1)
        self.assertIn("M9", problems[0])

    def test_dropped_number_needs_listing(self) -> None:
        _text, slots = protect("It took 2 minutes", notation="latin")
        self.assertTrue(token_problems(slots, "استغرق دقيقتين", "stem"))
        self.assertEqual(
            token_problems(slots, "استغرق دقيقتين", "stem", dropped=["N1"]),
            [],
        )
        _text, slots = protect("1 and 2 and 3", notation="latin")
        problems = token_problems(
            slots, "لا أرقام", "stem", dropped=["N1", "N2", "N3"]
        )
        self.assertTrue(problems)

    def test_editable_keeps_power(self) -> None:
        _text, slots = protect(r"A box of $27\text{ cm}^3$", notation="latin")
        self.assertEqual(
            token_problems(slots, r"⟦E1⟧$27\text{ سم}^3$⟦/E1⟧", "stem"),
            [],
        )
        problems = token_problems(slots, r"⟦E1⟧$27\text{ سم}$⟦/E1⟧", "stem")
        self.assertTrue(any("E1" in problem for problem in problems))

    def test_arabic_skeleton_folds_functions_and_mathbb(self) -> None:
        self.assertEqual(
            restored_problems(
                r"$f(\theta)=\sin\theta$",
                r"$د(\theta)=\text{جا}\,\theta$",
                "x",
                notation="arabic",
            ),
            [],
        )
        self.assertEqual(
            restored_problems(
                r"$x\in\mathbb{R}$",
                r"$س\in ح$",
                "x",
                notation="arabic",
            ),
            [],
        )
        self.assertEqual(
            restored_problems(
                r"$12\sin(180)^\circ$",
                r"$12\,\text{جا}(180)^\circ$",
                "x",
                notation="arabic",
            ),
            [],
        )
        self.assertTrue(
            restored_problems(
                r"$12\sin(180)^\circ$",
                r"$13\,\text{جا}(180)^\circ$",
                "x",
                notation="arabic",
            )
        )
        self.assertEqual(
            restored_problems(
                r"$\cos\frac{3\π}{2} = 0$",
                r"$\text{جتا}\,\frac{3\pi}{2} = 0$",
                "x",
                notation="arabic",
            ),
            [],
        )

    def test_vector_accents_fold_to_vec_but_widehat_does_not(self) -> None:
        self.assertEqual(
            restored_problems(
                r"$\overrightarrow{F}=3\hat{i}+4\hat{j}$",
                r"$\vec{ف}=3\vec{ا}+4\vec{ب}$",
                "stem",
                notation="arabic",
            ),
            [],
        )
        _text, slots = protect(
            r"Find $\overrightarrow{F}=3\hat{i}+4\hat{j}$.", notation="arabic"
        )
        self.assertEqual(
            token_problems(
                slots,
                r"Find ⟦E1⟧$\vec{ف}=3\vec{ا}+4\vec{ب}$⟦/E1⟧.",
                "stem",
            ),
            [],
        )
        self.assertNotEqual(
            skeleton(r"$\widehat{ABC}$", notation="arabic"),
            skeleton(r"$\vec{ABC}$", notation="arabic"),
        )
        self.assertTrue(
            restored_problems(
                r"$\widehat{ABC}$",
                r"$\vec{ابج}$",
                "stem",
                notation="arabic",
            )
        )

    def test_empty_and_single_token_scripts_compare_equal(self) -> None:
        for source, target in (
            (r"$\theta^{}=\frac{}{}$", r"$\theta^=\frac{}{}$"),
            ("$1^{}$", "$1^$"),
            (r"$\pi^{}$", r"$\pi^$"),
            (r"$\theta^{\text{د}}=\frac{}{}$", r"$\theta^د=\frac{}{}$"),
            ("$x^{2}$", "$x^2$"),
            ("$a_{n}$", "$a_n$"),
        ):
            self.assertEqual(
                skeleton(source, notation="arabic"),
                skeleton(target, notation="arabic"),
                f"{source} vs {target}",
            )
            self.assertEqual(
                restored_problems(source, target, "x", notation="arabic"),
                [],
            )
        self.assertTrue(
            restored_problems(
                r"$\theta^{}=\frac{1}{2}$",
                r"$\theta^=$",
                "x",
                notation="arabic",
            )
        )
        self.assertTrue(
            restored_problems("$1^{2}$", "$12$", "x", notation="arabic")
        )

    def test_arabic_notation_all_math_editable(self) -> None:
        text, slots = protect("$x+3=5$", notation="arabic")
        self.assertIn("E1", text)
        self.assertEqual(token_problems(slots, r"⟦E1⟧$س+3=5$⟦/E1⟧", "stem"), [])
        self.assertTrue(token_problems(slots, r"⟦E1⟧$س+4=5$⟦/E1⟧", "stem"))

    def test_restored_problems_good_and_lost_power(self) -> None:
        self.assertEqual(
            restored_problems(
                r"A $27\text{ cm}^3$ box",
                r"صندوق $27\text{ سم}^3$",
                "x",
                notation="latin",
            ),
            [],
        )
        self.assertTrue(
            restored_problems(
                r"A $27\text{ cm}^3$ box",
                r"صندوق $27\text{ سم}$",
                "x",
                notation="latin",
            )
        )

    def test_restored_problems_new_number(self) -> None:
        problems = restored_problems(
            "Ali has 12 pens",
            "لدى علي $12$ و $7$",
            "x",
            notation="latin",
        )
        self.assertTrue(any("new number" in problem for problem in problems))

    def test_prose_coefficient_counts_with_maths_and_labels_stay_ignored(self) -> None:
        self.assertEqual(
            restored_problems("4x - 9", r"$4س - 9$", "figure", notation="latin"),
            [],
        )
        self.assertEqual(
            restored_problems("2x + 7", r"$2س + 7$", "figure", notation="latin"),
            [],
        )
        self.assertEqual(_digit_counter("4x - 9"), _digit_counter(r"$4س - 9$"))
        self.assertEqual(_digit_counter("2x + 7"), _digit_counter(r"$2س + 7$"))
        invented = restored_problems(
            "Ali has 12 pens",
            "لدى علي $12$ و $7$",
            "figure",
            notation="latin",
        )
        self.assertTrue(any("new number" in problem for problem in invented), invented)
        self.assertEqual(_digit_counter("L_1 and T_2"), _digit_counter(r"$ل_1$ و$ل_2$"))
        self.assertNotIn("1", _digit_counter("L_1"))
        self.assertNotIn("1", _digit_counter("ل_1"))
        self.assertNotIn("2", _digit_counter("2nd place"))
        self.assertNotIn("3", _digit_counter("3cm wide"))
        self.assertNotIn("2", _digit_counter("select the 2D icon"))
        self.assertEqual(_digit_counter("[0, 2pi]"), _digit_counter(r"$[0, 2\pi]$"))
        self.assertEqual(
            _digit_counter(r"${}^8\mathrm{C}_3$"),
            _digit_counter(r"${}^8 ق_3$"),
        )
        self.assertEqual(
            _digit_counter(r"${}^{10}C_5$"),
            _digit_counter(r"${}^{10} ق_5$"),
        )
        self.assertEqual(_digit_counter(r"$\log_2 x$"), _digit_counter(r"$\text{لو}_2 س$"))
        self.assertEqual(_digit_counter(r"$3m_1 = 5m_2$"), _digit_counter(r"$3 ك_1 = 5 ك_2$"))
        self.assertEqual(_digit_counter(r"$m_1 = 4m_2$"), _digit_counter(r"$ك_1 = 4 ك_2$"))
        self.assertEqual(
            _digit_counter(r"$\frac{1}{2}mv_0^2$"),
            _digit_counter(r"$\frac{1}{2} ك ع_0^2$"),
        )
        swapped = restored_problems(
            "2x - 1",
            r"$2س - 5$",
            "figure",
            notation="latin",
        )
        self.assertTrue(
            any("new number" in problem and "5" in problem for problem in swapped),
            swapped,
        )

    def test_restored_problems_ratio_rejoined(self) -> None:
        self.assertEqual(
            restored_problems(
                "scale 1 : 2,500",
                "مقياس $1 : 2,500$",
                "x",
                notation="latin",
            ),
            [],
        )

    def test_merged_editable_spans_pass_and_a_dropped_answer_fails(self) -> None:
        _text, slots = protect(
            r"C, since $2\pi$ and $[-1, 1]$.",
            notation="arabic",
        )
        merged = r"بما أن ⟦E1⟧$2\pi=[-1, 1]$⟦/E1⟧."
        self.assertEqual(token_problems(slots, merged, "answer"), [])
        self.assertTrue(token_problems(slots, "ج", "answer"))
        changed = r"بما أن ⟦E1⟧$2\pi=[-1, 2]$⟦/E1⟧."
        self.assertTrue(token_problems(slots, changed, "answer"))

    def test_answer_prompt_keeps_explanation_placeholders(self) -> None:
        prompt = (
            Path(__file__).resolve().parents[2] / "prompts" / "translate" / "exercises.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Do not shorten an explained choice to the letter", prompt)

    def test_grade_wording_is_not_a_counted_quantity(self) -> None:
        self.assertEqual(
            restored_problems(
                "The heights of six Grade 7 students are $142$ cm.",
                "أطوال ستة طلاب في الصف السابع هي $142$ سم.",
                "x",
                notation="latin",
            ),
            [],
        )
        self.assertTrue(
            restored_problems(
                "The scores are $6$ and $7$.",
                "الدرجات هي $6$.",
                "x",
                notation="latin",
            )
        )

    def test_arabic_comma_matches_ascii_comma(self) -> None:
        self.assertEqual(
            restored_problems("$(0, 1)$", "$(0، 1)$", "x", notation="arabic"),
            [],
        )
        self.assertEqual(
            restored_problems(
                r"$r \in \{0, 1, 2, 3\}$",
                r"$ر \in \{0، 1، 2، 3\}$",
                "x",
                notation="arabic",
            ),
            [],
        )

    def test_prose_does_not_freeze_merged_letter_math(self) -> None:
        self.assertEqual(
            restored_problems(
                "then $a, b, c$ and $d$ are proportional",
                "فإن $a, b, c, d$ تكون متناسبة",
                "excerpt_1.text",
                notation="latin",
                freeze=False,
            ),
            [],
        )

    def test_protect_spec_round_trip_and_failing_field(self) -> None:
        from translate import spec_problem_field, spec_translation_problems

        source = {
            "overview": "",
            "objectives": [{"id": "objective_1", "text": "Find $x+12$."}],
            "prerequisites": [],
            "out_of_scope": [],
            "glossary": [],
            "teaching_points": [],
            "excerpts": [],
            "equations": [],
            "notations": [],
        }
        protected, slots = protect_spec(source, notation="arabic")
        self.assertIn("objective_1.text", slots)
        self.assertEqual(restore_spec(protected, slots)["objectives"][0]["text"], "Find $x+12$.")
        meta = {"language_code": "ar", "notation": "arabic", "numerals": "arabic"}
        protected_text = protected["objectives"][0]["text"]
        formula = protected_text[protected_text.index("⟦E") :]
        good_text = "أوجد " + formula.replace("$x+12$", "$س+12$")
        bad_text = "أوجد " + formula.replace("$x+12$", "$س+13$")
        good = {**source, "objectives": [{"id": "objective_1", "text": good_text}]}
        self.assertEqual(spec_translation_problems(source, good, meta, slots, []), [])
        bad = {**source, "objectives": [{"id": "objective_1", "text": bad_text}]}
        problems = spec_translation_problems(source, bad, meta, slots, [])
        self.assertTrue(problems)
        self.assertEqual(spec_problem_field(problems[0]), "objective_1.text")
        set_spec_strings(bad, {"objective_1.text": good["objectives"][0]["text"]})
        self.assertEqual(spec_translation_problems(source, bad, meta, slots, []), [])

    def test_protect_group_locations(self) -> None:
        group = {
            "id": "group_1",
            "stem": "stem",
            "figures": [],
            "poems": [],
            "exercises": [
                {
                    "id": "exercise_1",
                    "text": "text",
                    "options": ["one", "two"],
                    "answer": "answer",
                    "figures": [],
                }
            ],
        }
        _protected, slots = protect_group(group, notation="latin")
        self.assertEqual(
            set(slots),
            {
                "stem",
                "exercise_1.text",
                "exercise_1.options[0]",
                "exercise_1.options[1]",
                "exercise_1.answer",
            },
        )


if __name__ == "__main__":
    unittest.main()
