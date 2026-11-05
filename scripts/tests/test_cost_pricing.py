from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from cost import (  # noqa: E402
    Call,
    Group,
    Prices,
    Projection,
    Rate,
    RatePeriod,
    Report,
    load_prices,
    once_pass_calls,
    once_report_path,
    print_projection,
    print_spend,
    projection_section,
    read_call,
    step_units,
    throughput_section,
)
from src.api_logger import genai_usage  # noqa: E402


def prices() -> Prices:
    rate = Rate(
        currency="USD",
        verified=True,
        checked_on="2026-09-27",
        source="https://ai.google.dev/gemini-api/docs/pricing",
        placeholder=False,
        note="",
        periods=(
            RatePeriod(date(2026, 1, 1), date(2026, 12, 31), 0.75, 3.75, 0.075, 0.5),
            RatePeriod(date(2027, 1, 1), None, 1.5, 7.5, 0.15, 1.0),
        ),
    )
    return Prices(
        checked_on="2026-09-27",
        source=rate.source,
        thinking_billed_at="output",
        rates={"gemini-3.8-flash": rate},
        path=Path("prices.json"),
    )


def call(timestamp: str, **overrides) -> Call:
    values = {
        "step": "exercise_translate",
        "model": "gemini-3.8-flash",
        "item": "4.1_group_1",
        "sections": ("4.1",),
        "attempt": 1,
        "status": "success",
        "timestamp": timestamp,
        "input_tokens": 1_000,
        "cached_input_tokens": 400,
        "thinking_tokens": 50,
        "output_tokens": 100,
        "total_tokens": 1_150,
        "cache_storage_tokens": 1_000_000,
        "cache_storage_seconds": 3_600,
        "duration_seconds": 1.0,
        "record": "call.json",
    }
    values.update(overrides)
    return Call(**values)


