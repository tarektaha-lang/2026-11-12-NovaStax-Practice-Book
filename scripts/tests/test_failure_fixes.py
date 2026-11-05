"""The five remaining translation-failure causes, as general rules."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from new_questions import with_backslashes  # noqa: E402
from src.english_json import (  # noqa: E402
    dump_english_json,
    english_control_error,
    format_english_hits,
    load_english_json,
    repair_english_string,
    repair_english_tree,
)
from src.protect import (  # noqa: E402
    _digit_counter,
    latin_function_output_problems,
    restored_problems,
    skeleton,
)
from src.translation_quality import (  # noqa: E402
    _notation_form_problems,
    document_problems,
    function_letters_of,
)


ARABIC = {"language_code": "ar", "notation": "arabic", "numerals": "western"}


def _group(description: str, arabic: str) -> tuple[dict, dict]:
    source = {
        "id": "group_1",
        "stem": "Look at the figure.",
        "figures": [],
        "exercises": [
            {
                "id": "exercise_1",
                "text": "Describe the move.",
                "options": [],
                "answer": "left",
                "figures": [{"description": description}],
            }
        ],
    }
    target = {
        "id": "group_1",
        "stem": "انظر إلى الشكل.",
        "figures": [],
        "exercises": [
            {
                "id": "exercise_1",
                "text": "صف الحركة.",
                "options": [],
                "answer": "يسار",
                "figures": [{"description": arabic}],
            }
        ],
    }
    return source, target


class EnglishControlRepairTests(unittest.TestCase):
    def test_known_json_escapes_become_commands_and_a_second_pass_is_clean(self) -> None:
        times = json.loads(r'"$(n-2) \times 180^\\circ$"')
        fixed, hits = repair_english_string(times)
        self.assertEqual(fixed, r"$(n-2) \times 180^\circ$")
        self.assertNotIn("\t", fixed)
        self.assertEqual(hits[0][2], "repaired")
        again, second = repair_english_string(fixed)
        self.assertEqual(again, fixed)
        self.assertEqual(second, [])

        frac = json.loads(r'"\frac{1}{2}"')
        self.assertEqual(repair_english_string(frac)[0], r"\frac{1}{2}")
        triangle = json.loads(r'"\triangle ABC"')
        self.assertEqual(repair_english_string(triangle)[0], r"\triangle ABC")
        self.assertEqual(repair_english_string("line one\nline two")[0], "line one\nline two")
        self.assertEqual(repair_english_string("a\neq b")[0], "a\\neq b")
        self.assertEqual(
            repair_english_string("d.\ne. State your opinion")[0],
            "d.\ne. State your opinion",
        )

    def test_nul_before_a_command_or_dollar_is_repaired_and_other_controls_are_refused(self) -> None:
        parsed = json.loads(json.dumps("$\x00cos \\theta = x\x00$"))
        fixed, hits = repair_english_string(parsed)
        self.assertEqual(fixed, r"$\cos \theta = x$")
        self.assertTrue(all(status == "repaired" for _before, _after, status in hits))
        self.assertGreaterEqual(len(hits), 2)

        stray, hits = repair_english_string("keep\x0bthis")
        self.assertIn("\x0b", stray)
        self.assertTrue(any(status == "refused" for _b, _a, status in hits))
        _tree, tree_hits = repair_english_tree({"spec": {"overview": "keep\x0bthis"}})
        message = english_control_error("03_section_specs/04_03.json", tree_hits)
        self.assertIn("03_section_specs/04_03.json", message)
        self.assertIn("spec.overview", message)
        self.assertIn("<VT>", message)
        with self.assertRaises(ValueError) as caught:
            dump_english_json({"overview": "keep\x0bthis"})
        self.assertIn("overview", str(caught.exception))

    def test_dump_and_load_round_trip_without_rewriting_a_refused_or_clean_file(self) -> None:
        text = dump_english_json({"overview": json.loads(r'"$(n-2) \times 180"')})
        loaded = json.loads(text)
        self.assertEqual(loaded["overview"], "$(n-2) \\times 180")
        self.assertNotIn("\t", loaded["overview"])
        self.assertIn("\\\\times", text)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "04_03.json"
            path.write_text(json.dumps({"overview": "$\x00cos \\theta = x\x00$"}), encoding="utf-8")
            before = path.read_bytes()
            document = load_english_json(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(document["overview"], r"$\cos \theta = x$")
            lines = format_english_hits("mathematics_en_eg10_t1", path, repair_english_tree(
                json.loads(before)
            )[1])
            self.assertTrue(lines)
            self.assertTrue(all(line.startswith("mathematics_en_eg10_t1 | 04_03.json |") for line in lines))
            self.assertTrue(all(line.endswith("| repaired") for line in lines))

    def test_future_english_writer_repairs_only_known_commands(self) -> None:
        self.assertEqual(with_backslashes(json.loads(r'"(n-2) \times 180"')), r"(n-2) \times 180")
        with self.assertRaises(ValueError) as caught:
            with_backslashes("\thello")
        self.assertIn("hello", str(caught.exception))


class DroppedNumberMergeTests(unittest.TestCase):
    def test_a_declared_dual_survives_the_section_check_and_an_undeclared_number_does_not(self) -> None:
        source, target = _group("The point moves 2 units left.", "تتحرك النقطة وحدتين إلى اليسار.")
        wrapped_source = {"exercises": {"groups": [source]}}
        wrapped_target = {"exercises": {"groups": [target]}}
        bare = document_problems(wrapped_source, wrapped_target, ARABIC)
        self.assertTrue(any("number(s) missing: 2" in problem for problem in bare), bare)
        declared = document_problems(
            wrapped_source,
            wrapped_target,
            ARABIC,
            dropped={"group_1": {"exercise_1.figures[0].description": 1}},
        )
        self.assertFalse(any("number(s) missing" in problem for problem in declared), declared)
        other_group = document_problems(
            wrapped_source,
            wrapped_target,
            ARABIC,
            dropped={"group_9": {"exercise_1.figures[0].description": 1}},
        )
        self.assertTrue(any("number(s) missing: 2" in problem for problem in other_group))

        two, two_ar = _group(
            "The point moves 2 units and then 2 units.",
            "تتحرك النقطة وحدتين ثم وحدتين.",
        )
        still = document_problems(
            {"exercises": {"groups": [two]}},
            {"exercises": {"groups": [two_ar]}},
            ARABIC,
            dropped={"group_1": {"exercise_1.figures[0].description": 1}},
        )
        self.assertTrue(any("number(s) missing: 2" in problem for problem in still), still)


class LabelDigitTests(unittest.TestCase):
    def test_letter_digit_labels_match_maths_subscripts_and_a_real_number_still_fails(self) -> None:
        self.assertEqual(
            restored_problems(
                "Lines L1, L2, L3, and L4 meet A1 and A2.",
                "تلتقي $ل_1$ و$ل_2$ و$ل_3$ و$ل_4$ مع $ا_1$ و$ا_2$.",
                "figure",
                notation="latin",
            ),
            [],
        )
        extra = restored_problems(
            "Lines L1 and L2.",
            "المستقيمان $ل_1$ و$ل_2$ و$9$.",
            "figure",
            notation="latin",
        )
        self.assertTrue(any("new number" in problem for problem in extra), extra)
        missing = restored_problems(
            "The point moves 2 units left.",
            "تتحرك النقطة يسارًا.",
            "figure",
            notation="latin",
        )
        self.assertTrue(any("number(s) missing: 2" in problem for problem in missing), missing)
        self.assertEqual(
            _digit_counter(r"Polygons $M_1$ and $P_2$ with $\overline{PT_1}$ and $\text{polygon}_1$."),
            _digit_counter(r"مضلعات $م_1$ و$\text{مح}_2$ مع $\overline{ق ت_1}$ و$\text{المضلع}_1$."),
        )
        self.assertEqual(
            _digit_counter("Lines L_1 and L_2."),
            _digit_counter("المستقيمان ل_$1$ ول_$2$."),
        )
        self.assertEqual(_digit_counter(r"$k^2$ and $n-2$ and $180^\circ$"), {"2": 2, "180": 1})
        self.assertEqual(
            _digit_counter(r"$M_1 \sim \text{polygon } M_2$ and $K = \frac{3x - 1}{8}$"),
            _digit_counter(r"$م_1 \sim \text{المضلع } م_2$ و$ك = \frac{3س - 1}{8}$"),
        )
        power = restored_problems(
            r"The area scale is $k^2$ and $n-2$.",
            r"مقياس المساحة $ك^2$.",
            "notes",
            notation="latin",
        )
        self.assertTrue(any("number(s) missing: 2" in problem for problem in power), power)

    def test_ncr_and_log_base_digits_are_quantities(self) -> None:
        self.assertEqual(
            restored_problems(
                r"$1792$, from ${}^8\mathrm{C}_3 \times 2^5$",
                r"$1792$, from ${}^8 ق_3 \times 2^5$",
                "answer",
                notation="arabic",
            ),
            [],
        )
        self.assertEqual(
            restored_problems(
                r"$T_6 = {}^{10}C_5 (3)^5$",
                r"$ح_6 = {}^{10} ق_5 (3)^5$",
                "answer",
                notation="arabic",
            ),
            [],
        )
        self.assertEqual(
            restored_problems(
                r"$^1C_0, ^1C_1$ down to $^5C_0, ^5C_5$",
                r"$^1 ق_0, ^1 ق_1$ وصولًا إلى $^5 ق_0, ^5 ق_5$",
                "figure",
                notation="arabic",
            ),
            [],
        )
        self.assertEqual(
            restored_problems(
                r"If $f(x) = \log_2(5x - 1)$",
                r"إذا كانت $\text{د}(س) = \text{لو}_2(5س - 1)$",
                "text",
                notation="arabic",
            ),
            [],
        )
        self.assertNotIn("1", _digit_counter(r"$F_1$"))
        self.assertNotIn("1", _digit_counter(r"$ق_1$"))

    def test_glued_mass_and_velocity_subscripts_are_labels(self) -> None:
        self.assertEqual(
            restored_problems(
                r"$3m_1 = 5m_2$",
                r"$3 ك_1 = 5 ك_2$",
                "answer",
                notation="arabic",
            ),
            [],
        )
        self.assertEqual(
            restored_problems(
                r"$m_1 = 4m_2$",
                r"$ك_1 = 4 ك_2$",
                "text",
                notation="arabic",
            ),
            [],
        )
        self.assertEqual(
            restored_problems(
                r"$\frac{1}{2}mv_0^2$",
                r"$\frac{1}{2} ك ع_0^2$",
                "answer",
                notation="arabic",
            ),
            [],
        )


class GlossaryFormSkeletonTests(unittest.TestCase):
    def test_ln_radius_and_prose_2pi_match_and_invented_subscripts_fail(self) -> None:
        self.assertEqual(
            skeleton(r"$y = \ln(x^2 + 4)$", notation="arabic"),
            skeleton(r"$ص = \text{لو}_هـ(س^2 + 4)$", notation="arabic"),
        )
        self.assertEqual(
            restored_problems(
                r"$y = \ln(x^2 + 4)$",
                r"$ص = \text{لو}_هـ(س^2 + 4)$",
                "text",
                notation="arabic",
            ),
            [],
        )
        self.assertEqual(
            restored_problems(
                r"Local minimum value is $2 - 2\ln 2$",
                r"القيمة الصغرى المحلية هي $2 - 2\text{لو}_هـ 2$",
                "answer",
                notation="arabic",
            ),
            [],
        )
        self.assertEqual(
            restored_problems(
                r"$y = \frac{r - R}{h}x + R$",
                r"$ص = \frac{\text{نق}_2 - \text{نق}_1}{ع}س + \text{نق}_1$",
                "text",
                notation="arabic",
            ),
            [],
        )
        self.assertEqual(
            restored_problems(
                "over the interval [0, 2pi] to x = 2pi",
                r"على الفترة $[0، 2\pi]$ إلى $س = 2\pi$",
                "figure",
                notation="arabic",
            ),
            [],
        )
        invented = restored_problems(
            r"$\frac{1}{3}$",
            r"$\frac{1}{_3}$",
            "answer",
            notation="arabic",
        )
        self.assertTrue(
            any("changed beyond words" in problem for problem in invented),
            invented,
        )


class NotationMessageTests(unittest.TestCase):
    def test_cdot_names_each_span_and_mathbb_is_compared_on_the_whole_field(self) -> None:
        shifted = _notation_form_problems(
            r"$2n\pi$ and $n \cdot 360^\circ$ and $n \in \mathbb{Z}$",
            r"$1$ ثم $2ن\pi$ و $ن \cdot 360^\circ$ و $ن \in \mathbb{Z}$",
            "teaching_point_4.notes",
            "arabic",
            frozenset(),
        )
        self.assertFalse(any("mathbb" in problem for problem in shifted), shifted)
        self.assertFalse(any("cdot" in problem for problem in shifted), shifted)
        units = _notation_form_problems(
            r"$100\text{ kg}\cdot\text{m/sec}$",
            r"$100\text{ كجم}\cdot\text{م/ث}$",
            "exercise_131.text",
            "arabic",
            frozenset(),
        )
        self.assertEqual(units, [])
        invented = _notation_form_problems(
            r"$2\times 3$",
            r"$2\cdot 3$",
            "stem",
            "arabic",
            frozenset(),
        )
        self.assertTrue(any("cdot" in problem for problem in invented), invented)
        equation = _notation_form_problems(
            r"\alpha = \frac{\pi}{2} + 2n\pi, \quad n \in \mathbb{Z}",
            r"\alpha = \frac{\pi}{$2$} + 2n\pi, \quad n \in \mathbb{Z}",
            "equation_1.latex",
            "arabic",
            frozenset(),
        )
        self.assertFalse(any("mathbb" in problem for problem in equation), equation)
        dropped = _notation_form_problems(
            r"$n \in \mathbb{Z}$",
            r"$ن \in ح$",
            "notes",
            "arabic",
            frozenset(),
        )
        self.assertTrue(any("mathbb" in problem for problem in dropped), dropped)
        vectors = _notation_form_problems(
            r"$\vec{a} \cdot \vec{b}$",
            r"$\vec{ا} \cdot \vec{ب}$",
            "notes",
            "arabic",
            frozenset(),
        )
        self.assertEqual(vectors, [])
        arrows = _notation_form_problems(
            r"$\overrightarrow{a} \cdot \overrightarrow{b}$",
            r"$\overrightarrow{ا} \cdot \overrightarrow{ب}$",
            "notes",
            "arabic",
            frozenset(),
        )
        self.assertEqual(arrows, [])

    def test_caret_times_control_becomes_circ_and_is_never_a_times_exponent(self) -> None:
        parsed = "$120^" + "\times" + "$"
        self.assertIn("\t", parsed)
        self.assertTrue(parsed.startswith("$120^"))
        fixed, hits = repair_english_string(parsed)
        self.assertEqual(fixed, r"$120^\circ$")
        self.assertNotIn(r"^\times", fixed)
        self.assertEqual(hits[0][2], "repaired")
        dumped = dump_english_json({"description": parsed})
        loaded = json.loads(dumped)
        self.assertEqual(loaded["description"], r"$120^\circ$")
        self.assertNotIn(r"^\times", loaded["description"])

    def test_english_generation_prompts_state_times_and_unit_cdot(self) -> None:
        for relative in (
            "prompts/specs/notation/latin.md",
            "prompts/source_questions/notation/latin.md",
            "prompts/new_questions/notation/latin.md",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(r"Use `\times` for a number product", text)
            self.assertIn(r"Use `\cdot` between two units", text)
            self.assertIn(r"\vec{a}\cdot\vec{b}", text)


class SkeletonEquivalenceTests(unittest.TestCase):
    def test_parallel_slashes_and_implied_times_match_the_source(self) -> None:
        from src.protect import protect, token_problems

        self.assertEqual(
            skeleton(r"$\overline{AB}\parallel\overline{CD}$", notation="arabic"),
            skeleton(r"$\overline{AB}//\overline{CD}$", notation="arabic"),
        )
        self.assertEqual(
            skeleton(r"$4(-3)=-9$", notation="arabic"),
            skeleton(r"$4\times(-3)=-9$", notation="arabic"),
        )
        self.assertEqual(
            skeleton(r"$4\left(-3\right)=-9$", notation="arabic"),
            skeleton(r"$4\times\left(-3\right)=-9$", notation="arabic"),
        )
        _text, slots = protect(r"$\overline{AB}\parallel\overline{CD}$", notation="arabic")
        self.assertEqual(
            token_problems(slots, r"⟦E1⟧$\overline{AB}//\overline{CD}$⟦/E1⟧", "stem"),
            [],
        )
        _text, slots = protect(r"$4(-3)=-9$", notation="arabic")
        self.assertEqual(
            token_problems(slots, r"⟦E1⟧$4\times(-3)=-9$⟦/E1⟧", "stem"),
            [],
        )
        self.assertNotIn(
            r"\parallel",
            skeleton(r"\text{https://example.com}", notation="arabic"),
        )


class LatinFunctionCommandTests(unittest.TestCase):
    def test_arabic_text_lo_passes_and_bare_ln_fails(self) -> None:
        self.assertEqual(
            latin_function_output_problems(r"$\text{لو}\,س$", notation="arabic"),
            [],
        )
        self.assertEqual(
            skeleton(r"$\ln x$", notation="arabic"),
            skeleton(r"$\text{لو}\,س$", notation="arabic"),
        )
        self.assertEqual(
            skeleton(r"$\ln x$", notation="arabic"),
            skeleton(r"$\text{لو}_هـ س$", notation="arabic"),
        )
        self.assertEqual(
            latin_function_output_problems(r"$\ln س$", notation="arabic"),
            [r"Latin function command left in Arabic output: \ln"],
        )
        self.assertEqual(
            latin_function_output_problems(r"$\log x + \exp y$", notation="arabic"),
            [r"Latin function command left in Arabic output: \exp, \log"],
        )
        self.assertEqual(
            latin_function_output_problems(
                r"$\operatorname{Re}(ع) = -3$", notation="arabic"
            ),
            [r"Latin function command left in Arabic output: \operatorname{Re}"],
        )
        self.assertEqual(
            skeleton(r"$\operatorname{Re}(z)=-3$", notation="arabic"),
            skeleton(r"$\text{ح}(ع)=-3$", notation="arabic"),
        )
        self.assertEqual(
            skeleton(r"$\operatorname{Im}(z)=4$", notation="arabic"),
            skeleton(r"$\text{تخ}(ع)=4$", notation="arabic"),
        )


class FunctionNameCheckTests(unittest.TestCase):
    def test_force_times_paren_is_not_a_function(self) -> None:
        source = r"$M = 24(20\sin 150^\circ) - F(12\sin 150^\circ) = 240 - 6F = 216$"
        self.assertEqual(function_letters_of(source), frozenset())
        self.assertEqual(
            _notation_form_problems(
                source,
                r"$M = 24(20\text{جا}\, 150^\circ) - F(12\text{جا}\, 150^\circ) = 240 - 6F = 216$",
                "exercise_144.answer",
                "arabic",
                function_letters_of(source),
            ),
            [],
        )
        real = r"$f(x)=x$ and $F(12\sin 150^\circ)$"
        self.assertEqual(function_letters_of(real), frozenset("f"))

    def test_coordinate_tuples_are_not_functions(self) -> None:
        points = r"The point $F(2, 3, 6)$ and $G(0, 3, 6)$"
        self.assertEqual(function_letters_of(points), frozenset())
        self.assertEqual(
            _notation_form_problems(
                points,
                r"النقطة $F(2, 3, 6)$ و $G(0, 3, 6)$",
                "group_123.stem",
                "arabic",
                function_letters_of(points),
            ),
            [],
        )
        mixed = r"$F(2, 3, 6)$ and $f(x)=x$"
        self.assertEqual(function_letters_of(mixed), frozenset("f"))
        self.assertTrue(
            any(
                r"\text{د}\left(...\right)" in problem
                for problem in _notation_form_problems(
                    mixed,
                    r"$F(2, 3, 6)$ و $f(س)=س$",
                    "stem",
                    "arabic",
                    function_letters_of(mixed),
                )
            )
        )

    def test_whole_field_count_ignores_span_shifts_and_names_the_text_d_fix(self) -> None:
        self.assertNotIn("t", function_letters_of(r"$5t(6-t)=0$"))
        shifted = _notation_form_problems(
            r"$1$. $f(x)=ax^2+bx+c$. $2$. $f(0)=c$.",
            r"$١$. $\text{د}(س)=ا س^2+ب س+ج$. $٢$. $\text{د}(٠)=ج$.",
            "teaching_point_2.notes",
            "arabic",
            frozenset("f"),
        )
        self.assertFalse(any("function" in problem for problem in shifted), shifted)
        clock = _notation_form_problems(
            r"After 8:00, $T(t)=6-1.5t$ and $T(t)<0$.",
            r"بعد $٨{:}٠٠$، $\text{د}(ت)=٦-١.٥ت$ و$\text{د}(ت)<0$.",
            "exercise_66.text",
            "arabic",
            function_letters_of(r"$T(t)=6-1.5t$", r"$T(t)<0$"),
        )
        self.assertFalse(any("function" in problem for problem in clock), clock)
        product = _notation_form_problems(
            r"$k(x+1)$",
            r"$ك(س+١)$",
            "stem",
            "arabic",
            frozenset(),
        )
        self.assertEqual(product, [])
        inside_text = _notation_form_problems(
            r"$P=2(l+w)$",
            r"$\text{مح}=2(ل+ض)$",
            "stem",
            "arabic",
            frozenset(),
        )
        self.assertEqual(inside_text, [])
        bare = _notation_form_problems(
            r"$f(x)=x$",
            r"$د(س)=س$",
            "stem",
            "arabic",
            frozenset("f"),
        )
        self.assertTrue(
            any(r"\text{د}\left(...\right)" in problem for problem in bare),
            bare,
        )
        left = _notation_form_problems(
            r"$f(x)=x$",
            r"$\text{د}\left(س\right)=س$",
            "stem",
            "arabic",
            frozenset("f"),
        )
        self.assertEqual(left, [])
        measure_source = r"$m(\angle AOB)=\theta$"
        measure = _notation_form_problems(
            measure_source,
            r"$ق(\angle ا و ب)=\theta$",
            "notation_2.notation",
            "arabic",
            function_letters_of(measure_source),
        )
        self.assertEqual(measure, [])
        self.assertNotIn("m", function_letters_of(measure_source))
        for command in (
            r"\measuredangle",
            r"\widehat",
            r"\arc",
            r"\overarc",
            r"\overline",
        ):
            source = f"$m({command} AOB)=\\theta$"
            self.assertNotIn("m", function_letters_of(source), source)
        bare_linear = _notation_form_problems(
            r"$f(x)=2x+1$",
            r"$د(س)=2س+1$",
            "stem",
            "arabic",
            function_letters_of(r"$f(x)=2x+1$"),
        )
        self.assertTrue(
            any(r"\text{د}\left(...\right)" in problem for problem in bare_linear),
            bare_linear,
        )
        defined = _notation_form_problems(
            r"$m(x)=x^2$",
            r"$م(س)=س^2$",
            "stem",
            "arabic",
            function_letters_of(r"$m(x)=x^2$"),
        )
        self.assertTrue(
            any(r"\text{د}\left(...\right)" in problem for problem in defined),
            defined,
        )
        self.assertIn("m", function_letters_of(r"$m(x)=x^2$"))
        self.assertNotIn("O", function_letters_of(r"$\sum M_O = M_O(\vec{R})$"))
        self.assertNotIn("A", function_letters_of(r"$\text{Alg } M_A(\vec{R})$"))
        self.assertNotIn("m", function_letters_of(r"$N = m(g - g) = 0$"))
        self.assertNotIn("m", function_letters_of(r"$N = m(g + a)$"))
        moment = _notation_form_problems(
            r"$\sum M_O = M_O(\vec{R})$",
            r"$\sum جـ_و = جـ_و(\vec{ح})$",
            "teaching_point_4.notes",
            "arabic",
            function_letters_of(r"$\sum M_O = M_O(\vec{R})$"),
        )
        self.assertFalse(any("function" in problem for problem in moment), moment)
        product = _notation_form_problems(
            r"$ma = N - mg \implies N = m(g + a)$",
            r"$ك ا = ن - ك جـ \implies ن = ك(جـ + ا)$",
            "teaching_point_2.notes",
            "arabic",
            function_letters_of(r"$ma = N - mg \implies N = m(g + a)$"),
        )
        self.assertFalse(any("function" in problem for problem in product), product)
        zero = _notation_form_problems(
            r"$N = m(g - g) = 0$",
            r"$ن = ك(جـ - جـ) = 0$",
            "equation_2.latex",
            "arabic",
            function_letters_of(r"$N = m(g - g) = 0$"),
        )
        self.assertFalse(any("function" in problem for problem in zero), zero)
        self.assertIn("f", function_letters_of(r"$f(x)=$"))
        self.assertIn("T", function_letters_of(r"$T(t)=6-1.5t$"))


class ExercisesCompleteDropTests(unittest.TestCase):
    def test_saved_dual_drop_passes_and_an_undeclared_drop_names_the_check(self) -> None:
        from translate import (
            exercises_complete,
            exercises_incomplete_reason,
            group_row_problem,
        )

        source, target = _group(
            "The point moves 2 units left.",
            "تتحرك النقطة وحدتين إلى اليسار.",
        )
        en_doc = {"exercises": {"groups": [source]}}
        ar_doc = {"exercises": {"groups": [target]}}
        drops = {"group_1": {"exercise_1.figures[0].description": 1}}
        unnamed = group_row_problem(source, target, ARABIC)
        self.assertIn("number(s) missing", unnamed)
        self.assertEqual(group_row_problem(source, target, ARABIC, dropped=drops["group_1"]), "")
        with tempfile.TemporaryDirectory() as folder:
            ar_path = Path(folder) / "09.json"
            ar_path.write_text(json.dumps(ar_doc, ensure_ascii=False), encoding="utf-8")
            self.assertTrue(exercises_complete(en_doc, ar_path, ARABIC, dropped=drops))
            self.assertFalse(exercises_complete(en_doc, ar_path, ARABIC))
            reason = exercises_incomplete_reason(en_doc, ar_path, ARABIC)
            self.assertIn("number(s) missing", reason)
            state_path = Path(folder) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "groups": {
                            "group_1": {
                                "dropped_numbers": [
                                    {
                                        "field": "exercise_1.figures[0].description",
                                        "token": "N1",
                                        "reason": "dual: وحدتين",
                                    }
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                exercises_complete(en_doc, ar_path, ARABIC, state_path=state_path)
            )


class SpecReuseTests(unittest.TestCase):
    def test_prompt_change_does_not_redo_a_finished_spec(self) -> None:
        from translate import spec_file_reusable

        existing = {
            "spec": {"overview": "درس"},
            "translation": {
                "spec_key": "old-prompt-key",
                "spec_source_fp": "source-1",
            },
        }
        self.assertTrue(
            spec_file_reusable(
                existing,
                "new-prompt-key",
                "source-1",
                update=False,
                retranslate_on_prompt_change=False,
            )
        )
        self.assertFalse(
            spec_file_reusable(
                existing,
                "new-prompt-key",
                "source-2",
                update=False,
                retranslate_on_prompt_change=False,
            )
        )
        old_file = {"spec": {"overview": "درس"}, "translation": {"spec_key": "old"}}
        self.assertTrue(
            spec_file_reusable(
                old_file,
                "new",
                "source-1",
                update=False,
                retranslate_on_prompt_change=False,
            )
        )
        self.assertFalse(
            spec_file_reusable(
                old_file,
                "new",
                "source-1",
                update=False,
                retranslate_on_prompt_change=True,
            )
        )


class LabelsReuseTests(unittest.TestCase):
    def test_prompt_change_does_not_redo_finished_labels(self) -> None:
        from translate import labels_reusable

        self.assertTrue(
            labels_reusable(
                file_exists=True,
                stored_key="old-prompt",
                current_key="new-prompt",
                stored_source="source-1",
                current_source="source-1",
                update=False,
                retranslate_on_prompt_change=False,
            )
        )
        self.assertFalse(
            labels_reusable(
                file_exists=True,
                stored_key="old-prompt",
                current_key="new-prompt",
                stored_source="source-1",
                current_source="source-2",
                update=False,
                retranslate_on_prompt_change=False,
            )
        )
        self.assertTrue(
            labels_reusable(
                file_exists=True,
                stored_key="old",
                current_key="new",
                stored_source=None,
                current_source="source-1",
                update=False,
                retranslate_on_prompt_change=False,
            )
        )
        self.assertFalse(
            labels_reusable(
                file_exists=True,
                stored_key="old",
                current_key="new",
                stored_source=None,
                current_source="source-1",
                update=False,
                retranslate_on_prompt_change=True,
            )
        )
        self.assertFalse(
            labels_reusable(
                file_exists=False,
                stored_key="same",
                current_key="same",
                stored_source="source-1",
                current_source="source-1",
                update=False,
                retranslate_on_prompt_change=False,
            )
        )


class TikzOverlapAllowanceTests(unittest.TestCase):
    def test_only_new_overlaps_fail(self) -> None:
        from draw import new_label_overlaps

        self.assertEqual(new_label_overlaps(380, 380), 0)
        self.assertEqual(new_label_overlaps(380, 0), 380)
        self.assertEqual(new_label_overlaps(381, 380), 1)
        self.assertEqual(new_label_overlaps(0, 4), 0)

    def test_duplicate_ink_masks_collapse_to_one_label(self) -> None:
        from draw import Label, distinct_printed_labels

        copies = [
            Label(number=index, mask=0b1111, box=(10, 10, 20, 20))
            for index in range(1, 22)
        ]
        unique = distinct_printed_labels(copies)
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0].mask, 0b1111)

    def test_different_masks_stay_separate(self) -> None:
        from draw import Label, distinct_printed_labels

        unique = distinct_printed_labels(
            [
                Label(number=1, mask=0b0001, box=(10, 10, 20, 20)),
                Label(number=2, mask=0b0010, box=(10, 10, 20, 20)),
            ]
        )
        self.assertEqual([label.mask for label in unique], [0b0001, 0b0010])

    def test_extra_filed_overlaps_ignores_source_count(self) -> None:
        from translate import extra_filed_overlaps

        self.assertTrue(extra_filed_overlaps({"overlaps": 1}, {"overlaps": 0}))
        self.assertFalse(extra_filed_overlaps({"overlaps": 1}, {"overlaps": 1}))
        self.assertFalse(extra_filed_overlaps({"overlaps": None}, {"overlaps": 0}))
        self.assertFalse(extra_filed_overlaps({}, {"overlaps": 0}))

    def test_already_relabelled_reads_provenance(self) -> None:
        from translate import already_relabelled

        self.assertTrue(already_relabelled({"provenance": {"relabelled": True}}))
        self.assertFalse(already_relabelled({"provenance": {"english_key": "abc"}}))
        self.assertFalse(already_relabelled({"settled": True}))

    def test_section_needs_relabel_ignores_other_sections(self) -> None:
        from unittest.mock import patch

        from translate import section_needs_relabel

        ar_rows = [
            ({}, {
                "overlaps": 1,
                "provenance": {"section": "1.3.2", "english_key": "en1"},
            }),
            ({}, {
                "overlaps": 1,
                "provenance": {"section": "2.3.1", "english_key": "en2"},
            }),
        ]
        en_rows = [
            ({}, {"key": "en1", "overlaps": 0}),
            ({}, {"key": "en2", "overlaps": 0}),
        ]
        with patch("translate.figure_rows", side_effect=lambda book: en_rows if book == "en" else ar_rows):
            self.assertTrue(section_needs_relabel("ar", "en", "1.3.2"))
            self.assertTrue(section_needs_relabel("ar", "en", "2.3.1"))
            self.assertFalse(section_needs_relabel("ar", "en", "1.1.1"))

    def test_unclash_adopted_marks_provenance_and_files(self) -> None:
        from unittest.mock import patch

        from draw import unclash_adopted

        filed_rows = []

        def fake_filed(result, out):
            result.compiled = True
            result.touched = True
            return result

        def fake_unclashed(run, result, out):
            result.tikz = r"\begin{tikzpicture}\node [left] {$نيوتن$};\end{tikzpicture}"
            return result

        with (
            patch("draw.read_prompts", return_value=({"draw": "", "relabel": "", "pin": ""}, [])),
            patch("draw.book_metadata", return_value={}),
            patch("draw.Folders.under"),
            patch("draw.filed", side_effect=fake_filed),
            patch("draw.unclashed", side_effect=fake_unclashed),
            patch("draw.file_row", side_effect=lambda result, out: filed_rows.append(result)),
            patch("draw.library_row", side_effect=lambda result: {
                "tikz": result.tikz,
                "settled": result.settled,
                "provenance": result.figure.get("provenance"),
            }),
        ):
            row = unclash_adopted(
                "book_ar",
                "triangle",
                "plane geometry",
                r"\begin{tikzpicture}\node {$نيوتن$};\end{tikzpicture}",
                figure={"provenance": {"english_key": "abc"}},
                client=object(),
                model="gemini-3.8-flash",
                thinking="medium",
            )
        self.assertTrue(row["settled"])
        self.assertTrue(row["provenance"]["relabelled"])
        self.assertEqual(row["provenance"]["english_key"], "abc")
        self.assertEqual(len(filed_rows), 1)

    def test_rebuild_reads_filed_drawings_without_figure_data(self) -> None:
        from unittest.mock import patch

        import draw

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dest = root / "book_ar" / "11_book_figures"
            dest.mkdir(parents=True)
            (dest / "abc123.json").write_text(
                json.dumps({
                    "key": "abc123",
                    "description": "box",
                    "figure_type": "solid geometry",
                    "tikz": r"\begin{tikzpicture}\draw (0,0)--(1,0);\end{tikzpicture}",
                }),
                encoding="utf-8",
            )
            with patch.object(draw, "OUTPUT_DIR", root):
                found = draw.results_from_library("book_ar", None, ["abc123"])
                typed = draw.results_from_library(
                    "book_ar", ["solid_geometry"], ["abc123"],
                )
                missing = draw.results_from_library("book_ar", None, ["nope"])
        self.assertEqual([one.key for one in found], ["abc123"])
        self.assertEqual([one.key for one in typed], ["abc123"])
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
