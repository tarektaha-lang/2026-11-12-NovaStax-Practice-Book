#!/usr/bin/env python3
"""Re-check logged new_questions replies against the current checkers.

Opens files read-only and writes only stdout. No model is called.

    python3 -B scripts/replay_new_questions_checks.py --all
    python3 -B scripts/replay_new_questions_checks.py pure_mathematics_en_eg12_t0 \\
        --objective 1.3.1-objective_3 --objective 2.2.3-objective_1 --accepted
    python3 -B scripts/replay_new_questions_checks.py --all --parabolas --regen-list
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from pydantic import ValidationError  # noqa: E402

from figures.function_graph import quadratic_from_label  # noqa: E402
from new_questions import (  # noqa: E402
    EXERCISES_STAGE,
    WRITE_STEP,
    ExerciseGroup,
    WriteReply,
    interval_problems,
    pydantic_problems,
    repaired,
    repair_groups_excessive_tashkeel,
    repair_groups_mark_placement,
    restated,
    write_problems,
)
from review import REVIEW_STAGE  # noqa: E402
from source_questions import QUESTIONS_STAGE  # noqa: E402
from sources import OUTPUT_DIR, book_root, pdf_page_count  # noqa: E402
from src.geometry_arrows import section_is_vector_lesson  # noqa: E402
from src.prompting import ALPHABETS, Profile  # noqa: E402
from src.twin import (  # noqa: E402
    ACCEPTED_STAGE,
    APPLIED_BOOK_FILE,
    EN_MARK,
    FIGURE_DATA_STAGE,
    FIGURES_STAGE,
    PACKAGE_STAGE,
    PAPERS_STAGE,
    SOURCES_STAGE,
    TAPS_STAGE,
    TRANSLATION_STATE_STAGE,
    sibling_id,
)

QUESTION_JSONS_STAGE = "13_question_jsons"

LOGS_STAGE = "00_api_call_logs"
QUESTIONS_STEP = "section_questions"

EXERCISES_LOG = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}T[0-9.\-]+Z)_"
    r"(?P<item>(?P<section>\d+(?:\.\d+)*)-objective_(?P<n>\d+))_"
    + re.escape(WRITE_STEP)
    + r"$"
)
RESTATED_LOC = re.compile(
    r"group (\d+) exercise (\d+) says only what its stem says"
)
ASSIGNMENT_HEAD = "Assignment JSON:"
DEFERRED_BOOK = re.compile(r"_eg(?:0[7-9]|10)_")
SPARSE_PER_PAGE = 2.0
# 1.1.1 at 2.6 is the PDF-check band; 1.2.2 at 3.2 was measured as normal.
CHECK_MAX_PER_PAGE = 3.0
HIGH_MIN_PER_PAGE = 3.3
NQ_RETRY_USD = 0.30
SQ_SPLIT_USD = 1.00

TYPE_RULES = (
    ("restated", "says only what its stem says"),
    ("parabola_vertex", "parabola needs a vertex"),
    ("parabola_vertex", "neither a vertex nor x_intercepts"),
    ("interval", "broken interval"),
    ("naked_math", "outside $...$"),
    ("odd_dollars", "odd number of $ signs"),
    ("junk_leakage", "placeholder"),
    ("junk_leakage", "bypass"),
    ("junk_leakage", "preference indicator"),
    ("junk_leakage", "prompt leakage"),
    ("stuck_token", "trails "),
    ("choice_count", "choices, not the"),
    ("short_count", "exercises written, not the"),
    ("letter_without_choices", "choosing by letter"),
    ("unclosed", "says its instruction in words"),
    ("unclosed", "ends with a colon"),
    ("set_size", "is a set of"),
    ("in_sets", "sit in sets"),
    ("shared_stem", "sets share the instruction"),
    ("no_text", "has no text"),
    ("no_answer", "has no answer"),
    ("runaway", "repeats "),
    ("eof", "invalid json: eof"),
    ("max_tokens", "max_tokens"),
)


def load_json(path: Path) -> dict | None:
    """Read a JSON object, or None when the file is missing or not an object."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def output_books() -> list[str]:
    """Book ids under data/output that look like real books, not backup folders."""
    if not OUTPUT_DIR.is_dir():
        return []
    return sorted(
        path.name
        for path in OUTPUT_DIR.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    )


def read_meta(book_id: str) -> dict[str, str]:
    """Book attributes without touching academic_year (that path can write)."""
    review = OUTPUT_DIR / book_id / REVIEW_STAGE / APPLIED_BOOK_FILE
    payload = load_json(review)
    if payload:
        return {
            key: str(payload[key])
            for key in ("language_code", "numerals", "notation")
            if payload.get(key)
        }
    folder = OUTPUT_DIR / book_id / EXERCISES_STAGE
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            row = load_json(path)
            if row:
                return {
                    key: str(row[key])
                    for key in ("language_code", "numerals", "notation")
                    if row.get(key)
                }
    try:
        return dict(book_root(book_id).attrib)
    except (OSError, ValueError):
        return {}


def reviewed_sections(book_id: str) -> dict[str, dict]:
    """Section number -> reviewed section dict, or empty when the file is absent."""
    payload = load_json(OUTPUT_DIR / book_id / REVIEW_STAGE / APPLIED_BOOK_FILE)
    if not payload:
        return {}
    return {
        str(row.get("number") or ""): row
        for row in payload.get("sections") or []
        if isinstance(row, dict) and row.get("number")
    }


