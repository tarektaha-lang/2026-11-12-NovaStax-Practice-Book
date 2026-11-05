"""Legacy unit localization for replaying pre-2026-10-21 logs. Never import from the live translate path.

Map-aware unit localization for accepted Arabic translation data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.legacy_units import (
    ARABIC_LETTER,
    UNIT_ALIASES,
    arabic_unit_forms_equivalent,
    arabic_unit_stem,
    canonical_unit,
    pgf_foreach_in,
    preposition_in,
    strip_tashkeel,
    temperature_scale_token,
    unmeasured_time_word,
)


TEXT_UNIT = re.compile(
    r"\\(?P<command>text|mathrm|operatorname)\{(?P<body>[^{}]*)\}"
    r"(?P<power>\s*\^\s*(?:\{[^{}]+\}|[-+]?[0-9]+))?"
)
# Digit / closer immediately before ``\text``; a leading space inside the body
# is the stored suffix marker for ``5\text{ m}``.
MEASUREMENT_BEFORE = frozenset("0123456789)]}")
MATH_SEGMENT = re.compile(r"\$\$.+?\$\$|\$.+?\$", re.DOTALL)
MATH_AT_END = re.compile(
    r"(?P<math>\$\$.+?\$\$|\$.+?\$)(?P<space>\s*-?)$",
    re.DOTALL,
)
NUMBER_AT_END = re.compile(
    r"(?P<number>[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*$"
)
VARIABLE_AT_END = re.compile(
    r"(?:(?<![A-Za-z0-9])(?P<latin>[A-Za-z])|"
    r"(?<![\u0621-\u064A\u066E-\u06D3])(?P<arabic>[\u0621-\u064A\u066E-\u06D3])"
    r"[\u064B-\u065F\u0670]*)\s*$"
)
PAREN_EXPRESSION_AT_END = re.compile(r"(?P<expression>\([^()]+\))\s*$")
ARABIC_TOKEN = re.compile(
    r"[\u0621-\u064A\u066E-\u06D3]"
    r"[\u0621-\u064A\u066E-\u06D3\u064B-\u065F\u0670]*"
)
HYPHEN_NUMBER_AT_END = re.compile(r"(?P<number>(?<![\d.])\d+(?:\.\d+)?)-$")
CLOCK_TIME_AT_END = re.compile(r"\d{1,2}\s*(?:\{:\}|:)\s*\d{2}\s*$")
LATIN_MERIDIEM = re.compile(r"(?i)^(?:a\.?\s?m\.?|p\.?\s?m\.?)$")
ARABIC_MERIDIEM_WORDS = frozenset({"صباحا", "صباح", "مساء", "مساءا", "ظهرا"})
SCALE_AT_END = re.compile(
    r"(?:(?<=\s)|(?<=\$)|^)"
    r"(?P<scale>thousands?|millions?|billions?|hundreds?|"
    r"ألف|آلاف|ألاف|مليون|ملايين|مليار|مليارات)$",
    re.I,
)
UNIT_POWER_AFTER = re.compile(r"\^\s*\{?\s*(?P<power>[23])\s*\}?")
# One-letter codes are case-sensitive (``a`` is not ampere, ``n`` is not
# newton); a lowercase litre is the one spelling English also prints.
_CASE_FOLDED_LETTERS = frozenset({("l", "L")})
_LENGTH_CANONICALS = frozenset({"m", "cm", "mm", "km", "dm", "ft", "in", "yd", "mi"})
_TIME_CANONICALS = frozenset({"h", "s", "min"})
_TASHKEEL = frozenset(chr(code) for code in range(0x064B, 0x0660)) | {"\u0670"}


@dataclass(frozen=True)
class UnitOccurrence:
    canonical: str
    power: str


def _compact(value: str) -> str:
    return " ".join(value.strip().split())


def _letter_case_matches(token: str, canonical: str | None) -> bool:
    """A one-letter Latin token names a unit only in that unit's own case."""
    if canonical is None or len(token) != 1 or not token.isascii():
        return True
    return token == canonical or (token, canonical) in _CASE_FOLDED_LETTERS


def meridiem(prefix: str, body: str) -> bool:
    """True for a.m./p.m. (``ص``/``م`` after a clock time), never a unit."""
    compact = strip_tashkeel(_compact(body)).replace("ـ", "")
    if LATIN_MERIDIEM.fullmatch(compact):
        return True
    if compact in ARABIC_MERIDIEM_WORDS:
        return True
    return compact in {"ص", "م"} and bool(CLOCK_TIME_AT_END.search(prefix))


