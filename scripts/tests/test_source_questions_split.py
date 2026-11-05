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

from src.api_logger import ApiLogger  # noqa: E402
from source_questions import (  # noqa: E402
    ExtractReply,
    Question,
    QuestionGroup,
    extract_with_fallback,
    questions_per_page,
    record_questions_per_page,
    request_extract,
    size_abort,
    truncated_extract,
)


def ok_extract(*texts: str) -> ExtractReply:
    return ExtractReply(
        status="ok",
        groups=[
            QuestionGroup(
                questions=[Question(text=text, objectives=["objective_1"])]
            )
            for text in texts
        ],
    )


def abort(reason: str) -> ExtractReply:
    return ExtractReply(status="abort", abort_reason=reason, groups=[])


SIZE_REASON = (
    "The requested extraction exceeds the single-response context and "
    "generation output capacity of this model."
)


class FakeResponse:
    def __init__(self, text: str, finish: str = "STOP") -> None:
        self.text = text
        part = SimpleNamespace(text=text, thought=False, inline_data=None)
        self.candidates = [
            SimpleNamespace(
                content=SimpleNamespace(parts=[part]),
                finish_reason=finish,
            )
        ]
        self.usage_metadata = None

    def model_dump(self, mode="json", exclude_none=True):
        return {"candidates": [{"content": {"parts": [{"text": self.text}]}}]}


class FakeClient:
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.contents: list[list] = []
        self.configs: list = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.contents.append(list(contents))
        self.configs.append(config)
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class Ask:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[tuple] = []

    def __call__(self, parts, extra, thinking, item):
        self.calls.append((list(parts), extra, thinking, item))
        return self.script.pop(0)


class TruncationHelperTests(unittest.TestCase):
    def test_max_tokens_is_truncation_even_with_valid_json(self) -> None:
        reply = ok_extract("Find x.")
        self.assertTrue(truncated_extract(reply, None, "MAX_TOKENS"))
        self.assertFalse(truncated_extract(reply, None, "STOP"))

    def test_size_abort_matches_real_reason(self) -> None:
        reply = abort(SIZE_REASON)
        self.assertTrue(size_abort(reply))
        self.assertTrue(truncated_extract(reply, None, "STOP"))
        self.assertFalse(size_abort(abort("the pages are blank")))
        self.assertFalse(truncated_extract(abort("the pages are blank"), None, "STOP"))


