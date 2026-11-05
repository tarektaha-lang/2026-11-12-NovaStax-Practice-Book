from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from new_questions import (  # noqa: E402
    Exercise,
    ExerciseGroup,
    form_set_padding_problems,
    form_set_stem,
    pack_form_sets,
)


TEXTBOOK_MCQ_STEM = "اختر الإجابة الصحيحة فيما يلي:"


def standalone_mcq(text: str = "Question?") -> ExerciseGroup:
    return ExerciseGroup(
        exercises=[
            Exercise(
                text=text,
                options=["$1$", "$2$", "$3$", "$4$"],
                objectives=["objective_1"],
                answer="أ",
            )
        ]
    )


class PackFormSetsTests(unittest.TestCase):
    def test_four_listen_and_choose_pack_without_source_stems(self) -> None:
        groups, packed = pack_form_sets(
            [
                standalone_mcq("Listen and choose.\n\nQuestion one?"),
                standalone_mcq("Listen and choose.\n\nQuestion two?"),
                standalone_mcq("Listen and choose.\n\nQuestion three?"),
                standalone_mcq("Listen and choose.\n\nQuestion four?"),
            ],
            language_code="en",
            source_stems={},
        )
        self.assertEqual(packed, 4)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].stem, "Listen and choose.")
        self.assertNotIn("Listen and choose.", groups[0].exercises[0].text)

    def test_four_standalone_mcq_become_one_set(self) -> None:
        source = {"mcq": TEXTBOOK_MCQ_STEM}
        groups, packed = pack_form_sets(
            [standalone_mcq() for _ in range(4)],
            language_code="ar",
            source_stems=source,
        )
        self.assertEqual(packed, 4)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].exercises), 4)
        self.assertEqual(groups[0].stem, form_set_stem("mcq", "ar", source))
        self.assertEqual(len(groups[0].exercises), 4)

    def test_five_standalone_mcq_need_padding_before_set(self) -> None:
        problems = form_set_padding_problems(
            [standalone_mcq() for _ in range(5)], language_code="ar"
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("write 3 more", problems[0])
        self.assertIn("set of 8", problems[0])

    def test_eight_standalone_mcq_become_one_set(self) -> None:
        source = {"mcq": TEXTBOOK_MCQ_STEM}
        groups, packed = pack_form_sets(
            [standalone_mcq() for _ in range(8)],
            language_code="ar",
            source_stems=source,
        )
        self.assertEqual(packed, 8)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].exercises), 8)

    def test_existing_passage_set_is_unchanged(self) -> None:
        original = ExerciseGroup(
            stem="Shared passage.",
            exercises=[
                Exercise(
                    text="First?",
                    objectives=["objective_1"],
                    answer="…",
                ),
                Exercise(
                    text="Second?",
                    objectives=["objective_1"],
                    answer="…",
                ),
            ],
        )
        groups, packed = pack_form_sets([original], language_code="ar")
        self.assertEqual(packed, 0)
        self.assertEqual(groups[0].stem, "Shared passage.")
        self.assertEqual(len(groups[0].exercises), 2)


if __name__ == "__main__":
    unittest.main()