class BookCtx:
    """Alphabet, notation and section specs one book's logs share."""

    def __init__(self, book_id: str) -> None:
        self.book_id = book_id
        self.meta = read_meta(book_id)
        self.language_code = self.meta.get("language_code", "")
        self.notation = self.meta.get("notation") or "latin"
        self.sections = reviewed_sections(book_id)
        try:
            self.alphabet = Profile.of(self.meta).alphabet
        except (RuntimeError, KeyError):
            self.alphabet = ALPHABETS.get(self.language_code, frozenset())

    @property
    def english(self) -> bool:
        return EN_MARK in self.book_id or self.language_code == "en"

    def vector_lesson(self, number: str) -> bool:
        return section_is_vector_lesson(self.sections.get(number))


def classify(problem: str) -> str:
    """A short type name for one stored or live problem string."""
    low = problem.lower()
    for name, needle in TYPE_RULES:
        if needle in low:
            return name
    return "other"


def stored_problems(error_message: str | None) -> list[str]:
    """The '; '-joined list api_logger stored, without the exception class prefix."""
    text = error_message or ""
    text = re.sub(r"^(?:ValueError|ValidationError|ProblemsError):\s*", "", text)
    if not text.strip():
        return []
    return [part.strip() for part in text.split("; ") if part.strip()]


def type_counts(problems: list[str]) -> Counter[str]:
    return Counter(classify(item) for item in problems)


def letters_from(text: str) -> frozenset[str]:
    return frozenset(character for character in text if character.isalpha())


def request_letters(folder: Path) -> frozenset[str]:
    """Letters the prompt and parts used; feedback is not part of known."""
    known: set[str] = set()
    prompt = folder / "prompt.txt"
    if prompt.is_file():
        try:
            known |= letters_from(prompt.read_text(encoding="utf-8"))
        except OSError:
            pass
    for path in sorted(folder.glob("part_*.txt")):
        try:
            known |= letters_from(path.read_text(encoding="utf-8"))
        except OSError:
            pass
    return frozenset(known)


def assignment_from_parts(folder: Path) -> tuple[str, list[str], int] | None:
    """objective id, allowed names, target count from the assignment part."""
    for path in sorted(folder.glob("part_*.txt")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.startswith(ASSIGNMENT_HEAD):
            continue
        body = text[len(ASSIGNMENT_HEAD) :].lstrip()
        try:
            data = json.loads(body)
        except ValueError:
            return None
        row = data.get("objective") or {}
        objective = row.get("id") if isinstance(row, dict) else row
        if not objective:
            return None
        earlier = [str(name) for name in data.get("may_also_name") or []]
        allowed = list(earlier)
        if objective not in allowed:
            allowed.append(str(objective))
        try:
            target = int(data.get("exercises") or 0)
        except (TypeError, ValueError):
            target = 0
        return str(objective), allowed, target
    return None


def groups_of(payload: dict) -> list[dict]:
    groups = payload.get("groups")
    if isinstance(groups, list):
        return [row for row in groups if isinstance(row, dict)]
    exercises = payload.get("exercises")
    if isinstance(exercises, dict):
        rows = exercises.get("groups") or []
        return [row for row in rows if isinstance(row, dict)]
    return []


def restated_pairs_from_payload(payload: dict) -> list[tuple[str, str]]:
    """(stem, text) pairs the new restated() rule flags in a reply dict."""
    flagged: list[tuple[str, str]] = []
    for group in groups_of(payload):
        exercises = [row for row in group.get("exercises") or [] if isinstance(row, dict)]
        if len(exercises) <= 1:
            continue
        stem = str(group.get("stem") or "")
        siblings = [str(row.get("text") or "") for row in exercises]
        for row in exercises:
            text = str(row.get("text") or "")
            if restated(stem, text, siblings):
                flagged.append((stem, text))
    return flagged


def restated_pairs_from_error(payload: dict, error_message: str) -> list[tuple[str, str]]:
    """(stem, text) pairs named by a stored restatement error, looked up in the reply."""
    groups = groups_of(payload)
    pairs: list[tuple[str, str]] = []
    for found in RESTATED_LOC.finditer(error_message or ""):
        gi, ei = int(found.group(1)), int(found.group(2))
        if not (1 <= gi <= len(groups)):
            continue
        exercises = [
            row for row in groups[gi - 1].get("exercises") or [] if isinstance(row, dict)
        ]
        if not (1 <= ei <= len(exercises)):
            continue
        stem = str(groups[gi - 1].get("stem") or "")
        text = str(exercises[ei - 1].get("text") or "")
        pairs.append((stem, text))
    return pairs


def dump_choices(groups: list[ExerciseGroup]) -> None:
    """Print every choice and answer of a reply that would now pass."""
    for index, group in enumerate(groups, start=1):
        for number, exercise in enumerate(group.exercises, start=1):
            where = f"    group {index} exercise {number}"
            print(f"{where} text: {exercise.text}")
            print(f"{where} answer: {exercise.answer}")
            for letter, option in zip("ABCD", exercise.options):
                print(f"{where} choice ({letter}): {option}")


def recheck_folder(folder: Path, ctx: BookCtx, call: dict) -> dict:
    """Re-run validate -> repaired -> write_problems on one error log."""
    before_text = str(call.get("error_message") or "")
    before = stored_problems(before_text)
    reply_path = folder / "response_text.txt"
    result = {
        "folder": folder.name,
        "item": str(call.get("page_number") or folder.name),
        "section": str(call.get("section") or ""),
        "objective": str(call.get("objective") or ""),
        "before": before,
        "before_text": before_text,
        "after": [],
        "skip": "",
        "groups": None,
        "payload": None,
        "pass": False,
    }
    if not reply_path.is_file():
        result["skip"] = "no response_text.txt"
        return result
    try:
        raw = reply_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, ValueError) as error:
        result["skip"] = f"unparsable JSON: {error}"
        return result
    if not isinstance(payload, dict):
        result["skip"] = "response is not a JSON object"
        return result
    result["payload"] = payload

    assignment = assignment_from_parts(folder)
    objective = result["objective"]
    allowed = [objective] if objective else []
    target = int(call.get("target_count") or 0)
    if assignment:
        objective, allowed, assigned = assignment
        result["objective"] = objective
        if assigned:
            target = assigned
    if not target:
        target = int(call.get("target_count") or 0) or 1
    known = ctx.alphabet | request_letters(folder)
    vector = ctx.vector_lesson(result["section"])

    try:
        reply = WriteReply.model_validate(payload)
    except ValidationError as error:
        result["after"] = pydantic_problems(error)
        return result
    if reply.status != "ok":
        result["skip"] = f"abort: {reply.abort_reason or 'no reason'}"
        return result
    try:
        groups, _repairs = repaired(
            reply.groups, target, vector_lesson=vector, notation=ctx.notation
        )
        groups = repair_groups_excessive_tashkeel(groups)
        groups = repair_groups_mark_placement(groups)
        after = write_problems(groups, target, objective, allowed, known)
    except Exception as error:  # noqa: BLE001 - replay must not die on one log
        result["skip"] = f"{type(error).__name__}: {error}"
        return result
    result["after"] = after
    result["groups"] = groups
    result["pass"] = not after
    return result


