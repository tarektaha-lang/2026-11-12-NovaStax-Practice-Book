from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.prompting import Profile  # noqa: E402
from questions_review import (  # noqa: E402
    CHECK_STEP,
    ReplenishReply,
    ReviewPromptCache,
    SetReplenishJob,
    allowed_set_sizes,
    cache_content_stale,
    check_replenish,
    drop_figure_exercises,
    labelled_choice,
    load_logged_exercise_checks,
    plan_set_replenish,
    proofread,
    reject,
    replenish_known,
    rule_issues,
    valid_set_size,
)
from new_questions import Exercise  # noqa: E402


AR = Profile("ar", "western", "latin")
EN = Profile("en", "western", "latin")


def section_with(*, options: list[str], answer: str = "ب", text: str = "سؤال", stem: str = "") -> dict:
    return {
        "number": "1.3",
        "exercises": {
            "objectives": [{"id": "objective_1", "budget": 1, "exercises": 0, "word_problems": 0}],
            "total": 1,
            "word_problems": 0,
            "groups": [
                {
                    "id": "group_1",
                    "objective": "objective_1",
                    "stem": stem,
                    "exercises": [
                        {
                            "id": "exercise_1",
                            "text": text,
                            "options": list(options),
                            "answer": answer,
                            "objectives": ["objective_1"],
                        }
                    ],
                }
            ]
        },
    }


class StripFigureTests(unittest.TestCase):
    def test_group_stem_figure_drops_whole_set(self) -> None:
        document = section_with(
            options=["It is a good habit.", "It is a bad habit.", "Rare", "Hard"],
            answer="A",
            text="Picture 1: Drinking water.",
        )
        document["exercises"]["groups"][0]["figures"] = [
            {"figure_type": "other", "description": "Eight habit illustrations."}
        ]
        accepted, removed, dropped = drop_figure_exercises(document)
        self.assertEqual(accepted["exercises"]["total"], 0)
        self.assertEqual(len(removed), 1)
        self.assertEqual(dropped, 1)

    def test_exercise_figure_is_dropped_alone(self) -> None:
        document = {
            "number": "1.2.1",
            "exercises": {
                "objectives": [{"id": "objective_1", "budget": 2, "exercises": 0, "word_problems": 0}],
                "groups": [
                    {
                        "id": "group_1",
                        "objective": "objective_1",
                        "exercises": [
                            {
                                "id": "exercise_1",
                                "text": "No picture here.",
                                "options": ["A", "B", "C", "D"],
                                "objectives": ["objective_1"],
                                "answer": "A",
                            },
                            {
                                "id": "exercise_2",
                                "text": "Look at the picture.",
                                "figures": [{"figure_type": "other", "description": "A boy."}],
                                "options": ["A", "B", "C", "D"],
                                "objectives": ["objective_1"],
                                "answer": "A",
                            },
                        ],
                    }
                ],
                "total": 2,
                "word_problems": 0,
            },
        }
        accepted, removed, dropped = drop_figure_exercises(document)
        self.assertEqual(accepted["exercises"]["total"], 1)
        self.assertEqual(accepted["exercises"]["groups"][0]["exercises"][0]["id"], "exercise_1")
        self.assertEqual(len(removed), 1)
        self.assertEqual(dropped, 0)


class ReplenishKnownTests(unittest.TestCase):
    def test_arabic_book_allows_arabic_letters(self) -> None:
        meta = {
            "language_code": "ar",
            "numerals": "arabic",
            "notation": "arabic",
        }
        known = replenish_known("prompt", "part", meta)
        self.assertIn("ك", known)
        self.assertIn("ا", known)


