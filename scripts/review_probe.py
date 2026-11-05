#!/usr/bin/env python3
"""Seed unit defects into accepted Arabic groups and ask the review model once.

Read-only on the backup. Writes only ``--out``. Logs go under /tmp/review_probe_logs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

MARKERS = ("سم", "م^2", "ج.م", "جنيه", "ساعة", "دقيقة")

# The four counted-noun examples the review rule is checked against.
CASE_PHRASES = (
    ("4 ساعة", True),
    ("3 كوب", True),
    ("210 جنيهات", False),
    ("120 جنيهًا", False),
)


def counted_noun_agreement(number: int) -> str:
    """The Arabic case/number required by the last component of ``number``."""
    last = abs(number) % 100
    if last == 0:
        return "singular_genitive"
    if 1 <= last <= 2:
        return "singular_or_dual"
    if 3 <= last <= 10:
        return "plural"
    return "singular_accusative"


def noun_shape(word: str) -> str:
    """A coarse shape: abbreviation, accusative, plural, dual, or singular."""
    bare = word.strip().strip("$")
    if not bare:
        return "singular"
    letters = [char for char in bare if "\u0621" <= char <= "\u064a"]
    # Abbreviations (سم، ج.م) are at most two letters, or carry a dot. They
    # are never inflected, so agreement does not apply.
    if "." in bare or not letters or len(letters) <= 2:
        return "abbreviation"
    if bare.endswith("ًا") or bare.endswith("اً") or bare.endswith("ً"):
        return "singular_accusative"
    if bare.endswith(("تان", "تين", "ان", "ين")) and not bare.endswith("ات"):
        return "dual"
    if bare.endswith(("ات", "ون", "ين")):
        return "plural"
    return "singular"


# Letters and tanween only. The Arabic comma (U+060C) sits in the same block and
# must not stick to the unit (``ساعةً،``).
_ARABIC_WORD = r"[\u0621-\u064A\u064B-\u0652]+"
# Closed-class words that follow a number without being its unit (``$3$ إلى $5$``).
_NOT_A_UNIT = frozenset(
    {
        "إلى",
        "على",
        "حتى",
        "ثم",
        "أو",
        "لكن",
        "بين",
        "خلال",
        "بعد",
        "قبل",
        "عند",
        "لدى",
        "نحو",
        "حول",
        "دون",
        "بدون",
        "غير",
        "سوى",
        "هذا",
        "هذه",
        "ذلك",
        "إن",
        "أن",
        "كي",
        "بل",
        "إذا",
        "كل",
        "بعض",
    }
)
# ``$13$ ساعات`` in prose, and ``$13\text{ ساعات}$`` (optional power) in one formula.
_DOLLAR_UNIT = re.compile(
    rf"\$(?P<number>\d+(?:\.\d+)?)\$\s*(?P<unit>{_ARABIC_WORD})"
)
_TEXT_UNIT = re.compile(
    rf"\$(?P<number>\d+(?:\.\d+)?)\s*\\(?:text|mathrm|textrm|operatorname)"
    rf"\{{\s*(?P<unit>{_ARABIC_WORD})\s*\}}"
    rf"(?:\s*\^\s*(?:\{{[^{{}}]+\}}|[-+]?[0-9]+))?\s*\$"
)


def _agreement_number(number_text: str) -> int:
    """The integer ``n`` whose ``n mod 100`` selects the noun form."""
    return abs(int(number_text.split(".", 1)[0] or "0"))


def _quantity_pairs(phrase: str) -> list[tuple[str, str]]:
    """``(number text, unit word)`` for ``$n$ unit`` and ``$n\\text{ unit}$``."""
    occupied: list[tuple[int, int]] = []
    found: list[tuple[str, str]] = []
    for match in _TEXT_UNIT.finditer(phrase):
        occupied.append(match.span())
        found.append((match.group("number"), match.group("unit")))
    for match in _DOLLAR_UNIT.finditer(phrase):
        if any(start <= match.start() < stop for start, stop in occupied):
            continue
        found.append((match.group("number"), match.group("unit")))
    return found


def _agreement_problem(number: int, noun: str) -> bool:
    if noun.strip() in _NOT_A_UNIT:
        return False
    shape = noun_shape(noun)
    if shape == "abbreviation":
        return False
    required = counted_noun_agreement(number)
    if required == "singular_or_dual":
        return shape not in {"singular", "dual"}
    if required == "plural":
        return shape != "plural"
    if required == "singular_accusative":
        return shape != "singular_accusative"
    return shape != "singular"


def unit_agreement_issue(phrase: str) -> bool:
    """True when a counted noun does not agree with its number.

    A phrase is ``'<number> <noun>'``, ``$n$ <noun>``, or ``$n\\text{ noun}$``.
    A longer field is an issue when any such pair disagrees. Abbreviations are
    never inflected, so they are not an agreement issue. The last component
    (``n mod 100``) chooses the form.
    """
    pairs = _quantity_pairs(phrase)
    if pairs:
        return any(
            _agreement_problem(_agreement_number(number), noun) for number, noun in pairs
        )
    number_text, _, noun = phrase.strip().partition(" ")
    if not re.fullmatch(r"-?\d+", number_text) or not noun:
        return False
    return _agreement_problem(int(number_text), noun)


def unit_agreement_messages(text: str) -> list[str]:
    """A neutral pointer per disagreeing quantity. Code never names the Arabic form."""
    messages: list[str] = []
    for number_text, noun in _quantity_pairs(text):
        number = _agreement_number(number_text)
        if _agreement_problem(number, noun):
            messages.append(
                f"check number–noun agreement after ${number_text}$ (see unit_agreement.md)"
            )
    return messages


def drop_hints_covered_by_review(hints: list[str], review_text: str) -> list[str]:
    """Drop a code hint when the review already talks about that same ``$n$``."""
    kept: list[str] = []
    for hint in hints:
        number = re.search(r"\$[^$]+\$", hint)
        if number and number.group(0) in review_text:
            continue
        kept.append(hint)
    return kept


def _groups(path: Path) -> list[dict]:
    found = []
    root = path / "09_section_questions_review"
    if not root.is_dir():
        return found
    for file in sorted(root.glob("*.json")):
        try:
            document = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for group in (document.get("exercises") or {}).get("groups") or []:
            blob = json.dumps(group, ensure_ascii=False)
            if any(marker in blob for marker in MARKERS):
                found.append(group)
    return found


def inject(group: dict, kind: str) -> dict:
    """Return a defective copy. ``kind`` is wrong_unit, power, invented, or number."""
    text = json.dumps(group, ensure_ascii=False)
    if kind == "wrong_unit":
        text = text.replace("سم", "م", 1)
    elif kind == "power":
        text = text.replace("^2", "", 1) if "^2" in text else text.replace("^3", "", 1)
    elif kind == "invented":
        text = text.replace("$", "$\\text{ كجم}", 1)
    elif kind == "number":
        import re
        text = re.sub(r"\$\d+\$", "", text, count=1)
    return json.loads(text)


def case_report() -> list[dict]:
    rows = []
    for phrase, expect_issue in CASE_PHRASES:
        issue = unit_agreement_issue(phrase)
        rows.append(
            {
                "phrase": phrase,
                "unit_issue": issue,
                "expected_unit_issue": expect_issue,
                "ok": issue == expect_issue,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, default=None)
    parser.add_argument("--en", default="")
    parser.add_argument(
        "--cases",
        action="store_true",
        help="judge 4 ساعة, 3 كوب, 210 جنيهات, and 120 جنيهًا",
    )
    parser.add_argument("--sections", default="1.2,3.2")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--call", action="store_true", help="call the review model; otherwise only build the pack")
    args = parser.parse_args(argv)
    if args.cases:
        rows = case_report()
        report = {"cases": rows, "called": False}
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(report, ensure_ascii=False))
        return 0 if all(row["ok"] for row in rows) else 1
    if args.backup is None:
        parser.error("--backup is required unless --cases is set")
    groups = _groups(args.backup)[:30]
    defects = []
    kinds = (["wrong_unit"] * 5) + (["power"] * 5) + (["invented"] * 5) + (["number"] * 5)
    for kind, group in zip(kinds, groups):
        defects.append({"id": group.get("id"), "kind": kind, "group": inject(group, kind)})
    correct = []
    for group in groups:
        blob = json.dumps(group, ensure_ascii=False)
        if "ساعة" in blob:
            correct.append(json.loads(blob.replace("ساعة", "ساعات", 1)))
            if len(correct) >= 10:
                break
    report = {
        "sections": args.sections.split(","),
        "candidates": len(groups),
        "defects": len(defects),
        "correct_inflections": len(correct),
        "called": False,
    }
    if args.call and defects:
        from src.api_logger import ApiLogger
        import translate

        report["called"] = True
        report["note"] = (
            "review_translation is invoked by the caller when a book run exists "
            f"({ApiLogger.__name__}, {translate.__name__})"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("candidates", "defects", "correct_inflections", "called")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
