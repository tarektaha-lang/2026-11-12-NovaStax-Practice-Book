#!/usr/bin/env python3
"""Re-check logged exercise translations that failed validation, writing nothing.

Every ``*_exercise_translate`` call log that holds a ``validation_error.txt`` is
read back and put through the same check the run applies to a group reply, with
the checker as it stands now. A reply that now passes was a false rejection the
code has since stopped making; one that still fails is either a real model error
or a rejection still to look at. Nothing is written and no model is called.

With ``--kept`` it instead re-checks every group already on disk, so a checker
change that would send kept work back to the model shows up before a resume.

    python3 -B scripts/replay_translation_failures.py --book mathematics_ar_eg07_t1
    python3 -B scripts/replay_translation_failures.py --book mathematics_ar_eg10_t1 --since 2026-10-18
    python3 -B scripts/replay_translation_failures.py --book mathematics_ar_eg10_t1 --kept
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from translate import (  # noqa: E402
    ACCEPTED_STAGE,
    EXERCISE_STEP,
    OUTPUT_DIR,
    SPECS_STAGE,
    GroupTranslateReply,
    ObjectiveTranslateReply,
    SpecOnlyReply,
    _group_location_text,
    exercise_output_problems,
    _require_group,
    book_map_path,
    book_metadata,
    _drop_counts,
    group_row_complete,
    load_translated_groups,
    merge_group,
)
from src.protect import protect_group, restore_group, token_problems  # noqa: E402
from src.translation_quality import spec_problems  # noqa: E402
from src.twin import EXERCISES_STAGE, pair_books  # noqa: E402

LOG_NAME = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}T[0-9.\-]+Z)_(?P<section>\d+(?:\.\d+)*)_"
    r"(?P<group>group_\d+)_" + re.escape(EXERCISE_STEP) + r"$"
)
SPEC_LOG = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}T[0-9.\-]+Z)_(?P<section>\d+(?:\.\d+)*)_spec_translate$"
)


def recheck_spec_logs(
    logs: Path,
    *,
    meta: dict,
    since: str,
    section: str,
) -> int:
    """Re-check logged spec replies with the current spec checks. Nothing is written."""
    failing = passing = 0
    for folder in sorted(path for path in logs.iterdir() if path.is_dir() and SPEC_LOG.match(path.name)):
        found = SPEC_LOG.match(folder.name)
        assert found is not None
        if since and found.group("stamp")[:10] < since:
            continue
        if section and found.group("section") != section:
            continue
        english = folder / "english_spec.json"
        reply_path = folder / "response_text.txt"
        if not english.is_file() or not reply_path.is_file():
            print(f"skipped      {folder.name}")
            continue
        try:
            reply = SpecOnlyReply.model_validate_json(reply_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            failing += 1
            print(f"unparsable   {found.group('section')} {folder.name[:24]}: {error}")
            continue
        if reply.spec is None:
            print(f"aborted      {found.group('section')} {folder.name[:24]}: {reply.abort_reason}")
            continue
        source = json.loads(english.read_text(encoding="utf-8"))
        problems = spec_problems(source, reply.spec.model_dump(), meta)
        if problems:
            failing += 1
            print(f"still fails  {found.group('section')} {folder.name[:24]}: {'; '.join(problems)[:300]}")
        else:
            passing += 1
            print(f"now passes   {found.group('section')} {folder.name[:24]}")
    print(f"\n{passing} spec repl(ies) now pass, {failing} still fail")
    return 1 if failing else 0


OBJECTIVE_LOG = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}T[0-9.\-]+Z)_(?P<section>\d+(?:\.\d+)*)_"
    r"objective_(?P<objective>\d+|none)(?:_part\d+)?_" + re.escape(EXERCISE_STEP) + r"$"
)


def english_groups(en_id: str) -> tuple[dict[tuple[str, str], dict], dict[str, str]]:
    groups: dict[tuple[str, str], dict] = {}
    stems: dict[str, str] = {}
    for path in sorted((OUTPUT_DIR / en_id / ACCEPTED_STAGE).glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        number = str(document.get("number") or "")
        if not number:
            continue
        stems[number] = path.stem
        for group in (document.get("exercises") or {}).get("groups") or []:
            groups[(number, str(group.get("id") or ""))] = group
    return groups, stems


def section_spec(ar_id: str, stem: str | None) -> dict | None:
    if stem is None:
        return None
    path = OUTPUT_DIR / ar_id / SPECS_STAGE / f"{stem}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("spec")
    except (OSError, ValueError):
        return None


def signature(error: str) -> str:
    first = error.split(";")[0]
    first = re.sub(r"exercise_\d+", "exercise_N", first)
    return first[:160]


def kept_groups(
    ar_id: str,
    meta: dict,
    book_map: dict,
    groups: dict[tuple[str, str], dict],
    stems: dict[str, str],
    section: str,
) -> int:
    """Re-check every Arabic group already on disk; a rejection here is rework."""
    rejected = checked = 0
    for number, stem in sorted(stems.items(), key=lambda item: [int(p) for p in item[0].split(".")]):
        if section and number != section:
            continue
        spec = section_spec(ar_id, stem)
        for stage in (EXERCISES_STAGE, ACCEPTED_STAGE):
            for group_id, ar_group in load_translated_groups(
                OUTPUT_DIR / ar_id / stage / f"{stem}.json"
            ).items():
                en_group = groups.get((number, group_id))
                if en_group is None:
                    continue
                checked += 1
                if group_row_complete(en_group, ar_group, meta, book_map, spec=spec):
                    continue
                rejected += 1
                try:
                    merge_group(
                        en_group,
                        ar_group,
                        meta,
                        {},
                        spec=spec,
                    )
                    detail = "ids, counts or printable text"
                except (ValueError, TypeError, KeyError) as error:
                    detail = f"{type(error).__name__}: {error}"
                print(f"rejected     {stage[:2]} {number} {group_id}: {detail[:400]}")
    print(f"\n{checked} kept group(s) checked, {rejected} would be retranslated")
    return 1 if rejected else 0


def objective_group_error(
    en_group: dict,
    dumped: dict,
    *,
    notation: str,
    meta: dict,
    spec: dict | None,
) -> str:
    """The same placeholder and merge checks a live objective reply gets."""
    _protected, slots = protect_group(en_group, notation=notation)
    drops = dumped.get("dropped_numbers") or []
    by_field: dict[str, list[str]] = {}
    for drop in drops:
        by_field.setdefault(str(drop.get("field") or ""), []).append(str(drop.get("token") or ""))
    problems: list[str] = []
    for location, slot in slots.items():
        field_text = _group_location_text(dumped, location)
        problems.extend(
            token_problems(slot, field_text, location, dropped=by_field.get(location, []))
        )
    if problems:
        return "; ".join(problems[:3])
    restored = restore_group(dumped, slots)
    output_problems = exercise_output_problems(restored, notation)
    if output_problems:
        return "; ".join(output_problems[:6])
    try:
        merge_group(
            en_group,
            restored,
            meta,
            {},
            spec=spec,
            dropped=_drop_counts(drops),
        )
    except (ValueError, TypeError, KeyError) as error:
        return f"{type(error).__name__}: {error}"
    return ""


def recheck_objective_logs(
    logs: Path,
    groups: dict[tuple[str, str], dict],
    stems: dict[str, str],
    *,
    ar_id: str,
    meta: dict,
    since: str,
    section: str,
) -> int:
    """Re-check every objective exercise reply. Nothing is written."""
    notation = str(meta.get("notation") or "latin")
    by_group: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    folders = sorted(path for path in logs.iterdir() if path.is_dir() and OBJECTIVE_LOG.match(path.name))
    for folder in folders:
        found = OBJECTIVE_LOG.match(folder.name)
        assert found is not None
        if since and found.group("stamp")[:10] < since:
            continue
        if section and found.group("section") != section:
            continue
        number = found.group("section")
        try:
            reply = ObjectiveTranslateReply.model_validate_json(
                (folder / "response_text.txt").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            print(f"unparsable    {folder.name}: {error}")
            continue
        if reply.status == "abort":
            print(f"aborted       {folder.name}: {reply.abort_reason}")
            continue
        spec = section_spec(ar_id, stems.get(number))
        for row in reply.groups:
            key = (number, row.id)
            en_group = groups.get(key)
            if en_group is None:
                by_group[key].append((folder.name, "skipped", "English group not found"))
                continue
            detail = objective_group_error(
                en_group,
                row.model_dump(),
                notation=notation,
                meta=meta,
                spec=spec,
            )
            by_group[key].append((folder.name, "still fails" if detail else "now passes", detail))
    passing = failing = 0
    first_pass = first_fail = 0
    signatures: dict[str, list[str]] = defaultdict(list)
    for key in sorted(by_group, key=lambda k: ([int(p) for p in k[0].split(".")], int(k[1].split("_")[1]))):
        attempts = by_group[key]
        label = f"{key[0]} {key[1]}"
        first_state = attempts[0][1]
        if first_state == "now passes":
            first_pass += 1
        elif first_state == "still fails":
            first_fail += 1
        if any(state == "now passes" for _, state, _ in attempts):
            passing += 1
            if attempts[0][1] != "now passes":
                print(f"now passes   {label} (first attempt still failed)")
        else:
            failing += 1
            detail = attempts[-1][2]
            print(f"still fails  {label}: {detail[:400]}")
            signatures[signature(detail)].append(label)
    print(
        f"\n{passing} group(s) pass on some attempt, {failing} never pass; "
        f"first attempt {first_pass} pass / {first_fail} fail"
    )
    if signatures:
        print("\nnever passing, by error:")
        for sig, labels in sorted(signatures.items(), key=lambda item: -len(item[1])):
            print(f"  {len(labels):>3}  {sig}  [{', '.join(labels[:8])}{', ...' if len(labels) > 8 else ''}]")
    return 1 if failing else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--book", required=True, help="Arabic book id")
    parser.add_argument("--book-dir", type=Path, default=None, help="read this output directory instead of data/output/<book>")
    parser.add_argument("--v2", action="store_true", help="re-check objective replies with the placeholder checks")
    parser.add_argument("--specs", action="store_true", help="re-check spec replies with the current spec checks")
    parser.add_argument("--since", default="", help="only logs on/after this date (YYYY-MM-DD)")
    parser.add_argument("--section", default="", help="only this section number")
    parser.add_argument("--verbose", action="store_true", help="print every attempt")
    parser.add_argument(
        "--kept",
        action="store_true",
        help="instead re-check the groups already on disk (08_ and 09_)",
    )
    args = parser.parse_args()

    if "_en_" in args.book:
        en_id, ar_id = args.book, pair_books(args.book)
    else:
        ar_id, en_id = args.book, args.book.replace("_ar_", "_en_", 1)
        if pair_books(en_id) != ar_id:
            parser.error(f"{ar_id} is not the Arabic sibling of {en_id}")
    meta = book_metadata(ar_id)
    root = args.book_dir if args.book_dir is not None else OUTPUT_DIR / ar_id
    map_file = root / "00_translation_state" / "book_map.json"
    if map_file.is_file():
        book_map = json.loads(map_file.read_text(encoding="utf-8"))
    else:
        book_map = json.loads(book_map_path(ar_id).read_text(encoding="utf-8"))
    groups, stems = english_groups(en_id)
    if args.specs:
        print("specs: re-checking spec replies with the current checks")
        root_logs = (args.book_dir if args.book_dir is not None else OUTPUT_DIR / ar_id) / "00_api_call_logs"
        return recheck_spec_logs(root_logs, meta=meta, since=args.since, section=args.section)
    if args.v2:
        print("v2: re-checking objective replies with the current placeholder checks")
        return recheck_objective_logs(
            root / "00_api_call_logs",
            groups,
            stems,
            ar_id=ar_id,
            meta=meta,
            since=args.since,
            section=args.section,
        )
    if args.kept:
        return kept_groups(ar_id, meta, book_map, groups, stems, args.section)
    logs = root / "00_api_call_logs"

    by_group: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for folder in sorted(logs.iterdir()):
        found = LOG_NAME.match(folder.name)
        if not found or not folder.is_dir():
            continue
        if args.since and found.group("stamp")[:10] < args.since:
            continue
        if args.section and found.group("section") != args.section:
            continue
        error_file = folder / "validation_error.txt"
        if not error_file.is_file():
            continue
        logged = error_file.read_text(encoding="utf-8").strip()
        if not logged.startswith(("ValueError", "ValidationError")):
            continue
        key = (found.group("section"), found.group("group"))
        en_group = groups.get(key)
        if en_group is None:
            by_group[key].append((folder.name, "skipped", "English group not found"))
            continue
        try:
            reply = GroupTranslateReply.model_validate_json(
                (folder / "response_text.txt").read_text(encoding="utf-8")
            )
            _require_group(reply)
        except (OSError, ValueError) as error:
            by_group[key].append((folder.name, "still fails", f"unparsable reply: {error}"))
            continue
        if reply.status == "abort" or reply.group is None:
            by_group[key].append((folder.name, "aborted", reply.abort_reason or ""))
            continue
        try:
            merge_group(
                en_group,
                reply.group.model_dump(),
                meta,
                {},
                spec=section_spec(ar_id, stems.get(key[0])),
            )
        except (ValueError, TypeError, KeyError) as error:
            by_group[key].append((folder.name, "still fails", f"{type(error).__name__}: {error}"))
        else:
            by_group[key].append((folder.name, "now passes", logged))

    signatures: dict[str, list[str]] = defaultdict(list)
    passing = failing = 0
    for key in sorted(by_group, key=lambda k: ([int(p) for p in k[0].split(".")], int(k[1].split("_")[1]))):
        attempts = by_group[key]
        label = f"{key[0]} {key[1]}"
        if any(state == "now passes" for _, state, _ in attempts):
            passing += 1
            print(f"now passes   {label} ({len(attempts)} logged failure(s))")
        else:
            failing += 1
            state, detail = attempts[-1][1], attempts[-1][2]
            print(f"{state:<12} {label}: {detail[:400]}")
            signatures[signature(detail)].append(label)
        if args.verbose:
            for name, state, detail in attempts:
                print(f"    {name[:24]} {state}: {detail[:300]}")
    print(f"\n{passing} group(s) now pass, {failing} still fail or aborted")
    if signatures:
        print("\nstill failing, by error:")
        for sig, labels in sorted(signatures.items(), key=lambda item: -len(item[1])):
            print(f"  {len(labels):>3}  {sig}  [{', '.join(labels[:6])}{', ...' if len(labels) > 6 else ''}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