def text_command_is_measurement(text: str, found: re.Match[str]) -> bool:
    """Whether a ``\\text`` / ``\\mathrm`` run is a unit, not a label or function.

    Point names such as ``\\text{A}`` and the angle-measure function
    ``\\text{m}(\\angle A)`` share spellings with amperes and metres.  A
    following ``(`` is a function, not a unit.  A one-letter body that is not
    Latin uppercase (``m``, ``s``, ``م``) is a unit even in a list such as
    ``$\\text{km}, \\text{m}$``.  Uppercase one-letter bodies still need a
    quantity immediately before the command, so ``\\text{A}`` stays a point.
    """
    body = found.group("body")
    compact = _compact(body)
    power = found.group("power") or ""
    after = text[found.end() :].lstrip()
    if after.startswith("("):
        return False
    if not power and after.startswith("_"):
        return False
    prefix = text[: found.start()].rstrip()
    if temperature_scale_token(prefix, compact):
        return False
    if meridiem(prefix, body):
        return False
    if power or body[:1].isspace() or "/" in compact or len(compact) > 1:
        return True
    if compact.isalpha() and not compact.isupper():
        return True
    return bool(prefix and prefix[-1] in MEASUREMENT_BEFORE)


def _power(value: str) -> str:
    return re.sub(r"[\s^{}]", "", value or "")


def _lexical_power(token: str) -> str:
    """Area/volume power only when the remainder is a known length unit.

    ``square meters`` is m^2; ``square units`` is not.
    """
    folded = re.sub(r"\s+", " ", re.sub(r"\.", "", token.casefold())).strip()
    for prefix, power in (("square ", "2"), ("cubic ", "3"), ("sq ", "2")):
        if not folded.startswith(prefix):
            continue
        rest = folded[len(prefix) :].strip()
        if canonical_unit(rest) in _LENGTH_CANONICALS:
            return power
    return ""


def _letter_measure(prefix: str, token: str = "") -> bool:
    """True when a single-letter variable sits immediately before a unit.

    Latin ``x cm`` and Arabic ``س سم`` are lengths.  A one-letter Arabic SI
    form (``م``, ``ث``, ``س``) after another Arabic letter is a point or line
    name (``د م``, ``و م``), not metres or seconds.
    """
    found = VARIABLE_AT_END.search(prefix)
    if found is None:
        return False
    if (
        token
        and ARABIC_LETTER.search(token)
        and len(strip_tashkeel(token)) == 1
        and found.group("arabic")
    ):
        return False
    # ``a centimetre`` / ``a square meter`` is the article before a spelled
    # unit word; ``a cm`` keeps the variable reading.
    if (
        found.group("latin") in {"a", "A"}
        and token
        and len(token) > 2
        and canonical_unit(token) not in (None, token)
    ):
        return False
    return True


def unit_forms(
    projection: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], set[str]]:
    """Canonical-to-Arabic and Arabic-to-canonical forms in one map projection."""
    forward: dict[str, str] = {}
    reverse: dict[str, str] = {}
    surfaces: set[str] = set()
    for row in projection.get("entries") or []:
        if row.get("kind") != "unit":
            continue
        source = str(
            row.get("source_math")
            or row.get("canonical")
            or (row.get("source") or {}).get("canonical")
            or ""
        ).strip()
        target = str(
            row.get("math_text")
            or row.get("arabic_math")
            or (row.get("target") or {}).get("math_text")
            or ""
        ).strip()
        if source and target:
            forward[source] = target
            lemma = str(
                row.get("plain_lemma")
                or (row.get("target") or {}).get("plain_lemma")
                or ""
            ).strip()
            extras = [
                str(item).strip()
                for item in (row.get("also") or [])
                if str(item).strip()
            ]
            surfaces.update({target, lemma} - {""})
            target_forms = {target, lemma, *extras} - {""}
            for target_form in target_forms:
                previous = reverse.get(target_form)
                if previous is not None and previous != source:
                    raise ValueError(
                        f"Arabic unit form {target_form!r} maps to both "
                        f"{previous!r} and {source!r}"
                    )
                reverse[target_form] = source
    return forward, reverse, surfaces


def _variants(
    forward: dict[str, str], reverse: dict[str, str]
) -> list[str]:
    aliases = {
        alias
        for alias, canonical in UNIT_ALIASES.items()
        if canonical in forward
    }
    aliases |= {alias.upper() for alias in aliases}
    return sorted(set(forward) | set(reverse) | aliases, key=len, reverse=True)


