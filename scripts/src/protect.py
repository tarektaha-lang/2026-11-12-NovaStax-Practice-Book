"""Placeholder protection for maths and prose numbers on the live translate path."""

from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from src.print_digits import GRADE_WORDING, MATH_SEGMENT, PRINT_NUMBER, _protected_spans


TOKEN = re.compile(r"⟦(/?)([MEN])(\d+)⟧")
WORDY_BODY = re.compile(r"\\(?:text|mathrm|textrm)\{([^{}]*)\}")
BARE_WORD = re.compile(r"(?<![\\A-Za-z])[A-Za-z]{2,}")
SINGLE_LATIN = re.compile(r"(?<![\\A-Za-z])[A-Za-z](?![A-Za-z])")
FUNCTION_COMMAND = re.compile(
    r"\\(?:sin|cos|tan|cot|sec|csc|log|ln|exp|lim|Re|Im|arg)(?![A-Za-z])"
    r"|\\(?:operatorname|mathrm)\s*\{\s*(?:Re|Im|Arg)\s*\}"
)
MATHBB_COMMAND = re.compile(r"\\(?:mathbb|mathcal)\{[^{}]*\}")
# A backslash in front of a Unicode Greek letter is the same command as \pi.
UNICODE_MATH = str.maketrans({"π": "pi", "Π": "Pi"})
ARABIC_LETTER = re.compile(r"[\u0621-\u064A\u066E-\u06D3]")
INNER_DIGIT = re.compile(r"\d+(?:\.\d+)?")
# A name and the digits of its label, on either side of the translation.
# ``L1`` / ``PT_1`` / ``polygon_2`` in prose, ``ل_1`` / ``ل_$1$`` / ``L_{1}`` in
# maths, and ``\text{مح}_2`` when the name sits in ``\text`` and the index is a
# subscript. The digit is a name, not a quantity. A coefficient (``3m_1``,
# ``4m_2``, ``3v_0``) still names the same index as ``m_1``. A letter before
# the name is left out so ``\log_2`` is not a label; log bases and nCr indices
# are kept as quantities in ``_quantity_index_label``.
LABEL_DIGITS = re.compile(
    r"(?<![A-Za-z\\])"
    r"(?:\\text\s*\{[^{}]*\}|[A-Za-z\u0621-\u064A]+)"
    r"(?:_\s*\$\s*\d+(?:\.\d+)?\s*\$|_\s*\{\d+(?:\.\d+)?\}|_\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?)"
)
# ``\text{لو}_2`` is a logarithm base, not ``مح_2``. ``C_5`` / ``ق_5`` after
# ``{}^{10}`` is the r in nCr, not a named force ``ق_1`` or term ``ح_6``.
_LOG_TEXT_LABEL = re.compile(r"^\\text\s*\{لو\}")
_COMBINATION_LETTER = re.compile(r"^(?:C|ق)_")
_COMBINATION_BEFORE = re.compile(r"\^\{?\d+(?:\.\d+)?\}?\s*$")
# A coefficient glued to one variable letter: ``4x``, ``2س``. Two letters
# (``2nd``, ``3cm``) are wording, not a quantity. An uppercase follower
# (``2D``) is a label, not a variable. ``PRINT_NUMBER`` skips a Latin-glued
# coefficient (``(?![A-Za-z0-9])``), so the prose branch must count it the
# same way ``INNER_DIGIT`` does once the term is in ``$...$``.
_VAR_LETTER = r"[a-z\u0621-\u064A\u066E-\u06D3]"
_ANY_LETTER = r"[A-Za-z\u0621-\u064A\u066E-\u06D3]"
PROSE_COEFFICIENT = re.compile(
    rf"(?<![A-Za-z0-9\\])(\d+(?:\.\d+)?)(?={_VAR_LETTER}(?!{_ANY_LETTER}))"
)
# Figure prose writes ``2pi``; Arabic puts the same quantity in maths as
# ``$2\pi$``. ``PRINT_NUMBER`` skips the glued Latin letters.
PI_COEFFICIENT = re.compile(
    r"(?<![A-Za-z0-9\\])(\d+(?:\.\d+)?)(?:pi|π|\\pi)\b"
)
# Glossary ``\ln`` is ``\text{لو}_هـ``. Stripping ``\text{لو}`` then ``هـ``
# would leave a stray ``_``. ``\text{نق}_1`` is one radius letter, like ``r``.
_AR_LN = re.compile(r"\\text\s*\{لو\}\s*_\{?هـ\}?")
_AR_RADIUS = re.compile(
    r"(?:\\text\s*\{نق\}|نق(?![\u0621-\u064A\u066E-\u06D3]))"
    r"(?:_\s*\$\s*\d+(?:\.\d+)?\s*\$|_\s*\{\d+(?:\.\d+)?\}|_\s*\d+(?:\.\d+)?)?"
)
MAX_DROPPED_NUMBERS_PER_FIELD = 2


