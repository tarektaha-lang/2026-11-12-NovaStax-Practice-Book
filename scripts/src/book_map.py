"""Evidence-backed per-book Arabic terminology and symbol glossary."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any


MAP_SCHEMA_VERSION = 2
MAP_MERGE_VERSION = 2
TRANSLATION_STATE_STAGE = "00_translation_state"
BOOK_MAP_FILE = "book_map.json"


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


# Printed English variants of one base unit. Counting nouns are not in this table.
_UNIT_CANONICAL = {
    "min": "minute",
    "mins": "minute",
    "minute": "minute",
    "minutes": "minute",
    "sec": "second",
    "secs": "second",
    "second": "second",
    "seconds": "second",
    "hr": "hour",
    "hrs": "hour",
    "hour": "hour",
    "hours": "hour",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "m": "m",
    "kilometer": "km",
    "kilometers": "km",
    "kilometre": "km",
    "kilometres": "km",
    "km": "km",
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
    "cm": "cm",
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "mm": "mm",
    "gram": "g",
    "grams": "g",
    "g": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "kg": "kg",
    "time": "time",
    "times": "time",
    "page": "page",
    "pages": "page",
}


def canonical_unit(english: str) -> str:
    """One base unit for plural and abbreviated English (`minutes` and `min`)."""
    text = _normalized(english).strip("$")
    text = re.sub(r"\\(?:text|mathrm|operatorname)\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"(?:\^\{?\d+\}?|[²³])$", "", text)
    if text == "N" or text.casefold() in {"newton", "newtons"}:
        return "N"
    return _UNIT_CANONICAL.get(text.casefold(), text)


def _stable_id(kind: str, source: str) -> str:
    digest = hashlib.sha256(f"{kind}\0{_normalized(source)}".encode("utf-8")).hexdigest()
    return f"{kind}:{digest[:16]}"


def _also_list(row: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(item).strip()
            for item in (row.get("also") or [])
            if str(item).strip()
        }
    )


# Id-shaped keys, and any key ending in ``_id``. Their values are identifiers
# (``objective_3``, ``group_1``), not wording a glossary snippet should quote.
_ID_KEYS = frozenset({"id", "objective", "objectives", "group", "exercise"})


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in _ID_KEYS or str(key).endswith("_id"):
                continue
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


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
    """The role-aware source items one section asks its Arabic PDFs to map.

    Only spec glossary terms and spec notations. ``drawings`` stays in the
    signature for callers that still pass figure records.
    """
    del drawings
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

    return sorted(entries.values(), key=lambda row: (row["kind"], row["id"]))


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


_TEXT_BODY = re.compile(r"\\(?:text|mathrm|textrm)\{([^{}]*)\}")
_DIGIT_RUN = re.compile(r"\d+")
_ARABIC_LETTER = re.compile(r"[\u0621-\u064A]")
_BRACE_SPLIT = re.compile(r"[,،]")


def _english_snippets(
    section: dict,
    en_document: dict,
    drawings: dict[str, dict] | None,
    *,
    limit: int,
) -> list[str]:
    chunks: list[str] = []
    chunks.extend(_strings(section.get("spec") or {}))
    chunks.extend(_strings(en_document.get("exercises") or {}))
    drawings = drawings or {}
    descriptions = {
        str(figure.get("description") or "")
        for group in (en_document.get("exercises") or {}).get("groups") or []
        if isinstance(group, dict)
        for figure in (
            list(group.get("figures") or [])
            + [
                item
                for exercise in group.get("exercises") or []
                if isinstance(exercise, dict)
                for item in exercise.get("figures") or []
            ]
        )
        if isinstance(figure, dict)
    }
    for description in descriptions:
        row = drawings.get(description) or {}
        tikz = str(row.get("tikz") or "")
        if tikz:
            chunks.append(tikz)
    snippets: list[str] = []
    seen: set[str] = set()
    total = 0
    for text in chunks:
        candidates: list[str] = []
        for body in _TEXT_BODY.findall(text):
            candidates.append(body.strip())
        for match in _DIGIT_RUN.finditer(text):
            start = max(0, match.start() - 40)
            stop = min(len(text), match.end() + 40)
            candidates.append(text[start:stop].strip())
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            if total + len(candidate) > limit and snippets:
                return snippets
            seen.add(candidate)
            snippets.append(candidate)
            total += len(candidate)
            if total > limit:
                return snippets
    return snippets


def english_snippets(
    section: dict,
    en_document: dict,
    drawings: dict[str, dict] | None = None,
    *,
    limit: int = 15_000,
) -> list[str]:
    """Digit windows and ``\\text`` bodies from the section, capped by ``limit``."""
    return _english_snippets(section, en_document, drawings, limit=limit)


def _checked_evidence(
    evidence: Any,
    *,
    source_pages: dict[str, int],
    item_id: str,
) -> list[dict[str, Any]]:
    checked: list[dict[str, Any]] = []
    for citation in evidence or []:
        if not isinstance(citation, dict):
            raise ValueError(f"{item_id} evidence is not an object")
        source_id = str(citation.get("source_id") or "")
        page = citation.get("split_page")
        quote = _normalized(str(citation.get("quote") or ""))
        if source_id not in source_pages:
            raise ValueError(f"{item_id} cites unknown {source_id!r}")
        if not isinstance(page, int) or not 1 <= page <= source_pages[source_id]:
            raise ValueError(f"{item_id} cites page {page!r} outside {source_id}")
        if not quote:
            raise ValueError(f"{item_id} has an empty evidence quote")
        checked.append({"source_id": source_id, "split_page": page, "quote": quote})
    return checked


def validate_terminology_fragment(
    section: str,
    inventory: list[dict[str, Any]],
    fragment: dict[str, Any],
    *,
    source_pages: dict[str, int],
    notation: str = "",
) -> dict[str, Any]:
    """Validate terminology observations and unit rows. A section mismatch is stored, not raised."""
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
        if not isinstance(observation, dict):
            raise ValueError("map observation is not an object")
        item_id = actual[index]
        status = str(observation.get("status") or "")
        if status not in {"confirmed", "inferred"}:
            raise ValueError(f"map observation {item_id} has invalid status {status!r}")
        source = by_id[item_id]
        plain_lemma = _normalized(str(observation.get("plain_lemma") or ""))
        math_text = _normalized_math(str(observation.get("math_text") or ""), str(source["kind"]))
        if source["kind"] == "symbol" and notation == "latin":
            plain_lemma = ""
            math_text = str(source.get("source_math") or "")
        elif source["kind"] == "symbol":
            math_text = _strip_letter_text(math_text)
        required_field = math_text if source["kind"] == "symbol" else plain_lemma
        if not required_field:
            raise ValueError(f"map observation {item_id} has no required Arabic form")
        basis = _normalized(str(observation.get("basis") or ""))
        evidence = _checked_evidence(
            observation.get("evidence") or [],
            source_pages=source_pages,
            item_id=item_id,
        )
        if status == "confirmed" and not evidence:
            raise ValueError(f"confirmed map observation {item_id} has no evidence")
        if status == "inferred" and not basis:
            raise ValueError(f"inferred map observation {item_id} has no basis")
        output.append(
            {
                **source,
                "status": status,
                "plain_lemma": plain_lemma,
                "math_text": math_text,
                "basis": basis,
                "evidence": evidence,
                "section": str(section),
            }
        )
    units_out: list[dict[str, Any]] = []
    for raw in fragment.get("units") or []:
        if not isinstance(raw, dict):
            raise ValueError("unit observation is not an object")
        english = str(raw.get("english") or "").strip()
        arabic = _normalized(str(raw.get("arabic") or ""))
        if not english:
            raise ValueError("unit observation has an empty english form")
        if not arabic or not _ARABIC_LETTER.search(arabic):
            raise ValueError(f"unit {english!r} has no Arabic letter in {arabic!r}")
        if not isinstance(raw.get("abbreviation_only"), bool):
            raise ValueError(f"unit {english!r} abbreviation_only must be a bool")
        status = str(raw.get("status") or "")
        if status not in {"confirmed", "inferred"}:
            raise ValueError(f"unit {english!r} has invalid status {status!r}")
        basis = _normalized(str(raw.get("basis") or ""))
        evidence = _checked_evidence(
            raw.get("evidence") or [],
            source_pages=source_pages,
            item_id=english,
        )
        if status == "confirmed" and not evidence:
            raise ValueError(f"confirmed unit {english!r} has no evidence")
        if status == "inferred" and not basis:
            raise ValueError(f"inferred unit {english!r} has no basis")
        item_id = _stable_id("unit", english.strip().lower())
        units_out.append(
            {
                "id": item_id,
                "kind": "unit",
                "source_plain": english,
                "source_math": english,
                "status": status,
                "plain_lemma": arabic,
                "math_text": arabic,
                "also": _also_list(raw),
                "abbreviation_only": raw["abbreviation_only"],
                "basis": basis,
                "evidence": evidence,
                "section": str(section),
            }
        )
    merged_units: dict[str, dict[str, Any]] = {}
    for row in units_out:
        previous = merged_units.get(row["id"])
        if previous is None:
            merged_units[row["id"]] = row
            continue
        previous["also"] = _also_list({"also": previous["also"] + row["also"] + [row["math_text"]]})
        previous["evidence"] = (previous["evidence"] + row["evidence"])[:5]
    return {
        "schema_version": MAP_SCHEMA_VERSION,
        "section": str(section),
        "inventory": inventory,
        "observations": [*output, *merged_units.values()],
    }



GLOSSARY_MERGE_TOKEN_LIMIT = 150_000
_KIND_ORDER = {"unit": 0, "term": 1, "symbol": 2}
_TASHKEEL = {chr(code) for code in range(0x064B, 0x0653)} | {"\u0670", "\u0640"}


def loose_glossary_form(value: str) -> str:
    """Whitespace and diacritics only. No inflection or spelling fold."""
    text = unicodedata.normalize("NFKC", value)
    text = "".join(
        char
        for char in text
        if char not in _TASHKEEL and not unicodedata.combining(char)
    )
    return " ".join(text.split())


def glossary_candidate_rows(fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One input row per stored observation. Code does not decide which rows match."""
    rows: list[dict[str, Any]] = []
    for fragment in fragments:
        section = str(fragment.get("section") or "")
        for row in fragment.get("observations") or []:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "")
            if kind not in _KIND_ORDER:
                continue
            if kind == "symbol":
                english = str(row.get("source_math") or row.get("source_plain") or "")
                arabic = str(row.get("math_text") or row.get("plain_lemma") or "")
            else:
                english = str(row.get("source_plain") or row.get("source_math") or "")
                arabic = str(row.get("plain_lemma") or row.get("math_text") or "")
            contexts = row.get("contexts") or []
            if isinstance(contexts, str):
                contexts = [contexts]
            definition = " ".join(
                str(item).strip() for item in contexts if str(item).strip()
            )
            if not definition:
                definition = str(row.get("basis") or "")
            quotes = [
                str(citation.get("quote") or "")
                for citation in (row.get("evidence") or [])
                if isinstance(citation, dict) and str(citation.get("quote") or "").strip()
            ]
            item = {
                "id": str(row.get("id") or ""),
                "kind": kind,
                "english": _normalized(english),
                "definition": _normalized(definition),
                "arabic": arabic.strip(),
                "also": [
                    str(form).strip()
                    for form in (row.get("also") or [])
                    if str(form).strip()
                ],
                "abbreviation_only": bool(row.get("abbreviation_only")),
                "status": str(row.get("status") or ""),
                "section": str(row.get("section") or section),
                "quotes": quotes,
            }
            if kind == "unit":
                item["canonical_unit"] = canonical_unit(item["english"])
            if kind == "symbol":
                rows.extend(normalize_symbol_row(item))
            else:
                rows.append(item)
    return rows


