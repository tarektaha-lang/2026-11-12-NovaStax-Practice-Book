"""Pre-flight rules: no generated Arabic, open review issues, caches, glossary checks."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from cost import Call, Group, load_prices  # noqa: E402
from review_probe import unit_agreement_messages  # noqa: E402
from src.book_map import glossary_merge_problems  # noqa: E402
from src.protect import skeleton, token_problems  # noqa: E402
from src.protect import Slots  # noqa: E402
from translate import CACHE_TTL_SECONDS, PromptCaches, exercise_output_problems  # noqa: E402


def _row(item_id: str, kind: str, english: str, arabic: str) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "english": english,
        "arabic": arabic,
        "also": [],
        "quotes": [arabic],
        "section": "4.5",
    }


class AgreementHintTests(unittest.TestCase):
    def test_hints_name_no_arabic_form_that_the_output_lacks(self) -> None:
        text = "$3$ دقائق و$3$ ثوانٍ و$3$ أمثال"
        self.assertEqual(exercise_output_problems({"stem": text, "exercises": []}, "arabic"), [])
        for hint in unit_agreement_messages(text):
            self.assertNotRegex(hint, r"[\u0600-\u06FF]")
        bad = unit_agreement_messages("$3$ دقيقة")
        self.assertTrue(bad)
        for hint in bad:
            self.assertNotRegex(hint, r"[\u0600-\u06FF]")
            self.assertIn("unit_agreement.md", hint)

    def test_a_review_mention_drops_the_code_hint(self) -> None:
        from review_probe import drop_hints_covered_by_review

        hints = unit_agreement_messages("$13$ ساعة")
        self.assertTrue(hints)
        kept = drop_hints_covered_by_review(hints, "the word after $13$ disagrees")
        self.assertEqual(kept, [])


class ProductSkeletonTests(unittest.TestCase):
    def test_cdot_and_times_stay_distinct_and_a_real_change_does_not_match(self) -> None:
        source = r"$90^\circ+k\cdot360^\circ$"
        kept = r"$90^\circ+ك\cdot360^\circ$"
        swapped = r"$90^\circ+ك\times360^\circ$"
        self.assertEqual(skeleton(source, notation="arabic"), skeleton(kept, notation="arabic"))
        self.assertNotEqual(
            skeleton(source, notation="arabic"), skeleton(swapped, notation="arabic")
        )
        slots = Slots(frozen={}, editable={"E1": source}, numbers={})
        problems = token_problems(slots, f"⟦E1⟧{kept}⟦/E1⟧", "options[0]")
        self.assertFalse(any("changed beyond words" in item for item in problems))
        rewritten = token_problems(slots, f"⟦E1⟧{swapped}⟦/E1⟧", "options[0]")
        self.assertTrue(any("changed beyond words" in item for item in rewritten))
        changed = token_problems(slots, r"⟦E1⟧$90^\circ+ك\times36^\circ$⟦/E1⟧", "options[0]")
        self.assertTrue(any("changed beyond words" in item for item in changed))
        dotted = skeleton(r"$\vec{a}\cdot\vec{b}$", notation="arabic")
        self.assertIn(r"\cdot", dotted)
        self.assertNotIn(r"\times", dotted)
        units = skeleton(r"$100\text{ kg}\cdot\text{m/sec}$", notation="arabic")
        self.assertIn(r"\cdot", units)


class GlossaryStrictnessTests(unittest.TestCase):
    def test_todays_rejected_symbol_strings_pass_and_a_glued_name_fails(self) -> None:
        samples = [
            (r"$1'$", "1'"),
            (r"$\cap$", r"\cap"),
            (r"$\equiv$", r"\equiv"),
            (r"$\sim$", r"\sim"),
            (r"$\theta$", r"\theta"),
            (r"$\mathbb{R}$", r"\mathbb{R}"),
            (r"$\mathbb{R}^+$", r"\mathbb{R}^+"),
            (r"$\mathbb{Z}$", r"\mathbb{Z}"),
            (r"$[-1, 1]$", "[-1, 1]"),
            (r"]$-\infty, \infty[$", r"]-\infty, \infty["),
            (r"$\overrightarrow{EA}$", r"\overrightarrow{هـ ا}"),
            (r"$\triangle ABC$", r"\triangle ا ب جـ"),
            (r"$a, b, c$", "ا ، ب ، جـ"),
            (r"$a + bi$ and $a - bi$", r"ا + ب ت \text{ و } ا - ب ت"),
        ]
        rows = []
        entries = []
        for index, (english, arabic) in enumerate(samples):
            item_id = f"symbol:{index}"
            rows.append(_row(item_id, "symbol", english, arabic))
            entries.append(
                {
                    "kind": "symbol",
                    "english": english,
                    "arabic": arabic,
                    "also": [],
                    "abbreviation_only": False,
                    "source_row_ids": [item_id],
                    "reason": "printed",
                }
            )
        warnings: list[dict] = []
        problems = glossary_merge_problems(
            {"entries": entries, "dropped": []}, rows, "arabic", warnings
        )
        self.assertEqual(problems, [], problems)
        glued_rows = [_row("symbol:glued", "symbol", r"\triangle ABC", "ابجـ")]
        glued = glossary_merge_problems(
            {
                "entries": [
                    {
                        "kind": "symbol",
                        "english": r"\triangle ABC",
                        "arabic": "ابجـ",
                        "also": [],
                        "abbreviation_only": False,
                        "source_row_ids": ["symbol:glued"],
                        "reason": "printed",
                    }
                ],
                "dropped": [],
            },
            glued_rows,
            "arabic",
        )
        self.assertTrue(any("runs Arabic letters together" in item for item in glued))

    def test_an_unprinted_term_is_a_warning_not_an_error(self) -> None:
        rows = [_row("term:one", "term", "standard position", "وضع قياسي")]
        warnings: list[dict] = []
        problems = glossary_merge_problems(
            {
                "entries": [
                    {
                        "kind": "term",
                        "english": "standard position",
                        "arabic": "الوضع القياسي",
                        "also": [],
                        "abbreviation_only": False,
                        "source_row_ids": ["term:one"],
                        "reason": "preferred",
                    }
                ],
                "dropped": [],
            },
            rows,
            "arabic",
            warnings,
        )
        self.assertEqual(problems, [])
        self.assertEqual(warnings[0]["english"], "standard position")


class CacheAccountingTests(unittest.TestCase):
    def _caches(self, folder: Path, client, now):
        from unittest.mock import patch

        logger = MagicMock()
        with patch("translate.OUTPUT_DIR", folder):
            caches = PromptCaches(
                client, "gemini-3.8-flash", "mathematics_ar_scope_t1", logger=logger, now=now
            )
        caches._folder = folder
        return caches, logger

    def test_a_failed_create_retries_once_then_reports_uncached(self) -> None:
        client = MagicMock()
        client.caches.create.side_effect = RuntimeError("no cache")
        clock = {"t": 1_700_000_000.0}
        with tempfile.TemporaryDirectory() as folder:
            from unittest.mock import patch

            with patch("translate.OUTPUT_DIR", Path(folder)):
                caches = PromptCaches(
                    client,
                    "gemini-3.8-flash",
                    "mathematics_ar_scope_t1",
                    now=lambda: clock["t"],
                )
                self.assertIsNone(caches.name_for("exercise", "system"))
        self.assertEqual(client.caches.create.call_count, 2)
        self.assertEqual(len(caches.failures), 1)
        self.assertIn("no cache", caches.failures[0]["error"])
        caches.note_uncached("exercise")
        self.assertEqual(caches.failures[0]["uncached_calls"], 1)

    def test_a_replaced_cache_waits_for_inflight_calls(self) -> None:
        client = MagicMock()
        created = []

        def make():
            item = SimpleNamespace(name=f"caches/{len(created)}", usage_metadata=SimpleNamespace(total_token_count=10))
            created.append(item)
            return item

        client.caches.create.side_effect = lambda **_kwargs: make()
        clock = {"t": 1_700_000_000.0}
        with tempfile.TemporaryDirectory() as folder:
            from unittest.mock import patch

            with patch("translate.OUTPUT_DIR", Path(folder)):
                caches = PromptCaches(
                    client, "gemini-3.8-flash", "mathematics_ar_scope_t1", now=lambda: clock["t"]
                )
                first = caches.name_for("exercise", "system")
                caches.acquire(first)
                clock["t"] += CACHE_TTL_SECONDS
                second = caches.name_for("exercise", "system")
                self.assertNotEqual(first, second)
                self.assertEqual(client.caches.delete.call_count, 0)
                caches.release(first)
                self.assertEqual(client.caches.delete.call_count, 1)
                idle = caches.name_for("review", "other")
                clock["t"] += CACHE_TTL_SECONDS
                caches.name_for("review", "other")
                self.assertGreaterEqual(client.caches.delete.call_count, 2)
                self.assertTrue(idle)

    def test_delete_states_are_deleted_expired_and_failed(self) -> None:
        client = MagicMock()
        client.caches.create.return_value = SimpleNamespace(
            name="caches/a", usage_metadata=SimpleNamespace(total_token_count=4)
        )
        clock = {"t": 1_700_000_000.0}
        with tempfile.TemporaryDirectory() as folder:
            from unittest.mock import patch

            with patch("translate.OUTPUT_DIR", Path(folder)):
                caches = PromptCaches(
                    client, "gemini-3.8-flash", "mathematics_ar_scope_t1", now=lambda: clock["t"]
                )
                caches.name_for("exercise", "system")
                client.caches.delete.side_effect = RuntimeError("gone")
                caches.close()
                self.assertEqual(caches._leases[0]["state"], "failed")
                client.caches.delete.side_effect = None
                caches._closed = False
                caches._leases[0]["state"] = ""
                caches.close()
                self.assertEqual(caches._leases[0]["state"], "deleted")
                caches._closed = False
                caches._leases[0]["state"] = ""
                client.caches.delete.side_effect = RuntimeError("late")
                clock["t"] += CACHE_TTL_SECONDS + 5
                caches.close()
                self.assertEqual(caches._leases[0]["state"], "expired")
                self.assertEqual(caches._leases[0]["error"], "")

    def test_a_killed_run_is_recovered_once(self) -> None:
        client = MagicMock()
        clock = {"t": 1_700_000_000.0}
        with tempfile.TemporaryDirectory() as folder:
            from unittest.mock import patch

            root = Path(folder)
            with patch("translate.OUTPUT_DIR", root):
                caches = PromptCaches(
                    client, "gemini-3.8-flash", "mathematics_ar_scope_t1", now=lambda: clock["t"]
                )
                caches._append_ledger(
                    {
                        "name": "caches/orphan",
                        "task": "exercise",
                        "model": "gemini-3.8-flash",
                        "tokens": 100,
                        "created_at": clock["t"] - 100,
                        "created_at_iso": "2026-06-01T00:00:00+00:00",
                        "ttl": CACHE_TTL_SECONDS,
                        "closed": False,
                    }
                )
                caches.recover_orphans()
                self.assertEqual(client.caches.delete.call_count, 1)
                again = PromptCaches(
                    client, "gemini-3.8-flash", "mathematics_ar_scope_t1", now=lambda: clock["t"]
                )
                again.recover_orphans()
        self.assertEqual(client.caches.delete.call_count, 1)
        self.assertEqual(caches._leases[0]["state"], "recovered")

    def test_glossary_merge_creates_no_cache(self) -> None:
        client = MagicMock()
        with tempfile.TemporaryDirectory() as folder:
            from unittest.mock import patch

            with patch("translate.OUTPUT_DIR", Path(folder)):
                caches = PromptCaches(client, "gemini-3.8-flash", "mathematics_ar_scope_t1")
                self.assertIsNone(caches.name_for("glossary_merge", "system"))
        client.caches.create.assert_not_called()

    def test_a_2026_lease_uses_the_2026_rate_and_token_hours_do_not_change_usd(self) -> None:
        start = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc).timestamp()
        clock = {"t": start}
        client = MagicMock()
        client.caches.create.return_value = SimpleNamespace(
            name="caches/priced", usage_metadata=SimpleNamespace(total_token_count=1_000_000)
        )
        with tempfile.TemporaryDirectory() as folder:
            from unittest.mock import patch

            with patch("translate.OUTPUT_DIR", Path(folder)):
                caches = PromptCaches(
                    client, "gemini-3.8-flash", "mathematics_ar_scope_t1", now=lambda: clock["t"]
                )
                caches.name_for("exercise", "system")
                clock["t"] = start + 120
                caches.close()
                row = caches.storage_rows()[0]
        rate = load_prices().rates["gemini-3.8-flash"].period_for("2026-12-31T23:30:00+00:00")
        expected = 1_000_000 * (row["seconds"] / 3600) * rate.cache_storage_per_million_per_hour / 1_000_000
        self.assertAlmostEqual(row["usd"], expected)
        self.assertEqual(rate.cache_storage_per_million_per_hour, 0.5)
        call = Call(
            step="cache_storage",
            model="gemini-3.8-flash",
            item="cache",
            sections=(),
            attempt=1,
            status="success",
            timestamp="2026-12-31T23:30:00+00:00",
            input_tokens=0,
            cached_input_tokens=0,
            thinking_tokens=0,
            output_tokens=0,
            total_tokens=0,
            cache_storage_tokens=1_000_000,
            cache_storage_seconds=row["seconds"],
            duration_seconds=0,
            record="log",
        )
        group = Group()
        priced = call.cost(load_prices())
        group.add(call, priced)
        self.assertAlmostEqual(
            group.cache_storage_token_hours, 1_000_000 * row["seconds"] / 3600
        )
        self.assertAlmostEqual(group.cost, priced)
        self.assertAlmostEqual(group.cost, row["usd"])


if __name__ == "__main__":
    unittest.main()
