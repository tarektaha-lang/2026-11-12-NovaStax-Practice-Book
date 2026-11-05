"""Groups take their pages from the paper's selection and bbox marks."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from groups import Box, groups_in_selection, marks_agree, pages_of  # noqa: E402


def document(*groups: dict) -> dict:
    return {"number": "1.1", "exercises": {"groups": list(groups)}}


def group(group_id: str, *exercise_ids: str) -> dict:
    return {
        "id": group_id,
        "exercises": [{"id": exercise_id} for exercise_id in exercise_ids],
    }


def box(page: int, qid: str) -> Box:
    return Box(page=page, x=1, y=1, w=2, h=2, group_id=qid)


class GroupsFromPaperTests(unittest.TestCase):
    def test_selection_keeps_printed_groups_in_reading_order(self) -> None:
        source = document(
            group("group_a", "exercise_1"),
            group("group_b", "exercise_2", "exercise_3"),
            group("group_c", "exercise_4"),
        )
        kept = groups_in_selection(
            source, ["exercise_4", "exercise_2", "exercise_3"]
        )
        self.assertEqual([row["id"] for row in kept], ["group_c", "group_b"])

    def test_selection_that_splits_a_group_is_rejected(self) -> None:
        source = document(group("group_b", "exercise_2", "exercise_3"))
        with self.assertRaisesRegex(RuntimeError, "splits group"):
            groups_in_selection(source, ["exercise_2"])

    def test_mark_for_an_unprinted_exercise_is_rejected(self) -> None:
        source = document(group("group_a", "exercise_1"))
        kept = groups_in_selection(source, ["exercise_1"])
        with self.assertRaisesRegex(RuntimeError, "did not print"):
            marks_agree(
                "1.1",
                kept,
                {"exercise_1": [box(0, "exercise_1")], "exercise_9": [box(0, "exercise_9")]},
                1,
            )

    def test_a_divided_set_is_recorded_on_each_page_it_occupies(self) -> None:
        boxes = [
            box(2, "group_1"),
            box(2, "exercise_1"),
            box(3, "exercise_2"),
        ]
        self.assertEqual(pages_of(boxes), [2, 3])


if __name__ == "__main__":
    unittest.main()