def _word(char: str) -> bool:
    """True for letters, digits, and tashkeel so ``رُسم`` is one token, not ``ر`` + ``سم``."""
    return bool(char) and (
        char.isalpha() or char.isdecimal() or char == "_" or char in _TASHKEEL
    )


def _variant_at(
    text: str,
    index: int,
    variants: list[str],
) -> str | None:
    """The longest whole-token source or Arabic unit at ``index``."""
    for value in variants:
        actual = text[index : index + len(value)]
        if actual == value:
            matched = value
        elif (
            actual
            and not ARABIC_LETTER.search(value)
            and actual.casefold() == value.casefold()
            and _letter_case_matches(actual, canonical_unit(value))
        ):
            matched = actual
        else:
            continue
        before = text[index - 1] if index else ""
        stop = index + len(matched)
        after = text[stop] if stop < len(text) else ""
        if _word(matched[0]) and _word(before):
            continue
        if _word(matched[-1]) and _word(after):
            continue
        return matched
    return None


def _unit_token_at(
    text: str,
    index: int,
    variants: list[str],
    forward: dict[str, str],
    reverse: dict[str, str],
) -> str | None:
    """Longest map unit at ``index``, including inflected Arabic runs."""
    found = _variant_at(text, index, variants)
    before = text[index - 1] if index else ""
    if _word(before):
        return found
    arabic = ARABIC_TOKEN.match(text, index)
    if arabic is None:
        return found
    run = arabic.group(0)
    if _canonical_token(run, forward, reverse) is None:
        return found
    if found is None or len(run) >= len(found):
        return run
    return found


def _canonical_token(
    token: str, forward: dict[str, str], reverse: dict[str, str]
) -> str | None:
    if token in reverse:
        return reverse[token]
    source = canonical_unit(token)
    if source in forward and _letter_case_matches(token, source):
        return source
    if not ARABIC_LETTER.search(token):
        return None
    stripped = strip_tashkeel(token)
    if stripped in reverse:
        return reverse[stripped]
    stemmed = arabic_unit_stem(stripped)
    if stemmed in reverse:
        return reverse[stemmed]
    for form, canonical in reverse.items():
        if ARABIC_LETTER.search(form) and arabic_unit_forms_equivalent(
            stripped, form
        ):
            return canonical
    return None


def _map_surface_form(token: str, surfaces: set[str]) -> bool:
    return strip_tashkeel(token) in surfaces


def _canonical_expression(
    body: str, forward: dict[str, str], reverse: dict[str, str]
) -> str | None:
    """A complete simple/compound unit body as canonical IDs."""
    compact = _compact(body)
    if compact in reverse:
        return reverse[compact]
    if ARABIC_LETTER.search(compact) and "/" not in compact:
        stemmed = _canonical_token(compact, forward, reverse)
        if stemmed is not None:
            return stemmed
    direct = canonical_unit(compact)
    if direct in forward and _letter_case_matches(compact, direct) and (
        compact in forward
        or body[:1].isspace()
        or (" " in compact and compact.casefold() in UNIT_ALIASES)
    ):
        return direct

    variants = (
        _variants(forward, reverse)
        if body[:1].isspace()
        else sorted(set(forward) | set(reverse), key=len, reverse=True)
    )
    tokens: list[str] = []
    index = 0
    expect_unit = True
    while index < len(compact):
        if compact[index].isspace():
            index += 1
            continue
        if not expect_unit:
            if compact[index] != "/":
                return None
            tokens.append("/")
            expect_unit = True
            index += 1
            continue
        token = _unit_token_at(compact, index, variants, forward, reverse)
        if token is None:
            return None
        canonical = _canonical_token(token, forward, reverse)
        if canonical is None:
            return None
        tokens.append(canonical)
        expect_unit = False
        index += len(token)
    if expect_unit or not tokens:
        return None
    return "".join(tokens)


def _arabic_expression(canonical: str, forward: dict[str, str]) -> str:
    if canonical in forward:
        return forward[canonical]
    parts = canonical.split("/")
    if not parts or any(part not in forward for part in parts):
        raise ValueError(f"book map lacks a component of unit {canonical!r}")
    return "/".join(forward[part] for part in parts)


def _canonical_exact(
    body: str, forward: dict[str, str], reverse: dict[str, str]
) -> str | None:
    return _canonical_expression(body, forward, reverse)