class SetReplenishPlanTests(unittest.TestCase):
    def test_valid_sizes_for_multi_sets(self) -> None:
        group = {
            "id": "group_1",
            "objective": "objective_1",
            "stem": "Choose the correct answer.",
            "exercises": [
                {
                    "id": f"exercise_{index}",
                    "text": f"Item {index}",
                    "answer": "A",
                    "objectives": ["objective_1"],
                }
                for index in range(1, 9)
            ],
        }
        self.assertEqual(allowed_set_sizes(group), (4, 8, 12))
        self.assertTrue(valid_set_size(group))
        group["exercises"] = group["exercises"][:7]
        self.assertFalse(valid_set_size(group))

    def test_plan_after_one_rejection_from_eight(self) -> None:
        document = {
            "number": "1.1",
            "exercises": {
                "groups": [
                    {
                        "id": "group_1",
                        "objective": "objective_1",
                        "stem": "Pick the right word.",
                        "exercises": [
                            {
                                "id": f"exercise_{index}",
                                "text": f"Item {index}",
                                "answer": "A",
                                "objectives": ["objective_1"],
                            }
                            for index in range(1, 9)
                        ],
                    }
                ]
            },
        }
        accepted = copy.deepcopy(document)
        accepted["exercises"]["groups"][0]["exercises"] = accepted["exercises"]["groups"][0][
            "exercises"
        ][:7]
        rejected = [
            {
                "section": "1.1",
                "exercise": "exercise_8",
                "group": "group_1",
                "objective": "objective_1",
                "types": ["wrong_answer"],
                "reasons": ["The keyed answer does not match the sentence."],
            }
        ]
        jobs = plan_set_replenish(document, accepted, rejected)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].count, 1)
        self.assertEqual(jobs[0].target, 8)
        self.assertEqual(jobs[0].rejected[0]["reasons"], rejected[0]["reasons"])

    def test_form_set_stem_replenish_stays_one_group(self) -> None:
        stem = "Choose the correct answer in each of the following:"

        def mcq(index: int) -> dict:
            return {
                "id": f"exercise_{index}",
                "text": f"What is value {index} of $x$?",
                "options": ["$1$", "$2$", "$3$", "$4$"],
                "answer": "A",
                "objectives": ["objective_1"],
            }

        group = {
            "id": "group_1",
            "objective": "objective_1",
            "stem": stem,
            "exercises": [mcq(1), mcq(2), mcq(3)],
        }
        job = SetReplenishJob(
            section="6.2",
            stem=stem,
            group=group,
            objective="objective_1",
            target=4,
            count=1,
            kept=group["exercises"],
            rejected=[],
        )
        reply = ReplenishReply(
            status="ok",
            exercises=[Exercise.model_validate(mcq(4))],
        )
        document = {
            "number": "6.2",
            "exercises": {"objectives": [{"id": "objective_1"}]},
        }
        known = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
        check_replenish(reply, job, document, "mathematics_en_in10", known)


class LabelledChoiceTests(unittest.TestCase):
    def test_arabic_prefixed_letter_is_stripped(self) -> None:
        after, what = labelled_choice("(أ) الحذف", AR)
        self.assertEqual(after, "الحذف")
        self.assertIn("prefixed choice letter", what)

    def test_plain_choice_is_left(self) -> None:
        self.assertIsNone(labelled_choice("الحذف", AR))
        self.assertIsNone(labelled_choice("اسم", AR))
        self.assertIsNone(labelled_choice("بِـ", AR))

    def test_proofread_strips_option_labels_only(self) -> None:
        document = section_with(
            options=["(أ) الحذف", "(ب) الزيادة", "(ج) البدل", "(د) الفصل والوصل"],
            answer="أ) الحياة؛ بتبديل الواو ألفاً.",
        )
        found = proofread(document, AR)
        options = document["exercises"]["groups"][0]["exercises"][0]["options"]
        self.assertEqual(options, ["الحذف", "الزيادة", "البدل", "الفصل والوصل"])
        self.assertEqual(
            document["exercises"]["groups"][0]["exercises"][0]["answer"],
            "أ) الحياة؛ بتبديل الواو ألفاً.",
        )
        self.assertEqual(len(found), 4)
        self.assertTrue(all(finding.check == "labelled_choice" and finding.corrected for finding in found))

    def test_degree_bullet_is_rewritten(self) -> None:
        document = section_with(
            options=[r"$٤٨^\bullet$", r"$١٣٢^\circ$", r"$٩٠^\bullet$", r"$٤٨^\circ$"],
            answer=r"$٤٨^\bullet$",
            text=r"قياس الزاوية $٤٨^\bullet$.",
        )
        found = proofread(document, AR)
        exercise = document["exercises"]["groups"][0]["exercises"][0]
        self.assertEqual(exercise["text"], r"قياس الزاوية $٤٨^\circ$.")
        self.assertEqual(exercise["answer"], r"$٤٨^\circ$")
        self.assertEqual(exercise["options"][0], r"$٤٨^\circ$")
        self.assertTrue(
            any(finding.check == "degree_bullet" and finding.corrected for finding in found)
        )
        self.assertEqual(document["exercises"]["total"], 1)

    def test_odd_dollars_reject_the_exercise(self) -> None:
        document = section_with(options=["$1$", "$2$", "$3$", "$4$"], answer=r"$4\%")
        issues = rule_issues(document, AR)
        accepted, rejected, dropped = reject(document, issues)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["types"], ["maths_delimiters"])
        self.assertEqual(accepted["exercises"]["total"], 0)
        self.assertEqual(dropped, 1)

    def test_unbalanced_braces_reject_the_exercise(self) -> None:
        document = section_with(
            options=["$1$", "$2$", "$3$", "$4$"],
            text=r"$1\text{ ريال} = 1000\text{ بيسة$.",
        )
        issues = rule_issues(document, AR)
        accepted, rejected, _dropped = reject(document, issues)
        self.assertEqual(len(rejected), 1)
        self.assertTrue(any("unbalanced braces" in row for row in rejected[0]["reasons"]))
        self.assertEqual(accepted["exercises"]["total"], 0)

    def test_english_prefixed_letter_is_stripped(self) -> None:
        document = section_with(
            options=[
                "(A) All information is included",
                "(B) Correct English (punctuation and spelling)",
                "(C) Words are suitable and varied",
                "(D) It is neat and clear",
            ],
            answer="B",
            text="Which checklist point does this draft fail?",
        )
        proofread(document, EN)
        options = document["exercises"]["groups"][0]["exercises"][0]["options"]
        self.assertEqual(
            options,
            [
                "All information is included",
                "Correct English (punctuation and spelling)",
                "Words are suitable and varied",
                "It is neat and clear",
            ],
        )