def print_attempt(row: dict) -> None:
    """One attempt: removed types, remaining problems, would-pass."""
    item = row["item"]
    stamp = row["folder"][:24]
    if row["skip"]:
        print(f"skip         {item} {stamp}: {row['skip']}")
        if "unparsable" in row["skip"] or "Invalid JSON: EOF" in row["before_text"]:
            print("             (EOF / unparsable JSON is left as a note)")
        return
    before_c = type_counts(row["before"])
    after_c = type_counts(row["after"])
    removed = {
        name: before_c[name] - after_c.get(name, 0)
        for name in before_c
        if before_c[name] > after_c.get(name, 0)
    }
    status = "would pass" if row["pass"] else "still fails"
    removed_s = (
        ", ".join(f"{name}×{count}" for name, count in sorted(removed.items()))
        or "none"
    )
    remain_s = (
        ", ".join(f"{name}×{count}" for name, count in after_c.most_common())
        or "none"
    )
    print(f"{status:<12} {item} {stamp}")
    print(f"             removed: {removed_s}")
    print(f"             remaining: {remain_s}")
    if row["after"]:
        preview = row["after"][0]
        extra = f" (+{len(row['after']) - 1} more)" if len(row["after"]) > 1 else ""
        print(f"             e.g. {preview[:240]}{extra}")
    if row["pass"] and row["groups"] is not None:
        print("             full choice/answer text:")
        dump_choices(row["groups"])


def replay_failures(
    books: list[str],
    objectives: set[str],
    ctxs: dict[str, BookCtx],
) -> dict:
    """Re-check every error log. Returns the summary counters."""
    summary = {
        "attempts": 0,
        "skipped": 0,
        "unparsable": 0,
        "would_pass": 0,
        "still_fail": 0,
        "restated_before": 0,
        "restated_after": 0,
        "pure_32_pass": 0,
        "pure_32_fail": 0,
        "pure_32_skip": 0,
        "remaining": Counter(),
        "removed": Counter(),
        "all_math_before": 0,
        "all_math_after": 0,
    }
    seen_before: set[tuple[str, str]] = set()
    seen_after: set[tuple[str, str]] = set()

    for book_id in books:
        ctx = ctxs[book_id]
        logs = OUTPUT_DIR / book_id / LOGS_STAGE
        if not logs.is_dir():
            continue
        for folder in sorted(logs.glob(f"*_{WRITE_STEP}")):
            if not folder.is_dir():
                continue
            found = EXERCISES_LOG.match(folder.name)
            if found is None:
                continue
            item = found.group("item")
            if objectives and item not in objectives:
                continue
            call = load_json(folder / "call.json")
            if not call or call.get("status") != "error":
                continue
            summary["attempts"] += 1
            row = recheck_folder(folder, ctx, call)
            print_attempt(row)
            if row["pass"]:
                summary["would_pass"] += 1
            elif row["skip"]:
                summary["skipped"] += 1
                if "unparsable" in row["skip"]:
                    summary["unparsable"] += 1
            else:
                summary["still_fail"] += 1
                summary["remaining"].update(type_counts(row["after"]))
            if not row["skip"]:
                before_c = type_counts(row["before"])
                after_c = type_counts(row["after"])
                for name, count in before_c.items():
                    gone = count - after_c.get(name, 0)
                    if gone > 0:
                        summary["removed"][name] += gone
                summary["all_math_before"] += before_c.get("naked_math", 0)
                summary["all_math_after"] += after_c.get("naked_math", 0)
            payload = row.get("payload")
            if isinstance(payload, dict):
                for pair in restated_pairs_from_error(payload, row["before_text"]):
                    seen_before.add(pair)
                for pair in restated_pairs_from_payload(payload):
                    seen_after.add(pair)
            if book_id == "pure_mathematics_en_eg12_t0" and item in {
                "1.3.1-objective_3",
                "2.2.3-objective_1",
            }:
                if row["pass"]:
                    summary["pure_32_pass"] += 1
                elif row["skip"]:
                    summary["pure_32_skip"] += 1
                else:
                    summary["pure_32_fail"] += 1

    summary["restated_before"] = len(seen_before)
    summary["restated_after"] = len(seen_after)
    return summary