class CostPricingTests(unittest.TestCase):
    def test_verified_google_rates_are_loaded(self) -> None:
        prices = load_prices()
        rate = prices.rates["gemini-3.8-flash"]
        self.assertTrue(rate.verified)
        self.assertFalse(rate.placeholder)
        self.assertEqual(rate.source, "https://ai.google.dev/gemini-api/docs/pricing")
        self.assertEqual(rate.period_for("2026-09-27T00:00:00+00:00").input_per_million, 0.75)
        self.assertEqual(
            rate.period_for("2026-10-18T00:00:00+00:00", batch=True).input_per_million,
            0.375,
        )
        self.assertEqual(
            rate.period_for("2026-10-18T00:00:00+00:00", batch=True).output_per_million,
            1.875,
        )
        previous = prices.rates["gemini-3.6-flash"]
        self.assertTrue(previous.verified)
        self.assertFalse(previous.placeholder)
        self.assertEqual(
            previous.period_for("2026-09-28T00:00:00+00:00").output_per_million,
            3.75,
        )
        speech = prices.rates["gemini-3.8-flash-tts"]
        self.assertTrue(speech.verified)
        self.assertEqual(speech.period_for("2026-09-29T00:00:00+00:00").input_per_million, 0.5)
        self.assertEqual(speech.period_for("2026-09-29T00:00:00+00:00").output_per_million, 9.0)
        self.assertEqual(speech.period_for("2027-01-01T00:00:00+00:00").output_per_million, 18.0)
        self.assertEqual(
            speech.period_for("2026-10-11T00:00:00+00:00", batch=True).input_per_million,
            0.25,
        )
        self.assertEqual(
            speech.period_for("2026-10-11T00:00:00+00:00", batch=True).output_per_million,
            4.5,
        )
        self.assertEqual(
            speech.period_for("2027-01-01T00:00:00+00:00", batch=True).output_per_million,
            9.0,
        )
        lite = prices.rates["gemini-3.8-flash-lite-tts"]
        self.assertTrue(lite.verified)
        self.assertEqual(lite.period_for("2026-10-13T00:00:00+00:00").input_per_million, 0.5)
        self.assertEqual(lite.period_for("2026-10-13T00:00:00+00:00").output_per_million, 6.0)
        self.assertEqual(
            lite.period_for("2026-10-13T00:00:00+00:00", batch=True).input_per_million,
            0.25,
        )
        self.assertEqual(
            lite.period_for("2026-10-13T00:00:00+00:00", batch=True).output_per_million,
            3.0,
        )
        self.assertEqual(
            lite.period_for("2027-01-01T00:00:00+00:00", batch=True).output_per_million,
            6.0,
        )

    def test_tts_batch_flag_prices_at_half_standard(self) -> None:
        table = load_prices()
        common = {
            "model": "gemini-3.8-flash-tts",
            "thinking_tokens": 0,
            "cached_input_tokens": 0,
            "cache_storage_tokens": 0,
            "cache_storage_seconds": 0,
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        }
        standard = call("2026-10-11T00:00:00+00:00", **common)
        batched = call("2026-10-11T00:00:00+00:00", batch=True, **common)
        self.assertAlmostEqual(standard.cost(table), 9.5)
        self.assertAlmostEqual(batched.cost(table), 4.75)
        lite_common = {**common, "model": "gemini-3.8-flash-lite-tts"}
        lite_standard = call("2026-10-13T00:00:00+00:00", **lite_common)
        lite_batched = call("2026-10-13T00:00:00+00:00", batch=True, **lite_common)
        self.assertAlmostEqual(lite_standard.cost(table), 6.5)
        self.assertAlmostEqual(lite_batched.cost(table), 3.25)
        pro_common = {**common, "model": "gemini-2.5-pro-preview-tts"}
        pro_standard = call("2026-10-13T00:00:00+00:00", **pro_common)
        pro_batched = call("2026-10-13T00:00:00+00:00", batch=True, **pro_common)
        self.assertAlmostEqual(pro_standard.cost(table), 21.0)
        self.assertAlmostEqual(pro_batched.cost(table), 10.5)
        live = table.rates["gemini-3.1-flash-live-preview"]
        self.assertTrue(live.verified)
        self.assertEqual(live.period_for("2026-10-13T00:00:00+00:00").input_per_million, 0.75)
        self.assertEqual(live.period_for("2026-10-13T00:00:00+00:00").output_per_million, 12.0)
        live_common = {**common, "model": "gemini-3.1-flash-live-preview"}
        live_call = call("2026-10-13T00:00:00+00:00", **live_common)
        self.assertAlmostEqual(live_call.cost(table), 12.75)

    def test_cached_input_thinking_and_storage_are_priced_separately(self) -> None:
        # 600*.75 + 400*.075 + 150*3.75 + 1M*.5 token-hours.
        self.assertAlmostEqual(
            call("2026-09-27T00:00:00+00:00").cost(prices()),
            0.5010425,
        )

    def test_effective_date_boundary(self) -> None:
        before = call(
            "2026-12-31T23:59:59+00:00",
            cache_storage_tokens=0,
            cache_storage_seconds=0,
        ).cost(prices())
        after = call(
            "2027-01-01T00:00:00+00:00",
            cache_storage_tokens=0,
            cache_storage_seconds=0,
        ).cost(prices())
        self.assertAlmostEqual(after, before * 2)

    def test_logger_carries_cached_content_tokens(self) -> None:
        class Usage:
            prompt_token_count = 100
            cached_content_token_count = 40
            thoughts_token_count = 20
            candidates_token_count = 10
            total_token_count = 130

        class Response:
            usage_metadata = Usage()

        self.assertEqual(genai_usage(Response())["cached_input_tokens"], 40)

    def test_once_pass_keeps_last_success_per_item(self) -> None:
        failed = call(
            "2026-10-01T00:00:00+00:00",
            item="1.1_group_1",
            status="error",
            record="a/call.json",
        )
        first = call(
            "2026-10-01T01:00:00+00:00",
            item="1.1_group_1",
            record="b/call.json",
        )
        rerun = call(
            "2026-10-18T12:00:00+00:00",
            item="1.1_group_1",
            record="c/call.json",
        )
        other = call(
            "2026-10-01T02:00:00+00:00",
            item="1.1_group_2",
            step="tikz_translate",
            record="d/call.json",
        )
        kept = once_pass_calls([failed, first, other, rerun])
        self.assertEqual([row.record for row in kept], ["d/call.json", "c/call.json"])

    def test_once_report_path_beside_default_html(self) -> None:
        full = Path("data/output/mathematics_en_eg07_t1/cost_report.html")
        self.assertEqual(once_report_path(full).name, "cost_report_once.html")
        custom = Path("/tmp/spend.html")
        self.assertEqual(once_report_path(custom).name, "spend_once.html")


