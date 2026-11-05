from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from new_questions import (  # noqa: E402
    EXERCISES_STAGE,
    EXTRA_EXERCISES,
    PARTIAL_DIR,
    kept_objectives,
)
from regen_prepare import (  # noqa: E402
    BACKUP_DIR,
    as_exercise_group,
    main,
    prepare,
)
from source_questions import QUESTIONS_STAGE  # noqa: E402
from budgets import BUDGETS_STAGE  # noqa: E402

BOOK = "sample_en_eg12_t0"
STEM = "02_02_01"
NUMBER = "2.2.1"


def exercise(objective: str, text: str = "$x$", answer: str = "$1$") -> dict:
    return {
        "id": "exercise_1",
        "text": text,
        "objectives": [objective],
        "answer": answer,
    }


def n_exercises(objective: str, count: int, answer: str = "$1$") -> list[dict]:
    rows = []
    for index in range(count):
        row = exercise(objective, text=f"${index}$", answer=answer)
        row["id"] = f"exercise_{index + 1}"
        rows.append(row)
    return rows


def groups_for(objective: str, count: int, answer: str = "$1$") -> list[dict]:
    """`count` standalone groups, as 08 files store them."""
    return [
        {
            "id": f"group_{index + 1}",
            "objective": objective,
            "exercises": [row],
        }
        for index, row in enumerate(n_exercises(objective, count, answer))
    ]


def document(objectives: dict[str, tuple[int, str]]) -> dict:
    """08-shaped file. values are (budget, answer used in every exercise)."""
    groups: list[dict] = []
    rows = []
    total = 0
    budget_total = 0
    for objective, (budget, answer) in objectives.items():
        written = budget + EXTRA_EXERCISES
        groups.extend(groups_for(objective, written, answer))
        rows.append(
            {
                "id": objective,
                "budget": budget,
                "exercises": written,
                "word_problems": 0,
            }
        )
        total += written
        budget_total += budget
    return {
        "book_id": BOOK,
        "number": NUMBER,
        "exercises": {
            "total": total,
            "budget": budget_total,
            "word_problems": 0,
            "objectives": rows,
            "groups": groups,
        },
    }


def budget_file(objectives: dict[str, int]) -> dict:
    return {
        "budget": {
            "total": sum(objectives.values()),
            "objectives": [
                {"id": name, "exercises": count} for name, count in objectives.items()
            ],
        }
    }


