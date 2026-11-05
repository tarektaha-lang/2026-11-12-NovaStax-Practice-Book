#!/usr/bin/env python3
"""Write one lesson section spec per section of a practice book.

Every source PDF a section is drawn from goes into a single call, and the model
returns one spec covering them all: what the section teaches, the objectives it
serves, the wording later writing has to reuse word for word. Where two books
teach the same idea, the spec keeps one account of it rather than one per book.

A spec is a brief for whatever writes the study book, so nothing here writes
teaching prose itself. sources.py builds the section PDFs this reads and must run
first.

Sections are specified concurrently, one worker per section, because no section
needs anything another section produces. Lines therefore appear as each section
lands rather than in the order of the book.

A book is minutes of uploading and thinking, so the run says where it stands as
it goes: what it is about to send, each file as it lands, what Gemini says about
a file that is not ready yet, and where the whole book stands whenever half a
minute passes with nothing else to report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field, model_validator

    from src.api_logger import (
        ApiLogger,
        genai_answer_text,
        genai_finish_reason,
        genai_response_files,
        genai_usage,
        safe_log,
    )
    from src.arabic_verse import PoemRow, poem_linear_text
    from src.geometry_arrows import canonicalize_document
    from src.prompting import Prompt, assemble, prompt_subject
    from sources import (
        PROJECT_ROOT,
        SECTIONS_STAGE,
        OUTPUT_DIR,
        Span,
        book_metadata,
        book_root,
        book_spans,
        number_pad_width,
        number_stem,
        section_spans,
        section_stem,
    )
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# The prompt is assembled from prompts/specs/ by prompting.py, from the parts
# the book's language, numerals and notation choose.
PROMPT_STAGE = "specs"

SPECS_STAGE = "03_section_specs"
SPEC_STEP = "section_spec"

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING = "high"
THINKING_LEVELS = ("minimal", "low", "medium", "high")
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_ATTEMPTS = 4
# One worker per section still to be specified. The ceiling is what keeps a
# book with hundreds of sections from holding hundreds of PDFs in memory and
# hundreds of calls in flight at once.
MAX_WORKERS = 25

PDF_MIME = "application/pdf"
UPLOAD_READY_TIMEOUT = 120.0
UPLOAD_POLL_SECONDS = 2.0
# A file is usually ready the first time it is asked about, so nothing is said
# until a wait has run this long; saying it every time would bury the rest.
UPLOAD_STATUS_SECONDS = 10.0
# How long the run may go without a line before it says where it stands. The
# model call is the long pole and a section is silent for all of it.
STANDING_SECONDS = 30.0

RECITATION_RECOVERY_SUFFIX = """