@dataclass(frozen=True)
class Slots:
    frozen: dict[str, str]
    editable: dict[str, str]
    numbers: dict[str, str]


def math_is_editable(span: str, notation: str) -> bool:
    """arabic: always True. latin: True when a text-command body has a letter, or a bare Latin run of >=2 letters exists outside commands."""
    if notation == "arabic":
        return True
    if any(char.isalpha() for body in WORDY_BODY.findall(span) for char in body):
        return True
    return BARE_WORD.search(span) is not None


def _is_math_span(chunk: str) -> bool:
    return MATH_SEGMENT.fullmatch(chunk) is not None


def _protect_prose(text: str, numbers: dict[str, str], start: int) -> tuple[str, int]:
    pieces: list[str] = []
    cursor = 0
    index = start
    for match in PRINT_NUMBER.finditer(text):
        pieces.append(text[cursor : match.start()])
        index += 1
        key = f"N{index}"
        numbers[key] = match.group(0)
        pieces.append(f"⟦{key}⟧")
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces), index


def protect(text: str, *, notation: str) -> tuple[str, Slots]:
    frozen: dict[str, str] = {}
    editable: dict[str, str] = {}
    numbers: dict[str, str] = {}
    pieces: list[str] = []
    math_index = 0
    edit_index = 0
    number_index = 0
    cursor = 0
    for start, stop in _protected_spans(text):
        prose, number_index = _protect_prose(text[cursor:start], numbers, number_index)
        pieces.append(prose)
        chunk = text[start:stop]
        if _is_math_span(chunk):
            if math_is_editable(chunk, notation):
                edit_index += 1
                key = f"E{edit_index}"
                editable[key] = chunk
                pieces.append(f"⟦{key}⟧{chunk}⟦/{key}⟧")
            else:
                math_index += 1
                key = f"M{math_index}"
                frozen[key] = chunk
                pieces.append(f"⟦{key}⟧")
        else:
            pieces.append(chunk)
        cursor = stop
    prose, _number_index = _protect_prose(text[cursor:], numbers, number_index)
    pieces.append(prose)
    return "".join(pieces), Slots(frozen=frozen, editable=editable, numbers=numbers)


def _inside_math(text: str) -> bool:
    """True when an unescaped ``$`` / ``$$`` delimiter count so far is odd."""
    count = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            index += 2
            continue
        if char == "$":
            count += 1
            if index + 1 < len(text) and text[index + 1] == "$":
                index += 1
        index += 1
    return count % 2 == 1


def _emit_number(raw: str, *, inside: bool) -> str:
    number = raw
    percent = False
    if number.endswith("\\%"):
        percent = True
        number = number[:-2].rstrip()
    elif number.endswith("%"):
        percent = True
        number = number[:-1].rstrip()
    suffix = r"\%" if percent else ""
    body = f"{number}{suffix}"
    if inside:
        return body
    return f"${body}$"


