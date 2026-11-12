"""Focused mocked checks for the two question-stage Batch defaults."""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import new_questions  # noqa: E402
import questions_review  # noqa: E402


class Progress:
    def asking(self, *_args) -> None:
        pass

    def say(self, *_args) -> None:
        pass

    def done(self, *_args) -> None:
        pass


class ObjectiveStore:
    def __init__(self) -> None:
        self.saved: set[tuple[str, str]] = set()

    def has(self, stem: str, objective: str) -> bool:
        return (stem, objective) in self.saved

    def save(
        self,
        job: new_questions.ObjectiveJob,
        _groups: list[new_questions.ExerciseGroup],
    ) -> None:
        self.saved.add((job.section["stem"], job.objective["id"]))


class ReviewStore:
    def __init__(self) -> None:
        self.saved: set[tuple[str, str]] = set()

    def has(self, stem: str, exercise: str) -> bool:
        return (stem, exercise) in self.saved

    def item_applied(self, _item: str) -> bool:
        return False

    def save(self, stem: str, found: dict[str, list]) -> None:
        self.saved.update((stem, exercise) for exercise in found)


def objective_job() -> new_questions.ObjectiveJob:
    section = {
        "stem": "01_01",
        "number": "1.1",
        "title": "One",
        "spec": {"objectives": [{"id": "objective_1"}]},
    }
    return new_questions.ObjectiveJob(
        section=section,
        objective={"id": "objective_1"},
        count=1,
        parts=["part"],
        allowed=["objective_1"],
        source_stems=None,
        item="1.1-objective_1",
        name="1.1 objective_1",
        digest="digest",
    )


def objective_run(store: ObjectiveStore | None = None):
    return SimpleNamespace(
        client=object(),
        model="gemini-3.8-flash",
        logger=object(),
        book_id="book",
        meta={"language_code": "en", "notation": "latin"},
        thinking="high",
        prompt=SimpleNamespace(text="prompt", files=[]),
        progress=Progress(),
        alphabet=frozenset(),
        force_generate=False,
        section=None,
        update=False,
        store=store,
    )


def review_job() -> questions_review.Job:
    return questions_review.Job(
        row={
            "stem": "01_01",
            "number": "1.1",
            "title": "One",
            "module_path": [],
        },
        document={},
        group={"objective": "objective_1"},
        exercise={"id": "exercise_1"},
        item="1.1-exercise_1",
        name="1.1 exercise_1",
        part="part",
        ids=frozenset({"exercise_1"}),
        digest="digest",
    )


def review_run(store: ReviewStore):
    return SimpleNamespace(
        client=object(),
        model="gemini-3.8-flash",
        logger=object(),
        book_id="book",
        thinking="high",
        prompt=SimpleNamespace(text="prompt", files=[]),
        progress=Progress(),
        section=None,
        update=False,
        store=store,
    )


class DefaultModeTests(unittest.TestCase):
    def test_both_stage_functions_default_to_batch(self) -> None:
        self.assertTrue(new_questions.DEFAULT_BATCH)
        self.assertTrue(questions_review.DEFAULT_BATCH)
        self.assertTrue(
            inspect.signature(new_questions.write_book)
            .parameters["batch"]
            .default
        )
        self.assertTrue(
            inspect.signature(questions_review.check_book)
            .parameters["batch"]
            .default
        )

    def test_requests_have_stable_batch_keys(self) -> None:
        objective = new_questions.objective_request(
            objective_run(), objective_job(), 2
        )
        review = questions_review.batch_request(
            review_run(ReviewStore()), review_job(), 3
        )
        self.assertEqual(objective.metadata["item"], "1.1-objective_1")
        self.assertEqual(review.metadata["item"], "1.1-exercise_1")
        self.assertIn("|a2", objective.metadata["log_key"])
        self.assertIn("|a3", review.metadata["log_key"])


