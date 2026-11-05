#!/usr/bin/env python3
"""Read-only token and cost summary of one Arabic book's translation logs.

Reads ``<ar_dir>/00_api_call_logs/*/call.json`` and, when present, manifest v4
files under ``00_translation_state``. Nothing is written and no model is called.
The book-level ``glossary_merge`` step is included with the other call steps.

    python3 -B scripts/translation_metrics.py <ar_dir> [--since ISO8601] [--section 1.2] [--passes N] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from cost import load_prices  # noqa: E402


FOLDER_SECTION = re.compile(r"Z_(\d+(?:\.\d+)*)_")


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0


def _timestamp_on_or_after(timestamp: str, since: str) -> bool:
    """Compare instants, not raw strings, so a local offset does not drop UTC logs."""
    try:
        left = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        right = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        return timestamp >= since
    if left.tzinfo is None or right.tzinfo is None:
        return timestamp >= since
    return left >= right


def call_section(document: dict, folder: str) -> str:
    """Section of one logged call: extra.section, else the folder name."""
    extra = document.get("extra")
    if isinstance(extra, dict):
        section = extra.get("section")
        if isinstance(section, str) and section:
            return section
    match = FOLDER_SECTION.search(folder)
    return match.group(1) if match else ""


def _empty_step() -> dict:
    return {
        "calls": 0,
        "errors": 0,
        "input": 0,
        "cached_input": 0,
        "output": 0,
        "thinking": 0,
        "usd": 0.0,
    }


def summarize(
    ar_dir: Path,
    *,
    since: str | None = None,
    section: str | None = None,
) -> dict:
    """Aggregate calls, tokens, and cost. ``since`` keeps timestamps >= that instant."""
    root = Path(ar_dir)
    prices = load_prices()
    steps: dict[str, dict] = defaultdict(_empty_step)
    calls = 0
    for path in sorted((root / "00_api_call_logs").glob("*/call.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        timestamp = str(document.get("timestamp") or "")
        if since and not _timestamp_on_or_after(timestamp, since):
            continue
        found = call_section(document, path.parent.name)
        if section and found != section:
            continue
        usage = document.get("usage") if isinstance(document.get("usage"), dict) else {}
        model = str(document.get("model") or "")
        rate = prices.rates.get(model)
        current = rate.current if rate is not None else None
        input_tokens = _int(usage.get("input_tokens"))
        cached = _int(usage.get("cached_input_tokens"))
        if cached > input_tokens:
            cached = 0
        output_tokens = _int(usage.get("output_tokens"))
        thinking = _int(usage.get("thinking_tokens"))
        storage_tokens = _int(usage.get("cache_storage_tokens"))
        try:
            storage_seconds = float(usage.get("cache_storage_seconds") or 0)
        except (TypeError, ValueError):
            storage_seconds = 0.0
        usd = 0.0
        if current is not None:
            usd = (
                (input_tokens - cached) * current.input_per_million
                + cached * current.cached_input_per_million
                + (output_tokens + thinking) * current.output_per_million
                + storage_tokens
                * (storage_seconds / 3600)
                * current.cache_storage_per_million_per_hour
            ) / 1_000_000
        step = str(document.get("step") or "unknown")
        row = steps[step]
        row["calls"] += 1
        if step == "cache_storage":
            # A cache that was not deleted is still a priced storage row, not a failed call.
            if document.get("cache_deleted") is True:
                row["deleted"] = row.get("deleted", 0) + 1
            else:
                row["not_deleted"] = row.get("not_deleted", 0) + 1
        elif document.get("status") != "success":
            row["errors"] += 1
        row["input"] += input_tokens
        row["cached_input"] += cached
        row["output"] += output_tokens
        row["thinking"] += thinking
        row["usd"] += usd
        calls += 1

    groups = 0
    not_batch = 0
    issues_round1: dict[str, int] = defaultdict(int)
    state = root / "00_translation_state"
    if state.is_dir():
        for path in sorted(state.glob("*.json")):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(manifest, dict) or manifest.get("version") != 4:
                continue
            if section and str(manifest.get("section") or "") != section:
                continue
            for entry in (manifest.get("groups") or {}).values():
                if not isinstance(entry, dict):
                    continue
                groups += 1
                # first_path is the translation that produced the group. A later
                # review correction must not turn an accepted first pass into a rejection.
                taken = entry.get("first_path") or entry.get("path")
                if taken not in {"batch", "correction"}:
                    not_batch += 1
            review = manifest.get("review") or {}
            for round_row in review.get("rounds") or []:
                if not isinstance(round_row, dict) or round_row.get("round") != 1:
                    continue
                for issue in round_row.get("issues") or []:
                    if isinstance(issue, dict):
                        issues_round1[str(issue.get("category") or "other")] += 1

    cost = sum(row["usd"] for row in steps.values())
    return {
        "ar_dir": str(root),
        "section": section or "",
        "calls": calls,
        "cost_usd": cost,
        "steps": {name: steps[name] for name in sorted(steps)},
        "first_pass_rejection": {
            "groups": groups,
            "not_batch": not_batch,
            "rate": (not_batch / groups) if groups else 0.0,
        },
        "issues_round1_by_category": dict(issues_round1),
    }


def _print_report(summary: dict, passes: int) -> None:
    divisor = passes if passes > 1 else 1
    print(f"book: {summary['ar_dir']}")
    if summary["section"]:
        print(f"section: {summary['section']}")
    print(f"calls: {summary['calls']}")
    print(
        f"{'step':<24} {'calls':>7} {'errors':>7} {'err%':>7} "
        f"{'input':>12} {'cached':>10} {'output':>10} {'thinking':>10} {'usd':>10}"
    )
    for name, row in summary["steps"].items():
        calls = row["calls"]
        rate = (100.0 * row["errors"] / calls) if calls else 0.0
        print(
            f"{name:<24} {calls:7d} {row['errors']:7d} {rate:6.1f}% "
            f"{row['input']:12d} {row['cached_input']:10d} {row['output']:10d} "
            f"{row['thinking']:10d} {row['usd'] / divisor:10.2f}"
        )
        if name == "cache_storage":
            print(
                f"cache storage: {row.get('deleted', 0)} deleted, "
                f"{row.get('not_deleted', 0)} not deleted"
            )
    label = "single-pass equivalent" if divisor > 1 else "cost"
    print(f"{label}: ${summary['cost_usd'] / divisor:.2f}")
    rejection = summary["first_pass_rejection"]
    if rejection["groups"]:
        print(
            f"first-pass group rejection: {rejection['not_batch']}/{rejection['groups']} "
            f"({100.0 * rejection['rate']:.1f}%)"
        )
    if summary["issues_round1_by_category"]:
        print(f"review round-1 issues: {summary['issues_round1_by_category']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ar_dir", type=Path)
    parser.add_argument("--since")
    parser.add_argument("--section")
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.passes < 1:
        parser.error("--passes must be >= 1")
    summary = summarize(args.ar_dir, since=args.since, section=args.section)
    if args.passes > 1:
        summary = {
            **summary,
            "passes": args.passes,
            "single_pass_usd": summary["cost_usd"] / args.passes,
        }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_report(summary, args.passes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