class LogImportTests(unittest.TestCase):
    def test_load_logged_exercise_checks_keeps_newest_success(self) -> None:
        paper = section_with(options=[], answer="ok", text="q1")
        papers = {"01_03": paper}
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            for index, (stamp, wrong) in enumerate(
                (("2026-01-02T10:00:00", True), ("2026-01-02T11:00:00", False))
            ):
                folder = logs / f"2026-01-02T10-00-0{index}_1.3-exercise_1_{CHECK_STEP}"
                folder.mkdir()
                (folder / "call.json").write_text(
                    json.dumps(
                        {
                            "timestamp": stamp,
                            "page_number": "1.3-exercise_1",
                            "step": CHECK_STEP,
                            "status": "success" if not wrong else "error",
                            "returned": "ok" if not wrong else None,
                            "book_id": "book",
                        }
                    ),
                    encoding="utf-8",
                )
                if not wrong:
                    (folder / "response_text.txt").write_text(
                        '{"status":"ok","abort_reason":"","issues":[]}',
                        encoding="utf-8",
                    )
            merged, hits = load_logged_exercise_checks(
                "book",
                {"1.3": "01_03"},
                papers,
                logs_dir=logs,
            )
        self.assertEqual(hits, 1)
        self.assertIn("exercise_1", merged["01_03"])


class CacheRecoveryTests(unittest.TestCase):
    def test_cache_content_stale_matches_gemini_403(self) -> None:
        error = (
            "ClientError: 403 PERMISSION_DENIED. "
            "{'error': {'message': 'CachedContent not found (or permission denied)'}}"
        )
        self.assertTrue(cache_content_stale(error))
        self.assertFalse(cache_content_stale("ClientError: 429 RESOURCE_EXHAUSTED"))

    def test_review_prompt_cache_refreshes_before_ttl(self) -> None:
        counter = iter([100.0, 3400.0])
        serial = {"n": 0}

        class FakeClient:
            class caches:
                @staticmethod
                def create(**kwargs):
                    del kwargs
                    serial["n"] += 1

                    class Usage:
                        total_token_count = 100

                    class Cache:
                        name = f"cache/{serial['n']}"
                        usage_metadata = Usage()

                    return Cache()

        holder = ReviewPromptCache(FakeClient(), "model", "prompt", "book")
        with patch("questions_review.time.perf_counter", lambda: next(counter)):
            first = holder.current()
            second = holder.current()
        self.assertEqual(first, "cache/1")
        self.assertEqual(second, "cache/2")
        self.assertEqual(holder.names, ["cache/1", "cache/2"])

    def test_review_prompt_cache_uses_given_ttl(self) -> None:
        seen: dict[str, str] = {}

        class FakeClient:
            class caches:
                @staticmethod
                def create(**kwargs):
                    seen["ttl"] = kwargs["config"].ttl

                    class Usage:
                        total_token_count = 100

                    class Cache:
                        name = "cache/1"
                        usage_metadata = Usage()

                    return Cache()

        holder = ReviewPromptCache(
            FakeClient(), "model", "prompt", "book", ttl_seconds=86400
        )
        self.assertEqual(holder.current(), "cache/1")
        self.assertEqual(seen["ttl"], "86400s")


if __name__ == "__main__":
    unittest.main()