## Recitation recovery
The previous structured reply was blocked for quoting too much verbatim text from the PDF.
Return status "ok" with a complete spec. Paraphrase long passage wording in excerpts
(reuse rephrase_ok). Quote at most one short phrase (15 words or fewer) per excerpt;
do not paste full poems, letters, scripts, or reading passages."""

# Items are numbered on the way to disk rather than by the model, so that a
# later stage can cite "objective_2" and mean the second objective written here.
ID_PREFIXES = {
    "objectives": "objective",
    "prerequisites": "prerequisite",
    "out_of_scope": "out_of_scope",
    "glossary": "term",
    "teaching_points": "teaching_point",
    "excerpts": "excerpt",
    "equations": "equation",
    "notations": "notation",
    "language_lesson_practice_architecture": "lesson_practice",
    "language_scope_entries": "language_scope_entry",
    "strand_architecture": "strand",
}


class Objective(BaseModel):
    text: str = Field(description="An observable skill or piece of knowledge the learner gains")


class Prerequisite(BaseModel):
    text: str = Field(description="A skill or piece of knowledge the section assumes already")


class OutOfScope(BaseModel):
    text: str = Field(description="A topic or method this section must not teach")
    reason: str = Field(description="Why it is out of scope here")


class GlossaryEntry(BaseModel):
    term: str
    definition: str = Field(description="Short gloss in the book's language")


class Figure(BaseModel):
    type: str = Field(description="Short category, e.g. number line, labelled diagram")
    purpose: str = Field(description="One sentence on what it teaches")
    description: str = Field(
        description="Brief for recreating it: labels, values, relationships the sources show"
    )


class TeachingPoint(BaseModel):
    title: str = Field(description="Short name for the idea, skill or process")
    notes: str = Field(
        description="Enough to teach it without the PDFs, and how it is taught at this level"
    )
    figure: Figure | None = Field(
        default=None, description="The visual this point needs; omit when it needs none"
    )


class Excerpt(BaseModel):
    type: Literal["passage", "dialogue", "document", "poem", "key_statement", "other"]
    reuse: Literal["as_is", "rephrase_ok"] = Field(
        description="as_is when changing the words would break it; rephrase_ok otherwise"
    )
    text: str = Field(description="Exact copy of the chosen source's wording")
    kashida: bool = Field(
        default=True,
        description="False only for free verse; omit on non-poem excerpts",
    )
    rows: list[PoemRow] = Field(
        default_factory=list,
        description="Printed rows of a poem; one shatr, or sadr then ajuz; omit on every other type",
    )

    @model_validator(mode="after")
    def rows_only_on_poems(self):
        if self.type != "poem" and self.rows:
            raise ValueError("only a poem excerpt carries rows")
        return self


class Equation(BaseModel):
    latex: str = Field(description="A reusable general form, in $...$ or $$...$$")


class Notation(BaseModel):
    notation: str = Field(
        description="A symbol, unit, or written convention, e.g. bold for vectors"
    )
    meaning: str


class LanguageLessonPracticeArchitecture(BaseModel):
    strand: str
    item_order: str
    stimulus_rule: str
    multi_part: str
    standalone_rule: str


class LanguageScopeEntry(BaseModel):
    target_vocabulary: str
    target_grammar: str
    functional_phrases: str
    rubric_phrases: str
    notes: str


class StrandArchitecture(BaseModel):
    strand: str
    practice_form: str
    support_level: str
    limits: str
    notes: str


class SectionSpec(BaseModel):
    """What one section of the study book has to cover, drawn from its sources.

    Fields are declared in the order they are written to disk: what the section
    is, then what it must achieve, then the material it teaches with.
    """

    role: Literal["teach", "revision", "assessment"]
    overview: str = Field(description="4-8 analyst sentences on the section's pedagogical arc")
    objectives: list[Objective] = Field(min_length=1)
    prerequisites: list[Prerequisite] = Field(min_length=1)
    out_of_scope: list[OutOfScope] = Field(min_length=1)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    teaching_points: list[TeachingPoint] = Field(default_factory=list)
    excerpts: list[Excerpt] = Field(
        default_factory=list,
        description="Wording copied from a source that later writing must reuse; empty when none",
    )
    equations: list[Equation] = Field(default_factory=list)
    notations: list[Notation] = Field(default_factory=list)


class ArabicSectionSpec(SectionSpec):
    language_lesson_practice_architecture: list[LanguageLessonPracticeArchitecture] = Field(
        default_factory=list
    )


class EnglishSectionSpec(SectionSpec):
    language_scope_entries: list[LanguageScopeEntry] = Field(default_factory=list)
    strand_architecture: list[StrandArchitecture] = Field(default_factory=list)


class SpecReply(BaseModel):
    """The model's answer, which may refuse the section instead of specifying it."""

    status: Literal["ok", "abort"]
    abort_reason: str = Field(
        default="", description="Why the sources cannot be combined; empty when status is ok"
    )
    spec: SectionSpec

    @model_validator(mode="after")
    def poem_excerpts_have_rows(self):
        if self.status != "ok":
            return self
        for excerpt in self.spec.excerpts:
            if excerpt.type == "poem" and not excerpt.rows:
                raise ValueError("a poem excerpt needs rows of shatrs")
        return self


class ArabicSpecReply(BaseModel):
    status: Literal["ok", "abort"]
    abort_reason: str = Field(
        default="", description="Why the sources cannot be combined; empty when status is ok"
    )
    spec: ArabicSectionSpec

    @model_validator(mode="after")
    def poem_excerpts_have_rows(self):
        if self.status != "ok":
            return self
        for excerpt in self.spec.excerpts:
            if excerpt.type == "poem" and not excerpt.rows:
                raise ValueError("a poem excerpt needs rows of shatrs")
        return self


class EnglishSpecReply(BaseModel):
    status: Literal["ok", "abort"]
    abort_reason: str = Field(
        default="", description="Why the sources cannot be combined; empty when status is ok"
    )
    spec: EnglishSectionSpec


def blocked_abort_reply(
    reply_type: type[BaseModel], book_id: str, reason: str
) -> BaseModel:
    """When Gemini returns no JSON (often RECITATION), emit a schema-valid abort shell."""
    spec_type = spec_model_type(book_id)
    shell: dict = {
        "role": "teach",
        "overview": f"Aborted: {reason}",
        "objectives": [{"text": "Aborted section."}],
        "prerequisites": [{"text": "None required."}],
        "out_of_scope": [{"text": "N/A", "reason": "Aborted."}],
    }
    for name in spec_type.model_fields:
        if name not in shell:
            shell[name] = []
    return reply_type.model_validate(
        {"status": "abort", "abort_reason": reason, "spec": shell}
    )


