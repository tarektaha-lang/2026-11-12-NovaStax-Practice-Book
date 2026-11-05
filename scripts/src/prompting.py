#!/usr/bin/env python3
"""Put together the prompt a stage sends, from the parts a book's language, numerals
and notation choose.

A prompt is mostly the same for every book: what the stage is for, what it is given,
what it must return. What differs is the language the book is written in, the digits
its numbers are printed in, and the notation its mathematics is written in — and those
three vary independently enough to be kept apart. So each stage's prompt is a folder
under prompts/, and the prompt a call sees is assembled from it:

    prompts/<stage>/
        core.md               what holds for every book, with a slot per varying part
        language/<code>.md    en.md, ar.md — the rules for a book in that language
        numerals/<name>.md    western.md, arabic.md — how numbers are read and written
        notation/<name>.md    latin.md, arabic.md — how the mathematics is written

Mathematics books use that layout at the stage root. Arabic, English, and Science books
use the same three profile dimensions from prompts/<stage>/subject/, plus subject
overlays listed in subject/<family>/blocks.json for specs and new_questions only.

core.md carries {language_rules}, {numerals_rules} and {notation_rules} where those
parts are to be dropped in, and {book_context} where the one-line description of the
book goes. A stage whose work does not vary with the digits or the notation has no
numerals/ or notation/ folder and no slot for them, and the assembler enforces both
ways: a slot with no file to fill it, or a folder of rules with no slot to fall into,
stops the run rather than sending a prompt with a hole in it. A stage that fills its
own prompt, such as transcripts or audios, has no {book_context} slot; the checker
leaves that folder to the script that fills it.

Which parts a book gets is read off the <book> element of its table of contents:
language_code, numerals and notation. Only the combinations in PROFILES are allowed.
An English book is always Western digits and Latin notation. An Arabic book is one of
three: Western digits with Latin notation (Arabic prose around mathematics written as
an English book writes it), Western digits with Arabic notation (RTL Arabic maths that
still prints 0–9), or Arabic-Indic digits with Arabic notation. Anything else is
refused at validation, before a page is rendered or a call is made.

Every assembled prompt says which files went into it, so a call's log can record them
and a folder of logs can be read against a folder of prompts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"

CORE_FILE = "core.md"
BLOCKS_FILE = "blocks.json"

# Stages that may use prompts/<stage>/subject/ overlays (not mathematics).
SUBJECT_OVERLAY_STAGES = frozenset({"specs", "new_questions"})

# First token of the pipeline book id selects the prompt family for overlay stages.
OVERLAY_SUBJECTS = frozenset({"arabic", "english", "science"})
SUPPORTED_PROMPT_SUBJECTS = frozenset({"mathematics", *OVERLAY_SUBJECTS})

# The three things a book says about itself that change what a stage is told, and
# the values each may take. The <book> attribute is named for the dimension, except
# the language, which the table of contents carries as a code.
LANGUAGE_ATTR = "language_code"
NUMERALS_ATTR = "numerals"
NOTATION_ATTR = "notation"

LANGUAGES = {"en": "English", "ar": "Arabic"}
NUMERALS = ("western", "arabic")
NOTATIONS = ("latin", "arabic")

# The languages whose script runs right to left. A fact about the language rather than
# about any one stage, so it is kept with the language; latex.py reads it to turn the
# page round, and the prompts say the same thing in words.
RTL_LANGUAGES = frozenset({"ar"})

# The letters a book in each language may write beyond ASCII, for the check that
# refuses a reply with a word from another script in it. English needs none: its
# letters are ASCII. Arabic is the letters of the Arabic block proper, hamza to yeh,
# with the tatweel the prose joins a preposition to a symbol with ("بـ $-1$"); the
# Persian and Urdu additions outside that run are not Arabic and stay refused. The
# vowel marks are not letters and are not judged by that check at all.
ALPHABETS = {
    "en": frozenset(),
    "ar": frozenset(chr(code) for code in range(0x0621, 0x064B)) | {"\u0640"},
}

# The combinations a book may be. This is the whole list: a book that is not one of
# these has no prompts written for it and is refused rather than guessed at.
PROFILES = frozenset({
    ("en", "western", "latin"),
    ("ar", "western", "latin"),
    ("ar", "western", "arabic"),
    ("ar", "arabic", "arabic"),
})

# The slot each dimension's part is dropped into, and the folder it is read from.
SLOTS = {
    "language": "{language_rules}",
    "numerals": "{numerals_rules}",
    "notation": "{notation_rules}",
}
BOOK_CONTEXT_SLOT = "{book_context}"

SPECS_NAMED_BLOCKS = {
    "glossary": "{subject_glossary}",
    "teaching_points": "{subject_teaching_points}",
    "excerpts": "{subject_excerpts}",
    "figures": "{subject_figures}",
}
NEW_QUESTIONS_NAMED_BLOCKS = {
    "sets": "{subject_sets}",
    "figures": "{subject_figures}",
}
SUBJECT_SLOTS = frozenset(
    {
        *SPECS_NAMED_BLOCKS.values(),
        *NEW_QUESTIONS_NAMED_BLOCKS.values(),
        "{subject_extra}",
    }
)

# What the one-line description of the book carries, in the order it says it.
CONTEXT_FIELDS = (
    "subject", "grade", "term", "language", "language_code", "numerals", "notation",
    "country",
)


@dataclass(frozen=True)
class Profile:
    """Which of the allowed combinations a book is."""

    language_code: str
    numerals: str
    notation: str

    @property
    def language(self) -> str:
        return LANGUAGES[self.language_code]

    @property
    def rtl(self) -> bool:
        """Whether the book's prose runs right to left."""
        return self.language_code in RTL_LANGUAGES

    @property
    def alphabet(self) -> frozenset[str]:
        """The letters beyond ASCII a book in this language may write."""
        return ALPHABETS[self.language_code]

    @property
    def indic_digits(self) -> bool:
        """Whether the book prints Arabic-Indic digits rather than 0–9."""
        return self.numerals == "arabic"

    def describe(self) -> str:
        return f"{self.language} prose, {self.numerals} numerals, {self.notation} notation"

    @classmethod
    def of(cls, meta: dict[str, str]) -> Profile:
        """The profile the book's attributes describe, or a RuntimeError saying why not."""
        problems = profile_problems(meta)
        if problems:
            raise RuntimeError("; ".join(problems))
        return cls(meta[LANGUAGE_ATTR], meta[NUMERALS_ATTR], meta[NOTATION_ATTR])