class RetryTests(unittest.TestCase):
    def test_new_questions_retries_only_failed_objective_with_feedback(self) -> None:
        job = objective_job()
        store = ObjectiveStore()
        attempts: list[int] = []

        def fake_runner(*args, **kwargs) -> None:
            attempt = args[7]
            queue = args[4]
            attempts.append(attempt)
            self.assertEqual([row.item for row in queue[0].items], [job.item])
            if attempt == 1:
                kwargs["on_account"](
                    queue[0],
                    [(job, False, "bad reply", ["wrong count"], False)],
                )
            else:
                self.assertIn("wrong count", job.feedback or "")
                kwargs["on_account"](
                    queue[0], [(job, True, None, None, False)]
                )

        with (
            patch.object(new_questions, "run_batch_jobs", fake_runner),
            patch.object(new_questions.time, "sleep"),
        ):
            failures, _abandoned = new_questions.write_objectives_batch(
                objective_run(store), store, [job], object()
            )
        self.assertEqual(failures, [])
        self.assertEqual(attempts, [1, 2])

    def test_questions_review_retries_only_failed_exercise(self) -> None:
        job = review_job()
        store = ReviewStore()
        attempts: list[int] = []

        def fake_runner(*args, **kwargs) -> None:
            attempt = args[7]
            queue = args[4]
            attempts.append(attempt)
            outcomes = (
                [(job, False, "bad reply", False, None)]
                if attempt == 1
                else [(job, True, None, False, None)]
            )
            kwargs["on_account"](queue[0], outcomes)

        with (
            patch.object(questions_review, "run_batch_jobs", fake_runner),
            patch.object(questions_review.time, "sleep"),
        ):
            _written, failures, _abandoned = (
                questions_review.check_exercises_batch(
                    review_run(store), [job], object()
                )
            )
        self.assertEqual(failures, [])
        self.assertEqual(attempts, [1, 2])

    def test_saved_job_is_adopted_without_resubmitting(self) -> None:
        job = objective_job()
        store = ObjectiveStore()
        seen: dict[str, int] = {}
        entry = {
            "display_name": "saved",
            "attempt": 1,
            "keys": {job.item: job.digest},
        }

        def fake_runner(*args, **kwargs) -> None:
            seen["queued"] = len(args[4])
            seen["adopted"] = len(args[5])
            chunk = args[5][0][1]
            kwargs["on_account"](
                chunk, [(job, True, None, None, False)]
            )

        with patch.object(new_questions, "run_batch_jobs", fake_runner):
            failures, _abandoned = new_questions.write_objectives_batch(
                objective_run(store),
                store,
                [job],
                object(),
                adopted=[entry],
                submit_leftovers=False,
            )
        self.assertEqual(failures, [])
        self.assertEqual(seen, {"queued": 0, "adopted": 1})


class ResponseMappingTests(unittest.TestCase):
    def test_successful_rows_are_saved_by_item(self) -> None:
        objective_store = ObjectiveStore()
        objective = objective_job()
        with (
            patch.object(
                new_questions,
                "response_and_error",
                return_value=(object(), None),
            ),
            patch.object(
                new_questions,
                "generate_content_response",
                return_value=object(),
            ),
            patch.object(
                new_questions,
                "parse_objective_response",
                return_value=(
                    new_questions.WriteReply(status="ok", groups=[]),
                    {},
                ),
            ),
            patch.object(new_questions, "log_objective_batch_call"),
        ):
            ok, error, _problems, _terminal = (
                new_questions.apply_objective_row(
                    objective_run(),
                    objective_store,
                    objective,
                    object(),
                    1,
                    1.0,
                    "batches/job",
                )
            )
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertTrue(objective_store.has("01_01", "objective_1"))

        review_store = ReviewStore()
        review = review_job()
        run = review_run(review_store)
        with (
            patch.object(
                questions_review,
                "response_and_error",
                return_value=(object(), None),
            ),
            patch.object(
                questions_review,
                "generate_content_response",
                return_value=object(),
            ),
            patch.object(
                questions_review,
                "genai_answer_text",
                return_value="{}",
            ),
            patch.object(
                questions_review,
                "interpret_check_response",
                return_value=questions_review.CheckReply(
                    status="ok", issues=[]
                ),
            ),
            patch.object(questions_review, "log_check_call"),
        ):
            ok, error, _terminal = questions_review.apply_check_row(
                run, review, object(), 1, 1.0, "batches/job"
            )
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertTrue(review_store.has("01_01", "exercise_1"))


class PricingLogTests(unittest.TestCase):
    def test_batch_logs_set_boolean_batch_flag(self) -> None:
        captured: list[dict] = []

        def capture(_logger, **kwargs) -> None:
            captured.append(kwargs)

        run = objective_run()
        with patch.object(new_questions, "safe_log", capture):
            new_questions.log_objective_batch_call(
                run,
                objective_job(),
                1,
                None,
                None,
                "missing",
                1.0,
                "batches/job",
                {},
            )
        with patch.object(questions_review, "safe_log", capture):
            questions_review.log_check_call(
                object(),
                model="gemini-3.8-flash",
                prompt="prompt",
                parts=["part"],
                thinking="high",
                item="item",
                attempt=1,
                context={},
                response=None,
                result=None,
                error_message="missing",
                duration=1.0,
                batch=True,
                batch_name="batches/job",
                batch_key="key",
            )
        self.assertEqual([row["extra"]["batch"] for row in captured], [True, True])


if __name__ == "__main__":
    unittest.main()