def restore(text: str, slots: Slots) -> str:
    pieces: list[str] = []
    cursor = 0
    open_editable: dict[str, int] = {}
    for match in TOKEN.finditer(text):
        pieces.append(text[cursor : match.start()])
        closing, kind, digits = match.group(1), match.group(2), match.group(3)
        key = f"{kind}{digits}"
        if kind == "M" and not closing:
            pieces.append(slots.frozen.get(key, match.group(0)))
        elif kind == "E" and not closing:
            open_editable[key] = len("".join(pieces))
        elif kind == "E" and closing:
            open_editable.pop(key, None)
        elif kind == "N" and not closing:
            raw = slots.numbers.get(key, "")
            if raw:
                pieces.append(_emit_number(raw, inside=_inside_math("".join(pieces))))
            else:
                pieces.append(match.group(0))
        else:
            pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces)


# Two slashes as a parallel relation, not a URL and not a tally of three or more.
PARALLEL_SLASHES = re.compile(r"(?<![:/])//(?![/])")


# ``\hat`` (not ``\widehat``) and ``\overrightarrow`` are the same mark as ``\vec``.
_VECTOR_ACCENT = re.compile(r"\\(?:overrightarrow|hat)(?![A-Za-z])")


def _fold_vector_marks(text: str) -> str:
    """``\\overrightarrow`` and ``\\hat`` (not ``\\widehat``) as ``\\vec``."""
    return _VECTOR_ACCENT.sub(r"\\vec", text)


_EMPTY_SCRIPT = re.compile(r"([\^_])\{\}")
_SINGLE_SCRIPT = re.compile(r"([\^_])\{(\\[A-Za-z]+|[^{}\\])\}")


def _fold_single_scripts(text: str) -> str:
    """``^{X}`` / ``_{X}`` (X one token) and empty ``^{}`` match the bare form."""
    return _SINGLE_SCRIPT.sub(r"\1\2", _EMPTY_SCRIPT.sub(r"\1", text))


def _quantity_index_label(found: re.Match[str], text: str) -> bool:
    """True when the LABEL_DIGITS hit is a log base or an nCr index."""
    if _LOG_TEXT_LABEL.match(found.group(0)):
        return True
    if _COMBINATION_LETTER.match(found.group(0)) is None:
        return False
    return _COMBINATION_BEFORE.search(text[: found.start()]) is not None


def _blank_label_digits(text: str) -> str:
    """Wipe label digits, keeping log bases and nCr indices."""

    def repl(found: re.Match[str]) -> str:
        if _quantity_index_label(found, text):
            return found.group(0)
        return " " * len(found.group(0))

    return LABEL_DIGITS.sub(repl, text)


def skeleton(span: str, *, notation: str) -> str:
    text = span.strip()
    if text.startswith("$$") and text.endswith("$$") and len(text) >= 4:
        text = text[2:-2]
    elif text.startswith("$") and text.endswith("$") and len(text) >= 2:
        text = text[1:-1]
    text = text.translate(UNICODE_MATH)
    text = _fold_vector_marks(text)
    if notation == "arabic":
        text = _AR_LN.sub("", text)
        text = _AR_RADIUS.sub("", text)
    text = WORDY_BODY.sub("", text)
    if notation == "arabic":
        text = FUNCTION_COMMAND.sub("", text)
    text = BARE_WORD.sub("", text)
    text = ARABIC_LETTER.sub("", text)
    if notation == "arabic":
        text = MATHBB_COMMAND.sub("", text)
        text = SINGLE_LATIN.sub("", text)
    for spacing in (r"\,", r"\;", r"\:", r"\!", r"\ ", "~"):
        text = text.replace(spacing, "")
    text = re.sub(r"\s+", "", text)
    text = text.replace(r"\left", "").replace(r"\right", "")
    text = PARALLEL_SLASHES.sub(r"\\parallel", text)
    text = re.sub(r"\\times(?=\()", "", text)
    text = text.replace(",", "").replace("،", "").replace("؛", "")
    text = text.replace(r"\dfrac", r"\frac")
    return _fold_single_scripts(text)


def latin_function_output_problems(text: str, *, notation: str) -> list[str]:
    """Latin ``\\sin`` / ``\\cos`` / ``\\tan`` (every function command) left in Arabic output.

    The skeleton check stays relaxed: under arabic notation those commands are
    folded away, so a reply is not rejected for the skeleton. This check is the
    one that sends the call back before review.
    """
    if notation != "arabic":
        return []
    found = FUNCTION_COMMAND.findall(text)
    if not found:
        return []
    names = ", ".join(sorted(set(found)))
    return [f"Latin function command left in Arabic output: {names}"]


