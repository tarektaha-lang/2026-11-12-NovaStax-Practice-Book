from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from translate import (  # noqa: E402
    GroupTranslateReply,
    SpecTranslateReply,
    TranslationIssue,
    issues_by_group,
    review_batches,
    tikz_has_words,
)


class TranslationContractTests(unittest.TestCase):
    def test_unit_only_lengthmark_is_not_a_word_after_mapping(self) -> None:
        projection = {
            "entries": [
                {"kind": "unit", "source_math": "cm", "math_text": "سم"},
            ]
        }
        tikz = r"\lengthmark[][sloped]{B}{E}{A}{$7\text{ سم}$}"
        self.assertFalse(tikz_has_words(tikz, projection))
        self.assertTrue(tikz_has_words(r"\node at (0,0) {Height};"))
        self.assertTrue(
            tikz_has_words(
                r"\fill (-3,5) circle (2pt) node [above=3pt] {Playground ($B$)};"
            )
        )

    def test_spec_reply_requires_explicit_map_fragment(self) -> None:
        with self.assertRaises(ValidationError):
            SpecTranslateReply.model_validate(
                {
                    "status": "abort",
                    "abort_reason": "source unavailable",
                    "spec": None,
                    "terminology": [],
                }
            )
        reply = SpecTranslateReply.model_validate(
            {
                "status": "abort",
                "abort_reason": "source unavailable",
                "spec": None,
                "terminology": [],
                "map_fragment": None,
            }
        )
        self.assertIsNone(reply.map_fragment)

    def test_actual_partial_reply_fails_response_schema(self) -> None:
        fixture = (
            Path(__file__).parent
            / "fixtures"
            / "translation"
            / "group_7_partial_reply.json"
        )
        with self.assertRaises(ValidationError):
            GroupTranslateReply.model_validate_json(
                fixture.read_text(encoding="utf-8")
            )

    def test_complete_reply_requires_explicit_empty_fields(self) -> None:
        reply = GroupTranslateReply.model_validate(
            {
                "status": "ok",
                "abort_reason": "",
                "group": {
                    "id": "group_1",
                    "stem": "",
                    "figures": [],
                    "exercises": [
                        {
                            "id": "exercise_1",
                            "text": "أوجد $x$.",
                            "options": [],
                            "answer": "$x = 2$.",
                            "figures": [],
                        }
                    ],
                },
            }
        )
        self.assertEqual(reply.group.stem, "")

    def test_review_batches_pair_source_and_target(self) -> None:
        source = [
            {
                "id": "group_1",
                "stem": "",
                "exercises": [
                    {
                        "id": "exercise_1",
                        "text": "Find $x$.",
                        "options": [],
                        "answer": "$2$.",
                    }
                ],
            }
        ]
        target = [
            {
                "id": "group_1",
                "stem": "",
                "figures": [],
                "exercises": [
                    {
                        "id": "exercise_1",
                        "text": "أوجد $x$.",
                        "options": [],
                        "answer": "$2$.",
                        "figures": [],
                    }
                ],
            }
        ]
        batches = review_batches(
            source,
            target,
            {"group_1": [{"field": "exercise_1.text", "token": "N1", "reason": "dual"}]},
            {"group_1": [{"index": 0, "english_labels": ["5 cm"]}]},
        )
        pair = batches[0][0]
        self.assertEqual(pair["group"], "group_1")
        self.assertIn("english", pair)
        self.assertIn("arabic", pair)
        self.assertEqual(pair["dropped_numbers"][0]["token"], "N1")
        self.assertEqual(pair["figure_labels"][0]["english_labels"], ["5 cm"])

    def test_figure_labels_are_english_node_bodies(self) -> None:
        from translate import figure_labels_for_group

        group = {
            "id": "group_1",
            "figures": [{"description": "axis"}],
            "exercises": [{"id": "exercise_1", "figures": [{"description": "plot"}]}],
        }
        drawings = {
            "axis": {"tikz": r"\node at (0,0) {5 cm};"},
            "plot": {"tikz": r"\node {12 m};"},
        }
        self.assertEqual(
            figure_labels_for_group(group, drawings),
            [
                {"index": 0, "english_labels": ["5 cm"]},
                {"exercise": "exercise_1", "index": 0, "english_labels": ["12 m"]},
            ],
        )
        self.assertEqual(figure_labels_for_group(group, {}), [])

    def test_review_feedback_is_grouped_for_targeted_retry(self) -> None:
        feedback = issues_by_group(
            [
                TranslationIssue(
                    group="group_7",
                    exercise="exercise_14",
                    field="text",
                    reason="English prose remains.",
                ),
                TranslationIssue(
                    group="group_7",
                    exercise="",
                    field="stem",
                    reason="Terminology is incorrect.",
                ),
            ]
        )
        self.assertEqual(set(feedback), {"group_7"})
        self.assertIn("exercise_14.text", feedback["group_7"])
        self.assertIn("stem", feedback["group_7"])


if __name__ == "__main__":
    unittest.main()