def profile_problems(meta: dict[str, str]) -> list[str]:
    """Everything wrong with a book's language, numerals and notation attributes.

    Written as a list of lines rather than one exception, so that the table of
    contents validation in sources.py can report them beside its own findings.
    """
    problems: list[str] = []
    for attr, allowed in (
        (LANGUAGE_ATTR, tuple(LANGUAGES)),
        (NUMERALS_ATTR, NUMERALS),
        (NOTATION_ATTR, NOTATIONS),
    ):
        value = meta.get(attr)
        if value is None:
            problems.append(f"[book] <book> has no {attr} attribute; one of {', '.join(allowed)}")
        elif value not in allowed:
            problems.append(
                f"[book] <book> {attr}={value!r} is not one of {', '.join(allowed)}"
            )
    if problems:
        return problems

    combination = (meta[LANGUAGE_ATTR], meta[NUMERALS_ATTR], meta[NOTATION_ATTR])
    if combination not in PROFILES:
        allowed = "; ".join(
            f"{code} with {numerals} numerals and {notation} notation"
            for code, numerals, notation in sorted(PROFILES)
        )
        problems.append(
            f"[book] {LANGUAGE_ATTR}={combination[0]!r} with {NUMERALS_ATTR}="
            f"{combination[1]!r} and {NOTATION_ATTR}={combination[2]!r} is not a supported"
            f" book; the supported books are: {allowed}"
        )
    return problems


def book_context(meta: dict[str, str]) -> str:
    """The one-line description of the book that every prompt opens with.

    A field the book has left empty — the term of a book that runs the year — is left
    off the line rather than said as nothing; one the book has not got at all is said
    as unknown, since that is a table of contents with something missing.
    """
    return " | ".join(
        f"{name.replace('_', ' ').capitalize()}: {meta.get(name, 'unknown')}"
        for name in CONTEXT_FIELDS
        if meta.get(name, "unknown").strip()
    )