def _math_spans(text: str) -> list[str]:
    return [match.group(0) for match in MATH_SEGMENT.finditer(text)]


def _digit_counter(text: str) -> Counter[str]:
    """Count printed quantities. Grade, class, year and level numbers are wording."""
    text = GRADE_WORDING.sub(lambda match: " " * len(match.group(0)), text)
    text = _blank_label_digits(text)
    counts: Counter[str] = Counter()

    def take(chunk: str, *, maths: bool) -> None:
        if maths:
            body = chunk
            if body.startswith("$$") and body.endswith("$$"):
                body = body[2:-2]
            elif body.startswith("$") and body.endswith("$"):
                body = body[1:-1]
            body = body.replace(",", "")
            counts.update(INNER_DIGIT.findall(body))
            return
        claimed: list[tuple[int, int]] = []
        for match in PRINT_NUMBER.finditer(chunk):
            cleaned = match.group("number").replace(",", "")
            counts.update(INNER_DIGIT.findall(cleaned))
            claimed.append(match.span("number"))
        for match in PI_COEFFICIENT.finditer(chunk):
            start, end = match.span(1)
            if any(start < stop and end > begin for begin, stop in claimed):
                continue
            counts.update(INNER_DIGIT.findall(match.group(1)))
            claimed.append((start, end))
        for match in PROSE_COEFFICIENT.finditer(chunk):
            start, end = match.span(1)
            if any(start < stop and end > begin for begin, stop in claimed):
                continue
            counts.update(INNER_DIGIT.findall(match.group(1)))

    cursor = 0
    for match in MATH_SEGMENT.finditer(text):
        take(text[cursor : match.start()], maths=False)
        take(match.group(0), maths=True)
        cursor = match.end()
    take(text[cursor:], maths=False)
    return counts


def token_problems(
    slots: Slots,
    text: str,
    where: str,
    *,
    dropped: Iterable[str] = (),
) -> list[str]:
    problems: list[str] = []
    omitted = list(dropped)
    if len(omitted) > MAX_DROPPED_NUMBERS_PER_FIELD:
        problems.append(
            f"{where}: more than {MAX_DROPPED_NUMBERS_PER_FIELD} dropped numbers"
        )
    omitted_set = set(omitted)
    seen: Counter[str] = Counter()
    opens: dict[str, int] = {}
    inners: dict[str, str] = {}
    for match in TOKEN.finditer(text):
        closing, kind, digits = match.group(1), match.group(2), match.group(3)
        key = f"{kind}{digits}"
        known = (
            (kind == "M" and key in slots.frozen)
            or (kind == "E" and key in slots.editable)
            or (kind == "N" and key in slots.numbers)
        )
        if not known:
            problems.append(f"{where}: unknown token {key}")
            continue
        if kind == "E" and closing:
            if key not in opens:
                problems.append(f"{where}: editable maths {key} closes before it opens")
            else:
                inners[key] = text[opens[key] : match.start()]
            seen[f"/{key}"] += 1
        elif kind == "E":
            opens[key] = match.end()
            seen[key] += 1
        else:
            seen[key] += 1
    for key in slots.frozen:
        count = seen[key]
        if count != 1:
            problems.append(f"{where}: token {key} appears {count} times")
    for key in slots.numbers:
        count = seen[key]
        if count != 1 and key not in omitted_set:
            problems.append(f"{where}: token {key} appears {count} times")
        elif count != 0 and key in omitted_set and count != 1:
            problems.append(f"{where}: token {key} appears {count} times")
    editable_problems: list[str] = []
    for key in slots.editable:
        if seen[key] != 1 or seen[f"/{key}"] != 1:
            editable_problems.append(
                f"{where}: token {key} appears {seen[key]} times"
            )
            continue
        inner = inners.get(key, "").strip()
        if not _is_math_span(inner):
            editable_problems.append(f"{where}: editable maths {key} is not one formula")
            continue
        source = skeleton(slots.editable[key], notation="latin")
        # Notation is recovered by comparing both skeletons the caller cares about.
        # token_problems does not receive notation; editable checks use the
        # stricter latin skeleton unless the source itself has no Latin letters
        # left to keep. Callers that need arabic letter-stripping use
        # restored_problems. Here both sides drop words, and arabic notation
        # also drops single Latin letters when the stored span still has them.
        target = skeleton(inner, notation="latin")
        if source != target:
            source_ar = skeleton(slots.editable[key], notation="arabic")
            target_ar = skeleton(inner, notation="arabic")
            if source_ar == target_ar:
                continue
            editable_problems.append(
                f"{where}: editable maths {key} changed beyond words: {source_ar} -> {target_ar}"
            )
    if editable_problems and _editable_spans_merged(slots, inners, seen):
        editable_problems = [
            problem
            for problem in editable_problems
            if "appears" not in problem and "changed beyond words" not in problem
        ]
    problems.extend(editable_problems)
    return problems


