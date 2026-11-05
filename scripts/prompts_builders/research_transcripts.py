#!/usr/bin/env python3
"""Research transcript blocks and assemble the transcript prompt.

One call writes the spoken mathematics block and the spoken chemistry block for
a single country, book language, and notation taken from that book's XML.
Assembly places those blocks into prompts/transcripts/core.md and appends the
glyph rule for the book's notation and numerals.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from src.prompting import (
    LANGUAGE_ATTR,
    LANGUAGES,
    NOTATION_ATTR,
    Profile,
    Prompt,
    profile_problems,
    relative,
)

PROJECT_ROOT = SCRIPTS_DIR.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts" / "transcripts"
AUDIOS_DIR = PROJECT_ROOT / "prompts" / "audios"
DATA_TEMP_DIR = PROJECT_ROOT / "data" / "temp"
ISO_JSON_PATH = SCRIPTS_DIR / "iso3166_alpha2.json"

SYSTEM_PROMPT_PATH = PROMPTS_DIR / "research_system.txt"
USER_PROMPT_PATH = PROMPTS_DIR / "research_user.txt"
SCHEMA_PATH = PROMPTS_DIR / "research_schema.json"

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING = "high"
_TOOL_CALL_LIMIT = "too many tool calls"
_SEARCH_RETRY = (
    "\n\nThe previous attempt was rejected: Model generated too many tool calls. "
    "Search only a few times for the classroom habit, then fill the schema."
)
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
USER_SLOTS = (
    "{{country_code}}",
    "{{country_name}}",
    "{{language_code}}",
    "{{language_name}}",
    "{{notation}}",
    "{{glyph_rule}}",
)
SPOKEN_FIELDS = (
    "spoken_variety",
    "explanation_language_code",
    "spoken_math",
    "spoken_chemistry",
)

LOGGER = logging.getLogger("research_transcripts")

CORE_TEMPLATE = PROMPTS_DIR / "core.md"
INPUT_DIR = PROJECT_ROOT / "data" / "input"

_KEY = r"[A-Za-z_][A-Za-z0-9_]*"
_LINE_BLOCK = re.compile(
    rf"(?m)^\{{\{{#(?P<key>{_KEY})\}}\}}\n"
    rf"(?P<body>(?:(?!\{{\{{#).)*?)\n"
    rf"\{{\{{/(?P=key)\}}\}}[ \t]*(?P<nl>\n)?"
)
_INLINE_BLOCK = re.compile(
    rf"\{{\{{#(?P<key>{_KEY})\}}\}}"
    rf"(?P<body>(?:(?!\{{\{{#).)*?)"
    rf"\{{\{{/(?P=key)\}}\}}"
)
_PLACEHOLDER = re.compile(rf"\{{\{{({_KEY})\}}\}}")
_MARKER = re.compile(rf"\{{\{{[#/]({_KEY})\}}\}}")


def is_empty(value: object) -> bool:
    """Return whether a researched field was left unset.

    Args:
        value: The JSON value for one template key.

    Returns:
        True when the model omitted the field, sent null, or sent a blank string.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def fill(template: str, values: Mapping[str, object]) -> str:
    """Substitute ``values`` into ``template`` and drop clauses whose key is empty.

    Args:
        template: Prompt text with ``{{key}}`` placeholders and optional
            ``{{#key}}...{{/key}}`` clauses.
        values: Researched fields. A string fills ``{{key}}``. None, a blank
            string, or a missing key drops the clause controlled by that key.

    Returns:
        The template with every supplied value in place and every empty clause removed.

    Raises:
        TypeError: A provided value is neither a string nor null.
        ValueError: A placeholder or block marker remains, or a block is unbalanced.
    """
    for key, value in values.items():
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{key} must be a string or null, not {type(value).__name__}")
    text = template
    while True:
        updated = _LINE_BLOCK.sub(lambda match: _keep_or_drop(match, values, line=True), text)
        updated = _INLINE_BLOCK.sub(lambda match: _keep_or_drop(match, values, line=False), updated)
        if updated == text:
            break
        text = updated
    if _MARKER.search(text):
        raise ValueError("template has an unbalanced conditional block")
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = values.get(key)
        if is_empty(value):
            missing.append(key)
            return match.group(0)
        return str(value)

    text = _PLACEHOLDER.sub(replace, text)
    if missing:
        names = ", ".join(sorted(set(missing)))
        raise ValueError(f"template still needs a value for: {names}")
    return text


