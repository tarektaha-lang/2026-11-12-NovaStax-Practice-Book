from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.api_logger import ApiLogger, finish_reason  # noqa: E402
from new_questions import (  # noqa: E402
    ProblemsError,
    check_write,
    pydantic_problems,
    request_exercises,
    retry_feedback,
)


ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def ok_json(options: list[str] | None = None) -> str:
    return json.dumps(
        {
            "status": "ok",
            "groups": [
                {
                    "exercises": [
                        {
                            "text": "Which point?",
                            "options": options or ["$1$", "$2$", "$3$", "$4$"],
                            "objectives": ["objective_1"],
                            "answer": "B",
                        }
                    ]
                }
            ],
        }
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
        self.models = self

    def generate_content(self, model, contents, config):
        self.contents.append(list(contents))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _logger(root: Path) -> ApiLogger:
    with patch.object(ApiLogger, "_compute_ntp_offset", return_value=0.0):
        return ApiLogger(root)


class FinishReasonTests(unittest.TestCase):
    def test_reads_candidate_even_when_json_is_broken(self) -> None:
        self.assertEqual(finish_reason(FakeResponse("{", "MAX_TOKENS")), "MAX_TOKENS")
        self.assertIsNone(finish_reason(None))


class RetryFeedbackTests(unittest.TestCase):
    def test_t4_2_merges_locations_and_keeps_semicolon_messages(self) -> None:
        jerry = [
            f"group 1 exercise {number} choice ({letter}) trails 'Jerry' after its maths, "
            "which is not part of the choice"
            for number in range(1, 7)
            for letter in "ABCD"
        ]
        self.assertEqual(len(jerry), 24)
        text = retry_feedback(jerry)
        self.assertIsNotNone(text)
        self.assertIn("Your previous reply was rejected for these reasons:", text)
        self.assertIn("Write the whole reply again", text)
        self.assertEqual(text.count("trails 'Jerry'"), 1)
        self.assertIn("group 1 exercise 1 choice (A)", text)
        self.assertIn("group 1 exercise 6 choice (D)", text)
        mixed = [
            "group 1 exercise 1 text writes ';' outside $...$",
            "group 1 exercise 2 text writes ';' outside $...$",
            "group 1 exercise 1 has no answer",
        ]
        text = retry_feedback(mixed)
        self.assertIn("writes ';' outside $...$", text)
        self.assertEqual(text.count("writes ';' outside $...$"), 1)
        self.assertIn("has no answer", text)
        many = [f"unique problem {i} with a longish reason" for i in range(15)]
        text = retry_feedback(many)
        self.assertLessEqual(text.count("\n- "), 10)
        self.assertIn("… and", text)
        self.assertLessEqual(len(text), 2000)

    def test_t4_7_problems_error_string_matches_old_join(self) -> None:
        problems = [
            "group 1 exercise 1 has no answer",
            "group 1 exercise 2 offers 3 choices, not the 4 a multiple-choice exercise carries",
        ]
        error = ProblemsError(problems)
        self.assertEqual(str(error), "; ".join(problems))
        with self.assertRaises(ProblemsError) as caught:
            check_write(
                __import__("new_questions", fromlist=["WriteReply"]).WriteReply.model_validate_json(
                    ok_json(["$1$", "$2$", "$3$"])
                ),
                1,
                "objective_1",
                ["objective_1"],
                ALPHABET,
            )
        self.assertEqual(str(caught.exception), "; ".join(caught.exception.problems))


class RequestExercisesRetryTests(unittest.TestCase):
    @contextmanager
    def _run(self, responses, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            logger = _logger(Path(tmp))
            client = FakeClient(responses)
            with patch("new_questions.time.sleep"):
                result = request_exercises(
                    client,
                    "gemini-test",
                    "Which point?",
                    ["assignment"],
                    1,
                    "objective_1",
                    ["objective_1"],
                    "high",
                    logger,
                    "1.1-objective_1",
                    {"book_id": "test"},
                    alphabet=ALPHABET,
                    **kwargs,
                )
            yield result, client, Path(tmp)

    def test_t4_1_second_attempt_contents_end_with_feedback(self) -> None:
        with self._run(
            [
                FakeResponse(ok_json(["$1$", "$2$", "$3$"])),
                FakeResponse(ok_json()),
            ]
        ) as (result, client, _root):
            self.assertEqual(result.status, "ok")
            self.assertEqual(len(client.contents), 2)
            self.assertNotIn(
                "Your previous reply was rejected",
                "".join(map(str, client.contents[0])),
            )
            self.assertTrue(
                str(client.contents[1][-1]).startswith(
                    "Your previous reply was rejected for these reasons:"
                )
            )

    def test_t4_3_rate_limit_keeps_feedback_and_does_not_consume_attempt(self) -> None:
        with self._run(
            [
                FakeResponse(ok_json(["$1$", "$2$", "$3$"])),
                RuntimeError("RESOURCE_EXHAUSTED 429 too many"),
                FakeResponse(ok_json()),
            ]
        ) as (result, client, _root):
            self.assertEqual(result.status, "ok")
            self.assertEqual(len(client.contents), 3)
            self.assertTrue(
                str(client.contents[2][-1]).startswith(
                    "Your previous reply was rejected for these reasons:"
                )
            )

    def test_t4_4_transport_and_max_tokens_clear_feedback(self) -> None:
        with self._run(
            [
                FakeResponse(ok_json(["$1$", "$2$", "$3$"])),
                ConnectionError("connection reset"),
                FakeResponse(ok_json()),
            ]
        ) as (_result, client, _root):
            self.assertEqual(len(client.contents), 3)
            self.assertFalse(
                str(client.contents[2][-1]).startswith("Your previous reply was rejected")
            )
        with self._run(
            [
                FakeResponse("{", "MAX_TOKENS"),
                FakeResponse(ok_json()),
            ]
        ) as (_result, client, _root):
            self.assertEqual(len(client.contents), 2)
            self.assertFalse(
                str(client.contents[1][-1]).startswith("Your previous reply was rejected")
            )

    def test_t4_5_feedback_logged_as_feedback_txt_not_a_part(self) -> None:
        with self._run(
            [
                FakeResponse(ok_json(["$1$", "$2$", "$3$"])),
                FakeResponse(ok_json()),
            ]
        ) as (_result, _client, root):
            logs = sorted((root / "00_api_call_logs").iterdir())
            self.assertGreaterEqual(len(logs), 2)
            second = logs[1]
            self.assertTrue((second / "feedback.txt").is_file())
            self.assertTrue((second / "part_1.txt").is_file())
            self.assertFalse((second / "part_2.txt").is_file())
            extra = json.loads((second / "call.json").read_text(encoding="utf-8"))
            self.assertEqual(extra.get("feedback_from_attempt"), 1)
            self.assertTrue(extra.get("feedback_problems"))
            self.assertEqual(extra.get("finish_reason"), "STOP")
            first = json.loads((logs[0] / "call.json").read_text(encoding="utf-8"))
            self.assertNotIn("feedback_from_attempt", first)
            self.assertFalse((logs[0] / "feedback.txt").exists())

    def test_t4_6_known_excludes_feedback_letters(self) -> None:
        captured: list[frozenset[str]] = []
        original = check_write

        def wrapping(reply, target, objective, allowed, known):
            captured.append(frozenset(known))
            if len(reply.groups[0].exercises[0].options) == 4:
                return None
            return original(reply, target, objective, allowed, known)

        with tempfile.TemporaryDirectory() as tmp:
            logger = _logger(Path(tmp))
            client = FakeClient(
                [
                    FakeResponse(ok_json(["$1$", "$2$", "$3$"])),
                    FakeResponse(ok_json()),
                ]
            )
            with (
                patch("new_questions.time.sleep"),
                patch("new_questions.check_write", wrapping),
            ):
                request_exercises(
                    client,
                    "gemini-test",
                    "aaa",
                    ["bbb"],
                    1,
                    "objective_1",
                    ["objective_1"],
                    "high",
                    logger,
                    "1.1-objective_1",
                    {},
                    alphabet=frozenset(),
                )
        self.assertGreaterEqual(len(captured), 2)
        self.assertNotIn("Q", captured[1])
        self.assertTrue(captured[1] <= frozenset("ab"))

    def test_pydantic_problems_keep_loc_and_msg(self) -> None:
        from pydantic import ValidationError

        from new_questions import WriteReply

        with self.assertRaises(ValidationError) as caught:
            WriteReply.model_validate_json("{")
        rows = pydantic_problems(caught.exception)
        self.assertTrue(rows)
        self.assertTrue(any("EOF" in row or "json" in row.lower() for row in rows))


if __name__ == "__main__":
    unittest.main()
