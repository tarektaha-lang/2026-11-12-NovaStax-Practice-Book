from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.book_map import (  # noqa: E402
    english_snippets,
    glossary_block,
    glossary_entries,
    glossary_fp,
    section_inventory as live_section_inventory,
    validate_terminology_fragment,
)
from src.legacy_units import (  # noqa: E402
    PendingUnitFormConflict,
    arabic_unit_forms_equivalent,
    arabic_unit_stem,
    canonical_unit,
    load_pending_unit_equivalences,
    load_unit_equivalences,
    merge_fragments,
    project_map,
    section_inventory,
    units_in_text,
    validate_fragment,
)


class BookMapTests(unittest.TestCase):
    def inventory(self) -> list[dict]:
        section = {
            "number": "1.1",
            "spec": {
                "glossary": [
                    {"id": "term_1", "term": "irrational number", "definition": "not rational"}
                ],
                "notations": [
                    {"id": "notation_1", "notation": r"$\mathbb{R}$", "meaning": "real numbers"}
                ],
            },
        }
        document = {
            "exercises": {
                "groups": [
                    {
                        "stem": "",
                        "figures": [],
                        "exercises": [
                            {
                                "text": r"Volume is $27\text{ cm}^3$.",
                                "answer": "",
                                "options": [],
                                "figures": [],
                            }
                        ],
                    }
                ]
            }
        }
        return section_inventory(section, document, {})

    def reply(self, inventory: list[dict], *, unit: str = "سم") -> dict:
        observations = []
        for row in inventory:
            target = {
                "term": ("عدد غير نسبي", ""),
                "symbol": ("", r"\mathbb{R}"),
                "unit": ("سم", unit),
            }[row["kind"]]
            observations.append(
                {
                    "id": row["id"],
                    "status": "inferred",
                    "plain_lemma": target[0],
                    "math_text": target[1],
                    "basis": "The Egyptian Arabic textbook uses this form.",
                    "evidence": [],
                }
            )
        return {"section": "1.1", "observations": observations}

    def test_inventory_is_role_aware_and_ignores_tikz_dimensions(self) -> None:
        self.assertEqual(canonical_unit("LE"), "EGP")
        self.assertEqual(canonical_unit("EGP"), "EGP")
        inventory = self.inventory()
        self.assertEqual(
            {row["kind"] for row in inventory}, {"term", "symbol", "unit"}
        )
        self.assertEqual(
            units_in_text(r"\draw[line width=0.5cm] (0,0)--(1,0);"), set()
        )
        self.assertEqual(units_in_text(r"$5\text{ cm}$"), {"cm"})
        self.assertEqual(
            units_in_text(r"at $\text{A}$ and $\text{m}(\angle A)$"), set()
        )
        self.assertEqual(units_in_text(r"$5\text{A}$"), {"A"})
        self.assertEqual(units_in_text(r"$5\text{ m}$"), {"m"})
        self.assertEqual(units_in_text(r"$16^\circ\text{C}$"), set())
        self.assertEqual(units_in_text(r"$16^\circ\text{م}$"), set())
        self.assertEqual(
            units_in_text(
                r"$\text{Scale Drawing} = \frac{\text{Length in drawing}}{\text{Real length}}$"
            ),
            set(),
        )
        self.assertEqual(
            units_in_text(r"$\text{km}, \text{m}, \text{cm}, \text{mm}$"),
            {"km", "m", "cm", "mm"},
        )
        duplicated = section_inventory(
            {
                "number": "1",
                "spec": {
                    "glossary": [
                        {"term": "Base", "definition": "an exponential factor"},
                        {"term": "Base", "definition": "a triangle side"},
                    ],
                    "notations": [],
                },
            },
            {"exercises": {"groups": []}},
            {},
        )
        self.assertEqual(len({row["id"] for row in duplicated}), 2)

    def test_fragment_accepts_inference_and_validates_confirmed_evidence(self) -> None:
        inventory = self.inventory()
        fragment = validate_fragment(
            "1.1",
            inventory,
            self.reply(inventory),
            source_pages={"source_1": 4},
            notation="latin",
        )
        self.assertEqual(len(fragment["observations"]), len(inventory))
        mistyped = self.reply(inventory)
        mistyped["section"] = "4-5"
        overlaid = validate_fragment(
            "4.5",
            inventory,
            mistyped,
            source_pages={"source_1": 4},
            notation="latin",
        )
        self.assertEqual(overlaid["section"], "4.5")
        mistyped["section"] = "5"
        self.assertEqual(
            validate_fragment(
                "4.5",
                inventory,
                mistyped,
                source_pages={"source_1": 4},
                notation="latin",
            )["section"],
            "4.5",
        )
        confirmed = self.reply(inventory)
        confirmed["observations"][0].update(
            {
                "status": "confirmed",
                "basis": "",
                "evidence": [
                    {
                        "source_id": "source_1",
                        "split_page": 2,
                        "quote": "عدد غير نسبي",
                    }
                ],
            }
        )
        validate_fragment(
            "1.1",
            inventory,
            confirmed,
            source_pages={"source_1": 4},
            notation="latin",
        )
        confirmed["observations"][0]["evidence"][0]["split_page"] = 5
        with self.assertRaisesRegex(ValueError, "outside source_1"):
            validate_fragment(
                "1.1",
                inventory,
                confirmed,
                source_pages={"source_1": 4},
                notation="latin",
            )

    def test_merge_is_deterministic_and_rejects_conflicts(self) -> None:
        inventory = self.inventory()
        one = validate_fragment(
            "1.1", inventory, self.reply(inventory), source_pages={}, notation="latin"
        )
        two_reply = self.reply(inventory)
        two_reply["section"] = "1.2"
        two = validate_fragment(
            "1.2", inventory, two_reply, source_pages={}, notation="latin"
        )
        merged = merge_fragments(
            "mathematics_ar_eg08_t1",
            {"language_code": "ar", "numerals": "western", "notation": "latin"},
            [one, two],
        )
        projection = project_map(merged, inventory)
        self.assertEqual(len(projection["entries"]), len(inventory))
        conflicting_reply = self.reply(inventory)
        conflicting_reply["section"] = "1.2"
        term_index = next(
            index for index, row in enumerate(inventory) if row["kind"] == "term"
        )
        conflicting_reply["observations"][term_index]["plain_lemma"] = "عدد أصم"
        conflict = validate_fragment(
            "1.2",
            inventory,
            conflicting_reply,
            source_pages={},
            notation="latin",
        )
        with self.assertRaisesRegex(ValueError, "book map conflict"):
            merge_fragments(
                "mathematics_ar_eg08_t1",
                {"language_code": "ar", "numerals": "western", "notation": "latin"},
                [one, conflict],
            )
        conflict["observations"][term_index]["status"] = "confirmed"
        preferred = merge_fragments(
            "mathematics_ar_eg08_t1",
            {"language_code": "ar", "numerals": "western", "notation": "latin"},
            [one, conflict],
        )
        self.assertTrue(preferred["entries"])

    def test_confirmed_unit_forms_conflict_instead_of_choosing_shortest(self) -> None:
        inventory = self.inventory()
        unit_index = next(
            index for index, row in enumerate(inventory) if row["kind"] == "unit"
        )

        def confirmed(section: str, form: str) -> dict:
            reply = self.reply(inventory, unit=form)
            reply["section"] = section
            reply["observations"][unit_index].update(
                {
                    "status": "confirmed",
                    "basis": "",
                    "plain_lemma": form,
                    "math_text": form,
                    "evidence": [
                        {
                            "source_id": "source_1",
                            "split_page": 1,
                            "quote": form,
                        }
                    ],
                }
            )
            return validate_fragment(
                section,
                inventory,
                reply,
                source_pages={"source_1": 1},
                notation="latin",
            )

        with self.assertRaisesRegex(ValueError, "book map conflict"):
            merge_fragments(
                "book",
                {"language_code": "ar", "numerals": "western", "notation": "latin"},
                [confirmed("1.1", "سم"), confirmed("1.2", "سنتيمتر")],
            )
        short = confirmed("1.1", "سم")
        full = confirmed("1.2", "سم")
        full["observations"][unit_index]["plain_lemma"] = "سنتيمتر"
        merged = merge_fragments(
            "book",
            {"language_code": "ar", "numerals": "western", "notation": "latin"},
            [short, full],
        )
        unit = next(row for row in merged["entries"] if row["kind"] == "unit")
        self.assertEqual(unit["math_text"], "سم")
        self.assertEqual(unit["plain_lemma"], "سنتيمتر")

    def test_merge_accepts_inflected_arabic_unit_math_text(self) -> None:
        inventory = self.inventory()
        unit_index = next(
            index for index, row in enumerate(inventory) if row["kind"] == "unit"
        )

        def confirmed(section: str, math_text: str, lemma: str) -> dict:
            reply = self.reply(inventory, unit=math_text)
            reply["section"] = section
            reply["observations"][unit_index].update(
                {
                    "status": "confirmed",
                    "basis": "",
                    "plain_lemma": lemma,
                    "math_text": math_text,
                    "evidence": [
                        {
                            "source_id": "source_1",
                            "split_page": 1,
                            "quote": math_text,
                        }
                    ],
                }
            )
            return validate_fragment(
                section,
                inventory,
                reply,
                source_pages={"source_1": 1},
                notation="latin",
            )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unit_equivalences.json"
            merged = merge_fragments(
                "book",
                {"language_code": "ar", "numerals": "western", "notation": "latin"},
                [
                    confirmed("1.1", "ساعة", "ساعة"),
                    confirmed("2.1", "ساعات", "ساعة"),
                ],
                equivalences_path=path,
            )
            self.assertFalse(path.exists())
        unit = next(row for row in merged["entries"] if row["kind"] == "unit")
        self.assertIn(unit["math_text"], {"ساعة", "ساعات"})
        self.assertFalse(arabic_unit_forms_equivalent("ساعة", "ساعات"))
        self.assertNotIn("also", unit)

    def test_afal_broken_plural_stems_to_three_letter_lemma(self) -> None:
        self.assertEqual(arabic_unit_stem("أمتار"), "أمتار")
        self.assertEqual(arabic_unit_stem("مترًا"), "مترا")
        self.assertFalse(arabic_unit_forms_equivalent("متر", "أمتار"))
        self.assertFalse(arabic_unit_forms_equivalent("متر", "دنانير"))
        self.assertEqual(arabic_unit_stem("سم"), "سم")
        self.assertFalse(arabic_unit_forms_equivalent("دقيقة", "دقائق"))
        self.assertTrue(arabic_unit_forms_equivalent("مترًا", "مترا"))

    def test_merge_treats_lemma_copied_into_math_text_as_the_split_form(self) -> None:
        inventory = self.inventory()
        unit_index = next(
            index for index, row in enumerate(inventory) if row["kind"] == "unit"
        )

        def confirmed(section: str, math_text: str, lemma: str) -> dict:
            reply = self.reply(inventory, unit=math_text)
            reply["section"] = section
            reply["observations"][unit_index].update(
                {
                    "status": "confirmed",
                    "basis": "",
                    "plain_lemma": lemma,
                    "math_text": math_text,
                    "evidence": [
                        {
                            "source_id": "source_1",
                            "split_page": 1,
                            "quote": math_text,
                        }
                    ],
                }
            )
            return validate_fragment(
                section,
                inventory,
                reply,
                source_pages={"source_1": 1},
                notation="latin",
            )

        merged = merge_fragments(
            "book",
            {"language_code": "ar", "numerals": "western", "notation": "latin"},
            [
                confirmed("1.1", "م", "متر"),
                confirmed("4.4", "متر", "متر"),
            ],
        )
        unit = next(row for row in merged["entries"] if row["kind"] == "unit")
        self.assertEqual(unit["math_text"], "م")
        self.assertEqual(unit["plain_lemma"], "متر")

    def test_broken_plural_unit_forms_fail_without_a_saved_equivalence(self) -> None:
        inventory = self.inventory()
        unit_index = next(
            index for index, row in enumerate(inventory) if row["kind"] == "unit"
        )

        def confirmed(section: str, form: str) -> dict:
            reply = self.reply(inventory, unit=form)
            reply["section"] = section
            reply["observations"][unit_index].update(
                {
                    "status": "confirmed",
                    "basis": "",
                    "plain_lemma": form,
                    "math_text": form,
                    "evidence": [
                        {
                            "source_id": "source_1",
                            "split_page": 1,
                            "quote": form,
                        }
                    ],
                }
            )
            return validate_fragment(
                section,
                inventory,
                reply,
                source_pages={"source_1": 1},
                notation="latin",
            )

        with self.assertRaisesRegex(ValueError, "book map conflict"):
            merge_fragments(
                "book",
                {"language_code": "ar", "numerals": "western", "notation": "latin"},
                [confirmed("1.1", "دينار"), confirmed("1.2", "دنانير")],
            )

    def test_saved_broken_plural_keeps_math_lemma_and_also(self) -> None:
        inventory = self.inventory()
        unit_index = next(
            index for index, row in enumerate(inventory) if row["kind"] == "unit"
        )
        unit_id = inventory[unit_index]["id"]

        def confirmed(section: str, form: str) -> dict:
            reply = self.reply(inventory, unit=form)
            reply["section"] = section
            reply["observations"][unit_index].update(
                {
                    "status": "confirmed",
                    "basis": "",
                    "plain_lemma": form,
                    "math_text": form,
                    "evidence": [
                        {
                            "source_id": "source_1",
                            "split_page": 1,
                            "quote": form,
                        }
                    ],
                }
            )
            return validate_fragment(
                section,
                inventory,
                reply,
                source_pages={"source_1": 1},
                notation="latin",
            )

        merged = merge_fragments(
            "book",
            {"language_code": "ar", "numerals": "western", "notation": "latin"},
            [confirmed("1.1", "دينار"), confirmed("1.2", "دنانير")],
            equivalences={
                unit_id: {
                    "id": unit_id,
                    "source_math": "cm",
                    "math_text": "دينار",
                    "plain_lemma": "دينار",
                    "also": ["دنانير"],
                    "kind": "broken_plural",
                }
            },
        )
        unit = next(row for row in merged["entries"] if row["kind"] == "unit")
        self.assertEqual(unit["math_text"], "دينار")
        self.assertEqual(unit["plain_lemma"], "دينار")
        self.assertEqual(unit["also"], ["دنانير"])
        projection = project_map(merged, inventory)
        projected = next(row for row in projection["entries"] if row["kind"] == "unit")
        self.assertEqual(projected["also"], ["دنانير"])

    def test_abbrev_lemma_decision_keeps_short_math_and_full_lemma(self) -> None:
        inventory = self.inventory()
        unit_index = next(
            index for index, row in enumerate(inventory) if row["kind"] == "unit"
        )
        unit_id = inventory[unit_index]["id"]

        def confirmed(section: str, form: str) -> dict:
            reply = self.reply(inventory, unit=form)
            reply["section"] = section
            reply["observations"][unit_index].update(
                {
                    "status": "confirmed",
                    "basis": "",
                    "plain_lemma": form,
                    "math_text": form,
                    "evidence": [
                        {
                            "source_id": "source_1",
                            "split_page": 1,
                            "quote": form,
                        }
                    ],
                }
            )
            return validate_fragment(
                section,
                inventory,
                reply,
                source_pages={"source_1": 1},
                notation="latin",
            )

        merged = merge_fragments(
            "book",
            {"language_code": "ar", "numerals": "western", "notation": "latin"},
            [confirmed("1.1", "سم"), confirmed("1.2", "سنتيمتر")],
            equivalences={
                unit_id: {
                    "id": unit_id,
                    "source_math": "cm",
                    "math_text": "سم",
                    "plain_lemma": "سنتيمتر",
                    "also": ["سنتيمتر"],
                    "kind": "abbrev_lemma",
                }
            },
        )
        unit = next(row for row in merged["entries"] if row["kind"] == "unit")
        self.assertEqual(unit["math_text"], "سم")
        self.assertEqual(unit["plain_lemma"], "سنتيمتر")

    def test_tty_prompt_writes_unit_equivalences_sidecar(self) -> None:
        inventory = self.inventory()
        unit_index = next(
            index for index, row in enumerate(inventory) if row["kind"] == "unit"
        )

        def confirmed(section: str, form: str) -> dict:
            reply = self.reply(inventory, unit=form)
            reply["section"] = section
            reply["observations"][unit_index].update(
                {
                    "status": "confirmed",
                    "basis": "",
                    "plain_lemma": form,
                    "math_text": form,
                    "evidence": [
                        {
                            "source_id": "source_1",
                            "split_page": 1,
                            "quote": form,
                        }
                    ],
                }
            )
            return validate_fragment(
                section,
                inventory,
                reply,
                source_pages={"source_1": 1},
                notation="latin",
            )

        prompts: list[str] = []

        def ask(prompt: str) -> str:
            prompts.append(prompt)
            return "1"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unit_equivalences.json"
            merged = merge_fragments(
                "book",
                {"language_code": "ar", "numerals": "western", "notation": "latin"},
                [confirmed("1.1", "دينار"), confirmed("1.2", "دنانير")],
                equivalences_path=path,
                ask=ask,
                is_tty=True,
            )
            saved = load_unit_equivalences(path)
        unit = next(row for row in merged["entries"] if row["kind"] == "unit")
        self.assertEqual(unit["math_text"], "دينار")
        self.assertEqual(unit["plain_lemma"], "دينار")
        self.assertEqual(unit["also"], ["دنانير"])
        self.assertEqual(saved[unit["id"]]["math_text"], "دينار")
        self.assertEqual(saved[unit["id"]]["also"], ["دنانير"])
        self.assertEqual(len(prompts), 1)
        self.assertIn("دينار", prompts[0])
        self.assertIn("دنانير", prompts[0])

    def test_unattended_merge_writes_pending_unit_equivalences(self) -> None:
        inventory = self.inventory()
        unit_index = next(
            index for index, row in enumerate(inventory) if row["kind"] == "unit"
        )

        def confirmed(section: str, form: str) -> dict:
            reply = self.reply(inventory, unit=form)
            reply["section"] = section
            reply["observations"][unit_index].update(
                {
                    "status": "confirmed",
                    "basis": "",
                    "plain_lemma": form,
                    "math_text": form,
                    "evidence": [
                        {
                            "source_id": "source_1",
                            "split_page": 1,
                            "quote": form,
                        }
                    ],
                }
            )
            return validate_fragment(
                section,
                inventory,
                reply,
                source_pages={"source_1": 1},
                notation="latin",
            )

        with tempfile.TemporaryDirectory() as tmp:
            pending_path = Path(tmp) / "unit_equivalences.pending.json"
            with self.assertRaises(PendingUnitFormConflict):
                merge_fragments(
                    "book",
                    {"language_code": "ar", "numerals": "western", "notation": "latin"},
                    [confirmed("1.1", "دينار"), confirmed("1.2", "دنانير")],
                    pending_path=pending_path,
                )
            conflicts = load_pending_unit_equivalences(pending_path)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(set(conflicts[0]["forms"]), {"دينار", "دنانير"})

    def test_projection_contains_the_validated_unit_form(self) -> None:
        inventory = self.inventory()
        fragment = validate_fragment(
            "1.1",
            inventory,
            self.reply(inventory),
            source_pages={},
            notation="latin",
        )
        merged = merge_fragments(
            "book",
            {"language_code": "ar", "numerals": "western", "notation": "latin"},
            [fragment],
        )
        projection = project_map(merged, inventory)
        unit = next(
            row for row in projection["entries"] if row["kind"] == "unit"
        )
        self.assertEqual(unit["source_math"], "cm")
        self.assertEqual(unit["math_text"], "سم")