def iter_08(book_id: str):
    """Each 08 JSON as (path, document)."""
    folder = OUTPUT_DIR / book_id / EXERCISES_STAGE
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.json")):
        document = load_json(path)
        if document:
            yield path, document


def scan_accepted_restated(books: list[str], ctxs: dict[str, BookCtx]) -> int:
    """New restated() over every accepted English 08 multi-exercise group."""
    hits = 0
    groups_n = 0
    for book_id in books:
        if not ctxs[book_id].english:
            continue
        for path, document in iter_08(book_id):
            for index, group in enumerate(groups_of(document), start=1):
                exercises = [
                    row for row in group.get("exercises") or [] if isinstance(row, dict)
                ]
                if len(exercises) <= 1:
                    continue
                groups_n += 1
                stem = str(group.get("stem") or "")
                siblings = [str(row.get("text") or "") for row in exercises]
                for number, row in enumerate(exercises, start=1):
                    text = str(row.get("text") or "")
                    if restated(stem, text, siblings):
                        hits += 1
                        print(
                            f"restated hit {book_id} {path.name} "
                            f"group {index} exercise {number}: {text[:160]}"
                        )
    print(f"accepted en restated: {hits} hit(s) in {groups_n} multi-exercise group(s)")
    return hits


def interval_hits_in_document(document: dict, book_id: str, stem: str) -> list[dict]:
    """Broken intervals already stored in one 08 file."""
    hits: list[dict] = []
    for index, group in enumerate(groups_of(document), start=1):
        objective = str(group.get("objective") or "")
        gid = str(group.get("id") or f"group_{index}")
        for number, row in enumerate(group.get("exercises") or [], start=1):
            if not isinstance(row, dict):
                continue
            eid = str(row.get("id") or f"exercise_{number}")
            where = f"group {index} ({gid}) exercise {number} ({eid})"
            for letter, option in zip("ABCD", row.get("options") or []):
                for problem in interval_problems(str(option), f"{where} choice ({letter})"):
                    hits.append(
                        {
                            "book_id": book_id,
                            "stem": stem,
                            "objective": objective,
                            "where": problem,
                            "text": str(option),
                        }
                    )
            answer = str(row.get("answer") or "")
            for problem in interval_problems(answer, f"{where} answer"):
                hits.append(
                    {
                        "book_id": book_id,
                        "stem": stem,
                        "objective": objective,
                        "where": problem,
                        "text": answer,
                    }
                )
    return hits


def scan_accepted_intervals(books: list[str]) -> list[dict]:
    """Read-only list of broken intervals in accepted 08 files."""
    hits: list[dict] = []
    for book_id in books:
        for path, document in iter_08(book_id):
            hits.extend(interval_hits_in_document(document, book_id, path.stem))
    by_file: dict[tuple[str, str], int] = Counter()
    for hit in hits:
        by_file[(hit["book_id"], hit["stem"])] += 1
        print(
            f"interval {hit['book_id']} {hit['stem']}.json "
            f"{hit['objective']} {hit['where']}: {hit['text'][:160]}"
        )
    print(f"accepted intervals: {len(hits)} hit(s) in {len(by_file)} file(s)")
    for (book_id, stem), count in sorted(by_file.items()):
        print(f"  {count:>3}  {book_id} {stem}")
    return hits


# The parabola branch enrich_from_label used before quadratic_from_label. Copied
# so --parabolas can print old vs new a/opens/vertex without importing the backup.
_LEGACY_VERTEX = re.compile(
    r"=\s*(?P<sign>-)?\s*(?P<a>\d+(?:\.\d+)?)?\s*"
    r"(?:\\left\s*)?\(\s*x\s*(?P<hsign>[+-])\s*(?P<h>\d+(?:\.\d+)?)\s*"
    r"(?:\\right\s*)?\)\s*\^?\s*(?:\{?2\}?)"
    r"(?:\s*(?P<ksign>[+-])\s*(?P<k>\d+(?:\.\d+)?))?",
)
_LEGACY_SQUARED = re.compile(
    r"(?P<sign>-)?\s*(?P<a>\d+(?:\.\d+)?)?\s*"
    r"(?:[xt]|\\[xt])\s*(?:\^2|\^\{2\})",
    re.I,
)