def _embedded_unit(
    body: str,
    index: int,
    token: str,
    canonical: str | None,
    forward: dict[str, str],
    reverse: dict[str, str],
) -> bool:
    """Whether a unit spelling inside a longer ``\\text`` body is a unit there.

    Counting and rewriting share this, so the checker can never count a unit
    the stored text does not carry, or rewrite a label it did not count.
    """
    if canonical is None:
        return False
    after = body[index + len(token) :].lstrip()
    prefix = body[:index].rstrip()
    measured = bool(prefix and prefix[-1] in "0123456789)]")
    if preposition_in(token, after, measured=measured):
        return False
    if unmeasured_time_word(token, measured=measured):
        return False
    if canonical in _TIME_CANONICALS and not measured:
        return False
    # ``Grade A`` / ``Model A`` / ``د م``: a lone letter in wording is a label.
    if len(strip_tashkeel(token)) == 1 and not measured:
        return False
    alias = (
        token not in forward
        and token not in reverse
        and not ARABIC_LETTER.search(token)
    )
    alias_context = bool(
        prefix
        and (
            prefix[-1] in "0123456789)]"
            or (token.isupper() and len(token) > 1 and prefix[-1] == "(")
        )
    ) or (token.isupper() and len(token) > 1)
    return not alias or alias_context


def _embedded_units(
    body: str, forward: dict[str, str], reverse: dict[str, str]
) -> list[str]:
    exact = _canonical_exact(body, forward, reverse)
    if exact is not None:
        return [exact]
    variants = _variants(forward, reverse)
    result: list[str] = []
    index = 0
    while index < len(body):
        token = _unit_token_at(body, index, variants, forward, reverse)
        if token is None:
            index += 1
            continue
        canonical = _canonical_token(token, forward, reverse)
        if _embedded_unit(body, index, token, canonical, forward, reverse):
            result.append(canonical)
        index += len(token)
    return result


def _text_command_measured(text: str, found: re.Match[str]) -> bool:
    """True when ``\\text`` sits after a quantity or stores a leading-space suffix."""
    body = found.group("body")
    prefix = text[: found.start()].rstrip()
    return bool(prefix and prefix[-1] in MEASUREMENT_BEFORE) or body[:1].isspace()


def math_unit_occurrences(
    text: str, projection: dict[str, Any]
) -> list[UnitOccurrence]:
    """Ordered unit identities/powers carried by mathematical text runs."""
    forward, reverse, _surfaces = unit_forms(projection)
    occurrences: list[UnitOccurrence] = []
    for found in TEXT_UNIT.finditer(text):
        if not text_command_is_measurement(text, found):
            continue
        body = found.group("body")
        units = _embedded_units(body, forward, reverse)
        exact = _canonical_exact(body, forward, reverse)
        if exact in _TIME_CANONICALS:
            if not _text_command_measured(text, found):
                units = []
        else:
            units = [unit for unit in units if unit not in _TIME_CANONICALS]
        power = (
            _power(found.group("power") or "")
            or _lexical_power(_compact(body))
        ) if len(units) == 1 else ""
        occurrences.extend(UnitOccurrence(unit, power) for unit in units)
    return occurrences


def prose_unit_occurrences(
    text: str, projection: dict[str, Any]
) -> list[UnitOccurrence]:
    """Recognized measurement wording outside mathematics."""
    forward, reverse, _surfaces = unit_forms(projection)
    return [
        UnitOccurrence(canonical, power)
        for _, _, canonical, power, _ in _prose_unit_matches(
            text, forward, reverse
        )
    ]


def unit_occurrences(text: str, projection: dict[str, Any]) -> list[UnitOccurrence]:
    """All recognized units, for inventory projection rather than strict comparison."""
    return [
        *math_unit_occurrences(text, projection),
        *prose_unit_occurrences(text, projection),
    ]


def _replace_body(
    body: str,
    forward: dict[str, str],
    reverse: dict[str, str],
    *,
    to_arabic: bool,
) -> str:
    exact = _canonical_exact(body, forward, reverse)
    if exact is not None:
        value = _arabic_expression(exact, forward) if to_arabic else exact
        # A leading space is the stored marker for a standalone unit suffix.
        return " " + value
    variants = _variants(forward, reverse)
    pieces: list[str] = []
    cursor = 0
    index = 0
    while index < len(body):
        token = _unit_token_at(body, index, variants, forward, reverse)
        if token is None:
            index += 1
            continue
        canonical = _canonical_token(token, forward, reverse)
        if not _embedded_unit(body, index, token, canonical, forward, reverse):
            index += len(token)
            continue
        pieces.append(body[cursor:index])
        pieces.append(forward[canonical] if to_arabic else canonical)
        cursor = index + len(token)
        index = cursor
    pieces.append(body[cursor:])
    return "".join(pieces)