def _add(calls: list[Call]) -> Group:
    group = Group()
    for row in calls:
        group.add(row, 0.0)
    return group


def _batch_rows(
    n: int,
    name: str,
    seconds: float,
    *,
    step: str = "audios",
    start: int = 0,
) -> list[Call]:
    rows = []
    for i in range(start, start + n):
        rows.append(
            call(
                "2026-10-18T00:00:00+00:00",
                step=step,
                item=f"{step}_{i}",
                sections=("1.1",) if i % 2 == 0 else ("1.2",),
                duration_seconds=seconds,
                batch=True,
                batch_name=name,
                record=f"{step}_{i}/call.json",
            )
        )
    return rows


def _report(calls: list[Call], *, sections: int = 4) -> Report:
    costs = {row.record: 0.0 for row in calls}
    covered = len({section for row in calls for section in row.sections})
    return Report(
        calls=calls,
        costs=costs,
        prices=prices(),
        book_id="synthetic_book",
        step_filter=None,
        projection=Projection(
            units=step_units(calls, costs, covered or 1),
            covered=covered,
            sections=sections,
            sections_note="synthetic",
        ),
    )


def _minimal_log(**overrides) -> dict:
    document = {
        "model": "gemini-3.8-flash",
        "step": "audios",
        "attempt": 1,
        "duration_seconds": 500,
        "timestamp": "2026-10-18T00:00:00+00:00",
        "usage": {},
        "section": "1.1",
        "page_number": "seg_1",
        "status": "success",
        "batch": True,
        "batch_name": "batches/job-1",
    }
    document.update(overrides)
    return document