def glossary_input_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Put the same English and kind next to each other. The model decides identity."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (str(row.get("kind") or ""), str(row.get("english") or "").casefold())
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(row)
    groups = [
        {
            "kind": kind,
            "english": members[0]["english"],
            "rows": members,
        }
        for kind, _folded in order
        for members in [grouped[(kind, _folded)]]
    ]
    groups.sort(
        key=lambda group: (
            _KIND_ORDER.get(str(group["kind"]), 9),
            str(group["english"]).casefold(),
            str(group["english"]),
        )
    )
    return groups


def glossary_input_payload(rows: list[dict[str, Any]], notation: str) -> dict[str, Any]:
    return {"notation": notation, "groups": glossary_input_groups(rows)}


def glossary_input_tokens(payload: dict[str, Any]) -> int:
    encoded = json.dumps(payload, ensure_ascii=False)
    return max(1, len(encoded) // 4)


def split_glossary_rows(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """One part per kind that has rows, in unit, term, symbol order."""
    parts: list[tuple[str, list[dict[str, Any]]]] = []
    for kind in ("unit", "term", "symbol"):
        chosen = [row for row in rows if row.get("kind") == kind]
        if chosen:
            parts.append((kind, chosen))
    return parts


def _hamza_fold(value: str) -> str:
    """أ إ آ are the same letter as ا for the printed-form check."""
    return value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")


def _printed_key(value: str) -> str:
    """Hamza-folded, and a space between Arabic letters does not make a new form."""
    text = _hamza_fold(loose_glossary_form(value))
    return re.sub(r"(?<=[\u0621-\u064A\u0640])\s+(?=[\u0621-\u064A])", "", text)


def _strip_text_wrappers(value: str) -> str:
    """Peel ``\\text`` / ``\\mathrm`` / ``\\textrm`` to their inner content, innermost first."""
    text = value
    previous = None
    while previous != text:
        previous = text
        text = _TEXT_BODY.sub(r"\1", text)
    return text


_LETTER_OR_SEP = re.compile(
    r"\\(?:text|textrm|mathrm)\{([\u0621-\u064A]\u0640*|[،,;:\s]+)\}"
)


def _strip_letter_text(value: str) -> str:
    """Drop ``\\text{X}`` when X is one Arabic letter (optional tatweel) or a separator."""
    text = value
    previous = None
    while previous != text:
        previous = text
        text = _LETTER_OR_SEP.sub(r"\1", text)
    return text


def _symbol_english_key(english: str) -> str:
    """NFC/whitespace-normalized English with one outer ``$…$`` stripped. Case stays."""
    text = _normalized(english)
    if text.startswith("$") and text.endswith("$") and len(text) >= 2:
        text = text[1:-1].strip()
    return text


def _symbol_match_key(value: str) -> str:
    """Printed symbol form without ``\\text`` wrappers, ``$``, or spaces next to math punctuation."""
    text = _printed_key(_strip_text_wrappers(value)).replace("$", "").replace("،", ",")
    return re.sub(r"\s*([_^{}(),])\s*", r"\1", text)


def _wrap_symbol_english(part: str) -> str:
    text = part.replace("$", "").strip()
    return f"${text}$" if text else ""


def _outside_math_text(value: str) -> str:
    """Drop ``\\text{...}`` so a prose hamza is not treated as a math letter."""
    out: list[str] = []
    index = 0
    while index < len(value):
        if value.startswith("\\text{", index):
            close_at = index + 6
            depth = 1
            while close_at < len(value) and depth:
                if value[close_at] == "{":
                    depth += 1
                elif value[close_at] == "}":
                    depth -= 1
                close_at += 1
            out.append("\x00")
            index = close_at
            continue
        out.append(value[index])
        index += 1
    return "".join(out)


# Tatweel sits between alef and yeh in Unicode, so a letter class must leave it out.
_AR_MATH_LETTER = r"[\u0621-\u063A\u0641-\u064A\u0671]"
_AR_MATH_TOKEN = re.compile(rf"({_AR_MATH_LETTER}\u0640*)")


def _glued_run_is_prose_word(run: str, source_rows: list[dict[str, Any]] | None) -> bool:
    """True when the book prints ``run`` as a word in prose, not as point names.

    ``نق`` in ``لدائرة طول نصف قطرها نق`` is the radius word. ``ابجـ`` copied
    from a symbol field, or ``أب`` inside ``$\\overline{أب}$``, is still a run
    of point letters.
    """
    if not run or not source_rows:
        return False
    letter = r"[\u0621-\u063A\u0641-\u064A\u0671\u0640]"
    word = re.compile(rf"(?<!{letter}){re.escape(run)}(?!{letter})")
    for row in source_rows:
        for field in row.get("quotes") or []:
            quote = str(field)
            prose = re.sub(r"\$\$.+?\$\$|\$.+?\$", " ", quote)
            if prose.strip() == run:
                continue
            if word.search(prose):
                return True
    return False


def _symbol_form_problems(
    form: str, source_rows: list[dict[str, Any]] | None = None
) -> list[str]:
    """A symbol uses ا, and adjacent point letters have exactly one space."""
    problems: list[str] = []
    visible = _outside_math_text(form)
    if re.search(r"[أإآ]", visible):
        problems.append(f"{form!r} uses أ إ or آ inside maths; write ا")
    tokens = list(_AR_MATH_TOKEN.finditer(visible))
    index = 0
    while index < len(tokens) - 1:
        left, right = tokens[index], tokens[index + 1]
        gap = visible[left.end() : right.start()]
        if gap == "":
            end = index + 1
            while end < len(tokens) - 1 and visible[tokens[end].end() : tokens[end + 1].start()] == "":
                end += 1
            run = visible[tokens[index].start() : tokens[end].end()]
            if not _glued_run_is_prose_word(run, source_rows):
                problems.append(f"{form!r} runs Arabic letters together; write one space between them")
                break
            index = end + 1
            continue
        if gap.strip() == "" and gap != " ":
            problems.append(f"{form!r} has more than one space between Arabic letters")
            break
        index += 1
    return problems


def _symbol_arabic_is_allowed(arabic: str, source_rows: list[dict[str, Any]]) -> bool:
    """A set stays ``\\mathbb{X}``. A symbol the book prints with no Arabic letter may too."""
    if _ARABIC_LETTER.search(arabic) or re.search(r"\\mathbb\{", arabic):
        return True
    key = _symbol_match_key(arabic)
    for row in source_rows:
        for field in (row.get("arabic"), *(row.get("also") or [])):
            text = str(field)
            if _symbol_match_key(text) == key and not _ARABIC_LETTER.search(text):
                return True
    return False


def _form_is_printed(form: str, rows: list[dict[str, Any]], *, kind: str = "") -> bool:
    key = _symbol_match_key if kind == "symbol" else _printed_key
    folded = key(form)
    if not folded:
        return False
    for row in rows:
        printed = [str(row.get("arabic") or ""), *list(row.get("also") or []), *list(row.get("quotes") or [])]
        for item in printed:
            candidate = key(item)
            if candidate and (folded == candidate or folded in candidate):
                return True
    return False


def _rows_by_id(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Every observation that shares an id. A repeated id is still one item."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("id") or ""), []).append(row)
    return grouped


def glossary_merge_problems(
    reply: dict[str, Any],
    rows: list[dict[str, Any]],
    notation: str,
    warnings: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Reject a merge reply that drops, invents, or duplicates a row."""
    grouped = _rows_by_id(rows)
    problems: list[str] = []
    seen: list[str] = []
    english_keys: list[tuple[str, str]] = []
    for entry in reply.get("entries") or []:
        if not isinstance(entry, dict):
            problems.append("an entry is not an object")
            continue
        ids = [str(item) for item in (entry.get("source_row_ids") or [])]
        kind = str(entry.get("kind") or "")
        english = str(entry.get("english") or "")
        arabic = str(entry.get("arabic") or "")
        if not ids:
            problems.append(f"{kind} {english!r}: entry has no source_row_ids")
        seen.extend(ids)
        english_keys.append(
            (
                kind,
                _symbol_english_key(english) if kind == "symbol" else english.casefold(),
                english,
            )
        )
        unknown = [item for item in ids if item not in grouped]
        if unknown:
            problems.append(f"unknown source row id {unknown[0]}")
        source_rows = [row for item in ids for row in grouped.get(item, [])]
        if kind in {"term", "symbol"} and entry.get("abbreviation_only"):
            problems.append(f"{kind} {english!r}: abbreviation_only must be false")
        if kind == "symbol" and notation == "latin":
            if loose_glossary_form(arabic) != loose_glossary_form(english):
                problems.append(
                    f"symbol {english!r}: under latin notation arabic must equal the English symbol"
                )
        elif kind == "symbol":
            if not _symbol_arabic_is_allowed(arabic, source_rows):
                problems.append(f"{kind} {english!r}: arabic has no Arabic letter")
        elif not _ARABIC_LETTER.search(arabic):
            problems.append(f"{kind} {english!r}: arabic has no Arabic letter")
        for form in [arabic, *[str(item) for item in (entry.get("also") or [])]]:
            if form and not _form_is_printed(form, source_rows, kind=kind):
                if kind == "term":
                    if warnings is not None:
                        warnings.append(
                            {
                                "english": english,
                                "kind": "term",
                                "chosen": arabic,
                                "reason": f"{form} is not in the source rows",
                                "warning": True,
                            }
                        )
                    continue
                problems.append(
                    f"{kind} {english!r}: {form!r} is not in the source rows' arabic, also, or quotes"
                )
        if kind == "symbol":
            for form in [arabic, *[str(item) for item in (entry.get("also") or [])]]:
                for item in _symbol_form_problems(form, source_rows):
                    problems.append(f"symbol {english!r}: {item}")
    for item in reply.get("dropped") or []:
        if not isinstance(item, dict):
            problems.append("a dropped row is not an object")
            continue
        seen.append(str(item.get("row_id") or ""))
    counts: dict[str, int] = {}
    for row_id in seen:
        if not row_id:
            problems.append("a covered id is empty")
            continue
        counts[row_id] = counts.get(row_id, 0) + 1
    for row_id, count in counts.items():
        if count > 1:
            problems.append(f"row id {row_id} appears {count} times")
    missing = [str(row.get("id") or "") for row in rows if counts.get(str(row.get("id") or ""), 0) == 0]
    if missing:
        problems.append(
            f"row id {missing[0]} is neither merged nor dropped ({len(missing)} missing)"
        )
    seen_keys: set[tuple[str, str]] = set()
    for kind, key, raw in english_keys:
        if (kind, key) in seen_keys:
            problems.append(f"two {kind} entries share english {raw!r}")
        seen_keys.add((kind, key))
    return problems


def glossary_merge_key(
    paths: list[Path],
    prompt: str,
    schema: dict[str, Any],
    model: str,
    notation: str,
) -> str:
    """Hash of the terminology files, the merge prompt, the reply schema, model and notation."""
    files = [
        {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(paths, key=lambda item: item.name)
    ]
    return semantic_fingerprint(
        {
            "files": files,
            "prompt": prompt,
            "schema": schema,
            "model": model,
            "notation": notation,
        }
    )


def book_map_from_glossary_reply(
    book_id: str,
    profile: dict[str, str],
    reply: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    merge_key: str,
) -> dict[str, Any]:
    """Store the model's glossary. Terms and symbols keep an empty ``also``."""
    grouped = _rows_by_id(rows)
    entries: list[dict[str, Any]] = []
    for raw in reply.get("entries") or []:
        kind = str(raw.get("kind") or "")
        english = str(raw.get("english") or "")
        arabic = str(raw.get("arabic") or "")
        ids = [str(item) for item in (raw.get("source_row_ids") or [])]
        source = [row for item in ids for row in grouped.get(item, [])]
        term_or_symbol = kind in {"term", "symbol"}
        entries.append(
            {
                "id": _stable_id(kind, english if kind == "symbol" else english.casefold()),
                "kind": kind,
                "source_plain": "" if kind == "symbol" else english,
                "source_math": "" if kind == "term" else english,
                "preferred": arabic,
                "also": [] if term_or_symbol else [str(item) for item in (raw.get("also") or [])],
                "abbreviation_only": False if term_or_symbol else bool(raw.get("abbreviation_only")),
                "source_row_ids": ids,
                "reason": str(raw.get("reason") or ""),
                "status": (
                    "confirmed"
                    if any(row.get("status") == "confirmed" for row in source)
                    else "inferred"
                ),
                "sections": sorted({str(row.get("section") or "") for row in source if row.get("section")}),
            }
        )
    semantic = [
        {
            key: entry[key]
            for key in (
                "id",
                "kind",
                "source_plain",
                "source_math",
                "preferred",
                "also",
                "abbreviation_only",
            )
        }
        for entry in entries
    ]
    return {
        "schema_version": MAP_SCHEMA_VERSION,
        "merge_version": 3,
        "glossary_version": 1,
        "unit_policy": "glossary",
        "status": "ready",
        "book_id": book_id,
        "merge_key": merge_key,
        "profile": {
            key: profile.get(key, "")
            for key in ("language_code", "numerals", "notation")
        },
        "sections_merged": sorted(
            {section for entry in entries for section in entry.get("sections") or []}
        ),
        "sections_failed": {},
        "entries": entries,
        "semantic_fingerprint": semantic_fingerprint(semantic),
    }


def join_split_fragments(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep every observation from a split terminology call. The first copy of an id wins."""
    observations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for part in parts:
        for row in part.get("observations") or []:
            if not isinstance(row, dict):
                continue
            item_id = str(row.get("id") or "")
            if item_id in seen:
                continue
            seen.add(item_id)
            observations.append(row)
    return observations



def _plain_symbol(value: str) -> str:
    text = value.replace("$", "")
    text = _TEXT_BODY.sub(r"\1", text)
    return _normalized(text)


def normalize_symbol_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Wrap symbol English in ``$…$``, strip letter ``\\text{}``, and split composites."""
    english = str(row.get("english") or "")
    arabic = _strip_letter_text(str(row.get("arabic") or ""))
    english_parts = [_wrap_symbol_english(part) for part in _split_top_level(english)]
    if not english_parts:
        english_parts = [_wrap_symbol_english(english)]
    arabic_parts = _split_top_level(arabic)
    also = [_strip_letter_text(str(form)) for form in (row.get("also") or [])]
    if len(english_parts) > 1 and len(arabic_parts) == len(english_parts):
        rows: list[dict[str, Any]] = []
        item_id = str(row.get("id") or "")
        for index, part in enumerate(english_parts):
            item = dict(row)
            item["id"] = f"{item_id}#{index + 1}"
            item["english"] = part
            item["arabic"] = arabic_parts[index]
            split_also: list[str] = []
            for form in also:
                pieces = _split_top_level(form)
                if len(pieces) == len(english_parts):
                    split_also.append(pieces[index])
                else:
                    split_also.append(form)
            item["also"] = split_also
            rows.append(item)
        return rows
    if len(english_parts) > 1 or len(arabic_parts) > 1:
        print(
            f"  glossary input: kept composite symbol row {row.get('id')} "
            f"({len(english_parts)} English / {len(arabic_parts)} Arabic parts)"
        )
    item = dict(row)
    item["english"] = (
        ", ".join(english_parts) if len(english_parts) > 1 else (english_parts[0] if english_parts else "")
    )
    item["arabic"] = arabic
    item["also"] = also
    return [item]


def _split_top_level(value: str) -> list[str]:
    """Split on commas that are outside braces, brackets, and parentheses."""
    parts: list[str] = []
    depth_brace = depth_paren = depth_brack = 0
    start = 0
    for index, char in enumerate(value):
        if char == "{":
            depth_brace += 1
        elif char == "}" and depth_brace:
            depth_brace -= 1
        elif char == "(":
            depth_paren += 1
        elif char == ")" and depth_paren:
            depth_paren -= 1
        elif char == "[":
            depth_brack += 1
        elif char == "]" and depth_brack:
            depth_brack -= 1
        elif char in ",،" and depth_brace == depth_paren == depth_brack == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return [part for part in parts if part]


def glossary_entries(book_map: dict[str, Any]) -> list[dict[str, Any]]:
    """One serialised glossary object per term, symbol item, or unit."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in book_map.get("entries") or []:
        kind = str(entry.get("kind") or "")
        if kind not in {"term", "symbol", "unit"}:
            continue
        if kind == "term":
            english = _normalized(str(entry.get("source_plain") or ""))
            arabic = str(entry.get("preferred") or "")
            emitted = [(english, arabic)]
        else:
            english_plain = _plain_symbol(str(entry.get("source_math") or entry.get("source_plain") or ""))
            arabic_plain = _plain_symbol(str(entry.get("preferred") or ""))
            english_parts = _split_top_level(english_plain)
            arabic_parts = _split_top_level(arabic_plain)
            if len(english_parts) > 1:
                emitted = []
                for index, item in enumerate(english_parts):
                    arabic_item = (
                        arabic_parts[index] if len(arabic_parts) == len(english_parts) else arabic_plain
                    )
                    emitted.append((_plain_symbol(item), _plain_symbol(arabic_item)))
            else:
                emitted = [(english_plain, arabic_plain)]
        for english, arabic in emitted:
            key = (kind, english)
            if not english or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "kind": kind,
                    "english": english,
                    "arabic": arabic,
                    "also": [] if kind in {"term", "symbol"} else list(entry.get("also") or []),
                    "abbreviation_only": False if kind in {"term", "symbol"} else bool(entry.get("abbreviation_only")),
                }
            )
    rows.sort(key=lambda row: (row["kind"], row["english"].casefold(), row["english"]))
    return rows


def glossary_block(book_map: dict[str, Any]) -> str:
    lines = [
        "## Book glossary",
        "",
        "These are this book's own Arabic forms, one JSON object per line. Use them exactly: `arabic` is",
        "the preferred form; for a unit, an `also` form is allowed in prose. Use a glossary entry only",
        "where its concept actually appears; the English article a, preposition in, possessive 's,",
        "and point labels are not units.",
        "",
    ]
    for entry in glossary_entries(book_map):
        lines.append(json.dumps(entry, ensure_ascii=False, separators=(", ", ": ")))
    return "\n".join(lines).rstrip() + "\n"


def glossary_fp(book_map: dict[str, Any]) -> str:
    from src.translation_quality import json_fingerprint

    return json_fingerprint(glossary_entries(book_map))


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