def _editable_spans_merged(slots: Slots, inners: dict[str, str], seen: Counter[str]) -> bool:
    """True when several source formulas were written inside one returned formula.

    A missing ``E`` tag is accepted only when its skeleton sits inside a formula
    that was returned, and the combined formulas add no digit. Dropping a formula
    entirely is not a merge. A letter-only formula still has to be present.
    """
    blob = ""
    for key, inner in inners.items():
        if seen[key] != 1:
            return False
        blob += skeleton(inner, notation="arabic")
    for key, span in slots.editable.items():
        skel = skeleton(span, notation="arabic")
        if not skel:
            if seen[key] != 1 or seen[f"/{key}"] != 1:
                return False
            continue
        index = blob.find(skel)
        if index < 0:
            return False
        blob = blob[:index] + blob[index + len(skel) :]
    return not any(char.isdigit() for char in blob)


def restored_problems(
    source: str,
    target: str,
    where: str,
    *,
    notation: str,
    dropped_numbers: int = 0,
    freeze: bool = True,
) -> list[str]:
    problems: list[str] = []
    frozen_counts: Counter[str] = Counter()
    editable_skeletons: list[str] = []
    for span in _math_spans(source):
        skel = skeleton(span, notation=notation)
        editable = (not freeze) or math_is_editable(span, notation) or skel in {r"\%", "%"}
        if editable:
            if skel:
                editable_skeletons.append(skel)
        elif notation != "arabic" and skel:
            frozen_counts[span] += 1
    for span, count in frozen_counts.items():
        if target.count(span) < count:
            problems.append(f"{where}: frozen maths missing: {span}")
    blob = "".join(skeleton(span, notation=notation) for span in _math_spans(target))
    for skel in editable_skeletons:
        index = blob.find(skel)
        if index < 0:
            problems.append(f"{where}: editable maths changed beyond words: {skel}")
            continue
        blob = blob[:index] + blob[index + len(skel) :]
    source_numbers = _digit_counter(source)
    target_numbers = _digit_counter(target)
    missing = sum(
        source_numbers[key] - target_numbers[key]
        for key in source_numbers
        if source_numbers[key] > target_numbers[key]
    )
    if missing > dropped_numbers:
        lost = [
            key
            for key in source_numbers
            if source_numbers[key] > target_numbers[key]
        ]
        problems.append(f"{where}: number(s) missing: {', '.join(lost)}")
    invented = [
        key for key in target_numbers if target_numbers[key] > source_numbers[key]
    ]
    if invented:
        problems.append(f"{where}: new number(s): {', '.join(invented)}")
    return problems


def _locations(group: dict) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [("stem", str(group.get("stem") or ""))]
    for index, figure in enumerate(group.get("figures") or []):
        description = figure.get("description") if isinstance(figure, dict) else figure
        rows.append((f"figures[{index}].description", str(description or "")))
    for exercise in group.get("exercises") or []:
        exercise_id = str(exercise.get("id") or "")
        rows.append((f"{exercise_id}.text", str(exercise.get("text") or "")))
        for index, option in enumerate(exercise.get("options") or []):
            rows.append((f"{exercise_id}.options[{index}]", str(option)))
        rows.append((f"{exercise_id}.answer", str(exercise.get("answer") or "")))
        for index, figure in enumerate(exercise.get("figures") or []):
            description = figure.get("description") if isinstance(figure, dict) else figure
            rows.append(
                (f"{exercise_id}.figures[{index}].description", str(description or ""))
            )
    return rows