def spec_model_type(book_id: str) -> type[SectionSpec]:
    """The spec body shape for this book's subject."""
    subject = prompt_subject(book_id)
    if subject == "arabic":
        return ArabicSectionSpec
    if subject == "english":
        return EnglishSectionSpec
    return SectionSpec


def spec_reply_type(book_id: str) -> type[BaseModel]:
    """The response envelope Gemini must return for this book."""
    subject = prompt_subject(book_id)
    if subject == "arabic":
        return ArabicSpecReply
    if subject == "english":
        return EnglishSpecReply
    return SpecReply


def build_prompt(meta: dict[str, str], book_id: str = "") -> Prompt:
    """The prompt this stage sends for the book, assembled from its parts."""
    return assemble(PROMPT_STAGE, meta, book_id)


def course_toc(root: ET.Element, current: str) -> list[dict]:
    """The table of contents as titles alone, with the section being specified flagged.

    The model needs the surrounding course to judge what a section may assume and
    what it should leave to its neighbours, and titles carry enough of that.
    """
    marked = 0

    def walk(parent: ET.Element, numbers: list[str]) -> list[dict]:
        nonlocal marked
        nodes = []
        for module in parent:
            if module.tag != "module":
                continue
            path = numbers + [module.get("number", "?")]
            node: dict = {"title": module.get("title", "")}
            children = walk(module, path)
            if children:
                node["sections"] = children
            elif ".".join(path) == current:
                node["current"] = True
                marked += 1
            nodes.append(node)
        return nodes

    toc = walk(root, [])
    if marked != 1:
        raise RuntimeError(f"section {current} is not a single leaf of the table of contents")
    return toc


def toc_part(toc: list[dict], title: str) -> str:
    """Label the table of contents so the model can tell it from the prompt."""
    body = json.dumps({"current_title": title, "toc": toc}, ensure_ascii=False, indent=2)
    return f"Course TOC JSON:\n{body}"


