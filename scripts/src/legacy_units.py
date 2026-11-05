"""Legacy unit tables and merge; replay of pre-2026-10-21 logs only. Never import from the live translate path."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


MAP_SCHEMA_VERSION = 2
MAP_MERGE_VERSION = 2
TRANSLATION_STATE_STAGE = "00_translation_state"
BOOK_MAP_FILE = "book_map.json"
UNIT_EQUIVALENCES_FILE = "unit_equivalences.json"
UNIT_EQUIVALENCES_PENDING_FILE = "unit_equivalences.pending.json"
UNIT_EQUIVALENCES_SCHEMA = 1

# Recognition is deliberately separate from translation.  These are canonical
# unit spellings the English generator may use; their Arabic display forms are
# supplied by each book's model-produced map, never by this list.
UNIT_ALIASES = {
    "le": "EGP",
    "egyptian pound": "EGP",
    "egyptian pounds": "EGP",
    "amp": "A",
    "amps": "A",
    "ampere": "A",
    "amperes": "A",
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
    "cubic feet": "ft",
    "cubic foot": "ft",
    "cubic centimeter": "cm",
    "cubic centimeters": "cm",
    "cubic centimetre": "cm",
    "cubic centimetres": "cm",
    "cubic meter": "m",
    "cubic meters": "m",
    "cubic metre": "m",
    "cubic metres": "m",
    "degree celsius": "°C",
    "degrees celsius": "°C",
    "foot": "ft",
    "feet": "ft",
    "gram": "g",
    "grams": "g",
    "hour": "h",
    "hours": "h",
    "inch": "in",
    "inches": "in",
    "joule": "J",
    "joules": "J",
    "kilogram": "kg",
    "kilograms": "kg",
    "kilometer": "km",
    "kilometers": "km",
    "kilometre": "km",
    "kilometres": "km",
    "liter": "L",
    "liters": "L",
    "litre": "L",
    "litres": "L",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "mile": "mi",
    "miles": "mi",
    "minute": "min",
    "minutes": "min",
    "newton": "N",
    "newtons": "N",
    "second": "s",
    "seconds": "s",
    "square feet": "ft",
    "square foot": "ft",
    "square centimeter": "cm",
    "square centimeters": "cm",
    "square centimetre": "cm",
    "square centimetres": "cm",
    "square meter": "m",
    "square meters": "m",
    "square metre": "m",
    "square metres": "m",
    "sq m": "m",
    "sq. m": "m",
    "sq cm": "cm",
    "sq. cm": "cm",
    "sq ft": "ft",
    "sq. ft": "ft",
    "volt": "V",
    "volts": "V",
    "watt": "W",
    "watts": "W",
    "yard": "yd",
    "yards": "yd",
}

CANONICAL_UNITS = frozenset(
    {
        "%",
        "°",
        "°C",
        "A",
        "EGP",
        "Hz",
        "J",
        "K",
        "L",
        "MHz",
        "N",
        "Pa",
        "V",
        "W",
        "bar",
        "cm",
        "dm",
        "ft",
        "g",
        "h",
        "in",
        "kHz",
        "kJ",
        "kPa",
        "kW",
        "kWh",
        "kg",
        "km",
        "km/h",
        "km/min",
        "m",
        "m/s",
        "m/s^2",
        "mg",
        "mi",
        "min",
        "mL",
        "ml",
        "mm",
        "s",
        "yd",
    }
)

TEXT_UNIT = re.compile(
    r"\\(?:text|mathrm|operatorname)\{(?P<body>[^{}]*)\}"
    r"(?P<power>\s*\^\s*(?:\{[^{}]*\}|[-+]?[0-9]+))?",
    re.IGNORECASE,
)
MATH_SEGMENT = re.compile(r"\$\$.+?\$\$|\$.+?\$", re.DOTALL)
PROSE_UNIT = re.compile(
    r"(?<![A-Za-z0-9:.])(?:[0-9]+(?:\.[0-9]+)?|\\frac\{[^{}]+\}\{[^{}]+\})\s+"
    r"(?P<body>"
    + "|".join(
        sorted(
            [re.escape(unit) for unit in CANONICAL_UNITS]
            + [re.escape(alias) for alias in UNIT_ALIASES],
            key=len,
            reverse=True,
        )
    )
    + r")(?![A-Za-z])",
    re.IGNORECASE,
)
EMBEDDED_UNIT = re.compile(
    r"(?<![A-Za-z])("
    + "|".join(
        sorted(
            (re.escape(unit) for unit in CANONICAL_UNITS),
            key=len,
            reverse=True,
        )
    )
    + r")(?![A-Za-z])",
)


ARABIC_LETTER = re.compile(r"[\u0621-\u064A\u066E-\u06D3]")
TASHKEEL = frozenset(chr(code) for code in [*range(0x064B, 0x0660), 0x0670])
def strip_tashkeel(token: str) -> str:
    return "".join(char for char in token if char not in TASHKEEL)


def arabic_unit_stem(token: str) -> str:
    """The word with tashkeel removed. Code does not build another Arabic form."""
    return strip_tashkeel(token)


def arabic_unit_forms_equivalent(left: str, right: str) -> bool:
    """True when two spellings are the same letters once tashkeel is ignored."""
    return strip_tashkeel(left) == strip_tashkeel(right)


def arabic_unit_form_set_equivalent(forms: Iterable[str]) -> bool:
    values = [str(form) for form in forms]
    if any(not form.strip() for form in values) and any(form.strip() for form in values):
        return False
    nonempty = [form for form in values if form.strip()]
    if len(nonempty) <= 1:
        return True
    first = nonempty[0]
    return all(arabic_unit_forms_equivalent(first, other) for other in nonempty[1:])


def _merged_unit_math_text(item_id: str, authorities: list[dict[str, Any]]) -> str:
    math_values = [str(row.get("math_text") or "") for row in authorities]
    math_forms = set(math_values)
    if len(math_forms) == 1:
        return next(iter(math_forms))
    if arabic_unit_form_set_equivalent(math_forms):
        return preferred_arabic_unit_form(math_values)
    split_math = {
        str(row.get("math_text") or "")
        for row in authorities
        if str(row.get("math_text") or "")
        and str(row.get("plain_lemma") or "")
        and str(row.get("plain_lemma") or "") != str(row.get("math_text") or "")
        and not arabic_unit_forms_equivalent(
            str(row.get("plain_lemma") or ""),
            str(row.get("math_text") or ""),
        )
    }
    split_lemmas = {
        str(row.get("plain_lemma") or "")
        for row in authorities
        if str(row.get("math_text") or "") in split_math
        and str(row.get("plain_lemma") or "")
    }
    extra = {form for form in math_forms if form} - split_math
    if split_math and extra <= split_lemmas:
        return preferred_arabic_unit_form(
            form for form in math_values if form in split_math
        )
    raise UnresolvedUnitForms(item_id, math_forms, authorities=authorities)


class PendingUnitFormConflict(ValueError):
    """Unattended merge wrote a pending confirmation file."""

    def __init__(self, path: Path, names: str) -> None:
        self.path = path
        super().__init__(
            f"book map unit form conflict pending interactive confirmation "
            f"({names}); wrote {path}"
        )


class UnresolvedUnitForms(ValueError):
    """Two Arabic unit spellings the light stem cannot join."""

    def __init__(
        self,
        item_id: str,
        forms: Iterable[str],
        *,
        authorities: list[dict[str, Any]],
        field: str = "math_text",
    ) -> None:
        self.item_id = item_id
        self.forms = sorted({str(form) for form in forms if str(form).strip()})
        self.authorities = authorities
        self.field = field
        super().__init__(
            f"book map conflict for {item_id}: "
            + " versus ".join(repr(form) for form in self.forms)
        )


def unit_equivalences_document(entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": UNIT_EQUIVALENCES_SCHEMA,
        "entries": list(entries or []),
    }


def load_unit_equivalences(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = payload.get("entries") if isinstance(payload, dict) else payload
    result: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        item_id = str((row or {}).get("id") or "")
        if item_id:
            result[item_id] = dict(row)
    return result


def save_unit_equivalences(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(unit_equivalences_document(entries), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def load_pending_unit_equivalences(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(payload, dict):
        return list(payload.get("conflicts") or [])
    return list(payload or [])


def save_pending_unit_equivalences(
    path: Path, book_id: str, conflicts: list[dict[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"book_id": book_id, "conflicts": conflicts},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _also_list(row: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(item).strip()
            for item in (row.get("also") or [])
            if str(item).strip()
        }
    )


def _equivalence_covers(row: dict[str, Any], forms: Iterable[str]) -> bool:
    covered = {
        str(row.get("math_text") or "").strip(),
        str(row.get("plain_lemma") or "").strip(),
        *_also_list(row),
    } - {""}
    return bool(covered) and set(forms) <= covered


def _guess_unit_conflict_kind(forms: list[str]) -> str:
    ordered = sorted(forms, key=len)
    if (
        len(ordered) >= 2
        and len(ordered[0]) <= 2
        and len(ordered[-1]) > len(ordered[0]) + 1
    ):
        return "abbrev_lemma"
    return "broken_plural"


def _propose_unit_forms(forms: list[str], kind: str) -> tuple[str, str, list[str]]:
    if kind == "abbrev_lemma":
        math_text = min(forms, key=len)
        lemma = max(forms, key=len)
    else:
        math_text = min(forms, key=lambda form: (len(form), form))
        lemma = math_text
    also = sorted({form for form in forms if form not in {math_text, lemma}})
    if kind == "abbrev_lemma" and lemma != math_text:
        also = sorted(set(also) | {lemma})
    return math_text, lemma, also


def _unit_conflict_question(
    item_id: str,
    authorities: list[dict[str, Any]],
    forms: list[str],
) -> dict[str, Any]:
    kind = _guess_unit_conflict_kind(forms)
    math_text, lemma, also = _propose_unit_forms(forms, kind)
    source_math = str((authorities[0] if authorities else {}).get("source_math") or "")
    rows: list[dict[str, Any]] = []
    for form in forms:
        match = next(
            (
                row
                for row in authorities
                if form
                in {
                    str(row.get("math_text") or "").strip(),
                    str(row.get("plain_lemma") or "").strip(),
                }
            ),
            authorities[0] if authorities else {},
        )
        evidence = next(iter(match.get("evidence") or []), {})
        rows.append(
            {
                "form": form,
                "section": str(match.get("section") or ""),
                "quote": str(evidence.get("quote") or ""),
            }
        )
    return {
        "id": item_id,
        "source_math": source_math,
        "kind": kind,
        "forms": forms,
        "math_text": math_text,
        "plain_lemma": lemma,
        "also": also,
        "sections": sorted(
            {str(row.get("section") or "") for row in authorities} - {""}
        ),
        "rows": rows,
    }


def _prompt_unit_equivalence(
    question: dict[str, Any], ask: Callable[[str], str]
) -> dict[str, Any] | None:
    lines = [
        f"Book map unit {question['id']} ({question.get('source_math') or '?'}) "
        "has two Arabic spellings the automatic stem cannot join:",
    ]
    for row in question.get("rows") or []:
        quote = str(row.get("quote") or "")
        suffix = f"  quote: {quote}" if quote else ""
        lines.append(
            f"  section {row.get('section') or '?'}: {row.get('form')!r}{suffix}"
        )
    lines.append("Same unit?")
    lines.append(
        f"  [1] yes — math_text {question['math_text']!r}, "
        f"lemma {question['plain_lemma']!r}"
        + (
            f", also {question['also']!r}"
            if question.get("also")
            else ""
        )
    )
    lines.append("  [2] no — leave as a conflict")
    prompt = "\n".join(lines) + "\n> "
    while True:
        answer = ask(prompt).strip()
        if answer == "1":
            return {
                "id": question["id"],
                "source_math": question.get("source_math") or "",
                "math_text": question["math_text"],
                "plain_lemma": question["plain_lemma"],
                "also": list(question.get("also") or []),
                "kind": question.get("kind") or "broken_plural",
                "sections": list(question.get("sections") or []),
            }
        if answer == "2":
            return None


def _decision_from_equivalences(
    item_id: str,
    forms: list[str],
    equivalences: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    row = equivalences.get(item_id)
    if row is None or not _equivalence_covers(row, forms):
        return None
    return row


def preferred_arabic_unit_form(forms: Iterable[str]) -> str:
    cleaned = [strip_tashkeel(form) for form in forms if str(form).strip()]
    if not cleaned:
        return ""
    counts: dict[str, int] = {}
    for form in cleaned:
        counts[form] = counts.get(form, 0) + 1
    return min(counts, key=lambda form: (-counts[form], len(form), form))


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).strip().split())


def _normalized_math(value: str, kind: str) -> str:
    form = _normalized(value)
    if form.startswith("$") and form.endswith("$") and len(form) >= 2:
        form = form[1:-1].strip()
    if kind == "unit":
        wrapped = re.fullmatch(
            r"\\(?:text|mathrm|operatorname)\{([^{}]+)\}", form
        )
        if wrapped:
            form = wrapped.group(1).strip()
        form = re.sub(r"\s*/\s*", "/", form)
    return form


def _stable_id(kind: str, source: str) -> str:
    digest = hashlib.sha256(f"{kind}\0{_normalized(source)}".encode("utf-8")).hexdigest()
    return f"{kind}:{digest[:16]}"


TIME_WORD_ALIASES = frozenset(
    {"second", "seconds", "minute", "minutes", "hour", "hours"}
)
DEGREE_SCALE_LETTERS = frozenset({"c", "f", "k", "م", "س"})
DEGREE_PREFIX = re.compile(r"(?:°|\^\s*\\circ|\^\s*\{\s*\\circ\s*\}|\\circ)$")


def temperature_scale_token(prefix: str, token: str) -> bool:
    """True when a one-letter body after ``°`` / ``\\circ`` is C/F/K/م/س, not SI.

    Arabic ``$16^\\circ\\text{م}$`` is Celsius (مئوية), not metres.  ``$5\\text{ m}$``
    after a quantity is still a length.
    """
    compact = strip_tashkeel(token)
    if len(compact) != 1 or compact.casefold() not in DEGREE_SCALE_LETTERS:
        return False
    return bool(DEGREE_PREFIX.search(prefix.rstrip()))


PGF_FOREACH_VARS = re.compile(
    r"\\foreach\b(?:(?!\\foreach\b).)*"
    r"(?:\\[A-Za-z][A-Za-z0-9]*)(?:\s*/\s*\\[A-Za-z][A-Za-z0-9]*)*\s*$",
    re.DOTALL,
)


def preposition_in(token: str, after: str, *, measured: bool) -> bool:
    """True when ``in`` is the English preposition, not the inch unit."""
    if token.casefold() != "in" or canonical_unit(token) != "in":
        return False
    follows_word = bool(after and (after[0].isalpha() or after[0] in "$\\"))
    return follows_word or not measured


def pgf_foreach_in(prefix: str, token: str) -> bool:
    """True when ``in`` is the PGF ``\\foreach ... in {list}`` keyword, not inches."""
    if token.casefold() != "in":
        return False
    return bool(PGF_FOREACH_VARS.search(prefix.rstrip()))


def unmeasured_time_word(token: str, *, measured: bool) -> bool:
    """True when second/minute/hour is an adjective or ordinal, not a duration.

    ``second-degree`` and ``the second equation`` share a spelling with the SI
    second.  Count the alias only when a digit, closer, or digit-bearing math
    island precedes it — not after a single letter or article.
    """
    return token.casefold() in TIME_WORD_ALIASES and not measured


def canonical_unit(value: str) -> str | None:
    """Return a recognized canonical unit, without translating it."""
    compact = re.sub(r"\s*/\s*", "/", _normalized(value))
    if compact in CANONICAL_UNITS:
        return compact
    case_matches = [unit for unit in CANONICAL_UNITS if unit.casefold() == compact.casefold()]
    if len(case_matches) == 1:
        return case_matches[0]
    alias = UNIT_ALIASES.get(compact.casefold())
    if alias is not None:
        return alias
    if "/" in compact:
        parts = compact.split("/")
        canonical_parts = [canonical_unit(part) for part in parts]
        if all(canonical_parts):
            return "/".join(str(part) for part in canonical_parts)
    return None


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def units_in_text(text: str) -> set[str]:
    found: set[str] = set()
    for match in TEXT_UNIT.finditer(text):
        body = match.group("body")
        compact = _normalized(body)
        power = match.group("power") or ""
        letter_label = (
            not power
            and not body[:1].isspace()
            and "/" not in compact
            and len(compact) == 1
        )
        if letter_label:
            after = text[match.end() :].lstrip()
            if after.startswith("("):
                continue
            prefix = text[: match.start()].rstrip()
            if temperature_scale_token(prefix, compact):
                continue
            lowercase_unit = compact.isalpha() and not compact.isupper()
            if not lowercase_unit and not (
                prefix and prefix[-1] in "0123456789)]}"
            ):
                continue
        if unit := canonical_unit(body):
            found.add(unit)
            continue
        for embedded in EMBEDDED_UNIT.finditer(body):
            token = embedded.group(1)
            after = body[embedded.end() :].lstrip()
            prefix = body[: embedded.start()].rstrip()
            measured = bool(prefix and prefix[-1] in "0123456789)]")
            if preposition_in(token, after, measured=measured):
                continue
            if unit := canonical_unit(token):
                found.add(unit)
    cursor = 0
    for maths in MATH_SEGMENT.finditer(text):
        for match in PROSE_UNIT.finditer(text[cursor : maths.start()]):
            if unit := canonical_unit(match.group("body")):
                found.add(unit)
        cursor = maths.end()
    for match in PROSE_UNIT.finditer(text[cursor:]):
        if unit := canonical_unit(match.group("body")):
            found.add(unit)
    return found


def _entry(
    kind: str,
    source_plain: str,
    source_math: str,
    context: str,
) -> dict[str, Any]:
    if kind == "unit":
        identity = source_math
    elif kind == "symbol":
        identity = f"{source_math}\0{_normalized(context)}"
    else:
        identity = f"{source_plain}\0{_normalized(context)}"
    return {
        "id": _stable_id(kind, identity),
        "kind": kind,
        "source_plain": _normalized(source_plain),
        "source_math": _normalized(source_math),
        "contexts": [_normalized(context)] if context.strip() else [],
        "required": True,
    }


def section_inventory(
    section: dict,
    en_document: dict,
    drawings: dict[str, dict] | None = None,
) -> list[dict[str, Any]]:
    """The role-aware source items one section asks its Arabic PDFs to map."""
    spec = section.get("spec") or {}
    entries: dict[str, dict[str, Any]] = {}

    def add(row: dict[str, Any]) -> None:
        previous = entries.get(row["id"])
        if previous is None:
            entries[row["id"]] = row
            return
        previous["contexts"] = sorted(
            set(previous.get("contexts") or []) | set(row.get("contexts") or [])
        )

    for row in spec.get("glossary") or []:
        term = str(row.get("term") or "").strip()
        if term:
            add(
                _entry(
                    "term",
                    term,
                    "",
                    str(row.get("definition") or ""),
                )
            )
    for row in spec.get("notations") or []:
        notation = str(row.get("notation") or "").strip()
        if notation:
            add(
                _entry(
                    "symbol",
                    "",
                    notation,
                    str(row.get("meaning") or ""),
                )
            )

    values: list[str] = list(_strings(spec)) + list(_strings(en_document.get("exercises") or {}))
    drawings = drawings or {}
    descriptions = {
        str(figure.get("description") or "")
        for group in en_document.get("exercises", {}).get("groups") or []
        for figure in (
            list(group.get("figures") or [])
            + [
                item
                for exercise in group.get("exercises") or []
                for item in exercise.get("figures") or []
            ]
        )
        if isinstance(figure, dict)
    }
    for description in descriptions:
        row = drawings.get(description) or {}
        values.append(str(row.get("tikz") or ""))

    for text in values:
        for unit in units_in_text(text):
            add(_entry("unit", unit, unit, f"section {section.get('number', '')}"))

    return sorted(entries.values(), key=lambda row: (row["kind"], row["id"]))


def validate_fragment(
    section: str,
    inventory: list[dict[str, Any]],
    fragment: dict[str, Any],
    *,
    source_pages: dict[str, int],
    notation: str = "",
) -> dict[str, Any]:
    """Validate one model fragment and overlay immutable inventory fields.

    ``section`` is known to the caller. Models write ``4-5`` or ``5`` for
    ``4.5``; the supplied number is stored, and a mismatch is not a fault.
    """
    observations = fragment.get("observations")
    if not isinstance(observations, list):
        raise ValueError("map fragment has no observations list")
    expected = [row["id"] for row in inventory]
    actual = [str(row.get("id") or "") for row in observations if isinstance(row, dict)]
    if actual != expected:
        raise ValueError("map fragment observation ids/order do not match the supplied inventory")

    output: list[dict[str, Any]] = []
    by_id = {row["id"]: row for row in inventory}
    for index, observation in enumerate(observations):
        item_id = actual[index]
        status = str(observation.get("status") or "")
        if status not in {"confirmed", "inferred", "ambiguous"}:
            raise ValueError(f"map observation {item_id} has invalid status {status!r}")
        if status == "ambiguous":
            raise ValueError(f"map observation {item_id} is ambiguous")
        source = by_id[item_id]
        plain_lemma = _normalized(str(observation.get("plain_lemma") or ""))
        math_text = _normalized_math(
            str(observation.get("math_text") or ""), str(source["kind"])
        )
        if source["kind"] == "unit":
            plain_lemma = re.sub(r"\s*/\s*", "/", plain_lemma)
        if source["kind"] == "symbol" and notation == "latin":
            # The map records the symbol authority too, but this profile's symbol
            # policy is preservation.  Arabic wording belongs in the notation's
            # meaning, not in the symbol token itself.
            plain_lemma = ""
            math_text = str(source.get("source_math") or "")
        if source["kind"] == "unit":
            if canonical_unit(math_text) == source.get("source_math"):
                raise ValueError(
                    f"map observation {item_id} retained Latin unit form {math_text!r}"
                )
        required_field = math_text if source["kind"] in {"symbol", "unit"} else plain_lemma
        if not required_field:
            raise ValueError(f"map observation {item_id} has no required Arabic form")
        basis = _normalized(str(observation.get("basis") or ""))
        evidence = observation.get("evidence") or []
        if status == "confirmed" and not evidence:
            raise ValueError(f"confirmed map observation {item_id} has no evidence")
        if status == "inferred" and not basis:
            raise ValueError(f"inferred map observation {item_id} has no basis")
        checked_evidence: list[dict[str, Any]] = []
        for citation in evidence:
            source_id = str(citation.get("source_id") or "")
            page = citation.get("split_page")
            quote = _normalized(str(citation.get("quote") or ""))
            if source_id not in source_pages:
                raise ValueError(f"map observation {item_id} cites unknown {source_id!r}")
            if not isinstance(page, int) or not 1 <= page <= source_pages[source_id]:
                raise ValueError(
                    f"map observation {item_id} cites page {page!r} outside {source_id}"
                )
            if not quote:
                raise ValueError(f"map observation {item_id} has an empty evidence quote")
            checked_evidence.append(
                {"source_id": source_id, "split_page": page, "quote": quote}
            )
        output.append(
            {
                **source,
                "status": status,
                "plain_lemma": plain_lemma,
                "math_text": math_text,
                "basis": basis,
                "evidence": checked_evidence,
                "section": str(section),
            }
        )
    return {
        "schema_version": MAP_SCHEMA_VERSION,
        "section": str(section),
        "inventory": inventory,
        "observations": output,
    }


def semantic_entry(row: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": str(row["id"]),
        "kind": str(row["kind"]),
        "source_plain": str(row.get("source_plain") or ""),
        "source_math": str(row.get("source_math") or ""),
        "plain_lemma": str(row.get("plain_lemma") or ""),
        "math_text": str(row.get("math_text") or ""),
    }
    also = _also_list(row)
    if also:
        entry["also"] = also
    return entry


def semantic_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _store_unit_equivalence(
    equivalences: dict[str, dict[str, Any]],
    decision: dict[str, Any],
    path: Path | None,
) -> None:
    equivalences[str(decision["id"])] = decision
    if path is not None:
        save_unit_equivalences(path, list(equivalences.values()))


def _confirm_unresolved_unit(
    error: UnresolvedUnitForms,
    *,
    book_id: str,
    equivalences: dict[str, dict[str, Any]],
    equivalences_path: Path | None,
    pending_path: Path | None,
    pending: list[dict[str, Any]],
    ask: Callable[[str], str] | None,
    is_tty: bool,
) -> dict[str, Any] | None:
    """Return a stored/asked decision, queue a pending row, or re-raise."""
    saved = _decision_from_equivalences(error.item_id, error.forms, equivalences)
    if saved is not None:
        return saved
    question = _unit_conflict_question(error.item_id, error.authorities, error.forms)
    if is_tty and ask is not None:
        decision = _prompt_unit_equivalence(question, ask)
        if decision is None:
            raise error
        _store_unit_equivalence(equivalences, decision, equivalences_path)
        if pending_path is not None and pending_path.is_file():
            leftover = [
                row for row in load_pending_unit_equivalences(pending_path)
                if str(row.get("id") or "") != error.item_id
            ]
            if leftover:
                save_pending_unit_equivalences(pending_path, book_id, leftover)
            else:
                pending_path.unlink()
        return decision
    pending.append(question)
    if pending_path is None:
        raise error
    return None


def legacy_merge_fragments(
    book_id: str,
    profile: dict[str, str],
    fragments: list[dict[str, Any]],
    *,
    equivalences: dict[str, dict[str, Any]] | None = None,
    equivalences_path: Path | None = None,
    pending_path: Path | None = None,
    ask: Callable[[str], str] | None = input,
    is_tty: bool = False,
) -> dict[str, Any]:
    """Merge section fragments, rejecting every semantic conflict."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for fragment in fragments:
        for row in fragment.get("observations") or []:
            grouped.setdefault(str(row["id"]), []).append(row)

    stored = dict(equivalences or {})
    if equivalences_path is not None:
        stored.update(load_unit_equivalences(equivalences_path))
    pending_questions = load_pending_unit_equivalences(pending_path)
    if is_tty and ask is not None and pending_questions:
        for question in list(pending_questions):
            item_id = str(question.get("id") or "")
            forms = [str(form) for form in question.get("forms") or [] if str(form)]
            saved = _decision_from_equivalences(item_id, forms, stored)
            if saved is None:
                decision = _prompt_unit_equivalence(question, ask)
                if decision is None:
                    raise UnresolvedUnitForms(
                        item_id,
                        forms,
                        authorities=[],
                    )
                _store_unit_equivalence(stored, decision, equivalences_path)
            if pending_path is not None and pending_path.is_file():
                leftover = [
                    row
                    for row in load_pending_unit_equivalences(pending_path)
                    if str(row.get("id") or "") != item_id
                ]
                if leftover:
                    save_pending_unit_equivalences(pending_path, book_id, leftover)
                else:
                    pending_path.unlink()

    merged: dict[str, dict[str, Any]] = {}
    queued_pending: list[dict[str, Any]] = []
    for item_id, rows in grouped.items():
        confirmed = [row for row in rows if row.get("status") == "confirmed"]
        authorities = confirmed or rows
        if rows[0].get("kind") == "unit":
            also: list[str] = []
            try:
                math_text = _merged_unit_math_text(item_id, authorities)
            except UnresolvedUnitForms as error:
                decision = _confirm_unresolved_unit(
                    error,
                    book_id=book_id,
                    equivalences=stored,
                    equivalences_path=equivalences_path,
                    pending_path=pending_path,
                    pending=queued_pending,
                    ask=ask,
                    is_tty=is_tty,
                )
                if decision is None:
                    continue
                math_text = str(decision.get("math_text") or "")
                plain_lemma = str(decision.get("plain_lemma") or math_text)
                also = _also_list(decision)
            else:
                lemma_values = [
                    str(row.get("plain_lemma") or "")
                    for row in authorities
                    if str(row.get("plain_lemma") or "")
                    and not arabic_unit_forms_equivalent(
                        str(row.get("plain_lemma") or ""), math_text
                    )
                ]
                full_lemmas = set(lemma_values)
                if len(full_lemmas) > 1:
                    if arabic_unit_form_set_equivalent(full_lemmas):
                        plain_lemma = preferred_arabic_unit_form(lemma_values)
                    else:
                        error = UnresolvedUnitForms(
                            item_id,
                            full_lemmas,
                            authorities=authorities,
                            field="plain_lemma",
                        )
                        decision = _confirm_unresolved_unit(
                            error,
                            book_id=book_id,
                            equivalences=stored,
                            equivalences_path=equivalences_path,
                            pending_path=pending_path,
                            pending=queued_pending,
                            ask=ask,
                            is_tty=is_tty,
                        )
                        if decision is None:
                            continue
                        math_text = str(decision.get("math_text") or math_text)
                        plain_lemma = str(decision.get("plain_lemma") or math_text)
                        also = _also_list(decision)
                else:
                    plain_lemma = (
                        next(iter(full_lemmas)) if full_lemmas else math_text
                    )
            winner = authorities[0]
            merged_row = {
                **winner,
                "plain_lemma": plain_lemma,
                "math_text": math_text,
                "sections": sorted({str(row["section"]) for row in rows}),
                "evidence": [
                    {"source_id": source, "split_page": page, "quote": quote}
                    for source, page, quote in sorted(
                        {
                            (
                                str(citation["source_id"]),
                                int(citation["split_page"]),
                                str(citation["quote"]),
                            )
                            for row in rows
                            for citation in row.get("evidence") or []
                        }
                    )
                ],
            }
            if also:
                merged_row["also"] = also
            merged[item_id] = merged_row
            continue
        semantic_forms = {
            json.dumps(
                semantic_entry(row),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for row in authorities
        }
        if len(semantic_forms) != 1:
            forms = [
                semantic_entry(row)["plain_lemma"]
                or semantic_entry(row)["math_text"]
                for row in authorities
            ]
            raise ValueError(
                f"book map conflict for {item_id}: "
                + " versus ".join(repr(form) for form in sorted(set(forms)))
            )
        winner = authorities[0]
        merged[item_id] = {
            **winner,
            "sections": sorted({str(row["section"]) for row in rows}),
            "evidence": [
                {"source_id": source, "split_page": page, "quote": quote}
                for source, page, quote in sorted(
                    {
                        (
                            str(citation["source_id"]),
                            int(citation["split_page"]),
                            str(citation["quote"]),
                        )
                        for row in rows
                        for citation in row.get("evidence") or []
                    }
                )
            ],
        }

    if queued_pending:
        if pending_path is not None:
            save_pending_unit_equivalences(pending_path, book_id, queued_pending)
            names = ", ".join(
                str(row.get("id") or "unit") for row in queued_pending
            )
            raise PendingUnitFormConflict(pending_path, names)
        raise UnresolvedUnitForms(
            str(queued_pending[0].get("id") or "unit"),
            queued_pending[0].get("forms") or [],
            authorities=[],
        )

    entries = [merged[key] for key in sorted(merged)]
    semantic = [semantic_entry(row) for row in entries]
    return {
        "schema_version": MAP_SCHEMA_VERSION,
        "merge_version": MAP_MERGE_VERSION,
        "status": "ready",
        "book_id": book_id,
        "profile": {
            key: profile.get(key, "")
            for key in ("language_code", "numerals", "notation")
        },
        "entries": entries,
        "semantic_fingerprint": semantic_fingerprint(semantic),
        "fragment_fingerprints": [
            semantic_fingerprint(fragment) for fragment in fragments
        ],
    }


def project_map(book_map: dict[str, Any], inventory: list[dict[str, Any]]) -> dict[str, Any]:
    wanted = {row["id"] for row in inventory}
    entries = [
        semantic_entry(row)
        for row in book_map.get("entries") or []
        if str(row.get("id") or "") in wanted
    ]
    missing = wanted - {row["id"] for row in entries}
    if missing:
        raise ValueError(f"book map lacks required ids: {', '.join(sorted(missing))}")
    projection = {
        "entries": entries,
        "book_fingerprint": str(book_map.get("semantic_fingerprint") or ""),
    }
    projection["semantic_fingerprint"] = semantic_fingerprint(entries)
    return projection


def load_ready_map(path: Path, *, book_id: str = "") -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read book map at {path}: {error}") from error
    if value.get("status") != "ready":
        raise RuntimeError(f"book map at {path} is not ready")
    if book_id and value.get("book_id") != book_id:
        raise RuntimeError(
            f"book map at {path} belongs to {value.get('book_id')!r}, not {book_id!r}"
        )
    return value


merge_fragments = legacy_merge_fragments