def protect_group(group: dict, *, notation: str) -> tuple[dict, dict[str, Slots]]:
    protected = copy.deepcopy(group)
    slots_by_location: dict[str, Slots] = {}
    text, slots = protect(str(protected.get("stem") or ""), notation=notation)
    protected["stem"] = text
    slots_by_location["stem"] = slots
    figures = []
    for index, figure in enumerate(protected.get("figures") or []):
        if not isinstance(figure, dict):
            figure = {"description": str(figure or "")}
        text, slots = protect(str(figure.get("description") or ""), notation=notation)
        figure["description"] = text
        figures.append(figure)
        slots_by_location[f"figures[{index}].description"] = slots
    protected["figures"] = figures
    exercises = []
    for exercise in protected.get("exercises") or []:
        exercise_id = str(exercise.get("id") or "")
        text, slots = protect(str(exercise.get("text") or ""), notation=notation)
        exercise["text"] = text
        slots_by_location[f"{exercise_id}.text"] = slots
        options = []
        for index, option in enumerate(exercise.get("options") or []):
            text, slots = protect(str(option), notation=notation)
            options.append(text)
            slots_by_location[f"{exercise_id}.options[{index}]"] = slots
        exercise["options"] = options
        text, slots = protect(str(exercise.get("answer") or ""), notation=notation)
        exercise["answer"] = text
        slots_by_location[f"{exercise_id}.answer"] = slots
        figures = []
        for index, figure in enumerate(exercise.get("figures") or []):
            if not isinstance(figure, dict):
                figure = {"description": str(figure or "")}
            text, slots = protect(str(figure.get("description") or ""), notation=notation)
            figure["description"] = text
            figures.append(figure)
            slots_by_location[f"{exercise_id}.figures[{index}].description"] = slots
        exercise["figures"] = figures
        exercises.append(exercise)
    protected["exercises"] = exercises
    return protected, slots_by_location


def _set_location(group: dict, location: str, value: str) -> None:
    if location == "stem":
        group["stem"] = value
        return
    if location.startswith("figures["):
        index = int(location.split("[", 1)[1].split("]", 1)[0])
        group["figures"][index]["description"] = value
        return
    exercise_id, _, rest = location.partition(".")
    for exercise in group.get("exercises") or []:
        if str(exercise.get("id") or "") != exercise_id:
            continue
        if rest == "text":
            exercise["text"] = value
        elif rest == "answer":
            exercise["answer"] = value
        elif rest.startswith("options["):
            index = int(rest.split("[", 1)[1].split("]", 1)[0])
            exercise["options"][index] = value
        elif rest.startswith("figures["):
            index = int(rest.split("[", 1)[1].split("]", 1)[0])
            exercise["figures"][index]["description"] = value
        return


def restore_group(group: dict, slots_by_location: dict[str, Slots]) -> dict:
    restored = copy.deepcopy(group)
    for location, slots in slots_by_location.items():
        current = dict(restored)
        # Read through the same keys protect_group wrote.
        if location == "stem":
            _set_location(restored, location, restore(str(restored.get("stem") or ""), slots))
            continue
        if location.startswith("figures["):
            index = int(location.split("[", 1)[1].split("]", 1)[0])
            figure = restored["figures"][index]
            figure["description"] = restore(str(figure.get("description") or ""), slots)
            continue
        exercise_id, _, rest = location.partition(".")
        for exercise in restored.get("exercises") or []:
            if str(exercise.get("id") or "") != exercise_id:
                continue
            if rest == "text":
                exercise["text"] = restore(str(exercise.get("text") or ""), slots)
            elif rest == "answer":
                exercise["answer"] = restore(str(exercise.get("answer") or ""), slots)
            elif rest.startswith("options["):
                index = int(rest.split("[", 1)[1].split("]", 1)[0])
                exercise["options"][index] = restore(str(exercise["options"][index]), slots)
            elif rest.startswith("figures["):
                index = int(rest.split("[", 1)[1].split("]", 1)[0])
                figure = exercise["figures"][index]
                figure["description"] = restore(str(figure.get("description") or ""), slots)
            break
    del current
    return restored