def since(seconds: float) -> str:
    """A duration short enough to sit at the end of a status line."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{int(seconds) // 60}m{int(seconds) % 60:02d}s"


def in_mb(nbytes: int) -> str:
    """A size in the one unit every figure here is worth reading in."""
    return f"{nbytes / 1024**2:.1f} MB"


def in_kb(nbytes: int) -> str:
    """The size of a request made of text, never rounded down to nothing.

    A stage that sends a spec rather than a PDF sends kilobytes, and a short
    section sends less than one: reporting that as "0 KB" reads as though nothing
    was sent at all, so anything under a kilobyte is given in bytes.
    """
    if nbytes < 1024:
        return f"{nbytes} B"
    return f"{nbytes / 1024:.0f} KB"


def one_line(message: str) -> str:
    """A failure cut down to something a status line can carry.

    A rejected schema or a refused request comes back as a paragraph or more,
    and a hundred workers reporting one each would bury everything else. The
    whole of it is in the call log either way, which is where a failure is read.
    """
    flat = " ".join(message.split())
    return flat if len(flat) <= 120 else flat[:119] + "..."


class Progress:
    """What a run says about itself while its workers are in flight.

    Every line goes out under one lock, so no worker interleaves half of its
    line with another's, and each is flushed as it is written because stdout is
    block-buffered whenever it is not a terminal: a run watched through a pipe
    or a log file would otherwise say nothing at all until it was over.

    The counts are kept here rather than in the workers because what is worth
    watching is the book and not one section of it: how much of the upload has
    landed, and how many sections are uploading, waiting on the model, or done.
    A thread reports that whenever the run has gone quiet, which is what the
    model call otherwise is for minutes at a time.

    Shared by every stage that puts a section on each worker. One that sends
    text rather than files leaves the file counts at nothing and is followed by
    its sections alone, and one whose work runs through phases of its own names
    them in `phases`, in the order a reader should see them counted.
    """

    def __init__(
        self,
        sections: int,
        files: int = 0,
        nbytes: int = 0,
        phases: tuple[str, ...] = ("uploading", "still with the model"),
    ) -> None:
        self.sections = sections
        self._phases = phases
        self.files = files
        self.nbytes = nbytes
        self.files_up = 0
        self.bytes_up = 0
        self.settled = 0
        self._finished: set[str] = set()
        self._phase: dict[str, str] = {}
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._spoke = self._started
        self._stop = threading.Event()
        self._standing: threading.Thread | None = None

    def __enter__(self) -> Progress:
        self._standing = threading.Thread(target=self._keep_standing, daemon=True)
        self._standing.start()
        return self

    def __exit__(self, *exception: object) -> None:
        self._stop.set()
        if self._standing is not None:
            self._standing.join()

    def _emit(self, line: str) -> None:
        """Write one line, the lock already held."""
        self._spoke = time.monotonic()
        print(line, flush=True)

    def _keep_standing(self) -> None:
        """Say where the book stands whenever nothing else has been said."""
        while not self._stop.wait(1.0):
            with self._lock:
                if self._phase and time.monotonic() - self._spoke >= STANDING_SECONDS:
                    self._emit(f"  ... {self._where()}")

    def _where(self) -> str:
        counts = Counter(self._phase.values())
        parts = [f"{counts[name]} {name}" for name in self._phases if counts[name]]
        # What is neither finished nor in flight is waiting for a free worker,
        # which is most of a book whenever there are more of them than workers.
        # A finished item tried again is in flight but was already counted.
        fresh = sum(1 for name in self._phase if name not in self._finished)
        if (waiting := max(0, self.sections - self.settled - fresh)):
            parts.append(f"{waiting} not started")
        parts.append(f"{self.settled} of {self.sections} done")
        return f"{', '.join(parts)}, {since(time.monotonic() - self._started)} in"

    def say(self, line: str) -> None:
        with self._lock:
            self._emit(line)

    def step(self, name: str, phase: str, said: str = "") -> None:
        """Move one item into a phase, and say so where that is worth a line.

        A stage whose work runs through several short phases — drawn, compiled,
        looked at — tracks them all so the heartbeat can count them, but says
        only the ones worth reading: the rest would print more about the run
        than the run is worth.
        """
        with self._lock:
            self._phase[name] = phase
            if said:
                self._emit(f"  {name}: {said}")

    def uploading(self, section: str, files: int, nbytes: int) -> None:
        self.step(section, "uploading", f"uploading {files} PDF(s), {in_mb(nbytes)}")

    def landed(self, section: str, index: int, files: int, size: int) -> None:
        with self._lock:
            self.files_up += 1
            self.bytes_up += size
            self._emit(
                f"  {section}: file {index} of {files} up, {in_mb(size)} "
                f"({self.files_up}/{self.files} files, "
                f"{in_mb(self.bytes_up)} of {in_mb(self.nbytes)} for the book)"
            )

    def not_ready(
        self, section: str, index: int, files: int, state: str, waited: float
    ) -> None:
        with self._lock:
            self._emit(
                f"  {section}: file {index} of {files} still {state} after "
                f"{since(waited)}, asked Gemini again"
            )

    def rewind(self, files: int, nbytes: int) -> None:
        """Take back what a failed attempt sent, so a retry is not counted twice."""
        with self._lock:
            self.files_up -= files
            self.bytes_up -= nbytes

    def asking(self, section: str, said: str = "asking the model") -> None:
        """Mark a section as waiting on the model, in the words of its own stage.

        What is worth saying beside it differs: a stage that uploads reports how
        long that took, one that sends text reports how much of it went.
        """
        self.step(section, "still with the model", said)

    def done(self, section: str, line: str = "") -> None:
        """Take one item out of the run, with the line it finished on.

        A stage that reports its results in the book's order rather than as they
        land passes no line and prints its own later; the count still moves, so
        the heartbeat keeps saying how far along the run is.
        """
        with self._lock:
            self._phase.pop(section, None)
            self._finished.add(section)
            self.settled += 1
            if line:
                self._emit(f"{line}  [{self.settled}/{self.sections}]")

    def done_again(self, section: str) -> None:
        """Take a retried item out of flight; it was counted the first time."""
        with self._lock:
            self._phase.pop(section, None)


class Waiting:
    """How long one call has been out, said while it is still out.

    A stage with a section on every worker has their lines to show that it
    lives. A stage that asks once about the whole book has nothing to interleave
    with the wait, so it is reported from a thread of its own, or the run reads
    as hung for the minutes a high thinking level takes.

    Anything the stage says through say() resets the wait, so a line about a
    retry is not followed a moment later by one about being quiet.
    """

    def __init__(self, doing: str) -> None:
        self.doing = doing
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._spoke = self._started
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Waiting:
        self._started = time.monotonic()
        self._spoke = self._started
        self._thread = threading.Thread(target=self._keep_saying, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exception: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def say(self, line: str) -> None:
        with self._lock:
            self._spoke = time.monotonic()
            print(line, flush=True)

    def _keep_saying(self) -> None:
        while not self._stop.wait(1.0):
            with self._lock:
                now = time.monotonic()
                if now - self._spoke >= STANDING_SECONDS:
                    self._spoke = now
                    print(
                        f"  ... {self.doing} ({since(now - self._started)})", flush=True
                    )


def wait_until_ready(
    client, handle, report: Callable[[str, float], None] | None = None
):
    """Block until an uploaded PDF can be referenced in a request.

    report, when given, is told the file's state and how long the wait has run,
    but only once it passes UPLOAD_STATUS_SECONDS: a file is usually ready the
    first time it is asked about, and a line for every one of those would say
    nothing except that the upload is working.
    """
    deadline = time.monotonic() + UPLOAD_READY_TIMEOUT
    started = time.monotonic()
    reported = started
    while True:
        state = str(getattr(handle.state, "name", handle.state))
        if state == "ACTIVE":
            return handle
        if state == "FAILED":
            raise RuntimeError(f"upload failed for {handle.name}")
        now = time.monotonic()
        if now >= deadline:
            raise RuntimeError(
                f"upload was still {state} after {UPLOAD_READY_TIMEOUT:.0f}s: {handle.name}"
            )
        if report is not None and now - reported >= UPLOAD_STATUS_SECONDS:
            report(state, now - started)
            reported = now
        time.sleep(UPLOAD_POLL_SECONDS)
        handle = client.files.get(name=handle.name)


def upload_pdfs(
    client,
    paths: list[Path],
    *,
    section: str = "",
    progress: Progress | None = None,
) -> tuple[list[types.Part], list]:
    """A section's PDFs as request parts, every one of them uploaded first.

    Uploaded rather than sent inline even though a section is well under the
    inline cap, because a section PDF is read once and never reused: uploading
    streams it from disk instead of holding it in memory beside every other
    section running at the same time, and an attempt that has to be retried
    re-sends a URI rather than the bytes.

    Returns the parts and the handles, which the caller deletes once the answer
    is in. Retried as a whole, since every section depends on an upload now and
    a blip on one file would otherwise cost the section its four model attempts
    before it ever made one. progress, when given, is told what is about to go
    up, each file as it lands, and each file Gemini is still preparing.
    """
    last_error: str | None = None
    sizes = [path.stat().st_size for path in paths]

    def watch(index: int) -> Callable[[str, float], None] | None:
        """The readiness report for one file, or nothing when nobody is watching."""
        if progress is None:
            return None
        return lambda state, waited: progress.not_ready(
            section, index, len(paths), state, waited
        )

    if progress is not None:
        progress.uploading(section, len(paths), sum(sizes))

    for attempt in range(1, MAX_ATTEMPTS + 1):
        uploaded = []
        sent_files = sent_bytes = 0
        try:
            parts: list[types.Part] = []
            for index, (path, size) in enumerate(zip(paths, sizes), start=1):
                handle = client.files.upload(file=path, config={"mime_type": PDF_MIME})
                uploaded.append(handle)
                sent_files += 1
                sent_bytes += size
                if progress is not None:
                    progress.landed(section, index, len(paths), size)
                parts.append(
                    types.Part.from_uri(
                        file_uri=wait_until_ready(client, handle, watch(index)).uri,
                        mime_type=PDF_MIME,
                    )
                )
            return parts, uploaded
        except Exception as error:  # noqa: BLE001 - transport and readiness failures alike
            # Whatever went up before the failure holds quota until it expires,
            # and nobody else holds those handles to delete them.
            delete_uploads(client, uploaded)
            last_error = f"{type(error).__name__}: {error}"
            if progress is not None:
                progress.rewind(sent_files, sent_bytes)
            if attempt < MAX_ATTEMPTS:
                if progress is not None:
                    progress.say(
                        f"  {section}: upload failed, {one_line(last_error)}; "
                        f"attempt {attempt + 1} of {MAX_ATTEMPTS} in {2**attempt}s"
                    )
                time.sleep(2**attempt)

    raise RuntimeError(f"upload failed after {MAX_ATTEMPTS} attempts: {last_error}")


def delete_uploads(client, uploaded: list) -> None:
    """Drop uploads as soon as they are answered; they hold quota until they expire."""
    for handle in uploaded:
        try:
            client.files.delete(name=handle.name)
        except Exception:  # noqa: BLE001 - an abandoned upload expires by itself
            pass


def logged_call(
    client,
    model: str,
    prompt: str,
    toc: str,
    parts: list[types.Part],
    thinking: str,
    logger: ApiLogger,
    item: str,
    attempt: int,
    context: dict,
    reply_type: type[BaseModel],
) -> tuple[BaseModel | None, str | None]:
    """Ask for one section's spec once, logging the call however it turns out.

    Returns the model's reply, or None alongside the failure to report.
    """
    response = None
    result = None
    error_message = None
    started = time.perf_counter()
    try:
        response, result = generate_spec_reply(
            client,
            model,
            prompt,
            toc,
            parts,
            thinking,
            reply_type,
            str(context.get("book_id", "")),
            context,
        )
    except Exception as error:  # noqa: BLE001 - retry transport and parse failures alike
        error_message = f"{type(error).__name__}: {error}"
        response = None
        result = None
    duration = time.perf_counter() - started

    safe_log(
        logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            "course_toc.txt": toc,
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=item,
        step_name=SPEC_STEP,
        attempt=attempt,
        api="gemini",
        model=model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if result is not None else "error",
        error_message=error_message,
        extra={**context, "thinking_level": thinking, "returned": result.status if result else None},
    )
    return result, error_message


def generate_spec_reply(
    client,
    model: str,
    prompt: str,
    toc: str,
    parts: list[types.Part],
    thinking: str,
    reply_type: type[BaseModel],
    book_id: str,
    context: dict,
) -> tuple[object, BaseModel]:
    """One Gemini call, with a paraphrase retry when the API blocks verbatim output."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=reply_type,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )
    response = client.models.generate_content(
        model=model, contents=[prompt, toc, *parts], config=config
    )
    answer = genai_answer_text(response)
    if answer.strip():
        return response, reply_type.model_validate_json(answer)

    if genai_finish_reason(response) == "RECITATION" and not context.get("recitation_recovery"):
        context["recitation_recovery"] = True
        recovery_prompt = prompt + RECITATION_RECOVERY_SUFFIX
        response = client.models.generate_content(
            model=model, contents=[recovery_prompt, toc, *parts], config=config
        )
        answer = genai_answer_text(response)
        if answer.strip():
            return response, reply_type.model_validate_json(answer)

    if not answer.strip() and genai_finish_reason(response) == "RECITATION":
        return response, blocked_abort_reply(
            reply_type,
            book_id,
            "Model blocked verbatim output (RECITATION).",
        )
    raise ValueError("empty model reply")