def fill_file(template_path: Path, values: Mapping[str, object]) -> str:
    """Fill the template stored at ``template_path``."""
    return fill(template_path.read_text(encoding="utf-8"), values)


def _keep_or_drop(match: re.Match[str], values: Mapping[str, object], *, line: bool) -> str:
    """Return the block body when its key has text, and remove it otherwise."""
    if is_empty(values.get(match.group("key"))):
        return ""
    body = match.group("body")
    if line and match.group("nl"):
        return f"{body}\n"
    return body


def spoken_json_path(country_code: str, language_code: str, notation: str) -> Path:
    """The spoken mathematics and chemistry blocks for one book."""
    return PROMPTS_DIR / "spoken" / f"{country_code}_{language_code}_{notation}.json"


def glyph_path(notation: str) -> Path:
    """The fixed glyph rule for one mathematics notation."""
    return PROMPTS_DIR / "notation" / f"{notation}.txt"


def numerals_path(numerals: str) -> Path:
    """The fixed digit rule for one numeral system."""
    return PROMPTS_DIR / "numerals" / f"{numerals}.txt"


def glyph_rule(profile: Profile) -> tuple[str, tuple[Path, Path]]:
    """Notation letters and numeral digits as one transcript glyph block."""
    notation_file = glyph_path(profile.notation)
    digits_file = numerals_path(profile.numerals)
    for path in (notation_file, digits_file):
        if not path.is_file():
            raise RuntimeError(f"no glyph rule at {relative(path)}")
    text = (
        notation_file.read_text(encoding="utf-8").strip()
        + "\n\n"
        + digits_file.read_text(encoding="utf-8").strip()
    )
    return text, (notation_file, digits_file)