def protect_spec(spec: dict, *, notation: str) -> tuple[dict, dict[str, Slots]]:
    """Protect every printable string. Paths use row ids, matching spec checks."""
    slots: dict[str, Slots] = {}

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, str):
            text, slot = protect(value, notation=notation)
            if path:
                slots[path] = slot
            return text
        if isinstance(value, list):
            return [
                walk(item, f"{path}[{index}]") if path else walk(item, "")
                for index, item in enumerate(value)
            ]
        if isinstance(value, dict):
            node = str(value.get("id") or "") or path
            out: dict[str, Any] = {}
            for key, item in value.items():
                if key == "id":
                    out[key] = item
                    continue
                child = f"{node}.{key}" if node else key
                if isinstance(item, list):
                    rows = []
                    for index, row in enumerate(item):
                        if isinstance(row, dict) and row.get("id"):
                            rows.append(walk(row, str(row["id"])))
                        else:
                            rows.append(walk(row, f"{child}[{index}]"))
                    out[key] = rows
                else:
                    out[key] = walk(item, child)
            return out
        return value

    protected = walk(copy.deepcopy(spec), "")
    return protected, slots


def iter_spec_strings(spec: dict) -> list[tuple[str, str]]:
    """(path, text) for every printable string, using the same paths as protect_spec."""
    found: list[tuple[str, str]] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, str):
            if path:
                found.append((path, value))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]" if path else "")
            return
        if isinstance(value, dict):
            node = str(value.get("id") or "") or path
            for key, item in value.items():
                if key == "id":
                    continue
                child = f"{node}.{key}" if node else key
                if isinstance(item, list):
                    for index, row in enumerate(item):
                        if isinstance(row, dict) and row.get("id"):
                            walk(row, str(row["id"]))
                        else:
                            walk(row, f"{child}[{index}]")
                else:
                    walk(item, child)

    walk(spec, "")
    return found


def set_spec_strings(spec: dict, updates: dict[str, str]) -> None:
    """Replace printable strings in place. Paths match protect_spec."""

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            current = str(node.get("id") or "") or path
            for key, item in list(node.items()):
                if key == "id":
                    continue
                child = f"{current}.{key}" if current else key
                if isinstance(item, str):
                    if child in updates:
                        node[key] = updates[child]
                elif isinstance(item, list):
                    for index, row in enumerate(item):
                        row_path = (
                            str(row.get("id"))
                            if isinstance(row, dict) and row.get("id")
                            else f"{child}[{index}]"
                        )
                        if isinstance(row, str):
                            if row_path in updates:
                                item[index] = updates[row_path]
                        else:
                            walk(row, row_path)
                else:
                    walk(item, child)

    walk(spec, "")


def restore_spec(spec: dict, slots: dict[str, Slots]) -> dict:
    restored = copy.deepcopy(spec)

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            current = str(node.get("id") or "") or path
            for key, item in list(node.items()):
                if key == "id":
                    continue
                child = f"{current}.{key}" if current else key
                if isinstance(item, str):
                    slot = slots.get(child)
                    if slot is not None:
                        node[key] = restore(item, slot)
                elif isinstance(item, list):
                    for index, row in enumerate(item):
                        row_path = (
                            str(row.get("id"))
                            if isinstance(row, dict) and row.get("id")
                            else f"{child}[{index}]"
                        )
                        if isinstance(row, str):
                            slot = slots.get(row_path)
                            if slot is not None:
                                item[index] = restore(row, slot)
                        else:
                            walk(row, row_path)
                else:
                    walk(item, child)
        elif isinstance(node, list):
            for index, row in enumerate(node):
                walk(row, f"{path}[{index}]" if path else "")

    walk(restored, "")
    return restored