def request_spec(
    client,
    model: str,
    prompt: str,
    toc: str,
    parts: list[types.Part],
    thinking: str,
    logger: ApiLogger,
    item: str,
    context: dict,
    reply_type: type[BaseModel],
    report: Callable[[str], None] | None = None,
) -> BaseModel:
    """Call the model until it returns a spec, retrying transport and parse failures."""
    last_error: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        result, last_error = logged_call(
            client,
            model,
            prompt,
            toc,
            parts,
            thinking,
            logger,
            item,
            attempt,
            context,
            reply_type,
        )
        if result is not None:
            return result
        if attempt < MAX_ATTEMPTS:
            # Said once per retry and cut to a line: a section that is quietly
            # burning its four attempts looks the same as one that is thinking.
            if report is not None:
                report(
                    f"attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"asking again in {2**attempt}s"
                )
            time.sleep(2**attempt)

    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")


def spec_document(spec: SectionSpec) -> dict:
    """The spec as it is written to disk, with every item numbered.

    Dumping without defaults leaves figure out of the teaching points that do
    not have one, while the arrays themselves are always written, empty or not,
    so a reader can count on them being there.
    """
    body = spec.model_dump(exclude_defaults=True)
    document: dict = {}
    model_type = type(spec)
    for field in model_type.model_fields:
        if field not in ID_PREFIXES:
            document[field] = body[field]
            continue
        document[field] = [
            {"id": f"{ID_PREFIXES[field]}_{number}", **item}
            for number, item in enumerate(body.get(field) or [], start=1)
        ]
    for excerpt in document.get("excerpts") or []:
        if excerpt.get("type") == "poem" and excerpt.get("rows"):
            excerpt["text"] = poem_linear_text(excerpt["rows"])
    return document