def _rewrite(text: str, projection: dict[str, Any], *, to_arabic: bool) -> str:
    forward, reverse, surfaces = unit_forms(projection)

    def one(found: re.Match[str]) -> str:
        if not text_command_is_measurement(text, found):
            return found.group(0)
        body = found.group("body")
        exact = _canonical_exact(body, forward, reverse)
        if exact in _TIME_CANONICALS and not _text_command_measured(text, found):
            return found.group(0)
        body = _replace_body(
            body, forward, reverse, to_arabic=to_arabic
        )
        power = found.group("power") or ""
        if not power:
            lexical = _lexical_power(_compact(found.group("body")))
            power = f"^{lexical}" if lexical else ""
        return (
            rf"\text{{{body}}}"
            + power
        )

    rewritten = TEXT_UNIT.sub(one, text)
    matches = _prose_unit_matches(rewritten, forward, reverse)
    if not matches:
        return rewritten
    pieces: list[str] = []
    cursor = 0
    for start, stop, canonical, power, lead in matches:
        token = rewritten[start + len(lead) : stop].strip()
        if (
            to_arabic
            and ARABIC_LETTER.search(token)
            and not _map_surface_form(token, surfaces)
        ):
            pieces.append(rewritten[cursor:stop])
            cursor = stop
            continue
        prefix = rewritten[cursor:start]
        value = forward[canonical] if to_arabic else canonical
        if lead:
            value = f"{lead} {value}"
        adjacent = MATH_AT_END.search(prefix)
        if adjacent is not None:
            math = adjacent.group("math")
            opened = 2 if math.startswith("$$") else 1
            joined = (
                math[:-opened]
                + rf"\text{{ {value}}}"
                + (f"^{power}" if power else "")
                + math[-opened:]
            )
            pieces.append(prefix[: adjacent.start()] + joined)
        else:
            number = HYPHEN_NUMBER_AT_END.search(prefix) or NUMBER_AT_END.search(prefix)
            if number is not None:
                suffix = f"^{power}" if power else ""
                pieces.append(
                    prefix[: number.start()]
                    + f"${number.group('number')}"
                    + rf"\text{{ {value}}}"
                    + suffix
                    + "$"
                )
            else:
                expression = PAREN_EXPRESSION_AT_END.search(prefix)
                if expression is not None:
                    suffix = f"^{power}" if power else ""
                    pieces.append(
                        prefix[: expression.start()]
                        + f"${expression.group('expression')}"
                        + rf"\text{{ {value}}}"
                        + suffix
                        + "$"
                    )
                else:
                    variable = VARIABLE_AT_END.search(prefix)
                    if variable is not None:
                        name = variable.group("latin") or variable.group("arabic")
                        suffix = f"^{power}" if power else ""
                        pieces.append(
                            prefix[: variable.start()]
                            + f"${name}"
                            + rf"\text{{ {value}}}"
                            + suffix
                            + "$"
                        )
                    else:
                        pieces.append(prefix)
                        pieces.append(
                            value
                            + (
                                {"2": "²", "3": "³"}.get(power, f"^{power}")
                                if power
                                else ""
                            )
                        )
        cursor = stop
    pieces.append(rewritten[cursor:])
    return "".join(pieces)


