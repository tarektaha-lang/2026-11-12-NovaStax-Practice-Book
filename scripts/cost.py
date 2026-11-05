#!/usr/bin/env python3
"""Price the API call logs one book has produced, then cost that whole book.

Reads nothing but the call.json files api_logger.py leaves under
data/output/<book_id>/00_api_call_logs/, prices every call from prices.json, and
writes a console report plus two self-contained HTML pages beside that book's own
stage folders: every logged call, and a one-pass page that keeps only the last
success per work item (a book run once, without later fix-up re-runs). No model
is called and no network is touched: this script only reads files already on disk.

One book, named on the command line, and nothing else. Spend does not carry from
one book to another — a book's cost scales with how many sections it has, and its
figure cost with how many of those sections draw anything at all — so every figure
here belongs to the book asked for, and the whole-book projection scales that
book's own unit costs up to its own table of contents.

Thinking tokens are the reason it exists. Gemini bills them at the output rate,
and in every log here they are the largest of the three counts, so a report that
folded them into output would hide the cost driver and one that priced them as
input would understate the bill several times over. They are carried and shown
separately from input and output everywhere.

Speech uses that same expression. A TTS call logs no thinking, and its output
tokens are audio tokens, so the speech model's output rate in prices.json is
the published audio rate. A model with no entry fails the run rather than being
priced at zero, which is what kept TTS spend out of the report.

Each model carries its own verification source/date and effective periods in
prices.json. A report is verified when every model it actually uses is verified;
an unused legacy placeholder does not taint another model's published rates.
Cached input is a subset of input and is charged at its own rate. Explicit cache
storage is logged as token-hours and priced separately.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from sources import INPUT_DIR, PROJECT_ROOT, OUTPUT_DIR, section_spans
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# The price table and the style guide both sit beside the script that reads them.
PRICES_PATH = SCRIPT_DIR / "prices.json"
STYLE_GUIDE_PATH = SCRIPT_DIR / "docs-style-guide.md"

# api_logger.py's folder layout, which this script only ever reads.
LOGS_STAGE = "00_api_call_logs"
CALL_FILE = "call.json"

# The report is about one book, so it is written beside that book's own stage
# folders. Writing it to a fixed path in data/ would mean every book's run
# overwrote the last one's report.
REPORT_FILE = "cost_report.html"
ONCE_REPORT_FILE = "cost_report_once.html"

# The status api_logger writes for a call that produced the result being kept.
# Anything else is spend that bought nothing.
SUCCESS_STATUS = "success"

# Which rate thinking tokens are charged at. Read from prices.json rather than
# assumed, and only this value is implemented: see billing_rule in that file.
THINKING_BILLED_AT = "output"

TOKEN_KINDS = ("input", "thinking", "output")

# Bar widths are scaled so the largest bar spans this much of its track, as the
# style guide asks; the printed value beside it is always the true number.
BAR_MAX_PERCENT = 70.0

# The style guide's "Full CSS reference" block is the page's entire stylesheet,
# copied out at generation time rather than pasted into this file, so that
# "verbatim" stays mechanically true. This is the sha256 of that block stripped
# of surrounding whitespace; a guide edited without updating it stops the run
# rather than quietly producing a page in some other style.
STYLE_SHA256 = "8f2f445508afbf13d2123049d25e3ab027f9ac75e686b44dd43a53ec9283dcb1"

# The report's sections, grouped as the sidebar shows them. Nav and body are
# both built from this one list, so every anchor resolves and the two orders
# cannot drift apart.
NAV_GROUPS = (
    ("Overview", ("basis", "totals")),
    ("Actual spend", ("by-step", "by-model", "waste", "throughput")),
    ("The whole book", ("projection", "sample-limits")),
    ("Reference", ("prices", "records")),
)


# --- The price table ---
@dataclass(frozen=True)
class RatePeriod:
    """One model's rates over an inclusive effective-date interval."""

    effective_from: date
    effective_to: date | None
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float
    cache_storage_per_million_per_hour: float

    def contains(self, day: date) -> bool:
        return self.effective_from <= day and (
            self.effective_to is None or day <= self.effective_to
        )


@dataclass(frozen=True)
class Rate:
    """One model's verified metadata and effective rate periods."""

    currency: str
    verified: bool
    checked_on: str
    source: str
    placeholder: bool
    note: str
    periods: tuple[RatePeriod, ...]
    batch_periods: tuple[RatePeriod, ...] = ()
    # False when prices.json gave only flat input/output rates, so the cached
    # rate below is a stand-in (the full input rate), not a published price.
    explicit_cached: bool = True

    def period_for(self, timestamp: str, *, batch: bool = False) -> RatePeriod:
        try:
            day = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
        except ValueError as error:
            raise RuntimeError(f"cannot select a rate for timestamp {timestamp!r}") from error
        periods = self.batch_periods if batch else self.periods
        kind = "batch rate" if batch else "rate"
        if not periods:
            raise RuntimeError(f"no {kind} periods on this model")
        for period in periods:
            if period.contains(day):
                return period
        raise RuntimeError(f"no effective {kind} covers {day.isoformat()}")

    @property
    def current(self) -> RatePeriod:
        today = date.today()
        return next(
            (period for period in self.periods if period.contains(today)),
            self.periods[-1],
        )

    @property
    def input_per_million(self) -> float:
        return self.current.input_per_million

    @property
    def output_per_million(self) -> float:
        return self.current.output_per_million


@dataclass(frozen=True)
class Prices:
    """prices.json as read, with the billing rule it declares."""

    checked_on: str
    source: str
    thinking_billed_at: str
    rates: dict[str, Rate]
    path: Path

    @property
    def currency(self) -> str:
        """The single currency every rate is quoted in.

        load_prices refuses a table that mixes two, so one entry answers for all
        of them and an empty table falls back to a neutral label.
        """
        for rate in self.rates.values():
            return rate.currency
        return "?"