def written_spec(path: Path, envelope_keys: set[str], book_id: str) -> bool:
    """True when a section already holds a spec of the shape written today.

    Validation alone is too weak to decide this: unknown keys are ignored and
    absent arrays take their empty default, so a file written before a field was
    renamed would pass while quietly holding the old name and losing the new
    field. Comparing the key sets is what catches that, and anything
    half-written or renamed reads as absent and is specified again.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        spec_type = spec_model_type(book_id)
        if set(document) != envelope_keys or set(document["spec"]) != set(spec_type.model_fields):
            return False
        spec_type.model_validate(document["spec"])
    except (OSError, LookupError, TypeError, ValueError):
        return False
    return True


def describe(spec: SectionSpec) -> str:
    """The counts worth seeing on one line while a book runs."""
    counts = [
        (len(spec.objectives), "objectives"),
        (len(spec.teaching_points), "teaching points"),
        (len(spec.glossary), "terms"),
        (len(spec.excerpts), "excerpts"),
        (len(spec.equations), "equations"),
    ]
    return ", ".join(f"{number} {name}" for number, name in counts if number)


def section_pdfs(
    sources: list[Span], number_path: str, pad_width: int, sections_root: Path
) -> list[Path]:
    """The section PDF sources.py built for each of a section's sources."""
    return [
        sections_root / f"{section_stem(span.md5, number_path, pad_width)}.pdf"
        for span in sources
    ]