def write_tree(root: Path, *, with_08: bool = True, with_07: bool = False) -> None:
    (root / BOOK / BUDGETS_STAGE).mkdir(parents=True)
    (root / BOOK / BUDGETS_STAGE / f"{STEM}.json").write_text(
        json.dumps(budget_file({"objective_1": 1, "objective_4": 1}), indent=2) + "\n",
        encoding="utf-8",
    )
    if with_08:
        folder = root / BOOK / EXERCISES_STAGE
        folder.mkdir(parents=True)
        (folder / f"{STEM}.json").write_text(
            json.dumps(
                document(
                    {
                        "objective_1": (1, "$1$"),
                        "objective_4": (1, r"$-\infty, 0[$"),
                    }
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if with_07:
        folder = root / BOOK / QUESTIONS_STAGE
        folder.mkdir(parents=True)
        (folder / f"{STEM}.json").write_text(
            json.dumps({"book_id": BOOK, "number": NUMBER, "questions": {"count": 1}})
            + "\n",
            encoding="utf-8",
        )


class RegenPrepareTests(unittest.TestCase):
    def test_as_exercise_group_drops_paper_keys(self) -> None:
        group = as_exercise_group(groups_for("objective_1", 1)[0])
        dumped = group.model_dump(exclude_defaults=True)
        self.assertNotIn("id", dumped)
        self.assertNotIn("objective", dumped)
        self.assertEqual(len(group.exercises), 1)

    def test_dry_run_does_not_move(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_tree(root)
            src = root / BOOK / EXERCISES_STAGE / f"{STEM}.json"
            with patch("regen_prepare.OUTPUT_DIR", root):
                code = prepare(
                    BOOK, STEM, objectives=["objective_4"], apply=False, stamp="t"
                )
            self.assertEqual(code, 0)
            self.assertTrue(src.is_file())
            self.assertFalse((root / BOOK / BACKUP_DIR).exists())
            self.assertFalse(
                (root / BOOK / EXERCISES_STAGE / PARTIAL_DIR / f"{STEM}.json").exists()
            )

    def test_apply_moves_and_partial_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_tree(root)
            src = root / BOOK / EXERCISES_STAGE / f"{STEM}.json"
            sidecar = root / BOOK / EXERCISES_STAGE / PARTIAL_DIR / f"{STEM}.json"
            dest = (
                root / BOOK / BACKUP_DIR / "t" / EXERCISES_STAGE / f"{STEM}.json"
            )
            with patch("regen_prepare.OUTPUT_DIR", root):
                code = prepare(
                    BOOK, STEM, objectives=["objective_4"], apply=True, stamp="t"
                )
            self.assertEqual(code, 0)
            self.assertFalse(src.is_file())
            self.assertTrue(dest.is_file())
            self.assertTrue(sidecar.is_file())
            kept = kept_objectives(
                sidecar,
                [
                    {"id": "objective_1", "exercises": 1},
                    {"id": "objective_4", "exercises": 1},
                ],
            )
            self.assertIn("objective_1", kept)
            self.assertNotIn("objective_4", kept)
            self.assertEqual(
                sum(len(group.exercises) for group in kept["objective_1"]),
                1 + EXTRA_EXERCISES,
            )

    def test_refuse_interval_in_kept_objective(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_tree(root)
            src = root / BOOK / EXERCISES_STAGE / f"{STEM}.json"
            with patch("regen_prepare.OUTPUT_DIR", root):
                code = prepare(
                    BOOK, STEM, objectives=["objective_1"], apply=True, stamp="t"
                )
            self.assertEqual(code, 1)
            self.assertTrue(src.is_file())
            self.assertFalse((root / BOOK / BACKUP_DIR).exists())

    def test_missing_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_tree(root, with_08=False)
            with patch("regen_prepare.OUTPUT_DIR", root):
                code = prepare(BOOK, STEM, apply=True, stamp="t")
            self.assertEqual(code, 0)
            self.assertFalse((root / BOOK / BACKUP_DIR).exists())

    def test_already_moved_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_tree(root)
            with patch("regen_prepare.OUTPUT_DIR", root):
                self.assertEqual(
                    prepare(
                        BOOK, STEM, objectives=["objective_4"], apply=True, stamp="t1"
                    ),
                    0,
                )
                self.assertEqual(
                    prepare(
                        BOOK, STEM, objectives=["objective_4"], apply=True, stamp="t2"
                    ),
                    0,
                )
            self.assertTrue(
                (
                    root / BOOK / BACKUP_DIR / "t1" / EXERCISES_STAGE / f"{STEM}.json"
                ).is_file()
            )
            self.assertFalse(
                (
                    root / BOOK / BACKUP_DIR / "t2" / EXERCISES_STAGE / f"{STEM}.json"
                ).is_file()
            )

    def test_whole_file_moves_without_partial(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_tree(root)
            src = root / BOOK / EXERCISES_STAGE / f"{STEM}.json"
            with patch("regen_prepare.OUTPUT_DIR", root):
                code = prepare(BOOK, STEM, apply=True, stamp="t")
            self.assertEqual(code, 0)
            self.assertFalse(src.is_file())
            self.assertFalse(
                (root / BOOK / EXERCISES_STAGE / PARTIAL_DIR / f"{STEM}.json").exists()
            )

    def test_source_questions_moves_only_07(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_tree(root, with_07=True)
            src_07 = root / BOOK / QUESTIONS_STAGE / f"{STEM}.json"
            src_08 = root / BOOK / EXERCISES_STAGE / f"{STEM}.json"
            with patch("regen_prepare.OUTPUT_DIR", root):
                code = prepare(
                    BOOK, STEM, stage="source_questions", apply=True, stamp="t"
                )
            self.assertEqual(code, 0)
            self.assertFalse(src_07.is_file())
            self.assertTrue(src_08.is_file())
            self.assertTrue(
                (
                    root / BOOK / BACKUP_DIR / "t" / QUESTIONS_STAGE / f"{STEM}.json"
                ).is_file()
            )

    def test_objective_rejected_on_source_questions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_tree(root, with_07=True)
            with patch("regen_prepare.OUTPUT_DIR", root):
                code = main(
                    [
                        BOOK,
                        STEM,
                        "--stage",
                        "source_questions",
                        "--objective",
                        "objective_4",
                        "--apply",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertTrue((root / BOOK / QUESTIONS_STAGE / f"{STEM}.json").is_file())


if __name__ == "__main__":
    unittest.main()