class BatchDurationTests(unittest.TestCase):
    def test_one_batch_name_counts_once(self) -> None:
        group = _add(_batch_rows(150, "batches/job-1", 500))
        self.assertEqual(group.calls, 150)
        self.assertEqual(group.duration, 0.0)
        self.assertEqual(group.batch_duration, 500)
        self.assertNotEqual(group.batch_duration, 150 * 500)

    def test_two_batch_names_sum(self) -> None:
        rows = _batch_rows(80, "batches/job-a", 500)
        rows += _batch_rows(70, "batches/job-b", 120, start=80)
        group = _add(rows)
        self.assertEqual(group.batch_duration, 620)

    def test_non_batch_calls_are_summed(self) -> None:
        group = _add(
            [
                call("2026-10-18T00:00:00+00:00", duration_seconds=10, record="a/call.json"),
                call(
                    "2026-10-18T00:00:01+00:00",
                    duration_seconds=25,
                    item="other",
                    record="b/call.json",
                ),
            ]
        )
        self.assertEqual(group.duration, 35)
        self.assertEqual(group.batch_duration, 0.0)

    def test_adopted_job_elapsed_zero_is_unknown(self) -> None:
        rows = _batch_rows(10, "batches/adopted", 0.0)
        rows += _batch_rows(5, "batches/live", 500, start=10)
        group = _add(rows)
        self.assertEqual(group.batch_duration, 500)
        self.assertNotIn("batches/adopted", group.batch_job_durations)

    def test_batch_duration_uses_max_when_rows_differ(self) -> None:
        rows = _batch_rows(10, "batches/job-1", 500)
        rows[0] = call(
            **{**rows[0].__dict__, "duration_seconds": 510},
        )
        group = _add(rows)
        self.assertEqual(group.batch_duration, 510)

    def test_mixed_projection_matches_hand_computed_value(self) -> None:
        # Book has 4 sections; the log covers 2.
        # Interactive step: 2 calls × 30 s, one item per covered section.
        #   items_per_unit = 2 / 2 = 1
        #   seconds_per_unit = (60 / 2) * 1 = 30
        #   sequential whole book = 30 * 4 = 120
        # Batch step: 150 calls sharing one job of 500 s, items in the same 2
        # sections.
        #   batch_seconds_per_unit = 500 / 2 = 250  (job seconds / covered)
        #   not (500 / 150) * (150 / 2) = 250 coincidentally, but the same
        #   number from job time / covered rather than per-call mean × items
        #   batch whole book = 250 * 4 = 1000
        interactive = [
            call(
                "2026-10-18T00:00:00+00:00",
                step="exercise_translate",
                item="1.1_group_1",
                sections=("1.1",),
                duration_seconds=30,
                record="ex_1/call.json",
            ),
            call(
                "2026-10-18T00:00:01+00:00",
                step="exercise_translate",
                item="1.2_group_1",
                sections=("1.2",),
                duration_seconds=30,
                record="ex_2/call.json",
            ),
        ]
        batched = _batch_rows(150, "batches/job-1", 500)
        calls = interactive + batched
        costs = {row.record: 0.0 for row in calls}
        covered = 2
        units = step_units(calls, costs, covered)
        projection = Projection(
            units=units,
            covered=covered,
            sections=4,
            sections_note="synthetic",
        )
        by_step = {unit.step: unit for unit in units}
        self.assertAlmostEqual(by_step["exercise_translate"].seconds_per_unit, 30)
        self.assertAlmostEqual(by_step["exercise_translate"].batch_seconds_per_unit, 0)
        self.assertAlmostEqual(by_step["audios"].seconds_per_unit, 0)
        self.assertAlmostEqual(by_step["audios"].batch_seconds_per_unit, 250)
        self.assertAlmostEqual(projection.seconds, 120)
        self.assertAlmostEqual(projection.batch_seconds, 1000)

    def test_read_call_requires_batch_name_when_batch(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "call.json"
            path.write_text(json.dumps(_minimal_log()), encoding="utf-8")
            problems: list[str] = []
            got = read_call(path, "synthetic_book", problems)
            self.assertIsNotNone(got)
            self.assertEqual(got.batch_name, "batches/job-1")
            self.assertEqual(problems, [])

            missing_doc = _minimal_log()
            del missing_doc["batch_name"]
            path.write_text(json.dumps(missing_doc), encoding="utf-8")
            problems = []
            missing = read_call(path, "synthetic_book", problems)
            self.assertIsNotNone(missing)
            self.assertIsNone(missing.batch_name)
            self.assertTrue(any("batch_name" in row for row in problems))

    def test_reports_show_batch_time_apart_from_sequential_wall_clock(self) -> None:
        report = _report(
            [
                call(
                    "2026-10-18T00:00:00+00:00",
                    duration_seconds=12,
                    record="ex/call.json",
                ),
                *_batch_rows(4, "batches/job-1", 500),
            ]
        )
        buf = StringIO()
        with patch("sys.stdout", buf):
            print_spend(report)
            print_projection(report)
        text = buf.getvalue()
        self.assertIn("Sequential wall clock", text)
        self.assertIn(
            "Batch job time (queue + processing, jobs run in parallel)",
            text,
        )
        self.assertIn("time is per job", text)
        html = "\n".join(
            throughput_section(report)[1] + projection_section(report)[1]
        )
        self.assertIn("Sequential wall clock", html)
        self.assertIn(
            "Batch job time (queue + processing, jobs run in parallel)",
            html,
        )
        self.assertIn("Batch job time is per job", html)


if __name__ == "__main__":
    unittest.main()
