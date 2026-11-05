"""Glossary merge is one checked model call, with no page OCR."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.book_map import (  # noqa: E402
    _split_top_level,
    book_map_from_glossary_reply,
    glossary_candidate_rows,
    glossary_merge_problems,
)
from translation_metrics import summarize  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "glossary_eg12.json"


def _eg12() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _rekey_attempt3(entries: list[dict], rows: list[dict]) -> dict:
    """Rewrite attempt-3 source ids onto the split candidate rows."""
    by_base: dict[str, list[dict]] = {}
    for row in rows:
        base = str(row["id"]).split("#", 1)[0]
        by_base.setdefault(base, []).append(row)
    known = {row["id"] for row in rows}
    out: list[dict] = []
    for entry in entries:
        ids = [str(item) for item in entry.get("source_row_ids") or []]
        if len(ids) == 1 and ids[0] in known:
            out.append(entry)
            continue
        if len(ids) == 1 and ids[0] in by_base:
            members = by_base[ids[0]]
            if len(members) > 1:
                for row in members:
                    item = dict(entry)
                    item["english"] = row["english"]
                    item["arabic"] = row["arabic"]
                    item["source_row_ids"] = [row["id"]]
                    out.append(item)
                continue
        out.append(entry)
    covered = {item_id for entry in out for item_id in entry.get("source_row_ids") or []}
    dropped = [
        {"row_id": row["id"], "reason": "unused in trimmed fixture"}
        for row in rows
        if row["id"] not in covered
    ]
    return {"entries": out, "dropped": dropped}


def _row(item_id: str, kind: str, english: str, arabic: str, quote: str | None = None) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "english": english,
        "definition": "",
        "arabic": arabic,
        "also": [],
        "abbreviation_only": False,
        "status": "confirmed",
        "section": "1.1",
        "quotes": [quote if quote is not None else arabic],
    }


def _entry(kind: str, english: str, arabic: str, ids: list[str], *, also: list[str] | None = None) -> dict:
    return {
        "kind": kind,
        "english": english,
        "arabic": arabic,
        "also": also or [],
        "abbreviation_only": False,
        "source_row_ids": ids,
        "reason": "printed",
    }


class GlossaryMergeCheckTests(unittest.TestCase):
    def test_rejects_a_missing_row_an_invented_form_and_a_duplicate(self) -> None:
        rows = [
            _row("term:one", "term", "Base", "قاعدة"),
            _row("term:two", "term", "Base", "أساس"),
        ]
        missing = glossary_merge_problems(
            {
                "entries": [_entry("term", "Base", "قاعدة", ["term:one"])],
                "dropped": [],
            },
            rows,
            "latin",
        )
        self.assertTrue(any("term:two" in item for item in missing))
        warnings: list[dict] = []
        invented = glossary_merge_problems(
            {
                "entries": [_entry("term", "Base", "أصل", ["term:one"])],
                "dropped": [{"row_id": "term:two", "reason": "other concept"}],
            },
            rows,
            "latin",
            warnings,
        )
        self.assertEqual(invented, [])
        self.assertTrue(any("أصل" in item["reason"] for item in warnings))
        duplicate = glossary_merge_problems(
            {
                "entries": [
                    _entry("term", "Base", "قاعدة", ["term:one"]),
                    _entry("term", "Base", "أساس", ["term:two"]),
                ],
                "dropped": [],
            },
            rows,
            "latin",
        )
        self.assertTrue(any("share english" in item for item in duplicate))
        twice = glossary_merge_problems(
            {
                "entries": [_entry("term", "Base", "قاعدة", ["term:one", "term:one"])],
                "dropped": [{"row_id": "term:two", "reason": "dropped"}],
            },
            rows,
            "latin",
        )
        self.assertTrue(any("appears 2 times" in item for item in twice))

    def test_a_repeated_id_keeps_every_printed_form(self) -> None:
        first = _row("unit:hour", "unit", "hour", "ساعة", "1 ساعة")
        second = _row("unit:hour", "unit", "hour", "ساعة", "26 ساعة")
        second["also"] = ["ساعات"]
        second["section"] = "2.1"
        problems = glossary_merge_problems(
            {
                "entries": [_entry("unit", "hour", "ساعة", ["unit:hour"], also=["ساعات"])],
                "dropped": [],
            },
            [first, second],
            "latin",
        )
        self.assertEqual(problems, [])

    def test_symbol_alef_must_be_plain_and_spaced_and_may_match_a_hamza_quote(self) -> None:
        rows = [_row("symbol:ab", "symbol", r"\overline{AB}", "أب", r"$\overline{أب}$")]
        glued = glossary_merge_problems(
            {
                "entries": [_entry("symbol", r"\overline{AB}", "أب", ["symbol:ab"])],
                "dropped": [],
            },
            rows,
            "arabic",
        )
        self.assertTrue(any("ا" in item or "run" in item or "أ" in item for item in glued))
        spaced = glossary_merge_problems(
            {
                "entries": [_entry("symbol", r"\overline{AB}", r"\overline{ا ب}", ["symbol:ab"])],
                "dropped": [],
            },
            rows,
            "arabic",
        )
        self.assertEqual(spaced, [])
        tailed = glossary_merge_problems(
            {
                "entries": [_entry("symbol", r"\triangle ABC", r"\triangle ا ب جـ", ["symbol:ab"])],
                "dropped": [],
            },
            [_row("symbol:ab", "symbol", r"\triangle ABC", r"\triangle ابجـ", r"\triangle أبجـ")],
            "arabic",
        )
        self.assertEqual(tailed, [])

    def test_a_prose_word_is_not_run_together_point_letters(self) -> None:
        rows = [_row("symbol:r", "symbol", "$r$", "نق", "لدائرة طول نصف قطرها نق")]
        problems = glossary_merge_problems(
            {
                "entries": [_entry("symbol", "$r$", "نق", ["symbol:r"])],
                "dropped": [],
            },
            rows,
            "arabic",
        )
        self.assertEqual(problems, [])
        glued = glossary_merge_problems(
            {
                "entries": [_entry("symbol", r"\triangle ABC", "ابجـ", ["symbol:r"])],
                "dropped": [],
            },
            [_row("symbol:r", "symbol", r"\triangle ABC", "ا ب جـ", r"\triangle ا ب جـ")],
            "arabic",
        )
        self.assertTrue(any("runs Arabic letters together" in item for item in glued))

    def test_accepts_a_form_copied_from_a_quote(self) -> None:
        rows = [_row("unit:m", "unit", "m", "متر", "20 م")]
        problems = glossary_merge_problems(
            {
                "entries": [
                    {
                        **_entry("unit", "m", "م", ["unit:m"], also=["متر"]),
                        "abbreviation_only": True,
                    }
                ],
                "dropped": [],
            },
            rows,
            "latin",
        )
        self.assertEqual(problems, [])

    def test_latin_symbol_must_match_and_terms_are_not_abbreviations(self) -> None:
        rows = [_row("symbol:x", "symbol", "$x$", "$x$")]
        mismatch = glossary_merge_problems(
            {
                "entries": [_entry("symbol", "$x$", "س", ["symbol:x"])],
                "dropped": [],
            },
            rows,
            "latin",
        )
        self.assertTrue(any("latin notation" in item for item in mismatch))
        flagged = glossary_merge_problems(
            {
                "entries": [
                    {**_entry("term", "Base", "قاعدة", ["term:base"]), "abbreviation_only": True}
                ],
                "dropped": [],
            },
            [_row("term:base", "term", "Base", "قاعدة")],
            "arabic",
        )
        self.assertTrue(any("abbreviation_only must be false" in item for item in flagged))

    def test_symbol_case_is_kept_and_terms_still_fold(self) -> None:
        pairs = [
            ("$H$", "م", "$h$", "ل"),
            ("$W$", "ش", "$w$", "و"),
            ("$R$", "ر", "$r$", "ص"),
            (r"$\vec{R}$", r"\vec{ح}", r"$\vec{r}$", r"\vec{ر}"),
            ("$M$", "جـ", "$m$", "ك"),
        ]
        for upper, upper_ar, lower, lower_ar in pairs:
            rows = [
                _row("symbol:up", "symbol", upper, upper_ar),
                _row("symbol:lo", "symbol", lower, lower_ar),
            ]
            problems = glossary_merge_problems(
                {
                    "entries": [
                        _entry("symbol", upper, upper_ar, ["symbol:up"]),
                        _entry("symbol", lower, lower_ar, ["symbol:lo"]),
                    ],
                    "dropped": [],
                },
                rows,
                "arabic",
            )
            self.assertEqual(problems, [], (upper, lower))
        twice = glossary_merge_problems(
            {
                "entries": [
                    _entry("symbol", "$h$", "ل", ["symbol:one"]),
                    _entry("symbol", "$h$", "ل", ["symbol:two"]),
                ],
                "dropped": [],
            },
            [_row("symbol:one", "symbol", "$h$", "ل"), _row("symbol:two", "symbol", "$h$", "ل")],
            "arabic",
        )
        self.assertTrue(any("share english '$h$'" in item for item in twice))
        dollars = glossary_merge_problems(
            {
                "entries": [
                    _entry("symbol", "h", "ل", ["symbol:one"]),
                    _entry("symbol", "$h$", "ل", ["symbol:two"]),
                ],
                "dropped": [],
            },
            [_row("symbol:one", "symbol", "h", "ل"), _row("symbol:two", "symbol", "$h$", "ل")],
            "arabic",
        )
        self.assertTrue(any("share english" in item for item in dollars))
        terms = glossary_merge_problems(
            {
                "entries": [
                    _entry("term", "Force", "قوة", ["term:one"]),
                    _entry("term", "force", "قوة", ["term:two"]),
                ],
                "dropped": [],
            },
            [_row("term:one", "term", "Force", "قوة"), _row("term:two", "term", "force", "قوة")],
            "arabic",
        )
        self.assertTrue(any("share english" in item for item in terms))
        units = glossary_merge_problems(
            {
                "entries": [
                    _entry("unit", "Hour", "ساعة", ["unit:one"]),
                    _entry("unit", "hour", "ساعة", ["unit:two"]),
                ],
                "dropped": [],
            },
            [_row("unit:one", "unit", "Hour", "ساعة"), _row("unit:two", "unit", "hour", "ساعة")],
            "arabic",
        )
        self.assertTrue(any("share english" in item for item in units))

    def test_symbol_text_wrappers_match_source(self) -> None:
        rows = [
            _row("symbol:i", "symbol", r"$\vec{I}$", r"\vec{\text{د}}"),
            _row("symbol:v", "symbol", r"$\vec{v}_1, \vec{v}_2$", r"\vec{\text{ع}}_1, \vec{\text{ع}}_2"),
            _row("symbol:m", "symbol", r"$M_O(\vec{F})$", r"\text{جـ}_و(\vec{ق})"),
            _row("symbol:u", "symbol", r"$v_1, v_2$", r"\text{ع}_1, \text{ع}_2"),
        ]
        reply = {
            "entries": [
                _entry("symbol", r"$\vec{I}$", r"\vec{د}", ["symbol:i"]),
                _entry("symbol", r"$\vec{v}_1, \vec{v}_2$", r"\vec{ع}_1, \vec{ع}_2", ["symbol:v"]),
                _entry("symbol", r"$M_O(\vec{F})$", r"جـ_و(\vec{ق})", ["symbol:m"]),
                _entry("symbol", r"$v_1, v_2$", r"ع_1, ع_2", ["symbol:u"]),
            ],
            "dropped": [],
        }
        self.assertEqual(glossary_merge_problems(reply, rows, "arabic"), [])
        nested = glossary_merge_problems(
            {
                "entries": [_entry("symbol", r"$\vec{I}$", r"\vec{\text{\text{د}}}", ["symbol:i"])],
                "dropped": [{"row_id": "symbol:v", "reason": "x"}, {"row_id": "symbol:m", "reason": "x"}, {"row_id": "symbol:u", "reason": "x"}],
            },
            rows,
            "arabic",
        )
        self.assertEqual(nested, [])
        wrong = glossary_merge_problems(
            {
                "entries": [_entry("symbol", r"$\vec{I}$", r"\vec{ب}", ["symbol:i"])],
                "dropped": [{"row_id": "symbol:v", "reason": "x"}, {"row_id": "symbol:m", "reason": "x"}, {"row_id": "symbol:u", "reason": "x"}],
            },
            rows,
            "arabic",
        )
        self.assertTrue(any("not in the source rows" in item for item in wrong))

    def test_term_printed_form_does_not_strip_text(self) -> None:
        warnings: list[dict] = []
        problems = glossary_merge_problems(
            {
                "entries": [_entry("term", "Force", r"\text{قوة}", ["term:one"])],
                "dropped": [],
            },
            [_row("term:one", "term", "Force", "قوة")],
            "arabic",
            warnings,
        )
        self.assertEqual(problems, [])
        self.assertTrue(any("not in the source rows" in item["reason"] for item in warnings))

    def test_candidate_rows_normalize_and_split_symbols(self) -> None:
        fragments = [
            {
                "section": "2.2.3",
                "observations": [
                    {
                        "id": "symbol:08c16b9881428b3d",
                        "kind": "symbol",
                        "source_math": "v_1, v_2",
                        "math_text": r"\text{ع}_1, \text{ع}_2",
                        "status": "confirmed",
                        "evidence": [{"quote": r"\text{ع}_1"}],
                    },
                    {
                        "id": "symbol:a162e10a24127685",
                        "kind": "symbol",
                        "source_math": r"\vec{v}_1, \vec{v}_2",
                        "math_text": r"\vec{\text{ع}}_1, \vec{\text{ع}}_2",
                        "status": "confirmed",
                        "evidence": [{"quote": r"\vec{\text{ع}}_1"}],
                    },
                    {
                        "id": "symbol:2755c1da17f57acb",
                        "kind": "symbol",
                        "source_math": r"$\vec{s}$, $s$",
                        "math_text": r"\vec{ف}\text{، }ف",
                        "status": "confirmed",
                        "evidence": [{"quote": r"\vec{ف}"}],
                    },
                    {
                        "id": "symbol:mo",
                        "kind": "symbol",
                        "source_math": r"\vec{M}_O",
                        "math_text": r"\vec{جـ}_و",
                        "status": "confirmed",
                        "evidence": [{"quote": r"\vec{جـ}_و"}],
                    },
                    {
                        "id": "symbol:newton",
                        "kind": "symbol",
                        "source_math": r"$N$",
                        "math_text": r"\text{نيوتن}",
                        "status": "confirmed",
                        "evidence": [{"quote": r"\text{نيوتن}"}],
                    },
                    {
                        "id": "symbol:bad",
                        "kind": "symbol",
                        "source_math": r"$a, b, c$",
                        "math_text": r"ا, ب",
                        "status": "confirmed",
                        "evidence": [{"quote": "ا"}],
                    },
                ],
            }
        ]
        from io import StringIO

        with patch("sys.stdout", new_callable=StringIO) as stdout:
            rows = glossary_candidate_rows(fragments)
            printed = stdout.getvalue()
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["symbol:08c16b9881428b3d#1"]["english"], "$v_1$")
        self.assertEqual(by_id["symbol:08c16b9881428b3d#1"]["arabic"], "ع_1")
        self.assertEqual(by_id["symbol:08c16b9881428b3d#2"]["english"], "$v_2$")
        self.assertEqual(by_id["symbol:08c16b9881428b3d#2"]["arabic"], "ع_2")
        self.assertEqual(by_id["symbol:a162e10a24127685#1"]["english"], r"$\vec{v}_1$")
        self.assertEqual(by_id["symbol:a162e10a24127685#2"]["english"], r"$\vec{v}_2$")
        self.assertEqual(by_id["symbol:2755c1da17f57acb#1"]["english"], r"$\vec{s}$")
        self.assertEqual(by_id["symbol:2755c1da17f57acb#1"]["arabic"], r"\vec{ف}")
        self.assertEqual(by_id["symbol:2755c1da17f57acb#2"]["english"], "$s$")
        self.assertEqual(by_id["symbol:2755c1da17f57acb#2"]["arabic"], "ف")
        self.assertEqual(by_id["symbol:mo"]["english"], r"$\vec{M}_O$")
        self.assertEqual(by_id["symbol:newton"]["arabic"], r"\text{نيوتن}")
        self.assertIn("symbol:bad", by_id)
        self.assertNotIn("symbol:bad#1", by_id)
        self.assertIn("kept composite symbol row symbol:bad", printed)
        self.assertEqual(len(_split_top_level(by_id["symbol:bad"]["english"])), 3)

    def test_eg12_attempt3_rekeyed_passes(self) -> None:
        fixture = _eg12()
        rows = glossary_candidate_rows(fixture["fragments"])
        reply = _rekey_attempt3(fixture["attempt3_entries"], rows)
        self.assertEqual(glossary_merge_problems(reply, rows, "arabic"), [])

    def test_stable_id_keeps_symbol_case(self) -> None:
        rows = [
            _row("symbol:H", "symbol", "$H$", "م"),
            _row("symbol:h", "symbol", "$h$", "ل"),
        ]
        book = book_map_from_glossary_reply(
            "applied_mathematics_ar_eg12_t0",
            {"language_code": "ar", "numerals": "arabic", "notation": "arabic"},
            {
                "entries": [
                    _entry("symbol", "$H$", "م", ["symbol:H"]),
                    _entry("symbol", "$h$", "ل", ["symbol:h"]),
                ],
                "dropped": [],
            },
            rows,
            merge_key="test",
        )
        ids = [entry["id"] for entry in book["entries"]]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])


class GlossaryMergeCallTests(unittest.TestCase):
    def _run(self) -> SimpleNamespace:
        return SimpleNamespace(
            ar_id="mathematics_ar_eg07_t1",
            ar_meta={
                "language_code": "ar",
                "numerals": "western",
                "notation": "latin",
            },
            model="gemini-3.8-flash",
        )

    def _fragments(self, root: Path) -> tuple[list[Path], list[dict]]:
        state = root / "mathematics_ar_eg07_t1" / "00_translation_state" / "map_fragments"
        state.mkdir(parents=True)
        fragment = {
            "section": "1.1",
            "observations": [
                {
                    "id": "unit:m",
                    "kind": "unit",
                    "source_plain": "m",
                    "source_math": "m",
                    "plain_lemma": "م",
                    "math_text": "م",
                    "also": ["متر"],
                    "abbreviation_only": True,
                    "status": "confirmed",
                    "section": "1.1",
                    "evidence": [{"quote": "20 م"}],
                },
                {
                    "id": "term:base",
                    "kind": "term",
                    "source_plain": "Base",
                    "source_math": "",
                    "plain_lemma": "القاعدة",
                    "math_text": "",
                    "status": "confirmed",
                    "section": "1.1",
                    "contexts": ["a factor"],
                    "evidence": [{"quote": "القاعدة"}],
                },
                {
                    "id": "symbol:x",
                    "kind": "symbol",
                    "source_plain": "",
                    "source_math": "$x$",
                    "plain_lemma": "",
                    "math_text": "$x$",
                    "status": "confirmed",
                    "section": "1.1",
                    "evidence": [{"quote": "$x$"}],
                },
            ],
        }
        path = state / "01_01.json"
        path.write_text(json.dumps(fragment), encoding="utf-8")
        return [path], [fragment]

    def _good(self, part_rows: list[dict]) -> dict:
        entries = []
        for row in part_rows:
            arabic = row["english"] if row["kind"] == "symbol" else row["arabic"]
            entries.append(_entry(row["kind"], row["english"], arabic, [row["id"]]))
        return {"entries": entries, "dropped": [], "conflicts": []}

    def test_reuses_a_stored_merge_until_a_section_file_changes(self) -> None:
        from translate import book_map_path, ensure_book_glossary, write_json

        calls: list[str] = []

        def invoke(user, thinking, attempt, part, part_rows):
            del user, thinking, attempt
            calls.append(part)
            return self._good(part_rows)

        def refuse(*_args, **_kwargs):
            raise AssertionError("glossary merge was called again")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths, fragments = self._fragments(root)
            with patch("translate.OUTPUT_DIR", root):
                first, _reused = ensure_book_glossary(self._run(), paths, fragments, invoke=invoke)
                self.assertIn(_reused, {"ran", "reused"})
                write_json(book_map_path("mathematics_ar_eg07_t1"), first)
                ensure_book_glossary(self._run(), paths, fragments, invoke=refuse)
                paths[0].write_text(paths[0].read_text(encoding="utf-8") + "\n", encoding="utf-8")
                ensure_book_glossary(self._run(), paths, fragments, invoke=invoke)
        self.assertEqual(calls, ["book", "book"])

    def test_retry_ladder_is_medium_then_high(self) -> None:
        from translate import ensure_book_glossary

        seen: list[str] = []

        def invoke(user, thinking, attempt, part, part_rows):
            del user, part
            seen.append(thinking)
            if attempt < 3:
                raise ValueError("row id missing")
            return self._good(part_rows)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths, fragments = self._fragments(root)
            with patch("translate.OUTPUT_DIR", root):
                ensure_book_glossary(self._run(), paths, fragments, invoke=invoke)
        self.assertEqual(seen, ["medium", "medium", "high"])

    def test_over_the_token_limit_splits_by_kind(self) -> None:
        from translate import ensure_book_glossary

        parts: list[str] = []

        def invoke(user, thinking, attempt, part, part_rows):
            del thinking, attempt
            parts.append(part)
            payload = json.loads(user.split("\n\nThe previous reply")[0])
            kinds = {group["kind"] for group in payload["groups"]}
            self.assertEqual(kinds, {part})
            return self._good(part_rows)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths, fragments = self._fragments(root)
            with patch("translate.OUTPUT_DIR", root):
                merged, _reused = ensure_book_glossary(
                    self._run(), paths, fragments, token_limit=1, invoke=invoke
                )
        self.assertEqual(parts, ["unit", "term", "symbol"])
        self.assertEqual(len(merged["entries"]), 3)
        self.assertEqual(_reused, "ran")

    def test_no_ocr_remains_in_the_glossary_path(self) -> None:
        root = Path(__file__).resolve().parents[2]
        text = (root / "scripts" / "src" / "book_map.py").read_text(encoding="utf-8")
        text += (root / "scripts" / "translate.py").read_text(encoding="utf-8")
        for needle in ("tesseract", "pdftoppm", "_ocr_page", "_quote_verified"):
            self.assertNotIn(needle, text)

    def test_feedback_accumulates_across_truncation_and_splits(self) -> None:
        from translate import (
            _GLOSSARY_TRUNCATION_LINE,
            _glossary_extend_history,
            _glossary_line_for_kind,
            ensure_book_glossary,
        )

        seen: list[tuple[str, int, str, str]] = []

        def invoke(user, thinking, attempt, part, part_rows):
            seen.append((part, attempt, thinking, user))
            if part == "book" and attempt == 1:
                return {
                    "entries": [
                        _entry("symbol", "$h$", "ل", ["symbol:x"]),
                        _entry("symbol", "$h$", "ل", ["symbol:x"]),
                    ],
                    "dropped": [],
                }
            if part == "book" and attempt == 2:
                raise ValueError(
                    "truncated reply: ValidationError EOF while parsing\n"
                    "For further information visit https://errors.pydantic.dev/\n"
                    "input_value='{'"
                )
            return self._good(part_rows)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths, fragments = self._fragments(root)
            with patch("translate.OUTPUT_DIR", root):
                ensure_book_glossary(self._run(), paths, fragments, invoke=invoke)
        parts = [item[0] for item in seen]
        self.assertEqual(parts[0], "book")
        self.assertEqual(parts[1], "book")
        self.assertEqual(parts[2:], ["unit", "term", "symbol"])
        self.assertEqual([item[1] for item in seen[2:]], [3, 3, 3])
        self.assertEqual([item[2] for item in seen[2:]], ["high", "high", "high"])
        third_prompts = [item[3] for item in seen[2:]]
        joined = "\n".join(third_prompts)
        self.assertIn("two symbol entries share english", joined)
        self.assertIn(_GLOSSARY_TRUNCATION_LINE, joined)
        self.assertNotIn("For further information visit", joined)
        self.assertNotIn("input_value=", joined)
        self.assertEqual(joined.count(_GLOSSARY_TRUNCATION_LINE), 3)
        symbol_prompt = next(item[3] for item in seen if item[0] == "symbol")
        unit_prompt = next(item[3] for item in seen if item[0] == "unit")
        self.assertIn("two symbol entries share english", symbol_prompt)
        self.assertNotIn("two symbol entries share english", unit_prompt)
        self.assertTrue(
            _glossary_line_for_kind(
                "two symbol entries share english '$h$'", "symbol", {"symbol:x"}
            )
        )
        self.assertFalse(
            _glossary_line_for_kind(
                "two symbol entries share english '$h$'", "unit", {"unit:m"}
            )
        )
        history = _glossary_extend_history([], [f"old line {index}" for index in range(210)])
        history = _glossary_extend_history(history, ["newest validation", "truncated reply: EOF"])
        self.assertLessEqual(len(history), 200)
        self.assertIn("newest validation", history)
        self.assertIn(_GLOSSARY_TRUNCATION_LINE, history)
        self.assertNotIn("old line 0", history)

    def test_last_rung_truncation_fails_without_split(self) -> None:
        from translate import ensure_book_glossary

        seen: list[tuple[str, int]] = []

        def invoke(user, thinking, attempt, part, part_rows):
            del user, thinking, part_rows
            seen.append((part, attempt))
            if attempt < 3:
                raise ValueError("row id missing")
            raise ValueError("truncated reply: ValidationError EOF while parsing")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths, fragments = self._fragments(root)
            with patch("translate.OUTPUT_DIR", root):
                with self.assertRaises(RuntimeError) as error:
                    ensure_book_glossary(self._run(), paths, fragments, invoke=invoke)
        self.assertEqual(seen, [("book", 1), ("book", 2), ("book", 3)])
        self.assertIn("truncated reply", str(error.exception))

    def test_per_kind_truncation_is_not_split_further(self) -> None:
        from translate import ensure_book_glossary

        seen: list[str] = []

        def invoke(user, thinking, attempt, part, part_rows):
            del user, thinking, attempt
            seen.append(part)
            if part == "unit":
                raise ValueError("truncated reply: ValidationError EOF while parsing")
            return self._good(part_rows)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths, fragments = self._fragments(root)
            with patch("translate.OUTPUT_DIR", root):
                with self.assertRaises(RuntimeError) as error:
                    ensure_book_glossary(
                        self._run(), paths, fragments, token_limit=1, invoke=invoke
                    )
        self.assertEqual(seen, ["unit", "unit", "unit"])
        self.assertNotIn("unit#", "".join(seen))
        self.assertIn("truncated reply", str(error.exception))
        self.assertIn("for unit", str(error.exception))

    def test_metrics_count_a_glossary_merge_call(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log = root / "00_api_call_logs" / "sample_glossary_merge"
            log.mkdir(parents=True)
            (log / "call.json").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-10-22T10:00:00+00:00",
                        "step": "glossary_merge",
                        "status": "success",
                        "model": "gemini-3.8-flash",
                        "usage": {"input_tokens": 10, "output_tokens": 4, "thinking_tokens": 2},
                    }
                ),
                encoding="utf-8",
            )
            summary = summarize(root)
        self.assertEqual(summary["steps"]["glossary_merge"]["calls"], 1)
        self.assertEqual(summary["steps"]["glossary_merge"]["errors"], 0)


if __name__ == "__main__":
    unittest.main()
