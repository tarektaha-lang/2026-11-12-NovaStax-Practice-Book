#!/usr/bin/env python3
"""Backfill form sets on section JSON already written (choose, T/F, complete).

New runs pack form sets inside new_questions.repaired(); use this only to fix
sections on disk before re-running papers.

    python3 scripts/merge_question_sets.py science_and_technology_education_ar_dz05
    python3 scripts/merge_question_sets.py science_and_technology_education_ar_dz05 -s 1.1.1
    python3 scripts/merge_question_sets.py science_and_technology_education_ar_dz05 --dry-run
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from new_questions import extraction as load_source_questions
    from new_questions import pack_document_exercise_groups, written_sections
    from questions_review import STAGE, assert_sets_valid, recount, renumber_exercises
    from src.source_form_stems import source_form_stems_for_objective
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")


def section_documents(book_id: str, section: str | None) -> list[tuple[Path, dict]]:
    from sources import OUTPUT_DIR

    rows = written_sections(book_id, section, STAGE, "questions_review.py")
    return [(OUTPUT_DIR / book_id / STAGE / f"{stem}.json", document) for stem, document in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="book under data/output/")
    parser.add_argument("-s", "--section", help="one section number, e.g. 1.1.1")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print merge stats without writing files",
    )
    args = parser.parse_args(argv)

    sections = section_documents(args.book_id, args.section)

    totals = {"sections": 0, "standalone_in": 0, "sets_out": 0, "merged_exercises": 0}
    source_cache: dict[str, dict] = {}
    for path, document in sections:
        stem = path.stem
        if stem not in source_cache:
            try:
                source_cache[stem] = load_source_questions(args.book_id, stem)
            except RuntimeError:
                source_cache[stem] = {}
        source_doc = source_cache[stem]
        stems_by_objective = {
            row["id"]: source_form_stems_for_objective(source_doc, row["id"])
            for row in document["exercises"]["objectives"]
        }
        groups, stats = pack_document_exercise_groups(
            document["exercises"]["groups"],
            document["language_code"],
            source_by_objective=stems_by_objective,
        )
        totals["sections"] += 1
        for key in ("standalone_in", "sets_out", "merged_exercises"):
            totals[key] += stats[key]
        line = (
            f"  {document['number']}: {stats['standalone_in']} standalone → "
            f"{stats['sets_out']} set(s), {stats['merged_exercises']} exercise(s) merged, "
            f"{stats['left_standalone']} left alone"
        )
        if args.dry_run:
            print(line)
            continue
        merged = copy.deepcopy(document)
        merged["exercises"]["groups"] = groups
        renumber_exercises(merged)
        recount(merged)
        assert_sets_valid(merged)
        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(line)

    summary = (
        f"OK: {totals['sections']} section(s), "
        f"{totals['merged_exercises']} exercise(s) in {totals['sets_out']} new set(s)"
    )
    if args.dry_run:
        summary = "dry-run: " + summary
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