def rate_number(entry: dict, model: str, key: str) -> float:
    """One rate, refusing anything that is not a usable non-negative number."""
    if key not in entry:
        raise RuntimeError(f"{model} in {PRICES_PATH.name} has no {key}")
    value = entry[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{model}.{key} in {PRICES_PATH.name} is not a number: {value!r}")
    if value < 0:
        raise RuntimeError(f"{model}.{key} in {PRICES_PATH.name} is negative: {value!r}")
    return float(value)


def iso_day(value: object, where: str) -> date:
    if not isinstance(value, str):
        raise RuntimeError(f"{where} is not an ISO date: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise RuntimeError(f"{where} is not an ISO date: {value!r}") from error


def parse_periods(raw_periods: object, model: str, field: str) -> list[RatePeriod]:
    """One model's effective-date list, refusing overlap or a malformed row."""
    if not isinstance(raw_periods, list) or not raw_periods:
        raise RuntimeError(f"{model}.{field} in {PRICES_PATH.name} is not a non-empty list")
    periods: list[RatePeriod] = []
    for index, row in enumerate(raw_periods):
        if not isinstance(row, dict):
            raise RuntimeError(f"{model}.{field}[{index}] is not an object")
        label = f"{model}.{field}[{index}]"
        start = iso_day(row.get("effective_from"), f"{label}.effective_from")
        end_value = row.get("effective_to")
        end = iso_day(end_value, f"{label}.effective_to") if end_value else None
        if end is not None and end < start:
            raise RuntimeError(f"{label} ends before it starts")
        periods.append(
            RatePeriod(
                effective_from=start,
                effective_to=end,
                input_per_million=rate_number(row, label, "input_per_million"),
                output_per_million=rate_number(row, label, "output_per_million"),
                cached_input_per_million=rate_number(
                    row, label, "cached_input_per_million"
                ),
                cache_storage_per_million_per_hour=rate_number(
                    row, label, "cache_storage_per_million_per_hour"
                ),
            )
        )
    periods.sort(key=lambda period: period.effective_from)
    for before, after in zip(periods, periods[1:]):
        if before.effective_to is None or before.effective_to >= after.effective_from:
            raise RuntimeError(
                f"{model}.{field} overlap or have an open period before another"
            )
    return periods


def load_prices() -> Prices:
    """Read the price table, refusing anything this script cannot price honestly.

    Unverified rates are not a failure — the report still has to render, warning
    as it goes. A malformed table is, as is a billing rule or a tier this script
    does not implement: guessing at either would be a wrong number nobody could
    see was wrong.
    """
    try:
        document = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"cannot read the price table at {PRICES_PATH}: {error}") from error
    except ValueError as error:
        raise RuntimeError(f"{PRICES_PATH} is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise RuntimeError(f"{PRICES_PATH} should hold an object, not {type(document).__name__}")

    rule = document.get("billing_rule") or {}
    billed_at = rule.get("thinking_tokens_billed_at")
    if billed_at != THINKING_BILLED_AT:
        raise RuntimeError(
            f"{PRICES_PATH.name} bills thinking tokens at {billed_at!r}; this script only "
            f"implements {THINKING_BILLED_AT!r} and will not guess at another rule"
        )

    models = document.get("models")
    if not isinstance(models, dict):
        raise RuntimeError(f"{PRICES_PATH.name} has no models object")

    default_checked = str(document.get("checked_on", ""))
    default_source = str(document.get("source", ""))
    rates: dict[str, Rate] = {}
    for model, entry in models.items():
        if not isinstance(entry, dict):
            raise RuntimeError(f"{model} in {PRICES_PATH.name} is not an object")
        if "tier" in entry:
            raise RuntimeError(
                f"{model} in {PRICES_PATH.name} carries a tier, and nothing in this repo "
                f"records which side of a context threshold a call fell on; remove it or "
                f"teach cost.py to read it, but it will not be ignored"
            )
        currency = entry.get("currency")
        if not isinstance(currency, str) or not currency:
            raise RuntimeError(f"{model} in {PRICES_PATH.name} has no currency")
        raw_periods = entry.get("periods")
        explicit_cached = raw_periods is not None
        if raw_periods is None:
            raw_periods = [
                {
                    "effective_from": "1970-01-01",
                    "input_per_million": entry.get("input_per_million"),
                    "output_per_million": entry.get("output_per_million"),
                    "cached_input_per_million": entry.get("input_per_million"),
                    "cache_storage_per_million_per_hour": 0,
                }
            ]
        periods = parse_periods(raw_periods, model, "periods")
        raw_batch = entry.get("batch_periods")
        batch_periods = (
            parse_periods(raw_batch, model, "batch_periods")
            if raw_batch is not None
            else []
        )
        verified = bool(entry.get("verified"))
        checked_on = str(entry.get("checked_on") or default_checked)
        source = str(entry.get("source") or default_source)
        placeholder = bool(entry.get("placeholder"))
        if verified and (placeholder or not checked_on or not source):
            raise RuntimeError(
                f"{model} is verified but has placeholder rates or missing source/date"
            )
        rates[model] = Rate(
            currency=currency,
            verified=verified,
            checked_on=checked_on,
            source=source,
            placeholder=placeholder,
            note=str(entry.get("note", "")),
            periods=tuple(periods),
            batch_periods=tuple(batch_periods),
            explicit_cached=explicit_cached,
        )

    currencies = {rate.currency for rate in rates.values()}
    if len(currencies) > 1:
        raise RuntimeError(
            f"{PRICES_PATH.name} quotes rates in {', '.join(sorted(currencies))}; this report "
            f"adds costs together and cannot mix currencies"
        )

    return Prices(
        checked_on=str(document.get("checked_on", "")),
        source=str(document.get("source", "")),
        thinking_billed_at=billed_at,
        rates=rates,
        path=PRICES_PATH,
    )


# --- The call logs ---
@dataclass(frozen=True)
class Call:
    """One logged API call, as much of it as pricing and reporting need.

    No book_id: every call in a report belongs to the one book the report is
    about, and the folder it was read from is what proves that.
    """

    step: str
    model: str
    item: str
    sections: tuple[str, ...]
    attempt: int
    status: str
    timestamp: str
    input_tokens: int
    cached_input_tokens: int
    thinking_tokens: int
    output_tokens: int
    total_tokens: int
    cache_storage_tokens: int
    cache_storage_seconds: float
    duration_seconds: float
    record: str
    batch: bool = False
    batch_name: str | None = None

    @property
    def counted_tokens(self) -> int:
        """The three counts this report prices, which should equal total_tokens."""
        return self.input_tokens + self.thinking_tokens + self.output_tokens

    @property
    def section_text(self) -> str:
        """The sections worked for, as the records table prints them."""
        return ", ".join(self.sections) if self.sections else "-"

    @property
    def model_label(self) -> str:
        """The model name as the by-model table prints it, with Batch marked."""
        return f"{self.model} (batch)" if self.batch else self.model

    @property
    def wasted(self) -> bool:
        """True when this call did not produce the result being kept.

        Two kinds: a call that did not succeed, and an attempt superseded by a
        later one. Both were paid for and neither is in the output.
        """
        return self.status != SUCCESS_STATUS or self.attempt > 1

    def cost(self, prices: Prices) -> float:
        """What this call cost, with thinking billed at the output rate.

        The one piece of arithmetic in this script that could poison every
        figure in the report, so it is spelled out rather than inlined: Gemini
        charges thinking tokens as output, so they are added to output before
        the output rate is applied, never to input and never dropped.
        """
        model_rates = prices.rates[self.model]
        if self.cached_input_tokens and not model_rates.explicit_cached:
            raise RuntimeError(
                f"{self.record}: {self.model} has cached input but {PRICES_PATH.name} "
                f"gives it no periods, so it has no published cached rate"
            )
        if self.batch and not model_rates.batch_periods:
            raise RuntimeError(
                f"{self.record}: {self.model} is a Batch API call but "
                f"{PRICES_PATH.name} gives it no batch_periods"
            )
        rate = model_rates.period_for(self.timestamp, batch=self.batch)
        billed_input = self.input_tokens - self.cached_input_tokens
        billed_cached = self.cached_input_tokens
        billed_output = self.output_tokens + self.thinking_tokens
        return (
            billed_input * rate.input_per_million
            + billed_cached * rate.cached_input_per_million
            + billed_output * rate.output_per_million
            + self.cache_storage_tokens
            * (self.cache_storage_seconds / 3600)
            * rate.cache_storage_per_million_per_hour
        ) / 1_000_000


def token_count(usage: dict, key: str, record: str, problems: list[str]) -> int:
    """One token count, defaulting to zero.

    api_logger drops counts the API did not report, and a failed call logs no
    usage at all, so absence is normal and reads as zero. A present count that
    is not a whole number is not, and stops the run.
    """
    value = usage.get(key, 0)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append(f"[usage] {record}: {key} is not a whole number: {value!r}")
        return 0
    if value < 0:
        problems.append(f"[usage] {record}: {key} is negative: {value!r}")
        return 0
    return value


def cached_tokens_from_response(call_path: Path) -> int:
    """Backfill old logs whose response recorded cache hits before call.json did."""
    path = call_path.with_name("response.json")
    try:
        response = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    usage = response.get("usage_metadata") or {}
    value = usage.get("cached_content_token_count", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def call_sections(document: dict, record: str, problems: list[str]) -> tuple[str, ...]:
    """Which of the book's sections a call did work for.

    Most steps name one section in section. The retired figures.py stage's two steps
    (figure_drawn, figure_checked) named a list in sections instead, because one
    figure can be used by several sections, and its logs are still on disk and were
    the largest single item in the log: read only section, and the
    figure work would look like it belonged to no section at all and cost nothing
    per section. review.py writes a count rather than a list there, because it
    reads the whole book at once; that call belongs to no section, and the
    whole-book figures charge it once for the book instead of per section.
    """
    section = document.get("section")
    if section is not None:
        if not isinstance(section, str) or not section:
            problems.append(f"[section] {record}: section is not a section number: {section!r}")
            return ()
        return (section,)

    listed = document.get("sections")
    if listed is None or isinstance(listed, int) and not isinstance(listed, bool):
        return ()
    if not isinstance(listed, list):
        problems.append(
            f"[sections] {record}: sections holds {type(listed).__name__}, not a list of "
            f"section numbers"
        )
        return ()
    named = tuple(name for name in listed if isinstance(name, str) and name)
    if len(named) != len(listed):
        problems.append(f"[sections] {record}: sections is not all section numbers: {listed!r}")
        return ()
    return named


def read_call(path: Path, book_id: str, problems: list[str]) -> Call | None:
    """One call.json as a Call, or None with the reason it could not be read.

    A record that cannot be priced is never skipped quietly: a report missing
    calls understates the spend, and understating it is the failure mode this
    whole script exists to avoid.
    """
    record = short_path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        problems.append(f"[read] {record}: {error}")
        return None
    except ValueError as error:
        problems.append(f"[json] {record}: not valid JSON: {error}")
        return None
    if not isinstance(document, dict):
        problems.append(f"[json] {record}: holds {type(document).__name__}, not an object")
        return None

    model = document.get("model")
    if not isinstance(model, str) or not model:
        problems.append(f"[model] {record}: no model recorded, so the call cannot be priced")
        return None
    step = document.get("step")
    if not isinstance(step, str) or not step:
        problems.append(f"[step] {record}: no step recorded")
        return None

    usage = document.get("usage")
    if usage is None:
        usage = {}
    if not isinstance(usage, dict):
        problems.append(f"[usage] {record}: usage holds {type(usage).__name__}, not an object")
        usage = {}

    attempt = document.get("attempt", 1)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        problems.append(f"[attempt] {record}: attempt is not a positive whole number: {attempt!r}")
        return None

    duration = document.get("duration_seconds", 0)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
        problems.append(f"[duration] {record}: duration_seconds is not a number: {duration!r}")
        return None

    # The book a call belongs to is written into the record, and the book asked
    # for is the folder it was read from; a record claiming a third book is
    # reported rather than believed, because a report about one book cannot
    # quietly include a call from another.
    logged_book = document.get("book_id")
    if isinstance(logged_book, str) and logged_book and logged_book != book_id:
        problems.append(
            f"[book] {record}: logged book_id {logged_book!r} is not the book being priced "
            f"({book_id!r})"
        )
        return None

    input_tokens = token_count(usage, "input_tokens", record, problems)
    cached_input_tokens = token_count(
        usage, "cached_input_tokens", record, problems
    )
    if not cached_input_tokens:
        cached_input_tokens = cached_tokens_from_response(path)
    if cached_input_tokens > input_tokens:
        problems.append(
            f"[usage] {record}: cached input {cached_input_tokens} exceeds "
            f"input {input_tokens}"
        )
        cached_input_tokens = 0
    storage_seconds = usage.get("cache_storage_seconds", 0)
    if (
        isinstance(storage_seconds, bool)
        or not isinstance(storage_seconds, (int, float))
        or storage_seconds < 0
    ):
        problems.append(
            f"[usage] {record}: cache_storage_seconds is invalid: "
            f"{storage_seconds!r}"
        )
        storage_seconds = 0
    timestamp = str(document.get("timestamp", ""))
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        problems.append(f"[timestamp] {record}: invalid ISO timestamp {timestamp!r}")
        return None

    # Only a literal true marks a Batch API call. translate.py once logged its
    # review chunk number here ("batch": 1) on ordinary unary calls.
    batch = document.get("batch") is True
    raw_name = document.get("batch_name")
    batch_name: str | None = None
    if batch:
        if isinstance(raw_name, str) and raw_name:
            batch_name = raw_name
        else:
            problems.append(
                f"[batch_name] {record}: batch is true but batch_name is missing"
            )

    return Call(
        step=step,
        model=model,
        item=str(document.get("page_number", "")),
        sections=call_sections(document, record, problems),
        attempt=attempt,
        status=str(document.get("status", "")),
        timestamp=timestamp,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        thinking_tokens=token_count(usage, "thinking_tokens", record, problems),
        output_tokens=token_count(usage, "output_tokens", record, problems),
        total_tokens=token_count(usage, "total_tokens", record, problems),
        cache_storage_tokens=token_count(
            usage, "cache_storage_tokens", record, problems
        ),
        cache_storage_seconds=float(storage_seconds),
        duration_seconds=float(duration),
        record=record,
        batch=batch,
        batch_name=batch_name,
    )


def logged_books() -> list[str]:
    """Every book under data/output that has a call log folder, in name order."""
    if not OUTPUT_DIR.is_dir():
        return []
    return sorted(path.name for path in OUTPUT_DIR.iterdir() if (path / LOGS_STAGE).is_dir())


def read_calls(book_id: str) -> list[Call]:
    """Every logged call for one book, oldest first."""
    problems: list[str] = []
    calls: list[Call] = []
    logs_dir = OUTPUT_DIR / book_id / LOGS_STAGE
    for path in sorted(logs_dir.glob(f"*/{CALL_FILE}")):
        call = read_call(path, book_id, problems)
        if call is not None:
            calls.append(call)

    if problems:
        raise RuntimeError(
            "\n".join(
                [
                    f"{len(problems)} log record problem(s); every call has to be priced, so "
                    f"none is skipped",
                    *(f"  {problem}" for problem in problems),
                ]
            )
        )
    return sorted(calls, key=lambda call: (call.timestamp, call.record))


def unpriced_models(calls: list[Call], prices: Prices) -> list[str]:
    """Every model in the logs with no entry in the price table, as report lines.

    Pricing an unknown model at zero would understate the total without a mark
    on the page, so this is a hard failure and it names the records.
    """
    missing: dict[str, list[Call]] = {}
    for call in calls:
        if call.model not in prices.rates:
            missing.setdefault(call.model, []).append(call)

    lines: list[str] = []
    for model, group in sorted(missing.items()):
        lines.append(f"[model] {model}: {len(group)} call(s) with no entry in {PRICES_PATH.name}")
        lines.extend(f"    {call.record}" for call in group)
    return lines


def token_sum_anomalies(calls: list[Call]) -> list[Call]:
    """Calls whose three counts do not add up to the total the API reported.

    Reported, never repaired: the report prices the three counts it can name, so
    a mismatch means the vendor counted something this script does not model,
    and that is worth seeing rather than smoothing over.
    """
    return [call for call in calls if call.counted_tokens != call.total_tokens]


# --- Aggregation ---
@dataclass
class Group:
    """Running totals for one slice of the calls."""

    calls: int = 0
    cost: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    thinking_tokens: int = 0
    output_tokens: int = 0
    duration: float = 0.0
    batch_calls: int = 0
    batch_job_durations: dict[str, float] = field(default_factory=dict)
    cache_storage_token_hours: float = 0.0
    wasted_calls: int = 0
    wasted_cost: float = 0.0
    items: set[tuple[str, str]] = field(default_factory=set)
    sections: set[str] = field(default_factory=set)

    def add(self, call: Call, cost: float) -> None:
        self.calls += 1
        self.cost += cost
        self.input_tokens += call.input_tokens
        self.cached_input_tokens += call.cached_input_tokens
        self.thinking_tokens += call.thinking_tokens
        self.output_tokens += call.output_tokens
        # Batch rows all carry the job's submit-to-done elapsed, so summing
        # them would count the same wait once per item. Count each job once,
        # at the longest elapsed any of its rows recorded. Elapsed 0.0 is an
        # adopted job whose wait was not measured, not a duration of zero.
        if call.batch:
            self.batch_calls += 1
            name = call.batch_name
            elapsed = call.duration_seconds
            if name and elapsed > 0:
                recorded = self.batch_job_durations.get(name, 0.0)
                if elapsed > recorded:
                    self.batch_job_durations[name] = elapsed
        else:
            self.duration += call.duration_seconds
        self.cache_storage_token_hours += (
            call.cache_storage_tokens * call.cache_storage_seconds / 3600
        )
        if call.wasted:
            self.wasted_calls += 1
            self.wasted_cost += cost
        self.items.add((call.step, call.item))
        self.sections.update(call.sections)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.thinking_tokens + self.output_tokens

    @property
    def batch_duration(self) -> float:
        """Submit-to-done elapsed of each Batch job, counted once per job name."""
        return sum(self.batch_job_durations.values())

    @property
    def interactive_calls(self) -> int:
        return self.calls - self.batch_calls

    @property
    def section_count(self) -> int:
        return len(self.sections)

    @property
    def item_count(self) -> int:
        return len(self.items)

    def tokens_of(self, kind: str) -> int:
        """One of the three counts by name, so a report can loop over TOKEN_KINDS."""
        return getattr(self, f"{kind}_tokens")


def once_pass_calls(calls: list[Call]) -> list[Call]:
    """One kept success per work item: a book run once, no development re-runs.

    Failed calls and earlier successes of the same ``(step, item)`` are dropped.
    The last success is the result the pipeline kept. A group translated five
    times to fix later issues is billed once.
    """
    kept: dict[tuple[str, str], Call] = {}
    for call in calls:
        if call.status != SUCCESS_STATUS:
            continue
        kept[(call.step, call.item)] = call
    return sorted(kept.values(), key=lambda call: (call.timestamp, call.record))


def totals_of(calls: list[Call], costs: dict[str, float]) -> Group:
    group = Group()
    for call in calls:
        group.add(call, costs[call.record])
    return group


def group_by(
    calls: list[Call], costs: dict[str, float], key: Callable[[Call], str]
) -> list[tuple[str, Group]]:
    """The calls grouped by one attribute, dearest slice first."""
    groups: dict[str, Group] = {}
    for call in calls:
        groups.setdefault(key(call), Group()).add(call, costs[call.record])
    return sorted(groups.items(), key=lambda pair: (-pair[1].cost, pair[0]))


# --- Projection ---
@dataclass(frozen=True)
class StepUnit:
    """What one step costs per unit of work, as this book's own log measures it.

    The unit is one section for every step that names one, and the whole book for
    a step that does not: review.py reads the book at once, and charging that
    against sections would make a long book look as though it needed more of it.
    """

    step: str
    calls: int
    sections_seen: int
    cost_per_call: float
    items_per_unit: float
    input_per_unit: float
    thinking_per_unit: float
    output_per_unit: float
    seconds_per_unit: float
    batch_seconds_per_unit: float

    @property
    def book_level(self) -> bool:
        """True for a step that runs once for the book rather than once per section."""
        return not self.sections_seen

    @property
    def cost_per_unit(self) -> float:
        return self.cost_per_call * self.items_per_unit

    def tokens_per_unit(self, kind: str) -> float:
        """One of the three per-unit counts by name, to loop over TOKEN_KINDS."""
        return getattr(self, f"{kind}_per_unit")


def step_units(calls: list[Call], costs: dict[str, float], covered: int) -> list[StepUnit]:
    """Per-step unit costs, measured over the sections this log covers.

    Work per unit is counted in work items, not in calls: the item is what
    api_logger names a log folder after and what the pipeline retries on, so six
    spec calls for one section are one item repeated, and a book done once pays
    for it once. Development re-runs would otherwise be charged to every section.

    Every section-level step is divided by the same denominator, the sections of
    this book the log covers at all, rather than by the sections that step itself
    touched. Figures are why: only some sections have any, and dividing the
    figure steps by the sections that produced figures would price every section
    of the book as though it drew as many as the ones that did. The cost of a
    step that has run against less of the book than the rest of the pipeline
    reads low for the same reason, so each step's own count is carried beside it.
    """
    units: list[StepUnit] = []
    for step, group in group_by(calls, costs, lambda call: call.step):
        seen = group.section_count
        # A step with no section to its name did the book once, so its work per
        # unit is its work, whole: one book is all this report ever holds.
        per_unit = group.item_count / covered if seen and covered else float(group.item_count)

        def per_call(value: float, group: Group = group) -> float:
            return value / group.calls if group.calls else 0.0

        # Batch seconds are the job's own elapsed, counted once, so they are
        # spread over the sections this log covers rather than mean-call-time
        # times items, which would multiply the job by the item count again.
        if seen and covered:
            batch_per_unit = group.batch_duration / covered
        else:
            batch_per_unit = group.batch_duration

        units.append(
            StepUnit(
                step=step,
                calls=group.calls,
                sections_seen=seen,
                cost_per_call=per_call(group.cost),
                items_per_unit=per_unit,
                input_per_unit=per_call(group.input_tokens) * per_unit,
                thinking_per_unit=per_call(group.thinking_tokens) * per_unit,
                output_per_unit=per_call(group.output_tokens) * per_unit,
                seconds_per_unit=per_call(group.duration) * per_unit,
                batch_seconds_per_unit=batch_per_unit,
            )
        )
    return sorted(units, key=lambda unit: (-unit.cost_per_unit, unit.step))


def book_sections(book_id: str) -> tuple[int | None, str]:
    """How many sections this book lists, and where that count came from.

    Read through sources.py, so the number is the one the pipeline itself would
    work through rather than a guess. A book whose XML is missing or unreadable
    leaves the whole-book figures without a total; the report says so and drops
    them rather than assuming a count.
    """
    path = INPUT_DIR / book_id / f"{book_id}.xml"
    if not path.is_file():
        return None, f"no book XML at {short_path(path)}"
    try:
        sections = len(section_spans(book_id, None))
    except (OSError, ET.ParseError, KeyError, ValueError) as error:
        return None, (
            f"no readable book XML at {short_path(path)} "
            f"({type(error).__name__}: {error})"
        )
    return sections, f"{short_path(path)}, as sources.py reads its table of contents"


@dataclass(frozen=True)
class Projection:
    """What this whole book costs at the unit rates its own log measures.

    Not a forecast of another book: the section count is this book's own, and
    every rate behind it was measured on these sections. What it adds over the
    actual spend is a book counted once — no re-runs, no superseded attempts —
    and the sections the pipeline has not reached yet.
    """

    units: list[StepUnit]
    covered: int
    sections: int | None
    sections_note: str

    @property
    def section_steps(self) -> list[StepUnit]:
        return [unit for unit in self.units if not unit.book_level]

    @property
    def book_steps(self) -> list[StepUnit]:
        return [unit for unit in self.units if unit.book_level]

    @property
    def cost_per_section(self) -> float:
        return sum(unit.cost_per_unit for unit in self.section_steps)

    @property
    def cost_once(self) -> float:
        """What the book-level steps cost, once, whatever the section count."""
        return sum(unit.cost_per_unit for unit in self.book_steps)

    @property
    def left(self) -> int | None:
        """Sections of the book no call in this log has touched."""
        if self.sections is None:
            return None
        return max(self.sections - self.covered, 0)

    @property
    def complete(self) -> bool:
        return self.left == 0

    @property
    def unscalable(self) -> str:
        """Why the whole book cannot be costed, or "" where it can be.

        Two ways it cannot: no section count to scale to, and nothing measuring
        what a section costs. Either one would otherwise be reported as a total,
        and a total that rests on neither is worse than none at all.
        """
        if self.sections is None:
            return f"there is {self.sections_note}"
        if not self.section_steps:
            return "no call in scope names a section"
        return ""

    @property
    def cost(self) -> float | None:
        """The whole book at these rates, every section done exactly once."""
        if self.unscalable or self.sections is None:
            return None
        return self.cost_per_section * self.sections + self.cost_once

    @property
    def cost_to_finish(self) -> float | None:
        """What the sections still untouched would add at these rates."""
        if self.unscalable or self.left is None:
            return None
        return self.cost_per_section * self.left

    def tokens_per_section(self, kind: str) -> float:
        return sum(unit.tokens_per_unit(kind) for unit in self.section_steps)

    def tokens(self, kind: str) -> float | None:
        if self.unscalable or self.sections is None:
            return None
        once = sum(unit.tokens_per_unit(kind) for unit in self.book_steps)
        return self.tokens_per_section(kind) * self.sections + once

    @property
    def seconds_per_section(self) -> float:
        return sum(unit.seconds_per_unit for unit in self.section_steps)

    @property
    def seconds(self) -> float | None:
        if self.unscalable or self.sections is None:
            return None
        once = sum(unit.seconds_per_unit for unit in self.book_steps)
        return self.seconds_per_section * self.sections + once

    @property
    def batch_seconds_per_section(self) -> float:
        return sum(unit.batch_seconds_per_unit for unit in self.section_steps)

    @property
    def batch_seconds(self) -> float | None:
        if self.unscalable or self.sections is None:
            return None
        once = sum(unit.batch_seconds_per_unit for unit in self.book_steps)
        return self.batch_seconds_per_section * self.sections + once

    @property
    def coverage(self) -> str:
        """How much of the book the log reaches, as every caveat states it."""
        if self.sections is None:
            return f"{plural(self.covered, 'section')}, of an unknown total"
        return f"{plural(self.covered, 'section')} of {self.sections}"


def build_projection(
    calls: list[Call], costs: dict[str, float], book_id: str, covered: int
) -> Projection:
    """Scale this book's own unit costs up to its own table of contents."""
    sections, note = book_sections(book_id)
    return Projection(
        units=step_units(calls, costs, covered),
        covered=covered,
        sections=sections,
        sections_note=note,
    )


# --- The priced report both outputs read ---
@dataclass
class Report:
    """One priced slice of the logs, with every figure the two reports share.

    Derived once, here, rather than in each renderer: the console page and the
    HTML page quote the same totals and the same groupings, and the only way to
    guarantee they cannot drift is for there to be one of each.
    """

    calls: list[Call]
    costs: dict[str, float]
    prices: Prices
    book_id: str
    projection: Projection
    step_filter: str | None
    once: bool = False

    total: Group = field(init=False)
    by_step: list[tuple[str, Group]] = field(init=False)
    by_model: list[tuple[str, Group]] = field(init=False)
    anomalies: list[Call] = field(init=False)
    failed: list[Call] = field(init=False)
    retries: list[Call] = field(init=False)

    def __post_init__(self) -> None:
        self.total = totals_of(self.calls, self.costs)
        self.by_step = group_by(self.calls, self.costs, lambda call: call.step)
        self.by_model = group_by(self.calls, self.costs, lambda call: call.model_label)
        self.anomalies = token_sum_anomalies(self.calls)
        self.failed = [call for call in self.calls if call.status != SUCCESS_STATUS]
        self.retries = [
            call for call in self.calls if call.status == SUCCESS_STATUS and call.attempt > 1
        ]

    @property
    def currency(self) -> str:
        return self.prices.currency

    @property
    def used_models(self) -> set[str]:
        return {call.model for call in self.calls}

    @property
    def unverified_models(self) -> list[str]:
        return sorted(
            model
            for model in self.used_models
            if not self.prices.rates[model].verified
        )

    @property
    def placeholder_models(self) -> list[str]:
        return sorted(
            model
            for model in self.used_models
            if self.prices.rates[model].placeholder
        )

    @property
    def verified(self) -> bool:
        return not self.unverified_models and not self.placeholder_models

    @property
    def records(self) -> str:
        """How many log records every figure rests on, as both reports word it."""
        return plural(len(self.calls), "log record")

    @property
    def repeats(self) -> int:
        """Calls that redo an item the pipeline had already done.

        Not waste: the log's attempt field counts attempts within one run and
        cannot tell a pipeline retry from a fresh --update, so these are shown
        and deliberately left out of the wasted figure.
        """
        return self.total.calls - self.total.item_count

    def cost_of(self, calls: list[Call]) -> float:
        return sum(self.costs[call.record] for call in calls)

    def per_call(self, value: float) -> float:
        return value / self.total.calls

    def per_section(self, value: float) -> float:
        return value / self.total.section_count if self.total.section_count else 0.0

    def per_section_text(self, value: float, form: Callable[[float], str]) -> str:
        """A per-section average, or n/a where the sample touched no section."""
        if not self.total.section_count:
            return "n/a"
        return form(value / self.total.section_count)


# --- Formatting ---
def short_path(path: Path) -> str:
    """A path relative to the project root where it is inside it."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def folder_of(record: str) -> str:
    """The log folder a record sits in, which is how the page names it."""
    return record.rsplit("/", 2)[-2] if "/" in record else record


def money(value: float, currency: str) -> str:
    return f"{currency} {value:,.2f}"


def fine_money(value: float, currency: str) -> str:
    """Money at four decimals, for per-call and per-section figures too small to round."""
    return f"{currency} {value:,.4f}"


def count(value: float) -> str:
    return f"{value:,.0f}"


def ratio(value: float) -> str:
    return f"{value:,.2f}"


def percent(part: float, whole: float) -> str:
    if not whole:
        return "n/a"
    return f"{part / whole * 100:.1f}%"


def duration_text(seconds: float) -> str:
    """A wall-clock span at the coarsest unit that still says something."""
    if seconds >= 3600:
        hours, rest = divmod(seconds, 3600)
        return f"{hours:,.0f} h {rest / 60:.0f} min"
    if seconds >= 60:
        minutes, rest = divmod(seconds, 60)
        return f"{minutes:.0f} min {rest:.0f} s"
    return f"{seconds:.1f} s"


def plural(number: int, noun: str) -> str:
    return f"{number:,} {noun}" if number == 1 else f"{number:,} {noun}s"


def known(value: float | None, form: Callable[[float], str], missing: str = "n/a") -> str:
    """A figure that may not exist: formatted where it does, marked where it does not.

    The whole-book figures are all None together when the book's section count
    cannot be read, and a report that printed a zero there would be read as free.
    """
    return missing if value is None else form(value)


# --- The console report ---
def warnings_for(report: Report) -> list[list[str]]:
    """The caveats that belong on every screen, as paragraphs of lines."""
    projection = report.projection
    notes: list[list[str]] = []
    if not report.verified:
        notes.append(
            [
                "UNVERIFIED RATES: the calls in this report use "
                f"{', '.join(report.unverified_models or report.placeholder_models)}.",
                "Money for those models remains indicative until their model-level",
                "source, checked_on date, and effective rates are verified.",
            ]
        )
    placeholders = report.placeholder_models
    if placeholders:
        notes.append(
            [
                f"PLACEHOLDER RATES: {', '.join(placeholders)} priced from made-up numbers,",
                "not from a price list. The arithmetic is real; the money is not.",
            ]
        )
    notes.append(
        [
            f"ONE BOOK: every figure below is {report.book_id} and nothing else. The evidence "
            f"is",
            f"{plural(len(report.calls), 'call')} covering {projection.coverage}, all one "
            f"subject at one grade.",
            "Nothing here measures a longer section, a harder one, or another subject.",
        ]
    )
    return notes


def print_pairs(pairs: list[tuple[str, str]], indent: str = "  ") -> None:
    width = max((len(label) for label, _ in pairs), default=0)
    for label, value in pairs:
        print(f"{indent}{label:<{width}}  {value}")


def print_breakdown(title: str, rows: list[tuple[str, Group]], total: Group, currency: str) -> None:
    print(f"\n{title}")
    label_width = max((len(name) for name, _ in rows), default=0)
    label_width = max(label_width, 5)
    print(
        f"  {'':<{label_width}}  {'calls':>6}  {'cost':>13}  {'share':>7}  "
        f"{'input':>10}  {'thinking':>10}  {'output':>10}"
    )
    for name, group in rows:
        print(
            f"  {name:<{label_width}}  {group.calls:>6,}  "
            f"{money(group.cost, currency):>13}  {percent(group.cost, total.cost):>7}  "
            f"{count(group.input_tokens):>10}  {count(group.thinking_tokens):>10}  "
            f"{count(group.output_tokens):>10}"
        )


def print_spend(report: Report) -> None:
    """Actual spend: the totals, the three breakdowns, waste, then averages."""
    currency, total = report.currency, report.total

    print("\nActual spend")
    thinking_share = percent(total.thinking_tokens, total.tokens)
    print_pairs(
        [
            ("cost", money(total.cost, currency)),
            ("calls", count(total.calls)),
            ("input tokens", count(total.input_tokens)),
            ("  cached input", count(total.cached_input_tokens)),
            (
                "  cache storage token-hours",
                f"{total.cache_storage_token_hours:.4f}",
            ),
            ("thinking tokens", f"{count(total.thinking_tokens)}  ({thinking_share} of tokens)"),
            ("output tokens", count(total.output_tokens)),
            ("total tokens", count(total.tokens)),
            ("sections touched", count(total.section_count)),
            ("work items", count(total.item_count)),
        ]
    )

    print_breakdown("By step", report.by_step, total, currency)
    print_breakdown("By model", report.by_model, total, currency)

    print("\nWasted spend (calls that did not produce a kept result)")
    cost_share = percent(total.wasted_cost, total.cost)
    calls_share = percent(total.wasted_calls, total.calls)
    print_pairs(
        [
            ("wasted cost", f"{money(total.wasted_cost, currency)}  ({cost_share} of spend)"),
            ("wasted calls", f"{count(total.wasted_calls)}  ({calls_share} of calls)"),
            ("  status not success", count(len(report.failed))),
            ("  superseded attempts", count(len(report.retries))),
        ]
    )
    print(
        f"  Separately, and not counted above: {total.calls:,} call(s) cover "
        f"{total.item_count:,} work item(s),"
    )
    print(
        f"  so {report.repeats:,} call(s) repeat an item the pipeline had already done. The "
        f"log's attempt"
    )
    print("  field cannot tell a pipeline retry from a fresh --update run, so these are shown")
    print("  rather than billed as waste.")

    print("\nAverages and wall clock")
    sequential_per_call = (
        total.duration / total.interactive_calls if total.interactive_calls else 0.0
    )
    print_pairs(
        [
            ("cost per call", fine_money(report.per_call(total.cost), currency)),
            ("cost per section",
             report.per_section_text(total.cost, lambda value: fine_money(value, currency))),
            ("tokens per call", count(report.per_call(total.tokens))),
            ("tokens per section", report.per_section_text(total.tokens, count)),
            ("Sequential wall clock", duration_text(total.duration)),
            ("Sequential wall clock, per call", duration_text(sequential_per_call)),
            ("Sequential wall clock, per section",
             report.per_section_text(total.duration, duration_text)),
            (
                "Batch job time (queue + processing, jobs run in parallel)",
                duration_text(total.batch_duration),
            ),
        ]
    )


def print_projection(report: Report) -> None:
    """The whole book at its own unit costs: the per-step units, then the totals."""
    currency, projection, total = report.currency, report.projection, report.total

    print("\nThe whole book at these rates")
    print("  Unit costs measured on this book itself: for each step, the mean cost of one call")
    print("  times how many calls one section needs. Work is counted in items, so a section")
    print("  the pipeline redid six times counts once. Sections is how much of the book that")
    print("  step has reached; one that has reached less of it than the rest reads low.")
    if projection.section_steps:
        print(
            f"\n  {'step':<20}  {'calls':>6}  {'sections':>8}  {'items/sec':>9}  "
            f"{'cost/call':>13}  {'cost/section':>15}"
        )
        for unit in projection.section_steps:
            print(
                f"  {unit.step:<20}  {unit.calls:>6,}  {unit.sections_seen:>8,}  "
                f"{ratio(unit.items_per_unit):>9}  "
                f"{fine_money(unit.cost_per_call, currency):>13}  "
                f"{fine_money(unit.cost_per_unit, currency):>15}"
            )
    if projection.book_steps:
        print("\n  Once for the book, not once per section")
        print_pairs(
            [
                (
                    unit.step,
                    f"{unit.calls:>3} call(s)  {fine_money(unit.cost_per_unit, currency)}",
                )
                for unit in projection.book_steps
            ],
            indent="    ",
        )

    print()
    print_pairs(
        [
            (
                "cost per section",
                fine_money(projection.cost_per_section, currency)
                if projection.section_steps
                else "n/a",
            ),
            (
                "sections in the book",
                f"{known(projection.sections, count)}  ({projection.sections_note})",
            ),
            ("sections covered so far", count(projection.covered)),
            ("sections still to do", known(projection.left, count)),
            ("cost of the whole book", known(projection.cost, lambda v: money(v, currency))),
            ("cost to finish", known(projection.cost_to_finish, lambda v: money(v, currency))),
            ("input tokens", known(projection.tokens("input"), count)),
            ("thinking tokens", known(projection.tokens("thinking"), count)),
            ("output tokens", known(projection.tokens("output"), count)),
            ("Sequential wall clock", known(projection.seconds, duration_text)),
            (
                "Batch job time (queue + processing, jobs run in parallel)",
                known(projection.batch_seconds, duration_text),
            ),
        ]
    )
    print("  Sequential wall clock is interactive calls run one after another. Batch job")
    print("  time is per job (queue + processing); jobs run in parallel.")

    whole = projection.cost
    if whole is None:
        print(f"\n  WARNING: {projection.unscalable}, so the whole book cannot be costed;")
        print("  what has actually been spent, above, is all this log can say.")
        return
    print()
    if projection.complete:
        print(
            f"  Every section of the book has been through the pipeline. Getting here cost "
            f"{money(total.cost, currency)},"
        )
        print(
            f"  and the same book done once, with no re-runs, comes to "
            f"{money(whole, currency)}."
        )
    else:
        print(
            f"  {plural(projection.left or 0, 'section')} still to do: about "
            f"{money(projection.cost_to_finish or 0.0, currency)} more at these rates, on top"
        )
        print(f"  of the {money(total.cost, currency)} already spent.")


def print_report(report: Report) -> None:
    """The whole report on stdout: actual spend first, then the whole book."""
    headline = f"{plural(len(report.calls), 'call')} logged for {report.book_id}"
    if report.once:
        headline = (
            f"{plural(len(report.calls), 'kept success')} for {report.book_id} "
            f"(one pass, last success per item)"
        )
    if report.step_filter:
        headline += f", step {report.step_filter} only"
    print(f"{'One-pass cost report' if report.once else 'Cost report'}: {headline}")
    print(f"Priced from {short_path(report.prices.path)}")

    for note in warnings_for(report):
        print()
        for line in note:
            print(f"  {line}")

    print_spend(report)
    print_projection(report)

    if report.anomalies:
        print(f"\nToken counts that do not add up ({len(report.anomalies)} record(s))")
        for call in report.anomalies:
            print(
                f"  {call.record}: input+thinking+output = {call.counted_tokens:,}, "
                f"total_tokens = {call.total_tokens:,}"
            )
    else:
        print(
            f"\nToken counts check out: input + thinking + output equals total_tokens in "
            f"all {len(report.calls)} record(s)."
        )


# --- The HTML report: markup helpers ---
def load_style() -> str:
    """The style guide's full CSS reference block, verbatim.

    Taken from the guide at generation time rather than pasted into this file, so
    that a generated page and a hand-written one such as split.html cannot end
    up carrying different stylesheets.
    """
    try:
        guide = STYLE_GUIDE_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"cannot read the style guide at {STYLE_GUIDE_PATH}: {error}") from error
    blocks = re.findall(r"```css\n(.*?)```", guide, re.S)
    if not blocks:
        raise RuntimeError(f"{STYLE_GUIDE_PATH.name} has no css block to copy")
    style = blocks[-1].strip()
    digest = hashlib.sha256(style.encode("utf-8")).hexdigest()
    if digest != STYLE_SHA256:
        raise RuntimeError(
            f"the CSS in {STYLE_GUIDE_PATH.name} hashes to {digest}, not the "
            f"{STYLE_SHA256} this script was written against; review the change and update "
            f"STYLE_SHA256 in {Path(__file__).name}"
        )
    return style


def esc(value: object) -> str:
    """Everything that reaches the page goes through here.

    Book ids, model names, step names and section titles are all data read off
    disk, so a stray angle bracket, ampersand or quote in any of them would
    otherwise break the markup of a page nobody hand-checks. Quotes are escaped
    too, because some of these values land in attributes.
    """
    return html.escape(str(value), quote=True)


def code(text: object) -> str:
    return f"<code>{esc(text)}</code>"


def caption(text: str) -> list[str]:
    return [f'    <p class="caption">{text}</p>']


def panel(kind: str, lead: str, body: str) -> list[str]:
    return [
        f'    <div class="panel {kind}">',
        f"      <p><strong>{esc(lead)}</strong> {body}</p>",
        "    </div>",
    ]


def stat_grid(stats: list[tuple[str, str]]) -> list[str]:
    lines = ['    <div class="stat-grid">']
    for value, label in stats:
        lines.append(
            f'      <div class="stat"><span class="value">{esc(value)}</span>'
            f'<span class="label">{esc(label)}</span></div>'
        )
    lines.append("    </div>")
    return lines


Cells = list[tuple[str, object]]


def row(first: tuple[str, object], *numbers: object) -> Cells:
    """One header or body row: a labelled first cell, then right-aligned numbers.

    Nearly every table here is one name against a run of figures, so the num
    class is applied once by shape rather than typed out per cell.
    """
    return [first, *(("num", number) for number in numbers)]


def table(headers: Cells, rows: list[Cells]) -> list[str]:
    """A table from (class, text) cells; the class carries mono or num alignment."""
    lines = ["    <table>", "      <tr>"]
    for css_class, text in headers:
        attrs = f' class="{css_class}"' if css_class else ""
        lines.append(f"        <th{attrs}>{esc(text)}</th>")
    lines.append("      </tr>")
    for body_row in rows:
        lines.append("      <tr>")
        for css_class, text in body_row:
            attrs = f' class="{css_class}"' if css_class else ""
            lines.append(f"        <td{attrs}>{esc(text)}</td>")
        lines.append("      </tr>")
    lines.append("    </table>")
    return lines


def bars(rows: list[tuple[str, float, str]], alt: set[str] | None = None) -> list[str]:
    """Rows of label, bar and printed value, largest bar at BAR_MAX_PERCENT."""
    alt = alt or set()
    largest = max((value for _, value, _ in rows), default=0.0)
    lines = []
    for label, value, printed in rows:
        width = (value / largest * BAR_MAX_PERCENT) if largest > 0 else 0.0
        css_class = "bar-row alt" if label in alt else "bar-row"
        lines.append(
            f'    <div class="{css_class}"><span>{esc(label)}</span>'
            f'<div class="bar" style="width:{width:.1f}%"></div>'
            f'<span class="pct">{esc(printed)}</span></div>'
        )
    return lines


# --- The HTML report: one function per section of the page ---
def basis_section(report: Report) -> tuple[str, list[str]]:
    """What the page counts, and the caveats that have to be read before a figure."""
    prices, projection, total = report.prices, report.projection, report.total
    records = esc(report.records)
    if report.once:
        counted = (
            f"This page prices {code(report.book_id)} as if the pipeline had run the book "
            f"once. Only the last successful call per work item is counted; failed attempts "
            f"and later re-runs that fixed earlier issues are dropped. The {records} below "
            f"are that kept slice of "
            f"{code(f'data/output/{report.book_id}/{LOGS_STAGE}/')}, not every call on disk."
        )
    else:
        counted = (
            f"This page prices the API calls the pipeline has made against one book, "
            f"{code(report.book_id)}. Every figure comes from the {records} that "
            f"{code('api_logger.py')} wrote under "
            f"{code(f'data/output/{report.book_id}/{LOGS_STAGE}/')}; nothing here calls a "
            f"model, reads the network, or looks at anything but those files. Another book "
            f"has its own log, its own section count and its own report."
        )
    body = [f"    <p>{counted}</p>"]
    if not report.verified:
        body += panel(
            "warn",
            "The rates are unverified.",
            f"The calls use {esc(', '.join(report.unverified_models or report.placeholder_models))}; "
            f"verify that model's effective rates, source, and checked date in "
            f"{code(short_path(prices.path))} before quoting its money figures.",
        )
    body += panel(
        "warn",
        "The evidence is one book, at one grade, in one subject.",
        f"{records} covering {esc(projection.coverage)}. The whole-book figures below are "
        f"this book scaled to its own table of contents, not a rate that carries to another "
        f"book: nothing here measures a longer section, a harder one, or a subject whose "
        f"sections draw more figures.",
    )
    body += panel(
        "note",
        "Thinking tokens are billed as output.",
        f"Gemini charges a model's thinking tokens at its output rate, so cost is "
        f"{code('input x input_rate + (output + thinking) x output_rate')}. Thinking is "
        f"{esc(percent(total.thinking_tokens, total.tokens))} of all tokens counted here, so "
        f"it is shown as its own column and its own row everywhere rather than folded into "
        f"output.",
    )
    if report.once:
        body += panel(
            "note",
            "One pass: last success per work item.",
            f"A group or figure that the pipeline translated several times while fixing "
            f"issues is billed once, at the last success. The companion page "
            f"{code(REPORT_FILE)} still prices every logged call.",
        )
    if report.step_filter:
        body += panel(
            "note",
            "Filtered.",
            f"Only the {code(report.step_filter)} step is counted on this page, so the totals "
            f"are a slice of the log rather than everything spent.",
        )
    return "What this report counts", body


def totals_section(report: Report) -> tuple[str, list[str]]:
    """The headline figures, and the three token counts against each other."""
    currency, total = report.currency, report.total
    records = esc(report.records)
    if report.once:
        spend = (
            f"What {code(report.book_id)} costs when each work item is done once, across "
            f"{esc(plural(total.section_count, 'section'))} of it."
        )
    else:
        spend = (
            f"What the pipeline has spent on {code(report.book_id)} so far, across "
            f"{esc(plural(total.section_count, 'section'))} of it."
        )
    body = [f"    <p>{spend}</p>"]
    body += stat_grid(
        [
            (money(total.cost, currency), "Cost so far"),
            (count(total.calls), "API calls"),
            (count(total.thinking_tokens), "Thinking tokens"),
            (count(total.tokens), "Tokens, all kinds"),
            (duration_text(total.duration), "Sequential wall clock"),
            (
                duration_text(total.batch_duration),
                "Batch job time (queue + processing, jobs run in parallel)",
            ),
        ]
    )
    body += caption(f"From {records}, priced with {code(short_path(report.prices.path))}.")
    body += table(
        row(("", "Token kind"), "Tokens", "Share") + [("", "Billed at")],
        [
            row(("mono", kind), count(tokens), percent(tokens, total.tokens)) + [("", billed_at)]
            for kind, tokens, billed_at in (
                ("input", total.input_tokens, "input rate"),
                ("thinking", total.thinking_tokens, "output rate"),
                ("output", total.output_tokens, "output rate"),
            )
        ]
        + (
            [
                row(
                    ("mono", "cached input (subset)"),
                    count(total.cached_input_tokens),
                    percent(total.cached_input_tokens, total.input_tokens),
                )
                + [("", "cached input rate")]
            ]
            if total.cached_input_tokens
            else []
        )
        + [
            row(
                ("mono", "cache storage token-hours"),
                f"{total.cache_storage_token_hours:.4f}",
                "",
            )
            + [("", "storage rate; USD unchanged")]
        ]
        + [row(("mono", "total"), count(total.tokens), "100.0%") + [("", "")]],
    )
    body += bars(
        [(kind, float(total.tokens_of(kind)), count(total.tokens_of(kind))) for kind in TOKEN_KINDS],
        alt={"thinking"},
    )
    body += caption(
        f"Token counts summed over {records}. The thinking series is green because it "
        f"is the count most often overlooked and here the largest."
    )
    return "Actual spend", body


def breakdown_section(
    report: Report, title: str, label: str, rows: list[tuple[str, Group]], intro: str
) -> tuple[str, list[str]]:
    """One of the three breakdowns: the money and tokens as a table, then as bars.

    By book, by step and by model are the same shape over a different key, so
    they are one function; only the key and the paragraph above it differ.
    """
    currency, total = report.currency, report.total
    records = esc(report.records)
    body = [intro]
    body += table(
        row(("", label), "Calls", f"Cost ({esc(currency)})", "Share", "Input", "Thinking",
            "Output", "Wasted"),
        [
            row(("mono", name), count(group.calls), f"{group.cost:,.4f}",
                percent(group.cost, total.cost), count(group.input_tokens),
                count(group.thinking_tokens), count(group.output_tokens),
                f"{group.wasted_cost:,.4f}")
            for name, group in rows
        ],
    )
    body += bars([(name, group.cost, f"{group.cost:,.2f}") for name, group in rows])
    body += caption(
        f"Cost in {esc(currency)} per {esc(label.lower())}, from {records}. Bar widths are "
        f"scaled so the largest spans {BAR_MAX_PERCENT:.0f}% of the track; the printed "
        f"number is the figure itself."
    )
    return title, body


def by_step_section(report: Report) -> tuple[str, list[str]]:
    return breakdown_section(
        report, "Spend by step", "Step", report.by_step,
        "    <p>One row per pipeline step that called a model, as the step name in each "
        "record spells it. A step the pipeline has not reached yet has no row: it has spent "
        "nothing, which is not the same as being free.</p>",
    )


def by_model_section(report: Report) -> tuple[str, list[str]]:
    return breakdown_section(
        report, "Spend by model", "Model", report.by_model,
        f"    <p>Every model that appears in the log needs an entry in "
        f"{code(short_path(report.prices.path))}. One that does not stops the run rather than "
        f"being priced at zero.</p>",
    )


def waste_section(report: Report) -> tuple[str, list[str]]:
    """What was paid for and is not in the output, kept apart from repeated work."""
    currency, total = report.currency, report.total
    kept_calls = total.calls - total.wasted_calls
    kept_cost = total.cost - total.wasted_cost
    body = [
        f"    <p>Wasted spend is what was paid for and is not in the output: a call whose "
        f"{code('status')} is not {code('success')}, and an attempt superseded by a later "
        f"one ({code('attempt')} above 1). Both are counted below and both are also "
        f"included in the totals above, because the money left the account either way.</p>"
    ]
    body += table(
        row(("", "Kind"), "Calls", "Of all calls", f"Cost ({esc(currency)})", "Of all spend"),
        [
            row(("", kind), count(calls), percent(calls, total.calls), f"{cost:,.4f}",
                percent(cost, total.cost))
            for kind, calls, cost in (
                (f"Status other than {esc(SUCCESS_STATUS)}", len(report.failed),
                 report.cost_of(report.failed)),
                ("Superseded attempts (attempt above 1)", len(report.retries),
                 report.cost_of(report.retries)),
                ("Wasted spend, total", total.wasted_calls, total.wasted_cost),
                ("Spend that produced a kept result", kept_calls, kept_cost),
            )
        ],
    )
    body += caption(
        f"Counted over {esc(report.records)} by {code('status')} and {code('attempt')}."
    )
    body += panel(
        "note",
        "Repeated work is not the same thing.",
        f"{esc(count(total.calls))} calls cover {esc(count(total.item_count))} work items, "
        f"so {esc(count(report.repeats))} call(s) redo an item the pipeline had already "
        f"completed. Those are development re-runs, not retries: the log's "
        f"{code('attempt')} field counts attempts within one run and cannot tell a retry "
        f"from a fresh {code('--update')}. They are reported here and deliberately left out "
        f"of the wasted figure above.",
    )
    return "Wasted spend", body


def throughput_section(report: Report) -> tuple[str, list[str]]:
    """Per-call and per-section averages, and the wall clock behind them."""
    currency, total = report.currency, report.total
    records = esc(report.records)
    sequential_per_call = (
        total.duration / total.interactive_calls if total.interactive_calls else 0.0
    )
    body = [
        f"    <p>Per-call and per-section averages, and the wall-clock time the calls "
        f"took. Sequential wall clock sums {code('duration_seconds')} of interactive "
        f"(non-batch) calls; a run that calls in parallel finishes sooner than that sum. "
        f"Batch job time is the submit-to-done elapsed of each Gemini Batch job, counted "
        f"once per job (queue + processing); jobs run in parallel.</p>"
    ]
    body += table(
        row(("", "Measure"), "Per call", "Per section"),
        [
            row(("", measure), per_call, per_section)
            for measure, per_call, per_section in (
                (
                    f"Cost ({esc(currency)})",
                    f"{report.per_call(total.cost):,.4f}",
                    report.per_section_text(
                        total.cost, lambda value: f"{value:,.4f}"
                    ),
                ),
                (
                    "Input tokens",
                    count(report.per_call(float(total.input_tokens))),
                    report.per_section_text(float(total.input_tokens), count),
                ),
                (
                    "Thinking tokens",
                    count(report.per_call(float(total.thinking_tokens))),
                    report.per_section_text(float(total.thinking_tokens), count),
                ),
                (
                    "Output tokens",
                    count(report.per_call(float(total.output_tokens))),
                    report.per_section_text(float(total.output_tokens), count),
                ),
                (
                    "Sequential wall clock",
                    duration_text(sequential_per_call),
                    report.per_section_text(total.duration, duration_text),
                ),
                (
                    "Batch job time (queue + processing, jobs run in parallel)",
                    "n/a",
                    report.per_section_text(total.batch_duration, duration_text),
                ),
            )
        ],
    )
    body += caption(
        f"Averages over {records} and "
        f"{esc(plural(total.section_count, 'distinct section'))}; sequential wall clock "
        f"{esc(duration_text(total.duration))}. Batch job time (queue + processing, jobs "
        f"run in parallel) {esc(duration_text(total.batch_duration))}."
    )
    body += table(
        row(("", "Step"), "Calls", f"Mean cost ({esc(currency)})", "Mean tokens",
            "Mean wall clock", "Batch job time"),
        [
            row(
                ("mono", step),
                count(group.calls),
                f"{group.cost / group.calls:,.4f}",
                count(group.tokens / group.calls),
                (
                    duration_text(group.duration / group.interactive_calls)
                    if group.interactive_calls
                    else "—"
                ),
                (
                    duration_text(group.batch_duration)
                    if group.batch_job_durations
                    else "—"
                ),
            )
            for step, group in report.by_step
        ],
    )
    body += caption(
        f"Per-call means by step, over the same {records}. Batch job time is counted "
        f"once per job, not per row."
    )
    return "Averages and wall clock", body


def projection_section(report: Report) -> tuple[str, list[str]]:
    """What this whole book costs at the unit rates its own log measures."""
    currency, projection, total = report.currency, report.projection, report.total
    records = esc(report.records)
    body = [
        f"    <p>What this book costs from end to end, which is not what it has cost so far. "
        f"Spend to date carries every development re-run; a book done once pays for each "
        f"section once. So the figures here are built from unit costs: for each step, the "
        f"mean cost of one call multiplied by how many calls one section needs. Work per "
        f"section is counted in work items — what {code('api_logger.py')} names a log folder "
        f"after and what the pipeline retries on — so six spec calls against one section "
        f"count as one section's worth of work, not six.</p>"
    ]
    # The fourth figure is whichever of the two the book's state makes worth
    # reading: what is left to pay where the pipeline has sections to go, and
    # what it has already cost where it has none.
    if projection.complete:
        remaining = (money(total.cost, currency), "Spent getting here")
    elif projection.cost_to_finish is not None:
        remaining = (
            money(projection.cost_to_finish, currency),
            f"To finish {plural(projection.left or 0, 'section')}",
        )
    else:
        remaining = ("—", "To finish the book")
    body += stat_grid(
        [
            (
                known(projection.cost, lambda value: money(value, currency), "—"),
                "The whole book",
            ),
            (
                fine_money(projection.cost_per_section, currency)
                if projection.section_steps
                else "—",
                "Per section",
            ),
            (known(projection.sections, count, "—"), "Sections in the book"),
            remaining,
            (known(projection.seconds, duration_text, "—"), "Sequential wall clock"),
            (
                known(projection.batch_seconds, duration_text, "—"),
                "Batch job time (queue + processing, jobs run in parallel)",
            ),
        ]
    )
    clock = (
        "Sequential wall clock assumes interactive calls run one after another, which a "
        "run with workers does not. Batch job time is per job (queue + processing); jobs "
        "run in parallel."
    )
    if reason := projection.unscalable:
        body += caption(f"Measured from {records}. {clock}")
        body += panel(
            "warn",
            "The whole book cannot be costed from this log.",
            f"Because {esc(reason)}, every whole-book figure above is shown as a dash. What "
            f"has actually been spent is not affected: those figures are counted from the "
            f"log rather than scaled from it.",
        )
    else:
        body += caption(
            f"Scaled from {records} to the "
            f"{esc(plural(projection.sections or 0, 'section'))} this book lists in "
            f"{esc(projection.sections_note)}. {clock}"
        )
    sections = projection.sections
    body += table(
        row(("", "Step"), "Calls", "Sections seen", "Items/section",
            f"Cost/call ({esc(currency)})", f"Cost/section ({esc(currency)})",
            f"Whole book ({esc(currency)})"),
        [
            row(("mono", unit.step), count(unit.calls), count(unit.sections_seen),
                ratio(unit.items_per_unit), f"{unit.cost_per_call:,.4f}",
                f"{unit.cost_per_unit:,.4f}",
                "—" if sections is None else f"{unit.cost_per_unit * sections:,.2f}")
            for unit in projection.section_steps
        ],
    )
    body += caption(
        f"Unit costs by step, over the {esc(plural(projection.covered, 'section'))} of this "
        f"book the log covers. Sections seen is how much of it each step has reached: a step "
        f"that has reached less than the others has its cost spread over the whole book all "
        f"the same, so it reads low until the pipeline catches up."
    )
    if projection.book_steps:
        body += table(
            row(("", "Step"), "Calls", f"Cost per book ({esc(currency)})"),
            [
                row(("mono", unit.step), count(unit.calls), f"{unit.cost_per_unit:,.4f}")
                for unit in projection.book_steps
            ],
        )
        body += caption(
            "Steps that name no section, because they read the book whole. They are charged "
            "once per book rather than per section, so a longer book does not multiply them."
        )
    body += bars(
        [
            (unit.step, unit.cost_per_unit, f"{unit.cost_per_unit:,.4f}")
            for unit in projection.section_steps
        ]
    )
    body += caption(f"Cost per section by step, in {esc(currency)}, from {records}.")
    body += table(
        row(("", "Token kind"), "Per section", "Whole book"),
        [
            row(("mono", kind), count(projection.tokens_per_section(kind)),
                known(projection.tokens(kind), count, "—"))
            for kind in TOKEN_KINDS
        ],
    )
    body += caption("Tokens for the book done once, thinking kept separate from output.")
    if projection.cost is not None:
        if projection.complete:
            if report.once:
                body += panel(
                    "good",
                    "Every section has been through the pipeline.",
                    f"This one-pass slice comes to {esc(money(total.cost, currency))}. Scaled "
                    f"to the whole book at these unit rates it is "
                    f"{esc(money(projection.cost, currency))}. Failed attempts and later "
                    f"fix-up re-runs are not in either figure.",
                )
            else:
                body += panel(
                    "good",
                    "Every section has been through the pipeline.",
                    f"Getting here cost {esc(money(total.cost, currency))}, and the same book "
                    f"done once, with no re-runs and no superseded attempts, comes to "
                    f"{esc(money(projection.cost, currency))}. The difference is repeated work, "
                    f"which a second book would not have to pay for.",
                )
        else:
            body += panel(
                "note",
                "The pipeline has not finished this book.",
                f"{esc(plural(projection.left or 0, 'section'))} of it have had no call "
                f"logged against them, which at the rates above is about "
                f"{esc(money(projection.cost_to_finish or 0.0, currency))} more on top of "
                f"the {esc(money(total.cost, currency))} already spent.",
            )
    return "What the whole book costs", body


def sample_limits_section(report: Report) -> tuple[str, list[str]]:
    """What the whole-book figures cannot know, so none of them is quoted as a budget."""
    calls, projection, total = report.calls, report.projection, report.total
    records = esc(report.records)
    body = [
        "    <p>What the figures above cannot know, stated plainly so that none of them is "
        "quoted as a budget — least of all as the cost of a book that is not this one.</p>"
    ]
    body += panel(
        "warn",
        "One book, one subject, one grade.",
        f"Everything on this page is {code(report.book_id)}: {records} covering "
        f"{esc(projection.coverage)}. Section length drives input tokens directly and figure "
        f"count drives the dearest step, and one book shows neither spread.",
    )
    thinking_range = [call.thinking_tokens for call in calls]
    body += [
        "    <ul>",
        f"      <li><strong>Thinking is the loosest term.</strong> Thinking is "
        f"{esc(percent(total.thinking_tokens, total.tokens))} of all tokens counted here, and "
        f"across the {esc(plural(len(calls), 'call'))} in scope it ranges from "
        f"{esc(count(min(thinking_range)))} to {esc(count(max(thinking_range)))} tokens for "
        f"work of the same shape. It is billed at the output rate, so that spread lands "
        f"straight on the bill.</li>",
    ]
    thin = [unit for unit in projection.section_steps if unit.sections_seen < projection.covered]
    if thin:
        body.append(
            "      <li><strong>Some steps have not reached every section.</strong> "
            + "; ".join(
                f"{code(unit.step)} names {esc(count(unit.sections_seen))} of "
                f"{esc(count(projection.covered))}"
                for unit in thin
            )
            + ". Their spend is still spread over the whole book, which is the right "
            "average for a book shaped like this one — a section with no figure costs "
            "nothing to draw — and the wrong one for a book where every section needs "
            "what they do.</li>"
        )
    if total.wasted_calls:
        body.append(
            f"      <li><strong>Retry overhead is measured, barely.</strong> "
            f"{esc(plural(total.wasted_calls, 'call'))} of {esc(count(total.calls))} did not "
            f"produce a kept result, which is too few to read a failure rate from. The "
            f"whole-book figures count each section once and so carry none of it.</li>"
        )
    else:
        body.append(
            f"      <li><strong>No failures at all in the log.</strong> Every record reports "
            f"{code(SUCCESS_STATUS)} on {code('attempt')} 1, so the whole-book figures carry "
            f"no retry overhead whatever. A harder book will see failures, and nothing here "
            f"makes an allowance for them.</li>"
        )
    if not report.verified:
        body.append(
            f"      <li><strong>Unverified rates.</strong> Money for "
            f"{esc(', '.join(report.unverified_models or report.placeholder_models))} "
            f"is indicative; token counts remain measured.</li>"
        )
    body.append("    </ul>")
    body += caption(f"Limits read off the same {records} the figures above are built from.")
    return "What this book cannot tell us", body


def prices_section(report: Report) -> tuple[str, list[str]]:
    """The price table as it was read, so every figure can be recomputed by hand."""
    prices, currency = report.prices, report.currency
    body = [
        f"    <p>Rates come from {code(short_path(prices.path))}, keyed by model name, in "
        f"{esc(currency)} per million tokens. The billing rule is data in that file rather "
        f"than an expression in the code: thinking tokens are charged at the "
        f"{code(prices.thinking_billed_at)} rate.</p>"
    ]
    body += table(
        [("", "Field"), ("", "Value")],
        [
            [("mono", name), ("mono", value)]
            for name, value in (
                ("report models verified", "true" if report.verified else "false"),
                ("checked_on", prices.checked_on or "(empty)"),
                ("source", prices.source or "(empty)"),
                ("billing_rule.thinking_tokens_billed_at", prices.thinking_billed_at),
            )
        ],
    )
    price_rows: list[list[tuple[str, str]]] = []
    for model, rate in sorted(prices.rates.items()):
        price_rows.append(
            row(("mono", model), f"{rate.input_per_million:,.4f}",
                f"{rate.output_per_million:,.4f}")
            + [
                ("", "yes" if rate.verified and not rate.placeholder else "no"),
                (
                    "",
                    f"{rate.current.effective_from.isoformat()}–"
                    f"{rate.current.effective_to.isoformat() if rate.current.effective_to else 'open'}",
                ),
            ]
        )
        if rate.batch_periods:
            today = date.today()
            batch_current = next(
                (period for period in rate.batch_periods if period.contains(today)),
                rate.batch_periods[-1],
            )
            price_rows.append(
                row(
                    ("mono", f"{model} (batch)"),
                    f"{batch_current.input_per_million:,.4f}",
                    f"{batch_current.output_per_million:,.4f}",
                )
                + [
                    ("", "yes" if rate.verified and not rate.placeholder else "no"),
                    (
                        "",
                        f"{batch_current.effective_from.isoformat()}–"
                        f"{batch_current.effective_to.isoformat() if batch_current.effective_to else 'open'}",
                    ),
                ]
            )
    body += table(
        row(("", "Model"), f"Input / M ({esc(currency)})", f"Output / M ({esc(currency)})")
        + [("", "Verified"), ("", "Effective")],
        price_rows,
    )
    body += caption(
        f"The table as read at generation time. Thinking tokens are added to output before "
        f"the output rate is applied, per {code('billing_rule')}."
    )
    for model, rate in sorted(prices.rates.items()):
        if rate.note:
            body += panel("warn", f"{model}:", esc(rate.note))
    return "The price table", body


def records_section(report: Report) -> tuple[str, list[str]]:
    """Every priced record, so any figure above can be traced back to a file."""
    calls, currency = report.calls, report.currency
    records = esc(report.records)
    body = [
        "    <p>Every record behind the figures above, oldest first, priced individually. "
        "The record is the name of its log folder. Section is the section the call did work "
        "for; a figure used by two of them names both, and a step that reads the book whole "
        "names none.</p>"
    ]
    body += table(
        [
            ("", "Record"), ("", "Step"), ("", "Section"), ("num", "Att."), ("", "Status"),
            ("num", "Input"), ("num", "Thinking"), ("num", "Output"),
            ("num", f"Cost ({esc(currency)})"), ("num", "Seconds"),
        ],
        [
            [("mono", folder_of(call.record)), ("mono", call.step), ("mono", call.section_text),
             ("num", str(call.attempt)), ("mono", call.status)]
            + row(("num", count(call.input_tokens)), count(call.thinking_tokens),
                  count(call.output_tokens), f"{report.costs[call.record]:,.4f}",
                  f"{call.duration_seconds:,.1f}")
            for call in calls
        ],
    )
    body += caption(
        f"{records} read from "
        f"{code(f'data/output/{report.book_id}/{LOGS_STAGE}/*/{CALL_FILE}')}."
    )
    if report.anomalies:
        body += panel(
            "warn",
            "Token counts that do not add up.",
            f"{esc(plural(len(report.anomalies), 'record'))} where "
            f"{code('input + thinking + output')} does not equal {code('total_tokens')}: "
            + "; ".join(
                f"{esc(call.record)} ({call.counted_tokens:,} against {call.total_tokens:,})"
                for call in report.anomalies
            )
            + ". The three counts are what this report prices, so a mismatch means the API "
            "counted something the report does not model.",
        )
    else:
        body += panel(
            "good",
            "Token counts reconcile.",
            f"{code('input + thinking + output')} equals {code('total_tokens')} in all "
            f"{esc(plural(len(calls), 'record'))}.",
        )
    return "The log records behind the figures", body


# The page's sections, keyed by the id the sidebar links to. NAV_GROUPS decides
# the order they appear in; this decides what each one holds.
SECTION_BUILDERS: dict[str, Callable[[Report], tuple[str, list[str]]]] = {
    "basis": basis_section,
    "totals": totals_section,
    "by-step": by_step_section,
    "by-model": by_model_section,
    "waste": waste_section,
    "throughput": throughput_section,
    "projection": projection_section,
    "sample-limits": sample_limits_section,
    "prices": prices_section,
    "records": records_section,
}


def render_html(report: Report) -> str:
    """The whole report as one self-contained page: no scripts, no external files."""
    style = load_style()
    sections = {name: build(report) for name, build in SECTION_BUILDERS.items()}

    ordered = [section_id for _, ids in NAV_GROUPS for section_id in ids]
    if set(ordered) != set(sections) or len(ordered) != len(sections):
        raise RuntimeError(
            f"the sidebar lists {sorted(ordered)} but the page builds "
            f"{sorted(sections)}; every nav link has to name a section"
        )

    calls, projection = report.calls, report.projection
    lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="UTF-8" />',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0" />',
        f"  <title>{'One-pass cost report' if report.once else 'Cost report'}: "
        f"{esc(report.book_id)} — Documentation</title>",
        "  <style>",
        style,
        "</style>",
        "</head>",
        "<body>",
        '<div class="layout">',
        "  <nav>",
    ]
    for group, ids in NAV_GROUPS:
        lines.append(f"    <h2>{esc(group)}</h2>")
        for section_id in ids:
            title, _ = sections[section_id]
            lines.append(f'    <a href="#{esc(section_id)}">{esc(title)}</a>')
        lines.append("")
    lines[-1] = "  </nav>"

    call_pill = (
        f"{len(calls):,} kept successes"
        if report.once
        else f"{len(calls):,} logged calls"
    )
    pills = [
        report.book_id,
        call_pill,
        f"{projection.coverage} covered",
        f"{report.total.thinking_tokens:,} thinking tokens",
    ]
    if projection.cost is not None:
        pills.append(f"whole book: {money(projection.cost, report.currency)}")
    if report.once:
        pills.insert(1, "one pass")
    if not report.verified:
        pills.insert(0, "unverified rates")
    if report.once:
        hero_sub = (
            f"What {esc(report.book_id)} would have cost if each work item had been done "
            f"once: {esc(plural(len(calls), 'kept success'))} covering "
            f"{esc(projection.coverage)}, dropping failed attempts and later fix-up "
            f"re-runs. Generated from that book's call logs on disk; no model was called "
            f"to produce it."
        )
    else:
        hero_sub = (
            f"What the practice-book pipeline has spent on "
            f"<code>{esc(report.book_id)}</code>, from "
            f"{esc(plural(len(calls), 'logged API call'))} covering "
            f"{esc(projection.coverage)}, and what the whole book comes to at those rates. "
            f"Generated from that book's call logs on disk; no model was called to produce "
            f"it."
        )
    lines += [
        "  <main>",
        '    <header class="hero">',
        f"      <h1>{'One-pass pipeline cost report' if report.once else 'Pipeline cost report'}</h1>",
        f'      <div class="sub">{hero_sub}</div>',
        "      <div>",
    ]
    lines += [f'        <span class="pill">{esc(pill)}</span>' for pill in pills]
    lines += ["      </div>", "    </header>", ""]

    group_ends = {ids[-1] for _, ids in NAV_GROUPS}
    for position, section_id in enumerate(ordered):
        title, body = sections[section_id]
        lines.append(f'    <h2 class="section" id="{esc(section_id)}">{esc(title)}</h2>')
        lines += body
        lines.append("")
        # A plain rule between the report's four thematic groups.
        if section_id in group_ends and position < len(ordered) - 1:
            lines += ["    <hr />", ""]

    lines += [
        f'    <p class="toc-foot">Generated by '
        f"<code>python scripts/cost.py {esc(report.book_id)}</code> from "
        f"{esc(plural(len(calls), 'call log record'))} under "
        f"<code>data/output/{esc(report.book_id)}/{LOGS_STAGE}/</code>, priced with "
        f"<code>{esc(short_path(report.prices.path))}</code>"
        f"{'. This is the one-pass companion of ' + code(REPORT_FILE) if report.once else ''}"
        f". Regenerate it after every "
        f"pipeline run against this book, and after any change to the price table; the "
        f"figures are only as current as the logs and only as trustworthy as the rates.</p>",
        "  </main>",
        "</div>",
        "</body>",
        "</html>",
        "",
    ]
    return "\n".join(lines)


# --- Driver ---
def report_path(book_id: str) -> Path:
    """Where this book's report goes, beside its own stage folders."""
    return OUTPUT_DIR / book_id / REPORT_FILE


def once_report_path(full_output: Path) -> Path:
    """The one-pass companion of a full-spend HTML path."""
    if full_output.name == REPORT_FILE:
        return full_output.with_name(ONCE_REPORT_FILE)
    return full_output.with_name(f"{full_output.stem}_once{full_output.suffix}")


def priced_report(
    book_id: str,
    calls: list[Call],
    prices: Prices,
    step: str | None,
    *,
    once: bool,
) -> Report:
    costs = {call.record: call.cost(prices) for call in calls}
    covered = len({section for call in calls for section in call.sections})
    return Report(
        calls=calls,
        costs=costs,
        prices=prices,
        book_id=book_id,
        projection=build_projection(calls, costs, book_id, covered),
        step_filter=step,
        once=once,
    )


def write_html(report: Report, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(report), encoding="utf-8")


def check_logs(book_id: str) -> None:
    """Refuse a book with no call log, naming the books that have one."""
    logs_dir = OUTPUT_DIR / book_id / LOGS_STAGE
    if not logs_dir.is_dir():
        raise RuntimeError(
            f"no API call logs for {book_id}: {short_path(logs_dir)} does not exist. Books "
            f"with logs: {', '.join(logged_books()) or 'none'}"
        )


def build_report(book_id: str, step: str | None, output: Path) -> tuple[Path, int]:
    """Price one book's logs, print the report, and write the HTML page."""
    prices = load_prices()
    check_logs(book_id)

    calls = read_calls(book_id)
    if not calls:
        raise RuntimeError(
            f"no {CALL_FILE} records under {short_path(OUTPUT_DIR / book_id / LOGS_STAGE)}"
        )

    if step:
        steps = sorted({call.step for call in calls})
        calls = [call for call in calls if call.step == step]
        if not calls:
            raise RuntimeError(
                f"no {step} records for {book_id}; steps present: {', '.join(steps)}"
            )

    missing = unpriced_models(calls, prices)
    if missing:
        unpriced = sum(1 for call in calls if call.model not in prices.rates)
        raise RuntimeError(
            "\n".join(
                [
                    f"{unpriced} of {len(calls)} call(s) name a model with no entry in "
                    f"{PRICES_PATH.name}; pricing an unknown model at zero would understate "
                    f"the report, so nothing is written",
                    *(f"  {line}" for line in missing),
                ]
            )
        )

    report = priced_report(book_id, calls, prices, step, once=False)
    print_report(report)
    write_html(report, output)
    print(
        f"\nOK: {len(calls)} call(s) priced at "
        f"{money(report.total.cost, prices.currency)}"
        f"{'' if report.verified else ' (unverified rates)'} -> {short_path(output)} "
        f"({output.stat().st_size / 1024:.0f} KB)"
    )

    once_calls = once_pass_calls(calls)
    if once_calls:
        once_report = priced_report(book_id, once_calls, prices, step, once=True)
        once_output = once_report_path(output)
        write_html(once_report, once_output)
        print(
            f"OK: {len(once_calls)} one-pass success(es) priced at "
            f"{money(once_report.total.cost, prices.currency)}"
            f"{'' if once_report.verified else ' (unverified rates)'} -> "
            f"{short_path(once_output)} "
            f"({once_output.stat().st_size / 1024:.0f} KB)"
        )
    return output, len(report.anomalies)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. eg_mathematics_en_g8_t1")
    parser.add_argument("--step", help="count only this step, e.g. --step section_spec")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=f"where to write the HTML report "
        f"(default: {short_path(OUTPUT_DIR)}/<book_id>/{REPORT_FILE})",
    )
    args = parser.parse_args()

    output = args.output or report_path(args.book_id)
    try:
        _, anomalies = build_report(args.book_id, args.step, output)
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 1 if anomalies else 0


if __name__ == "__main__":
    sys.exit(main())