@dataclass(frozen=True)
class Prompt:
    """One assembled prompt and the files it was assembled from.

    `parts` are paths relative to the project root, in the order they were folded in,
    which is what a call's log records so the prompt can be read back from its pieces.
    """

    text: str
    parts: tuple[str, ...]

    @property
    def files(self) -> list[str]:
        return list(self.parts)


def prompt_subject(book_id: str) -> str:
    """The prompt family for overlay stages, from the book id's first token.

    An empty book id is treated as mathematics so callers that do not name a book keep
    today's default assembly. Nagwa numeric subject_id on the table of contents is not
    used here.
    """
    if not book_id:
        return "mathematics"
    token = book_id.split("_", 1)[0].lower()
    if token in SUPPORTED_PROMPT_SUBJECTS:
        return token
    supported = ", ".join(sorted(SUPPORTED_PROMPT_SUBJECTS))
    raise RuntimeError(
        f"book id {book_id!r} has unsupported subject token {token!r}; "
        f"supported tokens are: {supported}"
    )


def stage_dir(stage: str) -> Path:
    return PROMPTS_DIR / stage


def read_part(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"cannot read the prompt at {path}: {error}") from error


def relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def _fill_profile_slots_with_meta(
    text: str,
    parts: list[str],
    *,
    folder: Path,
    core: Path,
    meta: dict[str, str],
    profile: Profile,
) -> str:
    if BOOK_CONTEXT_SLOT not in text:
        raise RuntimeError(f"{relative(core)} has no {BOOK_CONTEXT_SLOT} placeholder")
    text = text.replace(BOOK_CONTEXT_SLOT, book_context(meta))

    chosen = {
        "language": profile.language_code,
        "numerals": profile.numerals,
        "notation": profile.notation,
    }
    for dimension, value in chosen.items():
        slot = SLOTS[dimension]
        rules_dir = folder / dimension
        path = rules_dir / f"{value}.md"
        if slot in text:
            if not path.is_file():
                raise RuntimeError(
                    f"{relative(core)} has a {slot} slot but there is no {relative(path)}"
                    f" to fill it for a book with {value} {dimension}"
                )
            text = text.replace(slot, read_part(path).strip())
            parts.append(relative(path))
        elif rules_dir.is_dir():
            raise RuntimeError(
                f"{relative(rules_dir)} holds rules but {relative(core)} has no {slot} slot,"
                " so they would be dropped silently"
            )

    for slot in SLOTS.values():
        if slot in text:
            raise RuntimeError(f"{relative(core)} still carries {slot} after assembly")
    return text


