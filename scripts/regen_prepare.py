#!/usr/bin/env python3
"""Move a section file aside and seed .partial with the objectives to keep.

Dry-run by default. Pass --apply to move files. Never deletes. An already-moved
or missing file is a no-op with a note, so the steps can be re-run.

    python3 scripts/regen_prepare.py applied_mathematics_en_eg12_t0 02_01_01 \\
        --stage source_questions --apply
    python3 scripts/regen_prepare.py pure_mathematics_en_eg12_t0 02_02_01 \\
        --objective objective_4 --apply

`--stage source_questions` moves only the 07 file. `--stage new_questions`
(the default) moves the 08 file; with `--objective` it also writes a
`.partial` sidecar of the kept objectives after refusing any of those that
still contain a broken interval. Run new_questions without `--update` so the
sidecar is used.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from budgets import BUDGETS_STAGE  # noqa: E402
from new_questions import (  # noqa: E402
    EXERCISES_STAGE,
    EXTRA_EXERCISES,
    ExerciseGroup,
    PARTIAL_DIR,
    interval_problems,
    write_partial,
)
from source_questions import QUESTIONS_STAGE  # noqa: E402
from sources import OUTPUT_DIR, PROJECT_ROOT  # noqa: E402

BACKUP_DIR = "_regen_backup"
STAGES = {
    "new_questions": EXERCISES_STAGE,
    "source_questions": QUESTIONS_STAGE,
}


def stage_dir_name(stage: str) -> str:
    try:
        return STAGES[stage]
    except KeyError as error:
        raise RuntimeError(f"unknown stage {stage!r}") from error


def load_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read {path}: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} is not a JSON object")
    return payload


def as_exercise_group(raw: dict) -> ExerciseGroup:
    """An 08 group without the paper's id/objective keys, for write_partial."""
    payload = {
        key: value for key, value in raw.items() if key in ExerciseGroup.model_fields
    }
    return ExerciseGroup.model_validate(payload)


def budget_rows(book_id: str, stem: str) -> list[dict]:
    """The 06 file's objective counts, in the order that file lists them."""
    path = OUTPUT_DIR / book_id / BUDGETS_STAGE / f"{stem}.json"
    try:
        rows = load_object(path)["budget"]["objectives"]
    except FileNotFoundError:
        raise RuntimeError(
            f"no budget at {display_path(path)}; run budgets.py first"
        ) from None
    except (LookupError, TypeError, RuntimeError) as error:
        raise RuntimeError(f"cannot read {display_path(path)}: {error}") from error
    return [{"id": row["id"], "exercises": row["exercises"]} for row in rows]


def interval_hits(groups: list[ExerciseGroup], objective: str) -> list[str]:
    """Broken intervals in a kept objective's options and answers."""
    problems: list[str] = []
    for index, group in enumerate(groups, start=1):
        for number, exercise in enumerate(group.exercises, start=1):
            where = f"{objective} group {index} exercise {number}"
            problems.extend(interval_problems(exercise.answer, f"{where} answer"))
            for letter, option in zip("ABCD", exercise.options):
                problems.extend(
                    interval_problems(option, f"{where} choice ({letter})")
                )
    return problems


def groups_by_objective(document: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for raw in (document.get("exercises") or {}).get("groups") or []:
        if not isinstance(raw, dict):
            continue
        objective = str(raw.get("objective") or "")
        grouped.setdefault(objective, []).append(raw)
    return grouped


def backup_dest(book_id: str, stage: str, stem: str, stamp: str) -> Path:
    return (
        OUTPUT_DIR
        / book_id
        / BACKUP_DIR
        / stamp
        / stage_dir_name(stage)
        / f"{stem}.json"
    )


def source_path(book_id: str, stage: str, stem: str) -> Path:
    return OUTPUT_DIR / book_id / stage_dir_name(stage) / f"{stem}.json"


def partial_path(book_id: str, stem: str) -> Path:
    return OUTPUT_DIR / book_id / EXERCISES_STAGE / PARTIAL_DIR / f"{stem}.json"


def display_path(path: Path) -> str:
    """A path relative to the project when it sits there, else as given."""
    for root in (PROJECT_ROOT, OUTPUT_DIR):
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(path)


def note(message: str) -> None:
    print(message, flush=True)


def prepare(
    book_id: str,
    stem: str,
    *,
    stage: str = "new_questions",
    objectives: list[str] | None = None,
    apply: bool = False,
    stamp: str | None = None,
) -> int:
    """Move one stage file aside. Returns 0, or 1 when kept objectives are unclean."""
    if stage not in STAGES:
        raise RuntimeError(f"unknown stage {stage!r}")
    if stage == "source_questions" and objectives:
        raise RuntimeError("--objective is only for --stage new_questions")

    src = source_path(book_id, stage, stem)
    if not src.is_file():
        note(f"no-op: missing {display_path(src)}")
        return 0

    when = stamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = backup_dest(book_id, stage, stem, when)
    relative_src = display_path(src)
    relative_dest = display_path(dest)

    kept: dict[str, list[ExerciseGroup]] = {}
    number = ""
    rows: list[dict] = []
    if stage == "new_questions" and objectives:
        document = load_object(src)
        number = str(document.get("number") or "")
        if not number:
            raise RuntimeError(f"{display_path(src)} has no dotted section number")
        rows = budget_rows(book_id, stem)
        named = set(objectives)
        unknown = sorted(named - {row["id"] for row in rows})
        if unknown:
            raise RuntimeError(
                f"{stem} budget does not list {', '.join(unknown)}"
            )
        grouped = groups_by_objective(document)
        refused: list[str] = []
        for row in rows:
            objective = row["id"]
            if objective in named:
                continue
            groups = [as_exercise_group(raw) for raw in grouped.get(objective) or []]
            hits = interval_hits(groups, objective)
            if hits:
                refused.extend(hits)
                continue
            count = sum(len(group.exercises) for group in groups)
            need = row["exercises"] + EXTRA_EXERCISES
            if count != need:
                refused.append(
                    f"{objective} has {count} exercises, not the {need} "
                    f"budget+{EXTRA_EXERCISES} kept_objectives requires; name it too"
                )
                continue
            kept[objective] = groups
        if refused:
            note(
                "refuse: kept objectives still write broken intervals; "
                "name them too:\n  " + "\n  ".join(refused)
            )
            return 1
        if not kept:
            note(
                f"no objectives to keep after {', '.join(objectives)}; "
                "will move the whole file and write no .partial"
            )

    action = "move" if apply else "would move"
    note(f"{action} {relative_src} -> {relative_dest}")
    sidecar = partial_path(book_id, stem)
    if kept:
        action_w = "write" if apply else "would write"
        note(
            f"{action_w} {display_path(sidecar)} "
            f"with {', '.join(kept)} kept "
            f"(budget+{EXTRA_EXERCISES} each)"
        )

    if not apply:
        return 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dest)
    if kept:
        write_partial(sidecar, book_id, number, kept, rows)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("book_id", help="Book ID, e.g. pure_mathematics_en_eg12_t0")
    parser.add_argument("stem", help="Padded section stem, e.g. 02_02_01")
    parser.add_argument(
        "--objective",
        action="append",
        default=[],
        metavar="ID",
        help="objective to regenerate (repeatable); the rest are kept in .partial",
    )
    parser.add_argument(
        "--stage",
        choices=tuple(STAGES),
        default="new_questions",
        help="which stage file to move (default: new_questions)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="move files; without this flag, print the plan only",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return prepare(
            args.book_id,
            args.stem,
            stage=args.stage,
            objectives=args.objective or None,
            apply=args.apply,
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
