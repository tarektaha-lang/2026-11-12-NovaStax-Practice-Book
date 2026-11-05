from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from pydantic import ValidationError  # noqa: E402

from figures.function_graph import curve_from_fields, quadratic_from_label  # noqa: E402
from new_questions import (  # noqa: E402
    Exercise,
    ExerciseGroup,
    Figure,
    Graph,
    GraphCurve,
    content_words,
    count_parabola_fixes,
    degree_bullets,
    field_problems,
    interval_problems,
    is_form_set_group,
    math_key,
    math_keys,
    pack_form_sets,
    parabola_fix,
    repair_groups_arabic_curve_labels,
    repair_groups_bare_underlines,
    repair_groups_degree_bullets,
    repair_groups_excessive_tashkeel,
    repair_groups_mark_placement,
    repair_groups_math_choices,
    repair_groups_paren_delimiters,
    repaired,
    restated,
    source_artifact_problems,
    strip_option_label,
    stuck_token,
    unlabelled_choices,
    write_problems,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "new_questions"


def one_group(*, text: str = "Which point?", options: list[str] | None = None) -> ExerciseGroup:
    return ExerciseGroup(
        exercises=[
            Exercise(
                text=text,
                options=options or ["$1$", "$2$", "$3$", "$4$"],
                objectives=["objective_1"],
                answer="B",
            )
        ]
    )


class DegreeBulletRepairTests(unittest.TestCase):
    def test_degree_bullets_rewrites_measure_not_operator(self) -> None:
        self.assertEqual(degree_bullets(r"$48^\bullet$"), r"$48^\circ$")
        self.assertEqual(degree_bullets(r"$٤٨^\bullet$"), r"$٤٨^\circ$")
        self.assertEqual(degree_bullets(r"$٣ س^\bullet$"), r"$٣ س^\circ$")
        self.assertEqual(degree_bullets(r"$(س + 20)^\bullet$"), r"$(س + 20)^\circ$")
        self.assertEqual(degree_bullets(r"$a \bullet b$"), r"$a \bullet b$")

    def test_repair_groups_degree_bullets_rewrites_fields(self) -> None:
        group = one_group(
            text=r"قياس الزاوية $٤٨^\bullet$.",
            options=[r"$٤٨^\bullet$", r"$١٣٢^\circ$", r"$٩٠^\bullet$", r"$٤٨^\circ$"],
        )
        group.exercises[0].answer = r"$٤٨^\bullet$"
        fixed = repair_groups_degree_bullets([group])[0]
        self.assertEqual(fixed.exercises[0].text, r"قياس الزاوية $٤٨^\circ$.")
        self.assertEqual(fixed.exercises[0].answer, r"$٤٨^\circ$")
        self.assertEqual(fixed.exercises[0].options[0], r"$٤٨^\circ$")
        self.assertEqual(fixed.exercises[0].options[2], r"$٩٠^\circ$")


class UnlabelledChoicesTests(unittest.TestCase):
    def test_strip_option_label_drops_prefixed_letter(self) -> None:
        self.assertEqual(strip_option_label("(أ) الحذف"), "الحذف")
        self.assertEqual(strip_option_label("(B) Correct English"), "Correct English")
        self.assertEqual(strip_option_label("الحذف"), "الحذف")

    def test_unlabelled_choices_rewrites_prefixed_options(self) -> None:
        group = one_group(
            options=[
                "(A) All information is included",
                "(B) Correct English (punctuation and spelling)",
                "(C) Words are suitable and varied",
                "(D) It is neat and clear",
            ]
        )
        rewritten, labelled = unlabelled_choices([group])
        self.assertEqual(labelled, 4)
        self.assertEqual(
            rewritten[0].exercises[0].options,
            [
                "All information is included",
                "Correct English (punctuation and spelling)",
                "Words are suitable and varied",
                "It is neat and clear",
            ],
        )

    def test_write_problems_flags_square_without_a_house(self) -> None:
        group = one_group(text=r"The digit at $\square$ is $\blank$.")
        problems = write_problems(
            [group], 1, "objective_1", ["objective_1"], frozenset()
        )
        self.assertTrue(any("short-division house" in row for row in problems))

    def test_write_problems_accepts_square_pointing_at_a_house(self) -> None:
        house = (
            r"$$\begin{array}{r}12\\4\overline{ )4\square}\end{array}$$"
        )
        group = ExerciseGroup(
            stem=r"The digit at $\square$ is $\blank$.",
            exercises=[
                Exercise(
                    text=house,
                    options=["$1$", "$2$", "$3$", "$4$"],
                    objectives=["objective_1"],
                    answer="B",
                )
            ],
        )
        problems = write_problems(
            [group], 1, "objective_1", ["objective_1"], frozenset()
        )
        self.assertFalse(any("short-division house" in row for row in problems))

    def test_write_problems_uses_objective_allow_list(self) -> None:
        problems = write_problems(
            [one_group()],
            1,
            "objective_1",
            ["objective_1"],
            frozenset(),
        )
        self.assertEqual(problems, [])

    def test_trailing_blank_is_closed(self) -> None:
        group = ExerciseGroup(
            exercises=[
                Exercise(
                    text=r"Investigating the sign of a function $f$ means \blank",
                    objectives=["objective_1"],
                    answer="where $f(x) > 0$",
                )
            ]
        )
        self.assertEqual(
            write_problems([group], 1, "objective_1", ["objective_1"], frozenset()),
            [],
        )

    def test_words_then_maths_still_need_a_stop(self) -> None:
        group = ExerciseGroup(
            exercises=[
                Exercise(
                    text=r"Simplify: $x^2$",
                    objectives=["objective_1"],
                    answer="$x^2$",
                )
            ]
        )
        problems = write_problems(
            [group], 1, "objective_1", ["objective_1"], frozenset()
        )
        self.assertTrue(any("full stop" in problem for problem in problems))

    def test_write_problems_rejects_odd_dollars(self) -> None:
        group = one_group()
        group.exercises[0].answer = r"$4\%"
        problems = write_problems(
            [group], 1, "objective_1", ["objective_1"], frozenset()
        )
        self.assertTrue(any("odd number of $ signs" in row for row in problems))

    def test_write_problems_rejects_unbalanced_braces(self) -> None:
        group = one_group()
        group.exercises[0].text = r"$1\text{ ريال عماني} = 1000\text{ بيسة$."
        problems = write_problems(
            [group], 1, "objective_1", ["objective_1"], frozenset()
        )
        self.assertTrue(any("unbalanced braces" in row for row in problems))

    def test_write_problems_rejects_template_placeholders_and_leakage(self) -> None:
        leaked = (
            r"$4\text{ m}$ preference indicator removed from options "
            r"purely mathematical value follows: $4\text{ m}$"
        )
        self.assertTrue(
            any(
                "unfilled template placeholder" in row
                for row in source_artifact_problems("${d}$", "options[1]")
            )
        )
        self.assertTrue(
            any(
                "generator prompt leakage" in row
                for row in source_artifact_problems(leaked, "options[0]")
            )
        )
        problems = write_problems(
            [
                one_group(
                    options=[leaked, "${d}$", "${e}$", "${f}$"],
                )
            ],
            1,
            "objective_1",
            ["objective_1"],
            frozenset(),
        )
        self.assertTrue(any("unfilled template placeholder" in row for row in problems))
        self.assertTrue(any("generator prompt leakage" in row for row in problems))


def _load_restated_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _group_from_fixture(payload: dict) -> ExerciseGroup:
    return ExerciseGroup(
        stem=payload["stem"],
        exercises=[
            Exercise(
                text=row["text"],
                objectives=row.get("objectives") or ["objective_1"],
                answer=row.get("answer") or "$0$",
            )
            for row in payload["exercises"]
        ],
    )


class RestatedMathAwareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.axis = _load_restated_fixture("restated_1_3_1.json")
        self.moments = _load_restated_fixture("restated_applied_moments.json")
        self.work = _load_restated_fixture("restated_applied_work.json")
        self.point_cases = _load_restated_fixture("restated_point_cases.json")
        self.axis_stem = self.axis["stem"]
        self.axis_texts = [row["text"] for row in self.axis["exercises"]]

    def test_t1_1_real_1_3_1_exercises_are_not_restated(self) -> None:
        for text in self.axis_texts:
            with self.subTest(text=text):
                self.assertFalse(restated(self.axis_stem, text, self.axis_texts))

    def test_t1_2_find_x_in_the_triangle_restates_the_stem(self) -> None:
        stem = (
            "In each of the following right-angled triangles, find the value of $x$:"
        )
        text = "Find $x$ in the triangle."
        self.assertTrue(restated(stem, text, [text, "$3, 4, 5$"]))

    def test_t1_3_identical_x_axis_siblings_are_restated(self) -> None:
        stem = "Find the distance to the $x$-axis:"
        siblings = [r"to the $x$-axis", r"to the $x$-axis."]
        self.assertTrue(restated(stem, siblings[0], siblings))

    def test_t1_4_full_instruction_under_1_3_1_stem_is_restated(self) -> None:
        text = (
            r"Find the perpendicular distance from $(0,-3,4)$ to the $x$-axis."
        )
        self.assertTrue(restated(self.axis_stem, text, [text, self.axis_texts[1]]))

    def test_t1_5_left_right_and_spacing_share_a_math_key(self) -> None:
        self.assertEqual(math_key(r"$\left(1,2\right)$"), math_key("$(1, 2)$"))

    def test_t1_6_write_problems_accepts_the_1_3_1_fixture(self) -> None:
        problems = write_problems(
            [_group_from_fixture(self.axis)],
            len(self.axis_texts),
            "objective_3",
            ["objective_3"],
            frozenset(),
        )
        self.assertFalse(
            any("says only what its stem says" in row for row in problems)
        )

    def test_t1_7_factorize_repeats_the_instruction(self) -> None:
        stem = "Factorize each of the following:"
        text = r"Factorize $x^2 - 9$."
        self.assertTrue(restated(stem, text, [text, r"$x^2 - 4$"]))

    def test_t1_8_point_moving_on_a_curve_is_not_restated(self) -> None:
        stem = (
            r"In each of the following, a point $(x, y)$ moves along the given "
            r"curve. Find the value of $\frac{dy}{dt}$ at the given instant:"
        )
        text = (
            r"$x^2 + y^2 = 25$, at the point $(3, 4)$, given that "
            r"$\frac{dx}{dt} = 4\text{ units/s}$"
        )
        sibling = (
            r"$y^2 = 8x$, at the point $(2, 4)$, given that "
            r"$\frac{dx}{dt} = 3\text{ units/s}$"
        )
        self.assertFalse(restated(stem, text, [text, sibling]))

    def test_t1_9_velocity_sentence_is_not_restated(self) -> None:
        stem = (
            r"A body of mass $1\text{ kg}$ moves along a straight line starting "
            r"from the origin $O$ with an initial velocity of $4\text{ m/sec}$ "
            r"under the action of a force $F = (6t - 2)\text{ N}$, where $t$ is "
            r"the time in seconds."
        )
        text = (
            r"The velocity of the body at $t = 2\text{ seconds}$ is "
            r"$\blank\text{ m/sec}$."
        )
        sibling = r"The displacement of the body at $t = 2\text{ seconds}$."
        self.assertFalse(restated(stem, text, [text, sibling]))

    def test_t1_10_capital_x_and_x_are_different_keys(self) -> None:
        self.assertNotEqual(math_key("$X$"), math_key("$x$"))
        self.assertNotEqual(math_keys("$X$"), math_keys("$x$"))

    def test_t1_11_applied_beam_point_siblings_are_not_restated(self) -> None:
        texts = [row["text"] for row in self.moments["exercises"]]
        for text in texts:
            with self.subTest(text=text):
                self.assertFalse(restated(self.moments["stem"], text, texts))

    def test_t1_12_due_east_beside_due_west_is_not_restated(self) -> None:
        texts = [row["text"] for row in self.work["exercises"]]
        self.assertIn("due East", texts)
        self.assertIn("due West", texts)
        self.assertFalse(restated(self.work["stem"], "due East", texts))

    def test_t1_13_identical_repeated_siblings_are_restated(self) -> None:
        stem = "Find the distance to the $x$-axis:"
        siblings = [r"to the $x$-axis"] * 3
        self.assertTrue(restated(stem, siblings[0], siblings))

    def test_t1_14_identical_find_x_siblings_are_restated_single_is_not_called(
        self,
    ) -> None:
        stem = (
            "In each of the following right-angled triangles, find the value of $x$:"
        )
        text = "Find $x$ in the triangle."
        self.assertTrue(restated(stem, text, [text, text, text]))
        group = ExerciseGroup(
            stem=stem,
            exercises=[
                Exercise(text=text, objectives=["objective_1"], answer="$3$")
            ],
        )
        problems = write_problems(
            [group], 1, "objective_1", ["objective_1"], frozenset()
        )
        self.assertFalse(
            any("says only what its stem says" in row for row in problems)
        )

    def test_t1_15_real_point_cases_are_not_restated(self) -> None:
        self.assertGreaterEqual(len(self.point_cases), 14)
        for row in self.point_cases:
            with self.subTest(text=row["text"], stem=row["stem"][:40]):
                self.assertFalse(
                    restated(row["stem"], row["text"], row["siblings"])
                )

    def test_t1_16_find_the_work_done_restates_before_uniqueness(self) -> None:
        stem = "Find the work done when its direction is:"
        text = "Find the work done."
        self.assertTrue(restated(stem, text, [text, "due West"]))

    def test_t1_17_pointing_word_skips_clause_start(self) -> None:
        stem = (
            r"In each of the following, a point $(x,y)$ moves along the given curve:"
        )
        text = r"a point moves on $x^2+y^2=25$"
        sibling = r"a point moves on $y^2=8x$"
        self.assertFalse(restated(stem, text, [text, sibling]))

    def test_t1_18_pointing_words_drop_before_rstrip(self) -> None:
        self.assertEqual(content_words("this is"), set())

    def test_t1_19_comma_clause_catches_triangle_siblings(self) -> None:
        stem = (
            "In each of the following right-angled triangles, find the value of $x$:"
        )
        siblings = [
            "Find $x$.",
            "Find $x$ in the triangle.",
            "Find the value of $x$.",
        ]
        for text in siblings:
            with self.subTest(text=text):
                self.assertTrue(restated(stem, text, siblings))

    def test_t1_20_pointing_start_under_a_comma_stem_is_not_restated(self) -> None:
        stem = (
            "In each of the following, find the derivative of the given function:"
        )
        text = r"in each case the function $f(x)=x^2$"
        sibling = r"in each case the function $f(x)=x^3$"
        self.assertFalse(restated(stem, text, [text, sibling]))


def _parabola_group(
    label: str,
    *,
    opens: str | None = None,
    vertex: list[float] | None = None,
) -> ExerciseGroup:
    curve_kwargs: dict = {"kind": "parabola", "label": label}
    if opens is not None:
        curve_kwargs["opens"] = opens
    if vertex is not None:
        curve_kwargs["vertex"] = vertex
    return ExerciseGroup(
        exercises=[
            Exercise(
                text="The graph of the given function.",
                objectives=["objective_1"],
                answer="$0$",
                figures=[
                    Figure(
                        figure_type="function graph",
                        description="A parabola on labelled axes.",
                        graph=Graph(
                            x_ticks=[1],
                            y_ticks=[1],
                            curves=[GraphCurve(**curve_kwargs)],
                        ),
                    )
                ],
            )
        ]
    )


class ParabolaCurveTests(unittest.TestCase):
    def test_vertex_without_intercepts_is_enough(self) -> None:
        GraphCurve(
            kind="parabola",
            label="f(x)=(x-2)^2",
            vertex=[2, 0],
            opens="up",
        )

    def test_parabola_needs_vertex_or_x_intercept(self) -> None:
        with self.assertRaises(ValidationError):
            GraphCurve(kind="parabola", label="y = f''(x)", opens="up")

    def test_t2_1_parser_table(self) -> None:
        accepted = [
            ("y = x^2 - 4", (1.0, 0.0, -4.0)),
            ("y = 4 - x^2", (-1.0, 0.0, 4.0)),
            ("f(x) = -2x^2 + 3x - 1", (-2.0, 3.0, -1.0)),
            (r"$y = \frac{1}{2}x^{2} + x$", (0.5, 1.0, 0.0)),
            ("y = 0.5x^2", (0.5, 0.0, 0.0)),
            ("y = -(x - 1)^2 + 3", (-1.0, 2.0, 2.0)),
            (r"y = 2\left(x+1\right)^2 - 8", (2.0, 4.0, -6.0)),
            ("f(t) = -t^2 + 4t - 3", (-1.0, 4.0, -3.0)),
            ("F = 2 + (t - 3)^2", (1.0, -6.0, 11.0)),
            ("F = 12 - 3(t-2)^2", (-3.0, 12.0, 0.0)),
        ]
        for label, expected in accepted:
            with self.subTest(label=label):
                got = quadratic_from_label(label)
                self.assertIsNotNone(got)
                self.assertEqual(len(got), 3)
                for value, want in zip(got, expected):
                    self.assertAlmostEqual(value, want, places=9)
        for label in (
            "y = f''(x)",
            "y = x^3",
            "y = 2x+1",
            "y = ax^2",
            "y = x^2 = 4",
            "ص = س^2",
        ):
            with self.subTest(label=label):
                self.assertIsNone(quadratic_from_label(label))

    def test_t2_2_real_curves_from_fixture(self) -> None:
        payload = json.loads(
            (FIXTURES / "parabola_2_2_3.json").read_text(encoding="utf-8")
        )
        for raw in payload["curves"]:
            label = raw["label"]
            with self.subTest(label=label):
                if quadratic_from_label(label) is None:
                    with self.assertRaises(ValidationError):
                        GraphCurve.model_validate(
                            {k: v for k, v in raw.items() if v is not None}
                        )
                    continue
                curve = GraphCurve.model_validate(
                    {k: v for k, v in raw.items() if v is not None}
                )
                self.assertIsNotNone(curve.vertex)
                if label == "y = x^2 - 4":
                    self.assertEqual(curve.vertex[0], 0.0)
                    self.assertAlmostEqual(curve.vertex[1], -4.0)
                if label == "y = 4 - x^2":
                    self.assertEqual(curve.vertex[0], 0.0)
                    self.assertAlmostEqual(curve.vertex[1], 4.0)

    def test_t2_3_opens_corrected_from_label(self) -> None:
        curve = GraphCurve(kind="parabola", label="y = x^2 - 4", opens="down")
        self.assertEqual(curve.opens, "up")
        self.assertTrue(curve._parabola_opens_corrected)
        self.assertTrue(curve._parabola_vertex_derived)
        vertex, opens = parabola_fix(
            {"kind": "parabola", "label": "y = x^2 - 4", "opens": "down"}
        )
        self.assertEqual(opens, "up")
        self.assertEqual(vertex[0], 0.0)
        self.assertAlmostEqual(vertex[1], -4.0)

    def test_t2_4_render_path_uses_the_parser(self) -> None:
        spec = curve_from_fields({"kind": "parabola", "label": "y = x^2 - 4"})
        self.assertEqual(spec.vertex[0], 0.0)
        self.assertAlmostEqual(spec.vertex[1], -4.0)
        self.assertAlmostEqual(spec.a, 1.0)
        self.assertEqual(spec.opens, "up")
        spec = curve_from_fields(
            {"kind": "parabola", "label": r"$y = \frac{1}{2}x^{2} + x$"}
        )
        self.assertAlmostEqual(spec.a, 0.5)
        self.assertAlmostEqual(spec.vertex[0], -1.0)
        self.assertAlmostEqual(spec.vertex[1], -0.5)

    def test_t2_5_existing_vertex_is_kept(self) -> None:
        spec = curve_from_fields(
            {
                "kind": "parabola",
                "label": "y = x^2 - 4",
                "vertex": [1, 2],
                "opens": "down",
            }
        )
        self.assertEqual(spec.vertex, (1.0, 2.0))
        self.assertAlmostEqual(spec.a, 1.0)
        self.assertEqual(spec.opens, "up")

    def test_t2_6_counting_pass_survives_canonicalize(self) -> None:
        raw = {
            "kind": "parabola",
            "label": "y = x^2 - 4",
            "opens": "down",
        }
        self.assertEqual(count_parabola_fixes({"curves": [raw]}), (1, 1))
        group = _parabola_group("y = x^2 - 4", opens="down")
        kept, repairs = repaired([group], 1)
        self.assertEqual(repairs["parabola_vertex_derived"], 1)
        self.assertEqual(repairs["parabola_opens_corrected"], 1)
        curve = kept[0].exercises[0].figures[0].graph.curves[0]
        self.assertEqual(curve.opens, "up")
        self.assertEqual(curve.vertex[0], 0.0)
        self.assertAlmostEqual(curve.vertex[1], -4.0)


class TashkeelGroupRepairTests(unittest.TestCase):
    def test_repair_groups_excessive_tashkeel_strips_full_vocalisation(self) -> None:
        stem = (
            "تُرْجِعُ الدِّرَاسَاتُ التَّارِيخِيَّةُ أَصْلَ مُصْطَلَحَاتِ "
            "العُصُورِ الحَجَرِيَّةِ إِلَى اللُّغَةِ."
        )
        group = ExerciseGroup(
            stem=stem,
            exercises=[
                Exercise(
                    text=stem,
                    objectives=["objective_1"],
                    answer="أ",
                )
            ],
        )
        fixed = repair_groups_excessive_tashkeel([group])[0]
        self.assertNotIn("تُرْجِعُ", fixed.stem)
        self.assertNotIn("تُرْجِعُ", fixed.exercises[0].text)

    def test_repair_groups_mark_placement_fixes_bare_shadda(self) -> None:
        group = ExerciseGroup(
            stem="بيّن معنى الجملة.",
            exercises=[
                Exercise(
                    text="حدّد دائماً.",
                    options=["شرعيًا", "كافيًا", "خاليًا", "تماماً"],
                    objectives=["objective_1"],
                    answer="أ",
                )
            ],
        )
        fixed = repair_groups_mark_placement([group])[0]
        self.assertTrue(fixed.stem.startswith("بيِّن"))
        self.assertIn("حدِّد", fixed.exercises[0].text)
        self.assertIn("دائمًا", fixed.exercises[0].text)
        self.assertEqual(fixed.exercises[0].options[0], "شرعيًّا")
        self.assertEqual(fixed.exercises[0].options[3], "تمامًا")


class ArabicCurveLabelRepairTests(unittest.TestCase):
    def test_latin_curve_label_follows_axis_letters_in_description(self) -> None:
        group = ExerciseGroup(
            stem="يبين التمثيل العلاقة بين الكتلة بالرطل ($س$) والأوقية ($ص$).",
            exercises=[
                Exercise(
                    text="ماذا يمثل الزوج $(3، 48)$؟",
                    objectives=["objective_1"],
                    answer="$48$ أوقية.",
                    figures=[
                        Figure(
                            figure_type="function graph",
                            description=(
                                "المحور الأفقي (س) والمحور الرأسي (ص). "
                                "يمر الخط بنقطة الأصل."
                            ),
                            graph=Graph(
                                x_ticks=[1, 2, 3],
                                y_ticks=[16, 32, 48],
                                domain=[0, 5],
                                range=[0, 80],
                                curves=[
                                    GraphCurve(
                                        kind="line",
                                        label="y = 16x",
                                        points=[[0, 0], [5, 80]],
                                    )
                                ],
                            ),
                        )
                    ],
                )
            ],
        )
        fixed = repair_groups_arabic_curve_labels([group])[0]
        self.assertEqual(fixed.exercises[0].figures[0].graph.curves[0].label, "ص = 16س")


class MathChoiceRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.choices = _load_restated_fixture("choices_2_2_3.json")
        self.one_three = _load_restated_fixture("choices_1_3_1.json")

    def test_bare_underline_in_arabic_prose_is_wrapped_for_math(self) -> None:
        text = r"سافر الشاب \underline{\text{رغبة}} في العلم."
        groups, wrapped = repair_groups_bare_underlines([one_group(text=text)])
        self.assertGreater(wrapped, 0)
        fixed = groups[0].exercises[0].text
        self.assertIn(r"$\underline{\text{رغبة}}$", fixed)
        self.assertEqual(field_problems({"text": fixed}, "group 1 exercise 1"), [])

    def test_t3_1_half_wrapped_intervals_then_clean(self) -> None:
        options = list(self.choices["half_wrapped"]) + ["$1$", "$2$", "$3$"]
        options = options[:4]
        groups, wrapped = repair_groups_math_choices([one_group(options=options)])
        self.assertGreater(wrapped, 0)
        option = groups[0].exercises[0].options[0]
        self.assertTrue(option.startswith("$") and option.endswith("$"))
        self.assertEqual(
            field_problems({"choice (A)": option}, "group 1 exercise 1"),
            [],
        )
        self.assertEqual(
            interval_problems(option, "group 1 exercise 1 choice (A)"),
            [],
        )

    def test_t3_2_and_case_stays_flagged(self) -> None:
        option = self.choices["and_case"]
        groups, wrapped = repair_groups_math_choices([one_group(options=[option, "$1$", "$2$", "$3$"])])
        self.assertEqual(groups[0].exercises[0].options[0], option)
        self.assertEqual(wrapped, 0)
        problems = write_problems(
            [one_group(options=[option, "$1$", "$2$", "$3$"])],
            1,
            "objective_1",
            ["objective_1"],
            frozenset(),
        )
        self.assertTrue(any("broken interval" in row for row in problems))

    def test_t3_3_garbled_flagged_unions_and_paren_interval_not(self) -> None:
        for option in self.choices["garbled"]:
            with self.subTest(option=option):
                self.assertTrue(
                    interval_problems(option, "group 1 exercise 1 choice (A)")
                )
        self.assertEqual(
            interval_problems(r"$(-\infty, 3]$", "group 1 exercise 1 choice (A)"),
            [],
        )
        self.assertTrue(
            interval_problems(r"$-\infty, 4]$", "group 1 exercise 1 answer")
        )
        for option in self.choices["unions"]:
            with self.subTest(option=option):
                self.assertEqual(
                    interval_problems(option, "group 1 exercise 1 choice (A)"),
                    [],
                )

    def test_t3_4_already_wrapped_and_prose_unchanged(self) -> None:
        options = ["Scalene", "$5$", r"$]1, \infty[$", "$4$"]
        groups, wrapped = repair_groups_math_choices([one_group(options=options)])
        self.assertEqual(groups[0].exercises[0].options, options)
        self.assertEqual(wrapped, 0)

    def test_t3_5_bare_math_choices_are_wrapped(self) -> None:
        options = [
            r"b^2 - 4ac",
            r"3xy(2x^2-1)",
            r"CP = \frac12 DP",
            r"2mg",
        ]
        groups, wrapped = repair_groups_math_choices([one_group(options=options)])
        self.assertEqual(wrapped, 4)
        self.assertEqual(
            groups[0].exercises[0].options,
            [f"${item}$" for item in options],
        )
        extra = [r"2\text{ length units}", r"2(x^2+y^2+z^2)", "$1$", "$2$"]
        groups, wrapped = repair_groups_math_choices([one_group(options=extra)])
        self.assertEqual(wrapped, 2)
        self.assertEqual(
            groups[0].exercises[0].options[:2],
            [r"$2\text{ length units}$", r"$2(x^2+y^2+z^2)$"],
        )

    def test_t3_6_paren_delimiters_converted(self) -> None:
        group = one_group(
            text=r"The value is \(5\).",
            options=[r"\(]2, \infty[\)", "$1$", "$2$", "$3$"],
        )
        group.exercises[0].answer = r"\(5\)"
        groups, converted = repair_groups_paren_delimiters([group])
        self.assertEqual(converted, 3)
        exercise = groups[0].exercises[0]
        self.assertEqual(exercise.text, r"The value is $5$.")
        self.assertEqual(exercise.options[0], r"$]2, \infty[$")
        self.assertEqual(exercise.answer, r"$5$")
        skipped = one_group(options=[r"\$Mg - T'", "$1$", "$2$", "$3$"])
        groups, converted = repair_groups_paren_delimiters([skipped])
        self.assertEqual(converted, 0)
        self.assertEqual(groups[0].exercises[0].options[0], r"\$Mg - T'")

    def test_t3_7_arabic_word_choice_unchanged(self) -> None:
        options = ["الحذف", "الإضافة", "الضرب", "القسمة"]
        groups, wrapped = repair_groups_math_choices([one_group(options=options)])
        self.assertEqual(wrapped, 0)
        self.assertEqual(groups[0].exercises[0].options, options)

    def test_t3_8_repaired_counts_wrapped_choices(self) -> None:
        group = one_group(options=[r"]1, \infty[$", "$1$", "$2$", "$3$"])
        _, repairs = repaired([group], 1)
        self.assertEqual(repairs["math_choices_wrapped"], 1)

    def test_t3_9_catherine_is_generator_leakage(self) -> None:
        option = self.one_three["catherine"]
        problems = source_artifact_problems(option, "group 1 exercise 1 choice (A)")
        self.assertTrue(any("generator prompt leakage" in row for row in problems))
        problems = write_problems(
            [one_group(options=[option, "$1$", "$2$", "$3$"])],
            1,
            "objective_1",
            ["objective_1"],
            frozenset(),
        )
        self.assertTrue(any("generator prompt leakage" in row for row in problems))

    def test_t3_10_interval_false_positives_and_hits(self) -> None:
        clean = [
            r"$D(2, -5)$",
            r"$A'(-4, 0)$",
            r"$\gcd(a, b)$",
            r"$-0.625$",
            r"$-0.5x(x-4)=0$",
            r"$30^\circ\text{--}60^\circ$",
            r"$n \in ]40, 250[$",
            r"$\mathbb{R} - ]-2, 2[$",
            r"$\mathbb{R} - [-2, 2]$",
        ]
        for option in clean:
            with self.subTest(option=option):
                self.assertEqual(interval_problems(option, "where"), [])
        broken = [r"$-\infty, 0[$", r"$-0, 3[$", r"$--2, 4[$"]
        for option in broken:
            with self.subTest(option=option):
                self.assertTrue(interval_problems(option, "where"))

    def test_jerry_stuck_token_still_flags(self) -> None:
        option = self.choices["jerry"]
        self.assertEqual(stuck_token(option), "Jerry")


class FormSetCapTests(unittest.TestCase):
    def test_eight_form_sets_do_not_hit_half_in_sets_cap(self) -> None:
        groups, _ = pack_form_sets(
            [
                ExerciseGroup(
                    exercises=[
                        Exercise(
                            text="Put ($\\checkmark$) or ($\\times$): statement.",
                            objectives=["objective_1"],
                            answer="($\\checkmark$)",
                        )
                    ]
                )
                for _ in range(8)
            ],
            language_code="en",
            source_stems={
                "tf": "Put ($\\checkmark$) for true and ($\\times$) for false:",
            },
        )
        self.assertEqual(len(groups), 1)
        self.assertTrue(is_form_set_group(groups[0]))
        problems = write_problems(groups, 8, "objective_1", ["objective_1"], frozenset())
        self.assertEqual(problems, [])

    def test_eight_passage_set_still_hits_half_cap(self) -> None:
        groups = [
            ExerciseGroup(
                stem="Read the passage and answer.",
                exercises=[
                    Exercise(
                        text=f"Question {index}?",
                        objectives=["objective_1"],
                        answer="Answer.",
                    )
                    for index in range(8)
                ],
            )
        ]
        problems = write_problems(groups, 8, "objective_1", ["objective_1"], frozenset())
        self.assertTrue(
            any("more than the 4 allowed" in line for line in problems),
            problems,
        )


if __name__ == "__main__":
    unittest.main()