class ExtractWithFallbackTests(unittest.TestCase):
    def test_whole_section_max_tokens_then_three_pdf_calls(self) -> None:
        pdfs = [Path("a.pdf"), Path("b.pdf"), Path("c.pdf")]
        parts = ["p1", "p2", "p3"]
        fixture = ok_extract("Q1", "Q2", "Q3")
        ask = Ask(
            [
                (None, "ValidationError: Invalid JSON: EOF", "MAX_TOKENS"),
                (ok_extract("Q1"), None, "STOP"),
                (ok_extract("Q2"), None, "STOP"),
                (ok_extract("Q3"), None, "STOP"),
            ]
        )
        reply = extract_with_fallback(
            ask, parts, pdfs, "high", "2.1.1", ["objective_1"]
        )
        self.assertEqual(len(ask.calls), 4)
        self.assertEqual(ask.calls[0][0], parts)
        self.assertIsNone(ask.calls[0][1])
        self.assertEqual(ask.calls[0][2], "high")
        for index, call in enumerate(ask.calls[1:], start=1):
            self.assertEqual(call[0], [parts[index - 1]])
            self.assertEqual(
                call[1],
                [f"source PDF {index} of 3: {pdfs[index - 1].name}"],
            )
            self.assertEqual(call[2], "high")
        self.assertEqual(
            [q.text for g in reply.groups for q in g.questions],
            [q.text for g in fixture.groups for q in g.questions],
        )

    def test_per_pdf_max_tokens_retries_medium(self) -> None:
        pdfs = [Path("a.pdf"), Path("b.pdf")]
        parts = ["p1", "p2"]
        ask = Ask(
            [
                (None, "EOF", "MAX_TOKENS"),
                (ok_extract("Q1"), None, "STOP"),
                (None, "EOF", "MAX_TOKENS"),
                (ok_extract("Q2"), None, "STOP"),
            ]
        )
        reply = extract_with_fallback(
            ask, parts, pdfs, "high", "1.1.1", ["objective_1"]
        )
        self.assertEqual(len(ask.calls), 4)
        self.assertEqual(ask.calls[2][2], "high")
        self.assertEqual(ask.calls[3][2], "medium")
        self.assertEqual(ask.calls[3][3], "1.1.1-pdf2-medium")
        self.assertEqual(len(reply.groups), 2)

    def test_size_abort_triggers_split_other_abort_stops(self) -> None:
        pdfs = [Path("a.pdf"), Path("b.pdf")]
        parts = ["p1", "p2"]
        ask = Ask(
            [
                (abort(SIZE_REASON), None, "STOP"),
                (ok_extract("Q1"), None, "STOP"),
                (ok_extract("Q2"), None, "STOP"),
            ]
        )
        reply = extract_with_fallback(
            ask, parts, pdfs, "high", "2.3.2", ["objective_1"]
        )
        self.assertEqual(len(ask.calls), 3)
        self.assertEqual(len(reply.groups), 2)
        ask = Ask([(abort("the pages are blank"), None, "STOP")])
        reply = extract_with_fallback(
            ask, parts, pdfs, "high", "2.3.2", ["objective_1"]
        )
        self.assertEqual(reply.status, "abort")
        self.assertEqual(len(ask.calls), 1)

    def test_one_pdf_high_goes_to_medium(self) -> None:
        pdfs = [Path("only.pdf")]
        parts = ["p1"]
        ask = Ask(
            [
                (None, "EOF", "MAX_TOKENS"),
                (ok_extract("Q1"), None, "STOP"),
            ]
        )
        reply = extract_with_fallback(
            ask, parts, pdfs, "high", "1.1.1", ["objective_1"]
        )
        self.assertEqual(len(ask.calls), 2)
        self.assertEqual(ask.calls[1][2], "medium")
        self.assertEqual(reply.status, "ok")

    def test_one_pdf_already_medium_fails_without_further_call(self) -> None:
        pdfs = [Path("only.pdf")]
        parts = ["p1"]
        ask = Ask([(None, "EOF", "MAX_TOKENS")])
        with self.assertRaises(RuntimeError):
            extract_with_fallback(ask, parts, pdfs, "medium", "1.1.1", ["objective_1"])
        self.assertEqual(len(ask.calls), 1)

    def test_non_size_abort_on_a_pdf_fails_the_section(self) -> None:
        pdfs = [Path("a.pdf"), Path("b.pdf")]
        parts = ["p1", "p2"]
        ask = Ask(
            [
                (None, "EOF", "MAX_TOKENS"),
                (abort("the pages are blank"), None, "STOP"),
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "blank"):
            extract_with_fallback(ask, parts, pdfs, "high", "1.1.1", ["objective_1"])


class RequestExtractStopTests(unittest.TestCase):
    def test_stops_on_first_max_tokens_and_logs_finish_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ApiLogger, "_compute_ntp_offset", return_value=0.0):
                logger = ApiLogger(Path(tmp))
            client = FakeClient([FakeResponse("{", "MAX_TOKENS")])
            with patch("source_questions.time.sleep"):
                result, error, finish = request_extract(
                    client,
                    "gemini-test",
                    "prompt",
                    "section",
                    [],
                    ["objective_1"],
                    "high",
                    logger,
                    "1.1.1",
                    {},
                )
            self.assertIsNone(result)
            self.assertEqual(finish, "MAX_TOKENS")
            self.assertIn("EOF", error or "")
            self.assertEqual(len(client.contents), 1)
            config = client.configs[0]
            self.assertEqual(getattr(config, "max_output_tokens", None), 65_536)
            call = json.loads(
                next((Path(tmp) / "00_api_call_logs").iterdir()).joinpath("call.json").read_text()
            )
            self.assertEqual(call["finish_reason"], "MAX_TOKENS")

    def test_schema_errors_still_retry(self) -> None:
        payload = ok_extract("Find x.").model_dump_json()
        leaked = ExtractReply(
            status="ok",
            groups=[
                QuestionGroup(
                    questions=[
                        Question(
                            text="| a | b |\n| --- | --- |",
                            objectives=["objective_1"],
                        )
                    ]
                )
            ],
        ).model_dump_json()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ApiLogger, "_compute_ntp_offset", return_value=0.0):
                logger = ApiLogger(Path(tmp))
            client = FakeClient(
                [
                    FakeResponse(leaked),
                    FakeResponse(payload),
                ]
            )
            with patch("source_questions.time.sleep"):
                result, error, finish = request_extract(
                    client,
                    "gemini-test",
                    "prompt",
                    "section",
                    [],
                    ["objective_1"],
                    "high",
                    logger,
                    "1.1.1",
                    {},
                )
            self.assertIsNotNone(result)
            self.assertEqual(result.status, "ok")
            self.assertEqual(len(client.contents), 2)
            self.assertEqual(finish, "STOP")


class QuestionsPerPageTests(unittest.TestCase):
    def test_warns_when_sparse(self) -> None:
        groups = ok_extract("Q1").groups
        notes: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ApiLogger, "_compute_ntp_offset", return_value=0.0):
                logger = ApiLogger(Path(tmp))
            (logger.logs_folder / "t_1.1.1_section_questions").mkdir()
            (
                logger.logs_folder / "t_1.1.1_section_questions" / "call.json"
            ).write_text("{}", encoding="utf-8")
            with patch(
                "sources.pdf_page_count", return_value=10
            ):
                rate = questions_per_page(groups, [Path("a.pdf")])
            self.assertAlmostEqual(rate, 0.1)
            record_questions_per_page(logger, "1.1.1", rate, notes.append)
            self.assertTrue(any("sparse" in note for note in notes))
            data = json.loads(
                (
                    logger.logs_folder
                    / "t_1.1.1_section_questions"
                    / "call.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(data["questions_per_page"], 0.1)


if __name__ == "__main__":
    unittest.main()
