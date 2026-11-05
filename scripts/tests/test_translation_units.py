from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.translation_units import (  # noqa: E402
    UnitOccurrence,
    canonicalize_units,
    normalize_translation_units,
    unit_occurrences,
)


BOOK_MAP = {
    "entries": [
        {"kind": "unit", "source_math": "g", "math_text": "جم"},
        {"kind": "unit", "source_math": "cm", "math_text": "سم"},
        {"kind": "unit", "source_math": "kg", "math_text": "كجم"},
        {
            "kind": "unit",
            "source_math": "m",
            "math_text": "م",
            "plain_lemma": "متر",
        },
        {"kind": "unit", "source_math": "km", "math_text": "كم"},
        {"kind": "unit", "source_math": "km/h", "math_text": "كم/س"},
        {
            "kind": "unit",
            "source_math": "s",
            "math_text": "ث",
            "plain_lemma": "ثانية",
        },
        {"kind": "unit", "source_math": "in", "math_text": "بوصة"},
    ]
}


class TranslationUnitTests(unittest.TestCase):
    def test_compound_unit_and_power_are_one_semantic_occurrence(self) -> None:
        source = r"$\sqrt[3]{20}\text{ g/cm}^3$"
        target = r"$\sqrt[3]{20}\text{ جم/سم}^3$"
        expected = [UnitOccurrence("g/cm", "3")]
        self.assertEqual(unit_occurrences(source, BOOK_MAP), expected)
        self.assertEqual(unit_occurrences(target, BOOK_MAP), expected)
        self.assertEqual(
            normalize_translation_units(source, source, BOOK_MAP),
            target,
        )
        self.assertEqual(
            normalize_translation_units(source, target, BOOK_MAP),
            target,
        )
        self.assertEqual(
            normalize_translation_units(
                "A side is 5 cm.", "طول الضلع $5$ سم.", BOOK_MAP
            ),
            r"طول الضلع $5\text{ سم}$.",
        )
        self.assertEqual(
            canonicalize_units(target, BOOK_MAP),
            source,
        )

    def test_wrong_unit_or_power_fails(self) -> None:
        source = r"$5\text{ cm}^3$"
        with self.assertRaisesRegex(ValueError, "mathematical unit occurrences differ"):
            normalize_translation_units(
                source, r"$5\text{ كجم}^3$", BOOK_MAP, "density"
            )
        with self.assertRaisesRegex(ValueError, "mathematical unit occurrences differ"):
            normalize_translation_units(
                source, r"$5\text{ سم}^2$", BOOK_MAP, "density"
            )

    def test_direct_compound_map_and_embedded_label(self) -> None:
        source = r"$80\text{ km/h}$ and $\text{speed (km/h)}$"
        target = r"$80\text{ كم/س}$ and $\text{speed (كم/س)}$"
        self.assertEqual(
            normalize_translation_units(source, source, BOOK_MAP),
            target,
        )

    def test_unit_tokens_do_not_match_variables_or_inside_arabic_words(self) -> None:
        value = (
            r"$m+A$ and $\text{المساحة}$ and "
            r"$(\text{first})^2-(\text{first})(\text{second})$"
        )
        self.assertEqual(unit_occurrences(value, BOOK_MAP), [])
        self.assertEqual(
            normalize_translation_units(value, value, BOOK_MAP),
            value,
        )

    def test_prose_measurement_suffixes_are_localized_but_articles_are_not(self) -> None:
        source = (
            "A diagram has sides 5 cm and (2x + 3) square metres, "
            "and is shown on a 24-hour scale."
        )
        target = (
            r"شكل له ضلعان $5\text{ سم}$ و$(2x + 3)\text{ م}^2$، "
            "ويظهر على مقياس 24-hour."
        )
        self.assertEqual(
            normalize_translation_units(source, source, BOOK_MAP),
            (
                r"A diagram has sides $5\text{ سم}$ and $(2x + 3)\text{ م}^2$, "
                "and is shown on a 24-hour scale."
            ),
        )
        self.assertEqual(
            normalize_translation_units(source, target, BOOK_MAP),
            target,
        )
        self.assertEqual(
            normalize_translation_units(
                r"$[12,18]$ meters", r"$[12,18]$ متر", BOOK_MAP
            ),
            r"$[12,18]\text{ م}$",
        )
        self.assertEqual(
            normalize_translation_units(
                r"\text{Daily Wage (LE)}",
                r"\text{الأجر اليومي (جنيه)}",
                BOOK_MAP,
            ),
            r"\text{الأجر اليومي (جنيه)}",
        )
        self.assertEqual(
            normalize_translation_units(
                "Savings (in EGP)", "المدخرات (جنيه)", BOOK_MAP
            ),
            "المدخرات (جنيه)",
        )
        self.assertEqual(
            normalize_translation_units(
                r"Bills in EGP; mode $36\text{ thousand LE}$.",
                r"الفواتير بوحدة جنيه؛ والمنوال $36\text{ ألف جنيه}$.",
                BOOK_MAP,
            ),
            r"الفواتير بوحدة جنيه؛ والمنوال $36\text{ ألف جنيه}$.",
        )
        self.assertEqual(
            normalize_translation_units(
                "Bills in EGP are quoted twice in EGP.",
                "الفواتير مقومة بالجنيه.",
                BOOK_MAP,
            ),
            "الفواتير مقومة بالجنيه.",
        )
        self.assertEqual(
            normalize_translation_units(
                r"Volume is $x^3+1$ cubic centimetres.",
                r"الحجم هو $x^3+1\text{ سم}^3$.",
                BOOK_MAP,
            ),
            r"الحجم هو $x^3+1\text{ سم}^3$.",
        )
        self.assertEqual(
            normalize_translation_units(
                r"Area is $2(x+3)\text{ square metres}$.",
                r"المساحة $2(x+3)\text{ م}^2$.",
                BOOK_MAP,
            ),
            r"المساحة $2(x+3)\text{ م}^2$.",
        )
        self.assertEqual(
            normalize_translation_units(
                r"Side is labelled (2x + 3) cm.",
                r"الضلع مسمى $(2x + 3)\text{ سم}$.",
                BOOK_MAP,
            ),
            r"الضلع مسمى $(2x + 3)\text{ سم}$.",
        )

    def test_pgf_foreach_in_is_not_inches(self) -> None:
        foreach = (
            r"\begin{tikzpicture}[x=1.2cm] "
            r"\foreach \x in {1, ..., 5} { \node [below] at (\x, 0) {$\x$}; } "
            r"\end{tikzpicture}"
        )
        self.assertEqual(unit_occurrences(foreach, BOOK_MAP), [])
        self.assertEqual(normalize_translation_units(foreach, foreach, BOOK_MAP), foreach)
        self.assertNotIn("بوصة", normalize_translation_units(foreach, foreach, BOOK_MAP))
        self.assertEqual(
            [row.canonical for row in unit_occurrences(r"$5\text{ in}$", BOOK_MAP)],
            ["in"],
        )
        self.assertEqual(
            normalize_translation_units(
                r"$5\text{ in}$", r"$5\text{ in}$", BOOK_MAP
            ),
            r"$5\text{ بوصة}$",
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences("The rod is 5 in.", BOOK_MAP)],
            ["in"],
        )

    def test_preposition_in_and_lemma_after_a_variable_are_not_units(self) -> None:
        source = (
            r"The solution in $\mathbb{Q}$ is $\blank$, "
            r"and after $t$ seconds the height is $(40t-5t^2)\text{ m}$ "
            r"at $t = 3\text{ seconds}$ in $\triangle ABC$."
        )
        target = (
            r"الحل في $\mathbb{Q}$ هو $\blank$، "
            r"وبعد $t$ ثانية يكون الارتفاع $(40t-5t^2)\text{ م}$ "
            r"عند $t = 3\text{ ث}$ في $\triangle ABC$."
        )
        self.assertEqual(
            [occurrence.canonical for occurrence in unit_occurrences(source, BOOK_MAP)],
            ["m", "s"],
        )
        self.assertEqual(
            [occurrence.canonical for occurrence in unit_occurrences(target, BOOK_MAP)],
            ["m", "s"],
        )
        self.assertEqual(normalize_translation_units(source, target, BOOK_MAP), target)
        self.assertEqual(
            normalize_translation_units(
                r"areas $ax$, $ay$, $bx$, and $by$ square metres",
                r"مساحاتها $ax$ و$ay$ و$bx$ و$by$ مترًا مربعًا",
                BOOK_MAP,
            ),
            r"مساحاتها $ax$ و$ay$ و$bx$ و$by$ مترًا مربعًا",
        )

    def test_point_labels_and_angle_measure_are_not_si_units(self) -> None:
        ampere_map = {
            "entries": [
                *BOOK_MAP["entries"],
                {"kind": "unit", "source_math": "A", "math_text": "أمبير"},
            ]
        }
        labels = (
            r"tangent at $\text{A}$, $\overrightarrow{\text{EA}}$, "
            r"and $\text{m}(\angle A)$"
        )
        arabic = (
            r"مماس عند $\text{أ}$، $\overrightarrow{\text{هـ أ}}$، "
            r"و$\text{ق}(\angle أ)$"
        )
        self.assertEqual(unit_occurrences(labels, ampere_map), [])
        self.assertEqual(
            normalize_translation_units(labels, arabic, ampere_map),
            arabic,
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(r"$5\text{A}$", ampere_map)],
            ["A"],
        )
        celsius = r"The daytime high was $16^\circ\text{C}$ and the low was $-2^\circ\text{C}$."
        arabic_celsius = (
            r"كانت العظمى $16^\circ\text{م}$ والصغرى $-2^\circ\text{م}$."
        )
        self.assertEqual(unit_occurrences(celsius, BOOK_MAP), [])
        self.assertEqual(unit_occurrences(arabic_celsius, BOOK_MAP), [])
        self.assertEqual(
            normalize_translation_units(celsius, arabic_celsius, BOOK_MAP),
            arabic_celsius,
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(r"$5\text{ m}$", BOOK_MAP)],
            ["m"],
        )
        self.assertEqual(
            normalize_translation_units(
                r"$5\text{A}$ and $5\text{ m}$",
                r"$5\text{ أمبير}$ و$5\text{ م}$",
                ampere_map,
            ),
            r"$5\text{ أمبير}$ و$5\text{ م}$",
        )

    def test_length_in_drawing_is_not_inches(self) -> None:
        source = (
            r"$\text{Scale Drawing} = \frac{\text{Length in drawing}}{\text{Real length}}$ "
            r"and $1\text{ km} = 100\text{ m}$."
        )
        target = (
            r"$\text{رسم المقياس} = \frac{\text{الطول في الرسم}}{\text{الطول الحقيقي}}$ "
            r"و$1\text{ كم} = 100\text{ م}$."
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(source, BOOK_MAP)],
            ["km", "m"],
        )
        self.assertEqual(normalize_translation_units(source, target, BOOK_MAP), target)

    def test_isolated_metre_in_a_unit_list_is_a_unit(self) -> None:
        source = r"$\text{km}, \text{m}, \text{cm}$"
        target = r"$\text{كم}, \text{م}, \text{سم}$"
        self.assertEqual(
            [row.canonical for row in unit_occurrences(source, BOOK_MAP)],
            ["km", "m", "cm"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(target, BOOK_MAP)],
            ["km", "m", "cm"],
        )
        self.assertEqual(
            normalize_translation_units(source, target, BOOK_MAP),
            r"$\text{ كم}, \text{ م}, \text{ سم}$",
        )

    def test_bare_variable_length_counts_as_a_unit(self) -> None:
        source = "A triangle with side lengths labeled x cm, (x + 4) cm, and (2x - 1) cm."
        target = (
            r"مثلث أطوال أضلاعه $x\text{ سم}$ و$(x + 4)\text{ سم}$ "
            r"و$(2x - 1)\text{ سم}$."
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(source, BOOK_MAP)],
            ["cm", "cm", "cm"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(target, BOOK_MAP)],
            ["cm", "cm", "cm"],
        )
        self.assertEqual(normalize_translation_units(source, target, BOOK_MAP), target)
        self.assertEqual(
            [row.canonical for row in unit_occurrences("drawn in cm.", BOOK_MAP)],
            [],
        )

    def test_square_units_are_not_metres_and_sq_m_is_area(self) -> None:
        source_units = r"$30\text{ square units}$"
        target_units = r"$30\text{ وحدة مربعة}$"
        self.assertEqual(unit_occurrences(source_units, BOOK_MAP), [])
        self.assertEqual(
            normalize_translation_units(source_units, target_units, BOOK_MAP),
            target_units,
        )
        self.assertNotIn("^2", normalize_translation_units(source_units, source_units, BOOK_MAP))
        expected = [UnitOccurrence("m", "2")]
        for source, target in (
            (r"$70\text{ sq m}$", r"$70\text{ م}^2$"),
            (r"$70\text{ sq. m}$", r"$70\text{ m}^2$"),
            (r"$70\text{ square meters}$", r"$70\text{ م}^2$"),
        ):
            self.assertEqual(unit_occurrences(source, BOOK_MAP), expected)
            self.assertEqual(unit_occurrences(target, BOOK_MAP), expected)
            self.assertEqual(
                normalize_translation_units(source, target, BOOK_MAP),
                r"$70\text{ م}^2$",
            )

    def test_prose_variable_length_counts_as_a_unit(self) -> None:
        source = "Segment EA = 6 cm, EB = 4 cm, EC = 3 cm, and ED = x cm."
        target = (
            r"القطعة $هـ أ = 6\text{ سم}$، و$هـ ب = 4\text{ سم}$، "
            r"و$هـ جـ = 3\text{ سم}$، و$هـ د = س\text{ سم}$."
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(source, BOOK_MAP)],
            ["cm", "cm", "cm", "cm"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(target, BOOK_MAP)],
            ["cm", "cm", "cm", "cm"],
        )
        self.assertEqual(normalize_translation_units(source, target, BOOK_MAP), target)

    def test_inflected_arabic_currency_counts_as_egp_and_stays_prose(self) -> None:
        egp_map = {
            "entries": [
                *BOOK_MAP["entries"],
                {
                    "kind": "unit",
                    "source_math": "EGP",
                    "math_text": "جنيه",
                    "plain_lemma": "جنيه",
                },
            ]
        }
        source = "The listed price is 108 LE."
        target = "السعر المدرج 108 جنيهات."
        self.assertEqual(
            [row.canonical for row in unit_occurrences(source, egp_map)],
            ["EGP"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(target, egp_map)],
            [],
        )
        with self.assertRaises(ValueError):
            normalize_translation_units(source, target, egp_map)
        self.assertEqual(
            normalize_translation_units(
                r"$5\text{ cm}$", r"$5\text{ سم}$", egp_map
            ),
            r"$5\text{ سم}$",
        )
        for form in ("الجنيه", "جنيهاً"):
            inflected = f"السعر المدرج 108 {form}."
            with self.assertRaises(ValueError):
                normalize_translation_units(source, inflected, egp_map)
        with self.assertRaisesRegex(
            ValueError, "mathematical unit occurrences differ"
        ):
            normalize_translation_units(
                source, "السعر المدرج 108 سم.", egp_map
            )
        pounds = "The listed price is 108 Egyptian pounds."
        inflected = "السعر المدرج 108 جنيهًا."
        self.assertEqual(
            [row.canonical for row in unit_occurrences(pounds, egp_map)],
            ["EGP"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(inflected, egp_map)],
            [],
        )
        with self.assertRaises(ValueError):
            normalize_translation_units(pounds, inflected, egp_map)

    def test_inflected_hours_count_against_lemma_math_text(self) -> None:
        hour_map = {
            "entries": [
                *BOOK_MAP["entries"],
                {
                    "kind": "unit",
                    "source_math": "h",
                    "math_text": "ساعة",
                    "plain_lemma": "ساعة",
                },
            ]
        }
        source = "How much does Nada earn if she works 8 h?"
        target = "كم تكسب ندى إذا عملت 8 ساعات؟"
        self.assertEqual(unit_occurrences(target, hour_map), [])
        with self.assertRaises(ValueError):
            normalize_translation_units(source, target, hour_map)

    def test_second_degree_is_not_seconds(self) -> None:
        source = (
            "An inequality involving a second-degree polynomial in one variable, "
            r"expressible as $ax^2 + bx + c > 0$ with $a \neq 0$."
        )
        target = (
            "متباينة تتضمن كثيرة حدود من الدرجة الثانية في متغير واحد، "
            r"يمكن كتابتها $أ س^2 + ب س + ج > 0$ حيث $أ \neq 0$."
        )
        self.assertEqual(unit_occurrences(source, BOOK_MAP), [])
        self.assertEqual(unit_occurrences(target, BOOK_MAP), [])
        self.assertEqual(normalize_translation_units(source, target, BOOK_MAP), target)
        self.assertEqual(
            [row.canonical for row in unit_occurrences("lasts 3 seconds.", BOOK_MAP)],
            ["s"],
        )
        self.assertEqual(
            normalize_translation_units(
                r"$3\text{ seconds}$",
                r"$3\text{ ث}$",
                BOOK_MAP,
            ),
            r"$3\text{ ث}$",
        )

    def test_arabic_letter_then_one_letter_si_is_a_point_name(self) -> None:
        names = "Line DM intersects line M and M' at distinct points."
        arabic_names = "المستقيم د م يقطع المستقيم م و م' في نقطتين مختلفتين."
        self.assertEqual(unit_occurrences(names, BOOK_MAP), [])
        self.assertEqual(unit_occurrences(arabic_names, BOOK_MAP), [])
        self.assertEqual(
            normalize_translation_units(names, arabic_names, BOOK_MAP),
            arabic_names,
        )
        self.assertEqual(unit_occurrences("و م و د م.", BOOK_MAP), [])
        self.assertEqual(
            [row.canonical for row in unit_occurrences("طول س سم.", BOOK_MAP)],
            ["cm"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(r"$س\text{ سم}$", BOOK_MAP)],
            ["cm"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(r"$10\text{ م}$", BOOK_MAP)],
            ["m"],
        )
        with self.assertRaisesRegex(
            ValueError, "mathematical unit occurrences differ"
        ):
            normalize_translation_units(
                "A vertical leg of length 10 meters and a hypotenuse of 16 meters.",
                r"ضلع رأسي طوله $10\text{ م}$.",
                BOOK_MAP,
            )
        two_metres = (
            "A vertical leg of length 10 meters, a hypotenuse of length 16 meters."
        )
        arabic_two_metres = "ضلع رأسي طوله 10 أمتار، ووتر طوله 16 مترًا."
        self.assertEqual(
            [row.canonical for row in unit_occurrences(two_metres, BOOK_MAP)],
            ["m", "m"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(arabic_two_metres, BOOK_MAP)],
            [],
        )
        with self.assertRaises(ValueError):
            normalize_translation_units(two_metres, arabic_two_metres, BOOK_MAP)

    def test_vocalized_arabic_verb_is_not_centimetres(self) -> None:
        source = (
            r"A square has an actual perimeter of $320\text{ meters}$. "
            r"If a model is drawn with a scale drawing of $1 : 400$, "
            "find the side length in the model in centimeters."
        )
        target = (
            r"قطعة أرض مربعة محيطها الحقيقي $320\text{ م}$. "
            r"إذا رُسم نموذج بمقياس رسم $1 : 400$، "
            "فأوجد طول الضلع في النموذج بالسنتيمتر."
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(source, BOOK_MAP)],
            ["m"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(target, BOOK_MAP)],
            ["m"],
        )
        stored = normalize_translation_units(source, target, BOOK_MAP)
        self.assertEqual(stored, target)
        self.assertNotIn(r"$ر\text{ cm}$", stored)
        self.assertIn("رُسم", stored)
        egp_map = {
            "entries": [
                *BOOK_MAP["entries"],
                {
                    "kind": "unit",
                    "source_math": "EGP",
                    "math_text": "جنيه",
                    "plain_lemma": "جنيه",
                },
            ]
        }
        money = r"A total sum of $2,800\text{ LE}$ is divided in the ratio $3:4$."
        arabic_money = r"قُسم مبلغ قدره $2,800\text{ جنيه}$ بالنسبة $3:4$."
        self.assertEqual(
            [row.canonical for row in unit_occurrences(money, egp_map)],
            ["EGP"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(arabic_money, egp_map)],
            ["EGP"],
        )
        self.assertEqual(
            normalize_translation_units(money, arabic_money, egp_map),
            arabic_money,
        )
        self.assertNotIn(r"$ق\text{ cm}$", arabic_money)

    def test_text_hour_labels_are_not_durations(self) -> None:
        time_map = {
            "entries": [
                *BOOK_MAP["entries"],
                {
                    "kind": "unit",
                    "source_math": "h",
                    "math_text": "ساعة",
                    "plain_lemma": "ساعة",
                },
                {
                    "kind": "unit",
                    "source_math": "min",
                    "math_text": "دقيقة",
                    "plain_lemma": "دقيقة",
                },
            ]
        }
        self.assertEqual(unit_occurrences(r"$\text{Hours}$", time_map), [])
        self.assertEqual(unit_occurrences(r"$\text{ساعات}$", time_map), [])
        self.assertEqual(
            [row.canonical for row in unit_occurrences(r"$6\text{ hours}$", time_map)],
            ["h"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(r"$6\text{ ساعة}$", time_map)],
            ["h"],
        )
        pages = r"$8\text{ pages per minute}$"
        arabic_pages = r"$8\text{ صفحات في الدقيقة}$"
        self.assertEqual(unit_occurrences(pages, time_map), [])
        self.assertEqual(unit_occurrences(arabic_pages, time_map), [])
        self.assertEqual(
            normalize_translation_units(pages, arabic_pages, time_map),
            arabic_pages,
        )
        header = (
            r"$$\begin{array}{|c|c|} \hline \text{Hours} & 6 \\ \hline \end{array}$$"
        )
        arabic_header = (
            r"$$\begin{array}{|c|c|} \hline \text{ساعات} & 6 \\ \hline \end{array}$$"
        )
        self.assertEqual(
            normalize_translation_units(header, arabic_header, time_map),
            arabic_header,
        )

    def test_also_spelling_counts_as_the_mapped_unit_and_stays_prose(self) -> None:
        dinar_map = {
            "entries": [
                *BOOK_MAP["entries"],
                {
                    "kind": "unit",
                    "source_math": "EGP",
                    "math_text": "دينار",
                    "plain_lemma": "دينار",
                    "also": ["دنانير"],
                },
            ]
        }
        source = "The listed amount is 8 LE."
        target = "المبلغ المدرج 8 دنانير."
        self.assertEqual(
            [row.canonical for row in unit_occurrences(source, dinar_map)],
            ["EGP"],
        )
        self.assertEqual(
            [row.canonical for row in unit_occurrences(target, dinar_map)],
            ["EGP"],
        )
        stored = normalize_translation_units(source, target, dinar_map)
        self.assertEqual(stored, target)
        self.assertIn("دنانير", stored)
        self.assertNotIn(r"\text", stored)