def _load_json(path: Path) -> dict[str, object]:
    """Read a researched JSON object."""
    if not path.is_file():
        raise RuntimeError(f"no researched values at {relative(path)}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"{relative(path)} is not a JSON object")
    return data


def assemble_transcript(meta: dict[str, str]) -> Prompt:
    """The transcript prompt for one book.

    Args:
        meta: Book attributes, including language, numerals, notation, and country.

    Returns:
        The filled prompt and the files that went into it.
    """
    profile = Profile.of(meta)
    country = str(meta.get("country_code", "")).strip().lower()
    spoken_path = spoken_json_path(country, profile.language_code, profile.notation)
    spoken = _load_json(spoken_path)
    glyph, glyph_files = glyph_rule(profile)
    text = fill(
        CORE_TEMPLATE.read_text(encoding="utf-8"),
        {
            "SPOKEN_MATH": str(spoken.get("spoken_math") or "").strip(),
            "SPOKEN_CHEMISTRY": str(spoken.get("spoken_chemistry") or "").strip(),
            "NOTATION": glyph,
        },
    )
    return Prompt(
        text,
        (
            relative(CORE_TEMPLATE),
            relative(spoken_path),
            *(relative(path) for path in glyph_files),
        ),
    )


def book_jobs() -> list[dict[str, str]]:
    """One job per distinct country, book language, and notation in data/input."""
    seen: set[tuple[str, str, str]] = set()
    jobs: list[dict[str, str]] = []
    if not INPUT_DIR.is_dir():
        return jobs
    for path in sorted(INPUT_DIR.glob("*/*.xml")):
        root = ElementTree.parse(path).getroot()
        if root.tag != "book":
            continue
        meta = {key: str(value) for key, value in root.attrib.items()}
        country = meta.get("country_code", "").strip().lower()
        language_code = meta.get(LANGUAGE_ATTR, "").strip()
        notation = meta.get(NOTATION_ATTR, "").strip()
        key = (country, language_code, notation)
        if not all(key) or key in seen:
            continue
        seen.add(key)
        meta["country_code"] = country
        jobs.append(meta)
    return jobs


def load_iso_data() -> dict[str, Any]:
    """Load country names from the project's ISO file."""
    if not ISO_JSON_PATH.exists():
        raise FileNotFoundError(f"Cannot find iso3166_alpha2.json at {ISO_JSON_PATH}")
    return json.loads(ISO_JSON_PATH.read_text(encoding="utf-8"))


def resolve_api_key() -> str:
    """Return the first configured Gemini API key."""
    for name in API_KEY_VARS:
        value = os.getenv(name)
        if value:
            return value
    raise RuntimeError(f"set one of {' / '.join(API_KEY_VARS)} to call the model")


def interaction_text(interaction: object) -> str:
    """Extract the model's answer text from an Interactions API response."""
    text = getattr(interaction, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    chunks: list[str] = []
    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for item in getattr(step, "content", None) or []:
            if getattr(item, "type", None) == "text" and getattr(item, "text", None):
                chunks.append(item.text)
    return "".join(chunks).strip()


def interaction_usage(interaction: object) -> dict[str, Any]:
    """Token counts from an Interactions API response, if the payload carries them."""
    usage = getattr(interaction, "usage", None) or getattr(interaction, "usage_metadata", None)
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return {key: value for key, value in usage.items() if value is not None}
    counts: dict[str, Any] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_token_count",
        "candidates_token_count",
        "thoughts_token_count",
    ):
        value = getattr(usage, key, None)
        if value is not None:
            counts[key] = value
    return counts


def extract_json(text: str) -> dict[str, Any]:
    """Parse JSON from a model response, stripping one markdown fence."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise json.JSONDecodeError("research response is not an object", cleaned, 0)
    return data


def validate_research(data: dict[str, Any], language_code: str, notation: str) -> list[str]:
    """Check that both spoken blocks and the variety are present.

    Args:
        data: Parsed research response.
        language_code: Printed language of the book that was requested.
        notation: Mathematics notation of that book.

    Returns:
        A list of problems. An empty list means the response can be written.
    """
    del language_code, notation
    problems: list[str] = []
    for key in SPOKEN_FIELDS:
        if is_empty(data.get(key)):
            problems.append(f"no {key}")
    return problems


def _create_interaction(
    client: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
    thinking: str,
    schema: dict[str, Any],
) -> Any:
    """Call the Interactions API, and retry once when search exceeds the tool limit.

    Args:
        client: Gemini client.
        model: Model name.
        system_prompt: Research system instruction.
        user_prompt: Filled research request.
        thinking: Interactions API thinking level.
        schema: JSON schema for the response.

    Returns:
        The completed interaction.

    Raises:
        Exception: The call failed for a reason other than the tool-call limit,
            or the retry failed as well.
    """
    prompt = user_prompt
    for attempt in (1, 2):
        try:
            return client.interactions.create(
                model=model,
                system_instruction=system_prompt,
                input=prompt,
                tools=[{"type": "google_search"}],
                generation_config={"thinking_level": thinking},
                response_format={"type": "text", "mime_type": "application/json", "schema": schema},
            )
        except Exception as error:
            if attempt == 2 or _TOOL_CALL_LIMIT not in str(error).lower():
                raise
            LOGGER.warning("search exceeded the tool-call limit; retrying once")
            prompt = user_prompt + _SEARCH_RETRY
    raise RuntimeError("research call did not return")


def research_job(
    client: Any,
    meta: dict[str, str],
    country_name: str,
    model: str,
    thinking: str,
) -> dict[str, Any]:
    """Call Gemini for one book's country, language, and notation."""
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = USER_PROMPT_PATH.read_text(encoding="utf-8")
    for token in USER_SLOTS:
        if token not in user_prompt:
            raise RuntimeError(f"{USER_PROMPT_PATH.name} has no {token} placeholder")
    language_code = meta["language_code"]
    notation = meta["notation"]
    glyph, _glyph_files = glyph_rule(Profile.of(meta))
    user_prompt = (
        user_prompt.replace("{{country_code}}", meta["country_code"])
        .replace("{{country_name}}", country_name)
        .replace("{{language_code}}", language_code)
        .replace("{{language_name}}", LANGUAGES[language_code])
        .replace("{{notation}}", notation)
        .replace("{{glyph_rule}}", glyph)
    )
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    started = time.perf_counter()
    interaction = _create_interaction(client, model, system_prompt, user_prompt, thinking, schema)
    duration = time.perf_counter() - started
    usage = interaction_usage(interaction)
    LOGGER.info(
        "research %s %s %s with %s thinking=%s in %.1fs usage=%s",
        meta["country_code"],
        language_code,
        meta["notation"],
        model,
        thinking,
        duration,
        usage or "{}",
    )
    raw_text = interaction_text(interaction)
    if not raw_text:
        raise RuntimeError("Gemini returned empty content")
    try:
        data = extract_json(raw_text)
    except json.JSONDecodeError as exc:
        LOGGER.error("Failed to parse JSON from Gemini. Raw content follows:")
        LOGGER.error("--- RAW START ---\n%s\n--- RAW END ---", raw_text)
        raise RuntimeError(f"Invalid JSON response from model: {exc}") from exc
    return data


def save_temp_json(stem: str, data: dict[str, Any]) -> Path:
    """Save the raw research JSON under data/temp."""
    DATA_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_TEMP_DIR / f"{stem}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_research(meta: dict[str, str], data: dict[str, Any]) -> None:
    """Write the spoken mathematics and chemistry blocks for one book.

    Args:
        meta: Book attributes for this job.
        data: Validated research response.

    Raises:
        RuntimeError: A required block is empty.
    """
    language_code = meta["language_code"]
    notation = meta["notation"]
    country_code = meta["country_code"]
    problems = validate_research(data, language_code, notation)
    stem = f"{country_code}_{language_code}_{notation}"
    save_temp_json(stem, data)
    if problems:
        raise RuntimeError("; ".join(problems))

    spoken = {key: str(data[key]).strip() for key in SPOKEN_FIELDS}
    spoken["country_code"] = country_code
    spoken["language_code"] = language_code
    spoken["notation"] = notation
    spoken_path = spoken_json_path(country_code, language_code, notation)
    spoken_path.parent.mkdir(parents=True, exist_ok=True)
    spoken_path.write_text(
        json.dumps(spoken, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    audio_dir = AUDIOS_DIR / "country"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / f"{country_code}.md").write_text(spoken["spoken_variety"] + "\n", encoding="utf-8")
    LOGGER.info("wrote %s", spoken_path.name)


def outputs_exist(meta: dict[str, str]) -> bool:
    """Return whether this book's spoken blocks are already on disk."""
    return spoken_json_path(
        meta["country_code"], meta["language_code"], meta["notation"]
    ).is_file()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", help="ISO country code to research from the book XMLs")
    parser.add_argument("--all", action="store_true", help="Research every distinct book in data/input")
    parser.add_argument("--force", action="store_true", help="Replace researched files that already exist")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Gemini model (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--thinking",
        default=DEFAULT_THINKING,
        choices=("minimal", "low", "medium", "high"),
        help=f"Interactions API thinking_level (default: {DEFAULT_THINKING})",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google_genai").setLevel(logging.WARNING)

    if not args.country and not args.all:
        print("Please specify --country <code> or --all")
        return 1

    try:
        api_key = resolve_api_key()
        iso_data = load_iso_data()
    except Exception as error:
        print(f"Error: {error}")
        return 1

    jobs = book_jobs()
    if args.country:
        code = args.country.lower()
        jobs = [meta for meta in jobs if meta["country_code"] == code]
        if not jobs:
            print(f"Error: no book XML for country {code}")
            return 1

    try:
        from google import genai
    except ModuleNotFoundError as error:
        print(f"Error: missing dependency ({error.name}): pip install -r requirements.txt")
        return 1
    client = genai.Client(api_key=api_key)
    failed = 0
    for meta in jobs:
        problems = profile_problems(meta)
        label = f"{meta['country_code']} {meta['language_code']} {meta['notation']}"
        if problems:
            failed += 1
            print(f"  {label}: {'; '.join(problems)}")
            continue
        if outputs_exist(meta) and not args.force:
            print(f"  {label}: already researched")
            continue
        country_name = iso_data["iso3166_alpha2"].get(meta["country_code"]) or meta.get("country", "")
        print(f"Researching {country_name} ({label})...", flush=True)
        try:
            data = research_job(client, meta, country_name, args.model, args.thinking)
            write_research(meta, data)
            print(f"  Wrote {label}", flush=True)
        except Exception as error:
            failed += 1
            print(f"  Failed {label}: {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