def _load_block_ids(subject_dir: Path) -> list[str]:
    blocks_path = subject_dir / BLOCKS_FILE
    try:
        document = json.loads(blocks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read {relative(blocks_path)}: {error}") from error
    block_ids = document.get("block_ids")
    if not isinstance(block_ids, list) or not block_ids:
        raise RuntimeError(f"{relative(blocks_path)} must list at least one block_id")
    return [str(block_id) for block_id in block_ids]


def _fill_subject_overlay(
    text: str,
    parts: list[str],
    *,
    stage: str,
    subject: str,
    subject_root: Path,
) -> str:
    named = SPECS_NAMED_BLOCKS if stage == "specs" else NEW_QUESTIONS_NAMED_BLOCKS
    subject_dir = subject_root / subject
    block_ids = _load_block_ids(subject_dir)
    slot_values = {slot: "" for slot in SUBJECT_SLOTS}
    extra_pieces: list[str] = []

    for block_id in block_ids:
        path = subject_dir / f"{block_id}.md"
        if not path.is_file():
            raise RuntimeError(f"{relative(subject_dir / BLOCKS_FILE)} lists {block_id!r} but"
                               f" there is no {relative(path)}")
        body = read_part(path).strip()
        parts.append(relative(path))
        slot = named.get(block_id)
        if slot:
            slot_values[slot] = body
        else:
            extra_pieces.append(body)

    slot_values["{subject_extra}"] = "\n\n".join(extra_pieces).strip()
    for slot, value in slot_values.items():
        if slot in text:
            text = text.replace(slot, value)

    for slot in SUBJECT_SLOTS:
        if slot in text:
            raise RuntimeError(
                f"{relative(subject_root / CORE_FILE)} still carries {slot} after subject assembly"
            )
    leftover = re.findall(r"\{subject_[^}]+\}", text)
    if leftover:
        raise RuntimeError(
            f"{relative(subject_root / CORE_FILE)} has unknown placeholders: {', '.join(leftover)}"
        )
    return text


def assemble(stage: str, meta: dict[str, str], book_id: str = "") -> Prompt:
    """The prompt one stage sends for one book, with every varying part in place.

    The book's own placeholder is filled here too, since every stage has one. Slots a
    stage adds for itself — the set sizes in new_questions, the width in draw — are
    left for that stage to fill, and it is that stage's business to check them.

    When book_id names an Arabic, English, or Science book and the stage is specs or
    new_questions, the overlay core and subject blocks are used. Mathematics and all
    other stages use the stage root core.md.
    """
    profile = Profile.of(meta)
    subject = prompt_subject(book_id) if book_id else "mathematics"

    if stage in SUBJECT_OVERLAY_STAGES and subject != "mathematics":
        folder = stage_dir(stage) / "subject"
        core = folder / CORE_FILE
        text = read_part(core)
        parts: list[str] = [relative(core)]
        text = _fill_profile_slots_with_meta(
            text, parts, folder=folder, core=core, meta=meta, profile=profile
        )
        text = _fill_subject_overlay(
            text, parts, stage=stage, subject=subject, subject_root=folder
        )
        return Prompt(text, tuple(parts))

    folder = stage_dir(stage)
    core = folder / CORE_FILE
    text = read_part(core)
    parts = [relative(core)]
    text = _fill_profile_slots_with_meta(
        text, parts, folder=folder, core=core, meta=meta, profile=profile
    )
    return Prompt(text, tuple(parts))


def stages_written() -> list[str]:
    """Every stage whose prompt this module assembles, for checking them all at once.

    A folder with a core.md but no {book_context} slot fills its own prompt, so it
    is not checked here. Demanding the slot from it would reject a prompt this
    assembler does not build.
    """
    if not PROMPTS_DIR.is_dir():
        return []
    stages: list[str] = []
    for path in sorted(PROMPTS_DIR.iterdir()):
        core = path / CORE_FILE
        if not core.is_file():
            continue
        if BOOK_CONTEXT_SLOT not in core.read_text(encoding="utf-8"):
            continue
        stages.append(path.name)
    return stages


def main() -> int:
    """Assemble every stage's prompt for every supported book, and say what went in.

    A check to run after editing a prompt: it reads no book and calls nothing, and it
    fails on the first folder whose parts do not fit together.
    """
    failures = 0
    overlay_book_ids = {
        "arabic": "arabic_ar_eg08_t1",
        "english": "english_en_eg08_t1",
        "science": "science_ar_eg08_t1",
    }
    for stage in stages_written():
        checks: list[tuple[str, dict[str, str], str]] = []
        for code, numerals, notation in sorted(PROFILES):
            meta = {
                LANGUAGE_ATTR: code,
                "language": LANGUAGES[code],
                NUMERALS_ATTR: numerals,
                NOTATION_ATTR: notation,
            }
            checks.append((f"{code}, {numerals}, {notation}", meta, ""))
        if stage in SUBJECT_OVERLAY_STAGES:
            for subject, sample_id in overlay_book_ids.items():
                code, numerals, notation = ("ar", "western", "latin")
                if subject == "english":
                    code = "en"
                meta = {
                    LANGUAGE_ATTR: code,
                    "language": LANGUAGES[code],
                    NUMERALS_ATTR: numerals,
                    NOTATION_ATTR: notation,
                    "subject": subject,
                }
                checks.append((f"{subject} overlay", meta, sample_id))

        for label, meta, book_id in checks:
            try:
                prompt = assemble(stage, meta, book_id)
            except RuntimeError as error:
                failures += 1
                print(f"FAILED {stage} ({label}): {error}")
                continue
            within = relative(stage_dir(stage)) + "/"
            print(
                f"{stage} ({label}): {len(prompt.text):,} chars from "
                + ", ".join(part.removeprefix(within) for part in prompt.parts)
            )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
