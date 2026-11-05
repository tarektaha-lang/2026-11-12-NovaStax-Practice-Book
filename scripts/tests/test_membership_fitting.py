from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from papers import lock_section_stems, unaccepted_lock_stems, unbound_book_note  # noqa: E402
from pipeline import TRANSLATE_STAGES, stage_command  # noqa: E402
from src.latex import (  # noqa: E402
    Block,
    apply_stored_selection,
    membership_lock_from,
    reusable_selection,
    select_blocks,
)


def exercise_block(group_id: str, *exercise_ids: str) -> Block:
    return Block(
        key=group_id,
        kind="set" if len(exercise_ids) > 1 else "exercise",
        objective="objective_1",
        group={
            "id": group_id,
            "objective": "objective_1",
            "exercises": [{"id": name} for name in exercise_ids],
        },
        group_key=group_id,
        height=30,
        depth=5,
    )


class MembershipFittingTests(unittest.TestCase):
    def test_lock_stems_drop_the_selection_json_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "04_05.selection.json").write_text("{}", encoding="utf-8")
            (root / "01_03.selection.json").write_text("{}", encoding="utf-8")
            self.assertEqual(lock_section_stems(root), ["01_03", "04_05"])
            self.assertEqual(
                unaccepted_lock_stems(root, {"04_05"}),
                ["01_03"],
            )
            self.assertNotEqual(
                [path.stem for path in root.glob("*.selection.json")],
                ["01_03", "04_05"],
            )

    def test_translation_pipeline_uses_membership_only_flag(self) -> None:
        stage = next(row for row in TRANSLATE_STAGES if row.name == "ar_papers")
        command = stage_command(
            stage,
            "mathematics_ar_eg08_t1",
            section="4",
            update=False,
            model=None,
            thinking_level=None,
            workers=None,
            tts_model=None,
            selection_from=Path("/tmp/en-papers"),
        )
        self.assertIn("--membership-from", command)
        self.assertNotIn("--selection-from", command)

    def test_membership_parser_rejects_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "selection.json"
            path.write_text(
                json.dumps(
                    {
                        "printed_ids": ["exercise_1"],
                        "left_out": {"objective_1": ["exercise_1"]},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "both prints and omits"):
                membership_lock_from(path)

    def test_membership_only_accepts_legacy_left_out_without_order(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "selection.json"
            path.write_text(
                json.dumps({"left_out": {"objective_1": ["exercise_2"]}}),
                encoding="utf-8",
            )
            lock = membership_lock_from(path, require_printed=False)
            self.assertEqual(lock.printed_ids, ())
            self.assertEqual(lock.left_out["objective_1"], ["exercise_2"])

    def test_same_membership_is_kept_without_order_lock(self) -> None:
        blocks = [
            exercise_block("group_1", "exercise_1"),
            exercise_block("group_2", "exercise_2"),
            exercise_block("group_3", "exercise_3"),
        ]
        chosen, selection = select_blocks(
            blocks,
            {"objective_1": 2},
            locked_left_out={"objective_1": ["exercise_2"]},
            expected_printed_ids=("exercise_1", "exercise_3"),
        )
        ids = {
            exercise["id"]
            for block in chosen
            for exercise in (block.group or {}).get("exercises", [])
        }
        self.assertEqual(ids, {"exercise_1", "exercise_3"})
        self.assertEqual(selection.printed, 2)

    def test_stored_selection_is_reused_until_the_exercise_set_changes(self) -> None:
        document = {
            "exercises": {
                "groups": [
                    {
                        "objective": "objective_1",
                        "exercises": [{"id": "exercise_1"}],
                    },
                    {
                        "objective": "objective_1",
                        "exercises": [{"id": "exercise_2"}],
                    },
                ]
            }
        }
        stored = {
            "printed_ids": ["exercise_1"],
            "left_out": {"objective_1": ["exercise_2"]},
        }
        with tempfile.TemporaryDirectory() as folder:
            tex = Path(folder) / "01_02.tex"
            tex.write_text("", encoding="utf-8")
            tex.with_suffix(".selection.json").write_text(
                json.dumps(stored), encoding="utf-8"
            )
            lock = reusable_selection(tex, document)
            self.assertIsNotNone(lock)
            assert lock is not None
            self.assertEqual(list(lock.printed_ids), ["exercise_1"])
            self.assertEqual(lock.left_out, {"objective_1": ["exercise_2"]})
            document["exercises"]["groups"][0]["exercises"].append({"id": "exercise_3"})
            self.assertIsNone(reusable_selection(tex, document))
            document["exercises"]["groups"][0]["exercises"].pop()
            left, printed, expected = apply_stored_selection(
                tex,
                document,
                {"objective_1": ["exercise_2"]},
                None,
                ["exercise_1"],
            )
            self.assertEqual(printed, ["exercise_1"])
            self.assertEqual(list(expected or []), ["exercise_1"])
            self.assertEqual(left, {"objective_1": ["exercise_2"]})
            _left, kept_order, _expected = apply_stored_selection(
                tex,
                document,
                {"objective_1": ["exercise_1"]},
                None,
                ["exercise_2"],
            )
            self.assertIsNone(kept_order)

    def test_stored_selection_discarded_when_lock_would_split_a_set(self) -> None:
        document = {
            "exercises": {
                "groups": [
                    {
                        "id": "group_1",
                        "objective": "objective_1",
                        "stem": "Listen and choose.",
                        "exercises": [
                            {"id": "exercise_1"},
                            {"id": "exercise_2"},
                        ],
                    }
                ]
            }
        }
        stored = {
            "printed_ids": ["exercise_1"],
            "left_out": {"objective_1": ["exercise_2"]},
        }
        with tempfile.TemporaryDirectory() as folder:
            tex = Path(folder) / "01_01.tex"
            tex.write_text("", encoding="utf-8")
            tex.with_suffix(".selection.json").write_text(
                json.dumps(stored), encoding="utf-8"
            )
            self.assertIsNone(reusable_selection(tex, document))

    def test_section_only_says_the_book_pdf_was_not_rebound(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "book.pdf"
            section = root / "01_02.pdf"
            book.write_bytes(b"%PDF")
            section.write_bytes(b"%PDF")
            os_mtime = section.stat().st_mtime + 5
            import os
            os.utime(section, (os_mtime, os_mtime))
            self.assertEqual(
                unbound_book_note(book, [section]),
                "book PDF was NOT rebound and is now older than the section paper",
            )
            os.utime(book, (os_mtime + 5, os_mtime + 5))
            self.assertEqual(
                unbound_book_note(book, [section]),
                "book PDF was NOT rebound",
            )

    def test_partial_set_membership_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "splits group"):
            select_blocks(
                [exercise_block("group_1", "exercise_1", "exercise_2")],
                {"objective_1": 1},
                locked_left_out={"objective_1": ["exercise_2"]},
                expected_printed_ids=("exercise_1",),
            )


if __name__ == "__main__":
    unittest.main()