def _prose_unit_matches(
    text: str,
    forward: dict[str, str],
    reverse: dict[str, str],
) -> list[tuple[int, int, str, str]]:
    """Suffix units in printable prose, excluding every math island."""
    variants = _variants(forward, reverse)
    maths = [match.span() for match in MATH_SEGMENT.finditer(text)]
    protected_spans = sorted(
        [*maths, *(match.span() for match in TEXT_UNIT.finditer(text))]
    )

    def protected(index: int) -> bool:
        return any(start <= index < stop for start, stop in protected_spans)

    def digit_math_before(position: int) -> bool:
        """A digit-bearing island ends at ``position`` (``$24$ hour``, ``$24$-hour``)."""
        for start, stop in reversed(maths):
            if stop > position:
                continue
            if text[stop:position].strip() not in {"", "-"}:
                return False
            opened = 2 if text.startswith("$$", start) else 1
            return bool(re.search(r"\d", text[start + opened : stop - opened]))
        return False

    def number_before(prefix: str) -> bool:
        return bool(prefix and prefix[-1] in "0123456789)]")

    matches: list[tuple[int, int, str, str, str]] = []
    index = 0
    while index < len(text):
        if protected(index):
            index = next(
                stop
                for start, stop in protected_spans
                if start <= index < stop
            )
            continue
        token = _unit_token_at(text, index, variants, forward, reverse)
        if token is None:
            index += 1
            continue
        prefix = text[:index].rstrip()
        # ``$980$ thousand LE`` / ``$980$ ألف جنيه``: the scale word belongs to
        # the quantity, so the unit is measured by the number before it.
        lead_start, lead = index, ""
        scale = SCALE_AT_END.search(prefix)
        if scale is not None:
            before_scale = text[: scale.start("scale")].rstrip()
            if number_before(before_scale) or digit_math_before(scale.start("scale")):
                prefix = before_scale
                lead_start, lead = scale.start("scale"), scale.group("scale")
        # A prose unit is a measurement suffix, never an isolated variable,
        # English preposition, or mapped lemma standing as ordinary wording.
        # Parenthesized algebra and normalized $number$ islands are accepted.
        # ``24-hour`` is the compound adjective for a 24 h quantity.
        direct_measure = number_before(prefix) or (
            not lead and bool(HYPHEN_NUMBER_AT_END.search(text[:index]))
        )
        variable_measure = _letter_measure(prefix, token) or bool(
            re.search(r"=\s*[A-Za-z]+$", prefix)
        )
        math_measure = digit_math_before(lead_start)
        explicit_code = bool(
            token.isupper()
            and len(token) > 1
            and token not in reverse
        )
        if not direct_measure and not math_measure and not explicit_code and not variable_measure:
            index += len(token)
            continue
        canonical = _canonical_token(token, forward, reverse)
        if canonical is not None:
            after = text[index + len(token) :].lstrip()
            follows_word = bool(
                after and (after[0].isalpha() or after[0] in "$\\")
            )
            if pgf_foreach_in(prefix, token):
                index += len(token)
                continue
            if (
                canonical == "in"
                and token.casefold() == "in"
                and (
                    follows_word
                    or (not direct_measure and not math_measure and not variable_measure)
                )
            ):
                index += len(token)
                continue
            if unmeasured_time_word(
                token, measured=direct_measure or math_measure
            ):
                index += len(token)
                continue
            power = _lexical_power(token)
            match_stop = index + len(token)
            if not power and match_stop < len(text):
                power = {"²": "2", "³": "3"}.get(text[match_stop], "")
                if power:
                    match_stop += 1
                elif written := UNIT_POWER_AFTER.match(text, match_stop):
                    power = written.group("power")
                    match_stop = written.end()
            matches.append(
                (lead_start, match_stop, canonical, power, lead)
            )
        index += len(token)
    return matches


def blank_unit_text_commands(text: str, projection: dict[str, Any]) -> str:
    """Clear mapped unit bodies inside \\text/\\mathrm so word checks ignore them."""
    forward, reverse, _surfaces = unit_forms(projection)

    def one(found: re.Match[str]) -> str:
        if _canonical_expression(
            _compact(found.group("body")), forward, reverse
        ) is None:
            return found.group(0)
        return rf"\{found.group('command')}{{}}" + (found.group("power") or "")

    return TEXT_UNIT.sub(one, text)


def canonicalize_units(text: str, projection: dict[str, Any]) -> str:
    """Replace mapped or source unit spellings with canonical source IDs."""
    return _rewrite(text, projection, to_arabic=False)


def normalize_translation_units(
    source: str,
    target: str,
    projection: dict[str, Any],
    where: str = "field",
) -> str:
    """Validate mathematical unit identity, then store mapped Arabic forms.

    Prose may inflect, repeat, or lexicalize a unit differently in Arabic.
    Mathematical quantities remain fail-closed after adjacent prose suffixes are
    first folded into their canonical math expression.
    """
    source_canonical = _rewrite(source, projection, to_arabic=False)
    target_canonical = _rewrite(target, projection, to_arabic=False)
    source_units = math_unit_occurrences(source_canonical, projection)
    target_units = math_unit_occurrences(target_canonical, projection)
    if source_units != target_units:
        raise ValueError(
            f"{where}: mathematical unit occurrences differ: "
            f"{source_units!r} -> {target_units!r}"
        )
    return _rewrite(target, projection, to_arabic=True)