class GlossaryTests(unittest.TestCase):
    def test_inventory_has_no_units(self) -> None:
        section = {
            "number": "1.2",
            "spec": {"glossary": [{"term": "ratio", "definition": "a comparison"}]},
        }
        document = {
            "exercises": {
                "groups": [
                    {"exercises": [{"text": "5 cm", "options": [], "figures": []}]}
                ]
            }
        }
        kinds = {row["kind"] for row in live_section_inventory(section, document, {})}
        self.assertNotIn("unit", kinds)

    def test_english_snippets_capped_and_generic(self) -> None:
        section = {"number": "1.1", "spec": {"overview": r"length $\text{cm}$ and 12"}}
        document = {"exercises": {"groups": []}}
        snippets = english_snippets(section, document, {}, limit=8)
        self.assertTrue(snippets)
        self.assertLessEqual(sum(len(item) for item in snippets), 8 + max(len(item) for item in snippets))
        self.assertTrue(any("cm" in item or "12" in item for item in snippets))

    def test_english_snippets_omit_spec_ids(self) -> None:
        section = {
            "number": "1.1",
            "spec": {
                "overview": "a length of 12 cm",
                "objectives": [{"id": "objective_1", "text": "a length of 12 cm"}],
                "glossary": [{"id": "term_1", "term": "ratio", "definition": "a comparison"}],
            },
        }
        document = {
            "exercises": {
                "groups": [
                    {
                        "id": "group_3",
                        "objective": "objective_3",
                        "group": "group_3",
                        "exercises": [
                            {
                                "id": "exercise_9",
                                "exercise": "exercise_9",
                                "source_id": "objective_3",
                                "text": "use 4 cm",
                                "objectives": ["objective_3"],
                            }
                        ],
                    }
                ]
            }
        }
        snippets = english_snippets(section, document, {})
        blob = "\n".join(snippets)
        self.assertNotIn("objective_1", blob)
        self.assertNotIn("objective_3", blob)
        self.assertNotIn("term_1", blob)
        self.assertNotIn("group_3", blob)
        self.assertNotIn("exercise_9", blob)
        self.assertTrue(any("12" in item for item in snippets))

    def _inventory(self) -> list[dict]:
        return live_section_inventory(
            {
                "number": "1.1",
                "spec": {
                    "glossary": [{"id": "term_1", "term": "ratio", "definition": "comparison"}],
                },
            },
            {"exercises": {"groups": []}},
            {},
        )

    def test_validate_terminology_fragment_units_ok(self) -> None:
        inventory = self._inventory()
        fragment = validate_terminology_fragment(
            "1.1",
            inventory,
            {
                "section": "1.1",
                "observations": [
                    {
                        "id": inventory[0]["id"],
                        "status": "inferred",
                        "plain_lemma": "النسبة",
                        "math_text": "",
                        "basis": "book wording",
                        "evidence": [],
                    }
                ],
                "units": [
                    {
                        "english": "cm",
                        "status": "confirmed",
                        "arabic": "سم",
                        "also": [],
                        "abbreviation_only": True,
                        "basis": "",
                        "evidence": [{"source_id": "s", "split_page": 1, "quote": "سم"}],
                    }
                ],
            },
            source_pages={"s": 2},
            notation="latin",
        )
        units = [row for row in fragment["observations"] if row["kind"] == "unit"]
        self.assertEqual(units[0]["math_text"], "سم")
        self.assertTrue(units[0]["abbreviation_only"])

    def test_validate_terminology_fragment_rejects_bad_page(self) -> None:
        inventory = self._inventory()
        with self.assertRaises(ValueError):
            validate_terminology_fragment(
                "1.1",
                inventory,
                {
                    "observations": [
                        {
                            "id": inventory[0]["id"],
                            "status": "inferred",
                            "plain_lemma": "النسبة",
                            "basis": "x",
                            "evidence": [],
                        }
                    ],
                    "units": [
                        {
                            "english": "cm",
                            "status": "confirmed",
                            "arabic": "سم",
                            "abbreviation_only": True,
                            "evidence": [{"source_id": "s", "split_page": 9, "quote": "سم"}],
                        }
                    ],
                },
                source_pages={"s": 2},
                notation="latin",
            )

    def test_validate_terminology_fragment_unit_needs_arabic_letter(self) -> None:
        inventory = self._inventory()
        with self.assertRaises(ValueError):
            validate_terminology_fragment(
                "1.1",
                inventory,
                {
                    "observations": [
                        {
                            "id": inventory[0]["id"],
                            "status": "inferred",
                            "plain_lemma": "النسبة",
                            "basis": "x",
                            "evidence": [],
                        }
                    ],
                    "units": [
                        {
                            "english": "cm",
                            "status": "inferred",
                            "arabic": "cm",
                            "abbreviation_only": True,
                            "basis": "guess",
                            "evidence": [],
                        }
                    ],
                },
                source_pages={"s": 1},
                notation="latin",
            )

    def test_section_mismatch_normalised(self) -> None:
        inventory = self._inventory()
        fragment = validate_terminology_fragment(
            "4.5",
            inventory,
            {
                "section": "4-5",
                "observations": [
                    {
                        "id": inventory[0]["id"],
                        "status": "inferred",
                        "plain_lemma": "النسبة",
                        "basis": "x",
                        "evidence": [],
                    }
                ],
                "units": [],
            },
            source_pages={},
            notation="latin",
        )
        self.assertEqual(fragment["section"], "4.5")

    def test_glossary_entries_serialisation(self) -> None:
        book = {
            "entries": [
                {
                    "kind": "term",
                    "source_plain": "ratio",
                    "source_math": "",
                    "preferred": "النسبة",
                    "also": ["x"],
                    "abbreviation_only": True,
                },
                {
                    "kind": "symbol",
                    "source_plain": "",
                    "source_math": r"$\text{LE}$",
                    "preferred": "ج.م",
                    "also": [],
                    "abbreviation_only": False,
                },
                {
                    "kind": "symbol",
                    "source_plain": "",
                    "source_math": r"$\text{km}, \text{m}, \text{cm}, \text{mm}$",
                    "preferred": "كم, م, سم, مم",
                    "also": [],
                    "abbreviation_only": False,
                },
            ]
        }
        rows = glossary_entries(book)
        english = [row["english"] for row in rows]
        self.assertEqual(english, ["cm", "km", "LE", "m", "mm", "ratio"])
        paired = glossary_entries(
            {
                "entries": [
                    {
                        "kind": "symbol",
                        "source_plain": "",
                        "source_math": "$(x, y)$",
                        "preferred": "$(س، ص)$",
                        "also": [],
                        "abbreviation_only": False,
                    }
                ]
            }
        )
        self.assertEqual([row["english"] for row in paired], ["(x, y)"])
        self.assertEqual(paired[0]["arabic"], "(س، ص)")
        self.assertEqual(list(rows[-1]), ["kind", "english", "arabic", "also", "abbreviation_only"])
        self.assertEqual(rows[-1]["also"], [])

    def test_glossary_block_exact_header(self) -> None:
        block = glossary_block({"entries": []})
        self.assertTrue(block.startswith("## Book glossary"))
        self.assertIn("preposition in", block)
        self.assertIn("point labels are not units", block)

    def test_units_merge_on_the_canonical_base_and_follow_the_printed_form(self) -> None:
        from src.book_map import canonical_unit as live_canonical_unit

        self.assertEqual(live_canonical_unit("minutes"), "minute")
        self.assertEqual(live_canonical_unit("min"), "minute")
        self.assertEqual(live_canonical_unit("meters"), "m")
        self.assertEqual(live_canonical_unit("kilometers"), "km")
        self.assertEqual(live_canonical_unit("N"), "N")
        prompt = (
            Path(__file__).resolve().parents[2] / "prompts" / "translate" / "terminology.md"
        ).read_text(encoding="utf-8")
        self.assertIn("`N`", prompt)
        self.assertIn("Counting nouns", prompt)

    def test_glossary_fp_stable(self) -> None:
        book = {
            "entries": [
                {
                    "kind": "unit",
                    "source_plain": "cm",
                    "source_math": "cm",
                    "preferred": "سم",
                    "also": [],
                    "abbreviation_only": True,
                }
            ]
        }
        first = glossary_fp(book)
        self.assertEqual(glossary_fp(book), first)
        changed = json.loads(json.dumps(book))
        changed["entries"][0]["preferred"] = "م"
        self.assertNotEqual(glossary_fp(changed), first)

    def test_validate_strips_letter_text_on_arabic_symbols_only(self) -> None:
        section = {
            "number": "1.1",
            "spec": {
                "notations": [
                    {"id": "notation_1", "notation": "$v$", "meaning": "velocity"}
                ],
            },
        }
        document = {"exercises": {"groups": []}}
        inventory = live_section_inventory(section, document, {})
        item_id = inventory[0]["id"]

        def observation(math_text: str) -> dict:
            return {
                "id": item_id,
                "status": "inferred",
                "plain_lemma": "",
                "math_text": math_text,
                "basis": "printed",
                "evidence": [],
            }

        stripped = validate_terminology_fragment(
            "1.1",
            inventory,
            {"observations": [observation(r"\text{ع}")], "units": []},
            source_pages={},
            notation="arabic",
        )
        self.assertEqual(stripped["observations"][0]["math_text"], "ع")
        kept = validate_terminology_fragment(
            "1.1",
            inventory,
            {"observations": [observation(r"\text{نيوتن}")], "units": []},
            source_pages={},
            notation="arabic",
        )
        self.assertEqual(kept["observations"][0]["math_text"], r"\text{نيوتن}")
        latin = validate_terminology_fragment(
            "1.1",
            inventory,
            {"observations": [observation(r"\text{ع}")], "units": []},
            source_pages={},
            notation="latin",
        )
        self.assertEqual(latin["observations"][0]["math_text"], inventory[0]["source_math"])


if __name__ == "__main__":
    unittest.main()