@dataclass(frozen=True)
class Run:
    """Everything a section is specified against that the whole book shares.

    Gathered into one object because a worker needs all of it and none of it
    changes between sections. The client and the logger are the two that matter
    for a concurrent run: both are made once here, and both are used from every
    worker.
    """

    client: genai.Client
    logger: ApiLogger
    book_id: str
    meta: dict[str, str]
    model: str
    thinking: str
    reply_type: type[BaseModel]
    root: ET.Element
    sections_root: Path
    pad_width: int
    prompt: Prompt
    progress: Progress


def write_section_spec(
    run: Run,
    number_path: str,
    sources: list[Span],
    out_path: Path,
    reply: BaseModel,
) -> None:
    from src.english_json import dump_english_json

    spec = getattr(reply, "spec")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        dump_english_json(
            canonicalize_document(
                {
                    "book_id": run.book_id,
                    **run.meta,
                    "number": number_path,
                    "title": sources[0].title,
                    "module_path": sources[0].module_path,
                    "spec": spec_document(spec),
                }
            )
        ),
        encoding="utf-8",
    )


def specify_section(
    run: Run, number_path: str, sources: list[Span], pdfs: list[Path], out_path: Path
) -> tuple[str, str]:
    """Specify one section, returning what became of it and the line to print.

    Runs on a thread of its own and shares nothing that is written to: its own
    PDFs go up, its own log folder is named for it, its own file is the only one
    it writes. What became of it is handed back for the caller to print, so that
    the count of finished sections is kept in one place, but the worker says as
    it goes what it is uploading and when it starts waiting on the model, since
    that is the minutes a book spends before its first section lands.
    """
    started = time.perf_counter()
    parts, uploaded = upload_pdfs(
        run.client, pdfs, section=number_path, progress=run.progress
    )
    upload_seconds = time.perf_counter() - started
    run.progress.asking(
        number_path, f"up in {since(upload_seconds)}, asking the model"
    )
    try:
        reply = request_spec(
            run.client,
            run.model,
            run.prompt.text,
            toc_part(course_toc(run.root, number_path), sources[0].title),
            parts,
            run.thinking,
            run.logger,
            number_path,
            {
                "book_id": run.book_id,
                "section": number_path,
                "section_title": sources[0].title,
                "module_path": sources[0].module_path,
                "sources": [
                    {
                        "md5": span.md5,
                        "start_page": span.start_raw,
                        "end_page": span.end_raw,
                    }
                    for span in sources
                ],
                "pdfs": [path.name for path in pdfs],
                "upload_seconds": round(upload_seconds, 1),
                "prompt_files": run.prompt.files,
            },
            report=lambda message: run.progress.say(f"  {number_path}: {message}"),
            reply_type=run.reply_type,
        )
    finally:
        delete_uploads(run.client, uploaded)

    elapsed = since(time.perf_counter() - started)
    write_section_spec(run, number_path, sources, out_path, reply)
    if reply.status == "abort":
        return "aborted", (
            f"  {number_path}: aborted, "
            f"{reply.abort_reason or 'no reason given'} ({elapsed})"
        )

    return "written", (
        f"  {number_path}: {len(pdfs)} source(s) -> {describe(reply.spec)} ({elapsed})"
    )