def legacy_parabola_from_label(label: str) -> dict:
    """Old enrich_from_label parabola parse: a, opens, vertex."""
    text = label.strip()
    out = {"a": None, "opens": None, "vertex": None}
    found = _LEGACY_VERTEX.search(text)
    if found:
        a = float(found.group("a") or "1")
        if found.group("sign"):
            a = -a
        out["a"] = a
        out["opens"] = "down" if a < 0 else "up"
        h = float(found.group("h"))
        if found.group("hsign") == "+":
            h = -h
        k = 0.0
        if found.group("k") is not None:
            k = float(found.group("k"))
            if found.group("ksign") == "-":
                k = -k
        out["vertex"] = [h, k]
        return out
    squared = _LEGACY_SQUARED.search(text)
    if squared:
        a = float(squared.group("a") or "1")
        if squared.group("sign"):
            a = -a
        out["a"] = a
        out["opens"] = "down" if a < 0 else "up"
        return out
    if re.search(r"-\s*(?:\d+(?:\.\d+)?)?\s*(?:\\left\s*)?\(", text) or re.search(
        r"=\s*-\s*(?:x\^2|\\?x\^\{?2\}?)", text
    ):
        out["opens"] = "down"
    elif re.search(r"=\s*\+?\s*(?:x\^2|\\?x\^\{?2\}?)", text):
        out["opens"] = "up"
    return out


def new_parabola_from_label(label: str) -> dict:
    """Current parser: a, opens, derived vertex."""
    out = {"a": None, "opens": None, "vertex": None}
    coeffs = quadratic_from_label(label)
    if coeffs is None:
        return out
    a, b, c = coeffs
    out["a"] = a
    out["opens"] = "down" if a < 0 else "up"
    h = round(-b / (2 * a), 6)
    k = round(c - (b * b) / (4 * a), 6)
    out["vertex"] = [
        0.0 if abs(h) < 1e-12 else h,
        0.0 if abs(k) < 1e-12 else k,
    ]
    return out


def _fmt_num(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list):
        return "(" + ", ".join(_fmt_num(part) for part in value) + ")"
    return str(value)


def walk_parabola_curves(document: dict):
    """Every parabola curve dict in an 08 document."""

    def walk(node):
        if isinstance(node, dict):
            if node.get("kind") == "parabola" and "label" in node:
                yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    yield from walk(document)


def scan_parabolas(books: list[str]) -> None:
    """Old vs new a/opens/vertex for every accepted parabola label."""
    n = 0
    differ = 0
    for book_id in books:
        for path, document in iter_08(book_id):
            for curve in walk_parabola_curves(document):
                n += 1
                label = str(curve.get("label") or "")
                old = legacy_parabola_from_label(label)
                new = new_parabola_from_label(label)
                stored_v = curve.get("vertex")
                stored_o = curve.get("opens")
                changed = (
                    old["a"] != new["a"]
                    or old["opens"] != new["opens"]
                    or old["vertex"] != new["vertex"]
                )
                if changed:
                    differ += 1
                mark = "DIFF" if changed else "same"
                print(
                    f"{mark} {book_id} {path.name} {label!r} "
                    f"old a={_fmt_num(old['a'])} opens={old['opens']} "
                    f"vertex={_fmt_num(old['vertex'])} | "
                    f"new a={_fmt_num(new['a'])} opens={new['opens']} "
                    f"vertex={_fmt_num(new['vertex'])} | "
                    f"stored opens={stored_o} vertex={_fmt_num(stored_v)}"
                )
    print(f"parabolas: {n} accepted curve(s), {differ} old/new difference(s)")
    print(
        "accepted pictures change only if you later run draw.py --again; "
        "this Build rewrites no 08 JSON and no filed TikZ"
    )


def deferred_book(book_id: str) -> bool:
    """Grades 7–10 are out of regen scope."""
    return bool(DEFERRED_BOOK.search(book_id))


def scoped_sparse(book_id: str, stem: str) -> bool:
    """User scope: only applied 2.1.1 is regenerated for a sparse extract."""
    return book_id.startswith("applied_mathematics") and stem == "02_01_01"


def scoped_check(book_id: str, stem: str) -> bool:
    """User scope: applied 1.1.1 is a PDF check, not an automatic regen."""
    return book_id.startswith("applied_mathematics") and stem == "01_01_01"


def scoped_interval(book_id: str, stem: str) -> bool:
    """User scope: only pure 02_02_01 objective_4 is regenerated for intervals."""
    return book_id.startswith("pure_mathematics") and stem == "02_02_01"


def source_density(book_id: str, stem: str) -> tuple[int, int, float] | None:
    """(questions, pages, q/page) from the 07 file and cropped PDFs."""
    path = OUTPUT_DIR / book_id / QUESTIONS_STAGE / f"{stem}.json"
    document = load_json(path)
    if not document:
        return None
    questions = document.get("questions") or {}
    count = questions.get("count")
    if not isinstance(count, int):
        count = sum(
            len(group.get("questions") or [])
            for group in questions.get("groups") or []
            if isinstance(group, dict)
        )
    sources = OUTPUT_DIR / book_id / SOURCES_STAGE
    pdfs = sorted(sources.glob(f"{stem}_*.pdf")) if sources.is_dir() else []
    pages = sum(pdf_page_count(pdf) or 0 for pdf in pdfs)
    if not pages:
        return count, 0, 0.0
    return count, pages, count / pages