WIDE_MAP = {
    "entries": [
        *BOOK_MAP["entries"],
        {"kind": "unit", "source_math": "A", "math_text": "أمبير", "plain_lemma": "أمبير"},
        {"kind": "unit", "source_math": "EGP", "math_text": "جنيه", "plain_lemma": "جنيه"},
        {"kind": "unit", "source_math": "h", "math_text": "ساعة", "plain_lemma": "ساعة"},
    ]
}


def canonicals(text: str) -> list[str]:
    return [row.canonical for row in unit_occurrences(text, WIDE_MAP)]


class UnitIdentityGuardTests(unittest.TestCase):
    def test_article_before_a_spelled_unit_is_not_a_variable(self) -> None:
        source = (
            "The price of a square meter of land increased from "
            r"$5,000\text{ LE}$ to $6,250\text{ LE}$."
        )
        self.assertEqual(canonicalize_units(source, WIDE_MAP).count("$"), 4)
        self.assertEqual(canonicals(source), ["EGP", "EGP"])
        self.assertEqual(
            canonicals("Give your answer to the nearest tenth of a centimetre."),
            [],
        )
        self.assertEqual(canonicals("The side is x cm long."), ["cm"])
        with self.assertRaises(ValueError):
            normalize_translation_units(
                "The side is x cm long.", "طول الضلع سم.", WIDE_MAP
            )

    def test_one_letter_codes_are_case_sensitive_and_never_subscripted(self) -> None:
        self.assertEqual(
            canonicals(r"makes $z = 12 + (3k - 15)i$ a pure real number."), []
        )
        answer = (
            r"$125\text{ cm}^2$, since $\frac{\text{a}_1}{\text{a}_2} = "
            r"\frac{9}{25}$, so $\text{a}_2 = 125\text{ cm}^2$"
        )
        self.assertEqual(canonicals(answer), ["cm", "cm"])
        self.assertEqual(canonicals(r"$5\text{ A}$"), ["A"])

    def test_one_letter_label_in_wording_is_not_a_unit(self) -> None:
        source = r"$$\begin{array}{|c|c|} \text{Model A} & 650 \\ \end{array}$$"
        target = r"$$\begin{array}{|c|c|} \text{الطراز A} & 650 \\ \end{array}$$"
        self.assertEqual(canonicals(source), [])
        stored = normalize_translation_units(source, target, WIDE_MAP)
        self.assertIn(r"\text{الطراز A}", stored)
        self.assertNotIn("أمبير", stored)
        self.assertNotIn(
            "أمبير",
            normalize_translation_units(
                r"$\text{Grade A}$", r"$\text{التقدير A}$", WIDE_MAP
            ),
        )

    def test_meridiem_is_not_metres_or_seconds(self) -> None:
        self.assertEqual(canonicals(r"at $6{:}00\text{ a.m.}$ and $6{:}00\text{ p.m.}$"), [])
        self.assertEqual(canonicals(r"$6{:}00\text{ م}$ و$6{:}00\text{ ص}$"), [])
        self.assertEqual(canonicals(r"$3{:}00\text{ صباحًا}$"), [])
        self.assertEqual(canonicals(r"$5\text{ م}$"), ["m"])
        source = (
            r"$6{:}00\text{ p.m.}$, since $t = 12\text{ hours}$ after "
            r"$6{:}00\text{ a.m.}$"
        )
        target = (
            r"$6{:}00\text{ م}$، مما يعطي $ن = 12\text{ ساعة}$ بعد الساعة "
            r"$6{:}00\text{ ص}$."
        )
        normalize_translation_units(source, target, WIDE_MAP)

    def test_hyphen_compound_scale_word_and_prose_power_are_measured(self) -> None:
        self.assertEqual(canonicals("during the 24-hour day"), ["h"])
        self.assertEqual(canonicals("during the $24$-hour day"), ["h"])
        normalize_translation_units(
            "during the 24-hour day", "خلال اليوم المكون من $24$ ساعة", WIDE_MAP
        )
        self.assertEqual(canonicals("costs $980$ thousand LE"), ["EGP"])
        normalize_translation_units(
            "costs $980$ thousand LE", r"يكلف $980\text{ ألف جنيه}$", WIDE_MAP
        )
        normalize_translation_units(
            "between $620$ and $680$ thousand LE",
            r"بين $620$ و$680\text{ ألف جنيه}$",
            WIDE_MAP,
        )
        with self.assertRaises(ValueError):
            normalize_translation_units(
                "costs $980$ thousand LE", r"يكلف $980$ ألفًا", WIDE_MAP
            )
        self.assertEqual(
            unit_occurrences("labelled Area = 54 cm^2.", WIDE_MAP),
            [UnitOccurrence("cm", "2")],
        )
        normalize_translation_units(
            "labelled Area = 54 cm^2.",
            r"مكتوب عليه $\text{المساحة} = 54\text{ سم}^2$.",
            WIDE_MAP,
        )
        with self.assertRaises(ValueError):
            normalize_translation_units(
                "labelled Area = 54 cm^2.",
                r"مكتوب عليه $\text{المساحة} = 54\text{ سم}$.",
                WIDE_MAP,
            )


if __name__ == "__main__":
    unittest.main()