def build_specs(
    book_id: str, section: str | None, model: str, thinking: str, update: bool
) -> tuple[Path, int]:
    """Specify every section of a book, or just one, from the PDFs behind it."""
    if not any(os.environ.get(name) for name in API_KEY_VARS):
        raise RuntimeError(f"set one of {' / '.join(API_KEY_VARS)} to call the model")

    sections = section_spans(book_id, section)
    if section and not sections:
        raise RuntimeError(f"no section matching {section} in {book_id}")

    root = book_root(book_id)
    meta = book_metadata(book_id)
    pad_width = number_pad_width(book_spans(book_id))
    sections_root = OUTPUT_DIR / book_id / SECTIONS_STAGE
    stage_dir = OUTPUT_DIR / book_id / SPECS_STAGE
    prompt = build_prompt(meta, book_id)
    reply_type = spec_reply_type(book_id)
    envelope_keys = {"book_id", *meta, "number", "title", "module_path", "spec"}

    pending: list[tuple[str, list[Span], list[Path], Path]] = []
    skipped = 0
    for number_path, sources in sections.items():
        out_path = stage_dir / f"{number_stem(number_path, pad_width)}.json"
        if not update and written_spec(out_path, envelope_keys, book_id):
            skipped += 1
            continue
        pdfs = section_pdfs(sources, number_path, pad_width, sections_root)
        pending.append((number_path, sources, pdfs, out_path))

    # Every section's PDFs are looked for before any call goes out. Sections run
    # together now, so a book that was never split would otherwise spend a whole
    # set of calls before the first missing file stopped it. Reported by section
    # rather than by file, because a section short of even one of its PDFs is
    # split again whole.
    unsplit = [
        number_path
        for number_path, _, pdfs, _ in pending
        if any(not path.is_file() for path in pdfs)
    ]
    if unsplit:
        raise RuntimeError(
            f"{len(unsplit)} section(s) have no PDF: {', '.join(unsplit)}; run sources.py first"
        )

    written = aborted = 0
    failures: list[str] = []

    if pending:
        files = [path for _, _, pdfs, _ in pending for path in pdfs]
        nbytes = sum(path.stat().st_size for path in files)
        workers = min(len(pending), MAX_WORKERS)
        opening = (
            f"specifying {len(pending)} section(s) with {workers} worker(s), "
            f"{len(files)} PDF(s), {in_mb(nbytes)} to upload"
        )
        if skipped:
            opening += f"; {skipped} already specified"
        print(opening, flush=True)

        # Both the client and the logger reach the network to start up, so a run
        # that specifies nothing needs neither. The logger's clock is set once
        # here rather than once per worker, and it is named on its own line
        # because setting it against NTP is the wait that can run into seconds.
        print("  opening the model client", flush=True)
        client = genai.Client()
        print("  setting the log clock", flush=True)
        logger = ApiLogger(OUTPUT_DIR / book_id)

        with Progress(len(pending), len(files), nbytes) as progress:
            run = Run(
                client=client,
                logger=logger,
                book_id=book_id,
                meta=meta,
                model=model,
                thinking=thinking,
                reply_type=reply_type,
                root=root,
                sections_root=sections_root,
                pad_width=pad_width,
                prompt=prompt,
                progress=progress,
            )
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(specify_section, run, *item): item[0] for item in pending
                }
                for future in as_completed(futures):
                    number_path = futures[future]
                    try:
                        outcome, line = future.result()
                    except Exception as error:  # noqa: BLE001 - the others still stand
                        failures.append(f"{number_path}: {type(error).__name__}: {error}")
                        progress.done(
                            number_path,
                            f"  {number_path}: failed, "
                            f"{one_line(f'{type(error).__name__}: {error}')}",
                        )
                        continue
                    if outcome == "written":
                        written += 1
                    else:
                        aborted += 1
                    progress.done(number_path, line)

    summary = f"{written} section spec(s) written"
    if skipped:
        summary += f", {skipped} already specified"
    if aborted:
        summary += f", {aborted} aborted"
    if failures:
        summary += f", {len(failures)} failed"
    print(f"OK: {summary} under {stage_dir.relative_to(PROJECT_ROOT)}")
    return stage_dir, len(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. eg_mathematics_en_g7_t1")
    parser.add_argument(
        "-s", "--section", help="specify this section, or every section under it, e.g. -s 1 or -s 01"
    )
    parser.add_argument(
        "--update", action="store_true", help="rewrite section specs that already exist"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"model to call (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--thinking-level",
        default=DEFAULT_THINKING,
        choices=THINKING_LEVELS,
        help=f"how hard the model thinks (default: {DEFAULT_THINKING})",
    )
    args = parser.parse_args()

    try:
        _, unspecified = build_specs(
            args.book_id, args.section, args.model, args.thinking_level, args.update
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 1 if unspecified else 0


if __name__ == "__main__":
    sys.exit(main())