def latest_questions_thinking(book_id: str, number: str) -> str | None:
    logs = OUTPUT_DIR / book_id / LOGS_STAGE
    if not logs.is_dir():
        return None
    folders = sorted(logs.glob(f"*_{number}_{QUESTIONS_STEP}"), reverse=True)
    for folder in folders:
        call = load_json(folder / "call.json")
        if call and call.get("status") == "success":
            return str(call.get("thinking_level") or "") or None
    return None


def nonempty_dir(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def existing_downstream_notes(book_id: str, stem: str) -> list[str]:
    root = OUTPUT_DIR / book_id
    notes: list[str] = []
    section_files = [
        (f"{ACCEPTED_STAGE}/{stem}.json", root / ACCEPTED_STAGE / f"{stem}.json"),
        (f"{PAPERS_STAGE}/{stem}.tex", root / PAPERS_STAGE / f"{stem}.tex"),
        (f"{PAPERS_STAGE}/{stem}.pdf", root / PAPERS_STAGE / f"{stem}.pdf"),
        (
            f"{PAPERS_STAGE}/{stem}.selection.json",
            root / PAPERS_STAGE / f"{stem}.selection.json",
        ),
    ]
    for relative, path in section_files:
        if path.is_file():
            notes.append(relative)
    for stage in (
        FIGURE_DATA_STAGE,
        FIGURES_STAGE,
        QUESTION_JSONS_STAGE,
        TAPS_STAGE,
        PACKAGE_STAGE,
        TRANSLATION_STATE_STAGE,
    ):
        folder = root / stage
        if nonempty_dir(folder):
            notes.append(f"{stage}/")
    try:
        other = sibling_id(book_id)
    except ValueError:
        return notes
    twin = OUTPUT_DIR / other
    if not twin.is_dir():
        return notes
    if (twin / EXERCISES_STAGE / f"{stem}.json").is_file():
        notes.append(f"{other}/{EXERCISES_STAGE}/{stem}.json")
    if (twin / ACCEPTED_STAGE / f"{stem}.json").is_file():
        notes.append(f"{other}/{ACCEPTED_STAGE}/{stem}.json")
    if nonempty_dir(twin / TRANSLATION_STATE_STAGE):
        notes.append(f"{other}/{TRANSLATION_STATE_STAGE}/")
    if nonempty_dir(twin / PAPERS_STAGE):
        notes.append(f"{other}/{PAPERS_STAGE}/")
    return notes


def document_objectives(document: dict) -> list[str]:
    rows = (document.get("exercises") or {}).get("objectives") or []
    return [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")]


def section_number_of(document: dict, stem: str) -> str:
    return str(document.get("number") or stem.replace("_", "."))


def print_regen_unit(
    tag: str,
    book_id: str,
    stem: str,
    units: str,
    reason: str,
    commands: list[str],
    cost: str,
    deferred: bool,
) -> None:
    downstream = existing_downstream_notes(book_id, stem)
    print(f"[{tag}] {book_id} {stem}")
    print(f"  units: {units}")
    print(f"  reason: {reason}")
    if deferred_book(book_id):
        scope = "deferred (grades 7–10)"
    elif deferred:
        scope = "out of current regen scope"
    else:
        scope = "in scope"
    print(f"  scope: {scope}")
    print(
        "  downstream: "
        + (", ".join(downstream) if downstream else "none (no 09–14, package, or Arabic sibling)")
    )
    if downstream:
        print(
            "  note: TAP/package follow-up is user-run; translate --update "
            "re-translates every group of that section (plus a possible glossary "
            "remake); 12_section_papers/<stem>.{tex,pdf,selection.json} are only "
            "built when missing, so they must be moved aside or rebuilt with "
            f"-s {stem.replace('_', '.')} --update"
        )
    print("  commands:")
    for line in commands:
        print(f"    {line}")
    print(f"  cost: {cost}")
    print()


def regen_list(books: list[str]) -> None:
    """Recompute the regeneration table from files on disk."""
    interval_rows: dict[tuple[str, str], dict] = {}
    for book_id in books:
        for path, document in iter_08(book_id):
            hits = interval_hits_in_document(document, book_id, path.stem)
            if not hits:
                continue
            objectives = sorted({hit["objective"] for hit in hits if hit["objective"]})
            interval_rows[(book_id, path.stem)] = {
                "document": document,
                "hits": hits,
                "objectives": objectives,
                "number": section_number_of(document, path.stem),
            }

    densities: list[tuple[str, str, str, int, int, float, str | None]] = []
    for book_id in books:
        folder = OUTPUT_DIR / book_id / QUESTIONS_STAGE
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            document = load_json(path)
            if not document:
                continue
            number = section_number_of(document, path.stem)
            density = source_density(book_id, path.stem)
            if density is None:
                continue
            count, pages, rate = density
            thinking = latest_questions_thinking(book_id, number)
            densities.append(
                (book_id, path.stem, number, count, pages, rate, thinking)
            )

    print("=== regeneration list (read-only; the USER runs these) ===")
    print(
        "Scope: applied 2.1.1, pure 02_02_01 objective_4, applied 1.1.1 after a "
        "PDF check; no grade 7–10 book now."
    )
    print()

    in_cost = 0.0
    maybe_cost = 0.0

    # A: sparse source_questions (< 2.0 q/page), plus its 08 redo.
    sparse = [
        row
        for row in densities
        if row[5] < SPARSE_PER_PAGE and row[4] > 0 and not deferred_book(row[0])
    ]
    if not sparse:
        # Still name the measured 2.1.1 row if the 07 file is there, even if
        # density could not be computed this run.
        for book_id in books:
            if book_id != "applied_mathematics_en_eg12_t0":
                continue
            density = source_density(book_id, "02_01_01")
            if density and density[2] < SPARSE_PER_PAGE:
                count, pages, rate = density
                number = "2.1.1"
                thinking = latest_questions_thinking(book_id, number)
                sparse.append((book_id, "02_01_01", number, count, pages, rate, thinking))

    for book_id, stem, number, count, pages, rate, thinking in sparse:
        nq = OUTPUT_DIR / book_id / EXERCISES_STAGE / f"{stem}.json"
        document = load_json(nq)
        n_obj = len(document_objectives(document)) if document else 0
        commands = [
            f"python3 scripts/regen_prepare.py {book_id} {stem} "
            "--stage source_questions --apply",
            f"python3 scripts/regen_prepare.py {book_id} {stem} "
            "--stage new_questions --apply   # moves 08 aside; no --objective = "
            "whole file, no .partial seed",
            f"python3 scripts/pipeline.py {book_id} -s {number} "
            "--only source_questions new_questions",
        ]
        sq = SQ_SPLIT_USD
        nq_cost = n_obj * NQ_RETRY_USD if n_obj else 8 * NQ_RETRY_USD
        total = sq + nq_cost
        in_scope = scoped_sparse(book_id, stem)
        if in_scope:
            in_cost += total
        print_regen_unit(
            "A" if in_scope else "A (out of current scope)",
            book_id,
            stem,
            "whole section (source_questions"
            + (f" + {n_obj} new_questions objectives" if n_obj else "")
            + ")",
            f"sparse extraction {count} Qs / {pages} pp = {rate:.1f} per page "
            f"(thinking={thinking or '?'}; under {SPARSE_PER_PAGE})",
            commands,
            f"~${total:.1f} (source split ~${sq:.1f} + {n_obj or 8} objectives "
            f"~${nq_cost:.1f})"
            + ("" if in_scope else " (not run now)"),
            deferred=not in_scope,
        )

    # A?: medium density in the 1.1.1 band (2.0–3.0), not yet sparse.
    maybe = [
        row
        for row in densities
        if SPARSE_PER_PAGE <= row[5] < CHECK_MAX_PER_PAGE
        and row[4] > 0
        and not deferred_book(row[0])
        and row[6] == "medium"
    ]
    for book_id, stem, number, count, pages, rate, thinking in maybe:
        nq = OUTPUT_DIR / book_id / EXERCISES_STAGE / f"{stem}.json"
        nq_note = (
            "the new_questions prepare is a no-op while 08 is missing"
            if not nq.is_file()
            else "08 already written"
        )
        commands = [
            "# only if the PDF check shows missing questions",
            f"python3 scripts/regen_prepare.py {book_id} {stem} "
            "--stage source_questions --apply",
            f"python3 scripts/regen_prepare.py {book_id} {stem} "
            "--stage new_questions --apply   # " + nq_note,
            f"python3 scripts/pipeline.py {book_id} -s {number} "
            "--only source_questions new_questions",
        ]
        total = 3.0
        in_scope = scoped_check(book_id, stem)
        if in_scope:
            maybe_cost += total
        print_regen_unit(
            "A?" if in_scope else "A? (out of current scope)",
            book_id,
            stem,
            "check only",
            f"medium extraction {count} Qs / {pages} pp = {rate:.1f} per page "
            f"(thinking={thinking or '?'}; below high-thinking minimum "
            f"{HIGH_MIN_PER_PAGE}; compare with its PDF before deciding)",
            commands,
            f"~${total:.1f} if regenerated"
            + ("" if in_scope else " (not run now)"),
            deferred=not in_scope,
        )

    normal = [
        row
        for row in densities
        if row[5] >= CHECK_MAX_PER_PAGE and not deferred_book(row[0])
    ]
    if normal:
        print("[—] source_questions density in normal range (no regen):")
        for book_id, stem, number, count, pages, rate, thinking in normal:
            print(
                f"  {book_id} {number} ({stem}): {count} Qs / {pages} pp = "
                f"{rate:.1f} (thinking={thinking or '?'})"
            )
        print()

    # B: broken intervals. In-scope units first so B1 is pure 02_02_01.
    b_index = 1
    interval_order = sorted(
        interval_rows.items(),
        key=lambda item: (
            0 if scoped_interval(item[0][0], item[0][1]) else 1,
            deferred_book(item[0][0]),
            item[0],
        ),
    )
    for (book_id, stem), row in interval_order:
        objectives = row["objectives"]
        n_hits = len(row["hits"])
        number = row["number"]
        in_scope = scoped_interval(book_id, stem)
        deferred = deferred_book(book_id) or not in_scope
        tag = f"B{b_index}" + ("" if in_scope else " (deferred)")
        obj_flags = " ".join(f"--objective {name}" for name in objectives)
        commands = [
            f"python3 scripts/regen_prepare.py {book_id} {stem} {obj_flags} --apply",
            f"python3 scripts/pipeline.py {book_id} -s {number} --only new_questions",
        ]
        cost = len(objectives) * NQ_RETRY_USD
        if in_scope:
            in_cost += cost
        print_regen_unit(
            tag,
            book_id,
            stem,
            ", ".join(objectives) or "unknown objective",
            f"{n_hits} broken interval(s)",
            commands,
            f"~${cost:.1f}" + (" (not run now)" if deferred else ""),
            deferred=deferred,
        )
        b_index += 1

    print("In-scope total ≈ ${:.1f}".format(in_cost) + (
        f" (≈ ${in_cost + maybe_cost:.1f} with A?)" if maybe_cost else ""
    ))
    print(
        "Applied and pure have no downstream or Arabic outputs in the current "
        "tree, so in-scope cost is new-question (and source_questions) calls only."
    )
    print("Nothing regenerated by this script.")
    print(
        "Deferred B notes (for when grades 7–10 are done): review, extract, draw, "
        "frontmatter and cost are whole-book; --updateboth passes --update to every "
        "stage and --again to draw. -s matches a leaf or a prefix. Extract/draw "
        "without --update add only missing items. translate -s re-translates only "
        "that section, but --updateboth has whole-book side effects. Per-section 12 "
        "papers must be moved aside or rebuilt with --update; 13/14 hash-named files "
        "of the bad items stay as orphans and must be named."
    )


def print_summary(summary: dict) -> None:
    print()
    print("=== failure replay summary ===")
    print(
        f"{summary['attempts']} error attempt(s): "
        f"{summary['would_pass']} would pass, "
        f"{summary['still_fail']} still fail, "
        f"{summary['skipped']} skipped "
        f"({summary['unparsable']} unparsable JSON)"
    )
    print(
        f"restated flags: {summary['restated_before']} unique stored "
        f"(stem, exercise) pair(s) in error replies; "
        f"{summary['restated_after']} still flagged by the new rule"
    )
    if summary["removed"]:
        print(
            "removed by type: "
            + ", ".join(
                f"{name}×{count}" for name, count in summary["removed"].most_common()
            )
        )
    if summary["remaining"]:
        print(
            "remaining by type: "
            + ", ".join(
                f"{name}×{count}" for name, count in summary["remaining"].most_common()
            )
        )
    print(
        f"naked-math choice errors: {summary['all_math_before']} stored, "
        f"{summary['all_math_after']} remaining "
        "(replay reports how many the all-math repair cleared)"
    )
    print(
        f"pure 1.3.1-objective_3 + 2.2.3-objective_1: "
        f"{summary['pure_32_pass']} would pass, "
        f"{summary['pure_32_fail']} still fail, "
        f"{summary['pure_32_skip']} skipped "
        "(of the 32 measured failures; remaining junk / 5-choice / short "
        "count / f''(x) / garbled intervals / letter-without-choices is success "
        "for items 1–3 even when few fully pass)"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "books",
        nargs="*",
        help="book id(s) under data/output; omit with --all",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="every book under data/output",
    )
    parser.add_argument(
        "--objective",
        action="append",
        default=[],
        metavar="ITEM",
        help="only this page_number, e.g. 1.3.1-objective_3 (repeatable)",
    )
    parser.add_argument(
        "--accepted",
        action="store_true",
        help="also scan accepted 08 files for restated (en) and broken intervals",
    )
    parser.add_argument(
        "--parabolas",
        action="store_true",
        help="old vs new a/opens/vertex for every accepted parabola label",
    )
    parser.add_argument(
        "--regen-list",
        action="store_true",
        dest="regen_list",
        help="print the regeneration table, commands and cost (read-only)",
    )
    args = parser.parse_args(argv)
    if not args.books and not args.all and not (
        args.accepted or args.parabolas or args.regen_list
    ):
        parser.error("name a book, or pass --all / --accepted / --parabolas / --regen-list")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    books = list(args.books) if args.books else output_books()
    if args.books and not args.all:
        missing = [name for name in books if not (OUTPUT_DIR / name).is_dir()]
        if missing:
            print(f"unknown book(s): {', '.join(missing)}", file=sys.stderr)
            return 1
    ctxs = {book_id: BookCtx(book_id) for book_id in books}
    do_failures = bool(args.books or args.all or args.objective)
    if do_failures:
        summary = replay_failures(books, set(args.objective), ctxs)
        print_summary(summary)
    if args.accepted:
        print()
        print("=== accepted 08 restated (English only) ===")
        scan_accepted_restated(books, ctxs)
        print()
        print("=== accepted 08 broken intervals ===")
        scan_accepted_intervals(books)
    if args.parabolas:
        print()
        print("=== accepted parabolas, old vs new parser ===")
        scan_parabolas(books)
    if args.regen_list:
        print()
        regen_list(books)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
