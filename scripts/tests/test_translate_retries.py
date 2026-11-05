from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from draw import same_picture  # noqa: E402
from specs import Progress  # noqa: E402
from translate import (  # noqa: E402
    GroupTranslateReply,
    request_reply,
    source_corruption_abort,
    terminal_section_error,
    translate_objective,
    transient_error,
)


def run_state():
    return SimpleNamespace(
        attempt_lock=threading.Lock(),
        attempt_counts={},
        attempt_errors={},
        blocked_items=set(),
    )


class TranslateRetryTests(unittest.TestCase):
    def test_repeated_semantic_error_opens_circuit_after_two_calls(self) -> None:
        run = run_state()
        error = "ValueError: exercise.answer: English prose remains: Area"
        with (
            patch("translate.logged_json_call", return_value=(None, error)) as call,
            patch("translate.time.sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "repeated 2 times"):
                request_reply(
                    run,
                    prompt="prompt",
                    parts=["payload"],
                    schema=GroupTranslateReply,
                    item="1.1_group_1",
                    step="exercise_translate",
                    context={},
                )
            self.assertEqual(call.call_count, 2)

            call.reset_mock()
            with self.assertRaisesRegex(RuntimeError, "retry circuit open"):
                request_reply(
                    run,
                    prompt="prompt",
                    parts=["payload"],
                    schema=GroupTranslateReply,
                    item="1.1_group_1",
                    step="exercise_translate",
                    context={},
                )
            call.assert_not_called()

    def test_retry_budget_is_shared_by_repeated_invocations(self) -> None:
        run = run_state()
        errors = iter(
            [
                (None, "ValueError: error one"),
                (None, "ValueError: error two"),
                (None, "ValueError: error three"),
                (None, "ValueError: error four"),
            ]
        )
        with (
            patch(
                "translate.logged_json_call",
                side_effect=lambda *_args, **_kwargs: next(errors),
            ) as call,
            patch("translate.time.sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                request_reply(
                    run,
                    prompt="prompt",
                    parts=["payload"],
                    schema=GroupTranslateReply,
                    item="1.1_group_2",
                    step="exercise_translate",
                    context={},
                )
            self.assertEqual(call.call_count, 4)
            with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                request_reply(
                    run,
                    prompt="prompt",
                    parts=["payload"],
                    schema=GroupTranslateReply,
                    item="1.1_group_2",
                    step="exercise_translate",
                    context={},
                )
            self.assertEqual(call.call_count, 4)

    def test_review_correction_has_its_own_budget(self) -> None:
        run = run_state()
        reply = GroupTranslateReply(status="abort", abort_reason="x", group=None)
        outcomes = iter(
            [(None, f"ValueError: error {index}") for index in range(4)]
            + [(reply, None)]
        )
        with (
            patch(
                "translate.logged_json_call",
                side_effect=lambda *_args, **_kwargs: next(outcomes),
            ),
            patch("translate.time.sleep"),
        ):
            ask = dict(
                prompt="prompt",
                parts=["payload"],
                schema=GroupTranslateReply,
                item="1.1_group_3",
                step="exercise_translate",
                context={},
            )
            with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                request_reply(run, **ask)
            self.assertIs(request_reply(run, **ask, attempt_scope="review1"), reply)

    def test_service_errors_do_not_use_attempts(self) -> None:
        run = run_state()
        reply = GroupTranslateReply(status="abort", abort_reason="x", group=None)
        outcomes = iter(
            [
                (None, "ServerError: 504 DEADLINE_EXCEEDED. {'error': {'code': 504}}"),
                (None, "ServerError: 503 UNAVAILABLE. {'error': {'code': 503}}"),
                (None, "ReadTimeout: The read operation timed out"),
                (None, "ValueError: error one"),
                (None, "ValueError: error two"),
                (None, "ValueError: error three"),
                (reply, None),
            ]
        )
        with (
            patch(
                "translate.logged_json_call",
                side_effect=lambda *_args, **_kwargs: next(outcomes),
            ) as call,
            patch("translate.time.sleep"),
        ):
            got = request_reply(
                run,
                prompt="prompt",
                parts=["payload"],
                schema=GroupTranslateReply,
                item="1.1_group_4",
                step="exercise_translate",
                context={},
            )
        self.assertIs(got, reply)
        self.assertEqual(call.call_count, 7)
        self.assertEqual(run.attempt_counts[("exercise_translate", "1.1_group_4")], 4)

    def test_untranslatable_english_is_terminal_without_a_model_call(self) -> None:
        group = {
            "id": "group_95",
            "stem": "",
            "exercises": [
                {
                    "id": "exercise_126",
                    "text": "Find the water depth at $t = 3$.",
                    "options": [
                        (
                            r"$4\text{ m}$ preference indicator removed from options "
                            r"purely mathematical value follows: $4\text{ m}$"
                        ),
                        "${d}$",
                        "${e}$",
                        "${f}$",
                    ],
                    "answer": "C",
                }
            ],
        }
        run = run_state()
        run.ar_meta = {"notation": "latin", "language_code": "ar"}
        run.book_map = {"entries": []}
        run.thinking_by_step = {"exercise": "medium"}
        run.progress = SimpleNamespace(asking=lambda *_a, **_k: None, say=lambda *_a, **_k: None)
        with patch("translate.request_reply") as call:
            _merged, errors, _drops = translate_objective(
                run,
                {"number": "4.5"},
                {"objectives": []},
                {"objectives": []},
                [],
                "",
                [group],
            )
        call.assert_not_called()
        self.assertTrue(any("untranslatable English source" in message for message in errors.values()))
        self.assertTrue(
            terminal_section_error(
                "4.5: 96/97 group(s) on disk; failed group_95; "
                "untranslatable English source: exercise_126"
            )
        )
        self.assertFalse(
            terminal_section_error("4.5: group_33 failed, ValueError: unbalanced")
        )
        self.assertTrue(
            source_corruption_abort(
                "The English source in exercise_126 contains corrupted options "
                "with leaked prompt instructions and unresolved template variables "
                "(${d}, ${e}, ${f})"
            )
        )
        self.assertFalse(source_corruption_abort("unit collision on token in"))

    def test_transient_error_wording(self) -> None:
        for error in (
            "ServerError: 504 DEADLINE_EXCEEDED. {}",
            "ServerError: 500 INTERNAL. {}",
            "ReadTimeout: The read operation timed out",
            "ConnectError: [Errno 54] Connection reset by peer",
        ):
            self.assertTrue(transient_error(error), error)
        for error in (
            None,
            "ValueError: exercise_1.text: UNAVAILABLE stays English",
            "ClientError: 400 INVALID_ARGUMENT. {}",
            "ClientError: 429 RESOURCE_EXHAUSTED. {}",
        ):
            self.assertFalse(transient_error(error), error)


class ProgressCountTests(unittest.TestCase):
    def test_retried_section_is_not_counted_twice_or_left_in_flight(self) -> None:
        progress = Progress(2)
        progress.asking("1.1")
        progress.asking("1.2")
        self.assertNotIn("not started", progress._where())
        progress.done("1.1")
        progress.done("1.2")
        progress.asking("1.2")
        where = progress._where()
        self.assertNotIn("-", where.split(",")[0])
        self.assertNotIn("not started", where)
        self.assertIn("2 of 2 done", where)
        progress.done_again("1.2")
        self.assertNotIn("still with the model", progress._where())
        self.assertIn("2 of 2 done", progress._where())


class SamePictureTests(unittest.TestCase):
    def test_spacing_and_ink_box_do_not_make_a_new_picture(self) -> None:
        source = (
            r"\begin{tikzpicture}[x=1.2cm] \draw (0, 0) -- (5, 0); "
            r"\foreach \x in {1, ..., 5} { \node [below] at (\x, 0) {$\x$}; } "
            r"\end{tikzpicture}"
        )
        boxed = source.replace(
            r"\end{tikzpicture}",
            "\n% bounding box brought down to the ink above and below\n"
            r"\coordinate (inksouthwest) at (current bounding box.south west);" "\n"
            r"\coordinate (inknortheast) at (current bounding box.north east);" "\n"
            r"\pgfresetboundingbox" "\n"
            r"\useasboundingbox ([shift={(0pt,3.978pt)}]inksouthwest) rectangle "
            r"([shift={(0pt,0.002pt)}]inknortheast);" "\n"
            r"\end{tikzpicture}",
        )
        self.assertTrue(same_picture(source, boxed))
        self.assertTrue(same_picture(source, source.replace("; ", ";\n  ")))
        self.assertFalse(same_picture(source, source.replace("{$\\x$}", "{$\\x$ سم}")))
        self.assertFalse(same_picture("", source))


class RedesignContractTests(unittest.TestCase):
    def setUp(self) -> None:
        from sources import book_metadata
        from src.prompting import assemble
        from translate import bind_profile

        self.meta = book_metadata("mathematics_ar_eg07_t1")
        self.prompt = bind_profile(assemble("translate", self.meta), self.meta)
        self.book_map = {
            "entries": [
                {
                    "kind": "unit",
                    "source_plain": "cm",
                    "source_math": "cm",
                    "preferred": "سم",
                    "also": [],
                    "abbreviation_only": True,
                }
            ],
            "semantic_fingerprint": "abc",
        }

    def make_run(self, **extra):
        from translate import DEFAULT_THINKING_BY_STEP

        base = dict(
            prompt=self.prompt,
            ar_meta=self.meta,
            book_map=self.book_map,
            thinking_by_step=dict(DEFAULT_THINKING_BY_STEP),
            thinking="medium",
            escalation_thinking="high",
            update=False,
            retranslate_on_prompt_change=False,
            rereview=False,
            use_cache=True,
            caches=None,
            en_id="mathematics_en_eg07_t1",
            ar_id="mathematics_ar_eg07_t1",
        )
        base.update(extra)
        return SimpleNamespace(**base)

    def test_terminology_call_has_no_glossary_block(self) -> None:
        from translate import system_prompt

        text = system_prompt(self.make_run(), "terminology.md").text
        self.assertNotIn("## Book glossary", text)

    def test_full_glossary_in_system_instruction(self) -> None:
        from translate import system_prompt
        from src.book_map import glossary_block, glossary_entries

        run = self.make_run()
        block = glossary_block(run.book_map)
        entries = glossary_entries(run.book_map)
        for task in ("specs.md", "labels.md", "exercises.md", "review.md", "tikz.md"):
            text = system_prompt(run, task).text
            self.assertTrue(text.rstrip().endswith(block.strip()) or block.strip() in text)
            for entry in entries:
                self.assertIn(entry["english"], text)

    def test_cache_key_changes_with_glossary_fp(self) -> None:
        from translate import PromptCaches

        created = []
        deleted: list[str] = []

        class Caches:
            def create(self, **kwargs):
                created.append(kwargs["config"].system_instruction)
                return SimpleNamespace(name=f"cache-{len(created)}")

            def delete(self, name: str) -> None:
                deleted.append(name)

        with tempfile.TemporaryDirectory() as folder, patch("translate.OUTPUT_DIR", Path(folder)):
            caches = PromptCaches(SimpleNamespace(caches=Caches()), "gemini-3.8-flash", "book", True)
            caches.name_for("exercise", "prompt A سم")
            caches.name_for("exercise", "prompt B م")
            caches.name_for("exercise", "prompt A سم")
            self.assertEqual(len(created), 2)
            caches.close()
            self.assertEqual(deleted, ["cache-1", "cache-2"])

    def test_parallel_cache_create_is_one_name_and_close_deletes_it(self) -> None:
        import threading
        from translate import PromptCaches

        created: list[str] = []
        deleted: list[str] = []

        class Caches:
            def create(self, **kwargs):
                created.append(kwargs["config"].system_instruction)
                return SimpleNamespace(name=f"cached/{len(created)}")

            def delete(self, name: str) -> None:
                deleted.append(name)

        with tempfile.TemporaryDirectory() as folder, patch("translate.OUTPUT_DIR", Path(folder)):
            caches = PromptCaches(SimpleNamespace(caches=Caches()), "gemini-3.8-flash", "book", True)
            barrier = threading.Barrier(6)

            def ask() -> None:
                barrier.wait()
                caches.name_for("exercise", "same prompt")

            threads = [threading.Thread(target=ask) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(created, ["same prompt"])
            caches.close()
            self.assertEqual(deleted, ["cached/1"])

    def test_cache_storage_prices_held_time_or_the_ttl(self) -> None:
        from unittest.mock import patch
        from translate import PromptCaches

        class Usage:
            total_token_count = 1_000_000

        class Caches:
            def create(self, **kwargs):
                return SimpleNamespace(name="cached/1", usage_metadata=Usage())

            def delete(self, name: str) -> None:
                return None

        with tempfile.TemporaryDirectory() as folder, patch("translate.OUTPUT_DIR", Path(folder)):
            caches = PromptCaches(SimpleNamespace(caches=Caches()), "gemini-3.8-flash", "book", True)
            with patch("translate.time.time", side_effect=[1_000.0, 4_600.0]):
                caches.name_for("exercise", "prompt")
                caches.close()
            row = caches.storage_rows()[0]
            self.assertEqual(row["tokens"], 1_000_000)
            self.assertEqual(row["seconds"], 3_600.0)
            self.assertTrue(row["deleted"])
            self.assertAlmostEqual(row["usd"], 0.5)

            class Failing:
                def create(self, **kwargs):
                    return SimpleNamespace(name="cached/2", usage_metadata=Usage())

                def delete(self, name: str) -> None:
                    raise RuntimeError("still billing")

            missed = PromptCaches(SimpleNamespace(caches=Failing()), "gemini-3.8-flash", "book", True)
            missed.name_for("review", "prompt")
            missed.close()
            kept = missed.storage_rows()[0]
            self.assertFalse(kept["deleted"])
            self.assertEqual(kept["seconds"], 3_600.0)
            self.assertAlmostEqual(kept["usd"], 0.5)

    def test_update_section_forces_redo(self) -> None:
        from translate import _select_group, group_keys

        run = self.make_run(update=True)
        group = self._group()
        stored = {**group_keys(run, group, {}, {}), "status": "reviewed", "review_fp": "x"}
        self.assertEqual(_select_group(run, group, stored, self._arabic(), {}, {}), "translate")

    def test_retranslate_on_prompt_change_scope(self) -> None:
        from translate import _select_group, group_keys

        group = self._group()
        arabic = self._arabic()
        run = self.make_run()
        stored = {**group_keys(run, group, {}, {}), "status": "reviewed", "review_fp": "same", "prompt_fp": "old"}
        self.assertEqual(_select_group(run, group, stored, arabic, {}, {}), "keep")
        run.retranslate_on_prompt_change = True
        self.assertEqual(_select_group(run, group, stored, arabic, {}, {}), "translate")

    def test_rereview_reviews_all_groups(self) -> None:
        from translate import _select_group, group_keys, review_key

        group = self._group()
        arabic = self._arabic()
        run = self.make_run()
        stored = {**group_keys(run, group, {}, {}), "status": "reviewed", "review_fp": "old"}
        self.assertEqual(_select_group(run, group, stored, arabic, {}, {}), "keep")
        run.rereview = True
        self.assertEqual(_select_group(run, group, stored, arabic, {}, {}), "review")
        self.assertTrue(review_key(run))

    def test_glossary_change_revalidates_and_rereviews(self) -> None:
        from translate import _select_group, group_keys

        group = self._group()
        arabic = self._arabic()
        run = self.make_run()
        stored = {**group_keys(run, group, {}, {}), "status": "reviewed", "review_fp": "old"}
        stored["glossary_fp"] = "different"
        self.assertEqual(_select_group(run, group, stored, arabic, {}, {}), "revalidate")

    def test_glossary_change_invalid_row_retranslated(self) -> None:
        from translate import _select_group, group_keys

        group = self._group()
        broken = self._arabic()
        broken["exercises"] = []
        run = self.make_run()
        stored = {**group_keys(run, group, {}, {}), "glossary_fp": "different", "status": "reviewed"}
        self.assertEqual(_select_group(run, group, stored, broken, {}, {}), "translate")

    def test_terminology_prompt_edit_reextracts(self) -> None:
        import tempfile
        from translate import ensure_terminology, terminology_key

        run = self.make_run(pad_width=2)
        section = {"number": "1.1", "stem": "01_01", "spec": {"glossary": [], "notations": []}}
        document = {"exercises": {"groups": []}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "section.pdf"
            pdf.write_bytes(b"%PDF-1.1\n")
            accepted = root / run.en_id / "09_section_questions_review"
            accepted.mkdir(parents=True)
            (accepted / "01_01.json").write_text(json.dumps(document), encoding="utf-8")
            state = root / run.ar_id / "00_translation_state" / "map_fragments"
            state.mkdir(parents=True)
            sources = [{"source_id": "source_1", "filename": "section.pdf", "pages": 1, "sha256": "abc"}]
            with (
                patch("translate.OUTPUT_DIR", root),
                patch("translate.section_source_pdfs", return_value=[pdf]),
                patch("translate.pdf_source_manifest", return_value=sources),
                patch("translate.ar_identity", return_value=("Title", "module")),
                patch("translate.extract_terminology", return_value={"observations": []}) as extract,
                patch("translate._validate_stored_fragment", return_value={"observations": []}),
            ):
                key = terminology_key(run, section, [], sources, [])
                (state / "01_01.json").write_text(
                    json.dumps({"input_fingerprint": key, "observations": [], "section": "1.1"}),
                    encoding="utf-8",
                )
                ensure_terminology(run, section, {})
                self.assertEqual(extract.call_count, 0)
                (state / "01_01.json").write_text(
                    json.dumps({"input_fingerprint": "stale", "observations": [], "section": "1.1"}),
                    encoding="utf-8",
                )
                ensure_terminology(run, section, {})
                self.assertGreaterEqual(extract.call_count, 1)

    def test_adopt_section_figures_keeps_a_stored_settled_figure(self) -> None:
        import json as json_mod
        import tempfile
        from unittest.mock import patch

        import translate
        from src.book_map import glossary_fp

        stored_path = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "output"
            / "mathematics_ar_eg07_t1"
            / "11_book_figures"
            / "154bb31c523c.json"
        )
        if not stored_path.is_file():
            self.skipTest("settled figure fixture is generated output and is not on disk")
        stored = json_mod.loads(stored_path.read_text(encoding="utf-8"))
        description = stored["description"]
        tikz = stored["tikz"]
        figure = {"description": description, "figure_type": stored["figure_type"]}
        document = {
            "exercises": {
                "groups": [
                    {
                        "id": "group_64",
                        "exercises": [
                            {"id": "exercise_104", "figures": [figure]}
                        ],
                    }
                ]
            }
        }
        run = self.make_run()
        ar_key = translate.translated_figure_key(
            "4bcbe9aab5c2",
            tikz,
            description,
            run.ar_meta,
            glossary_fp(run.book_map),
        )
        drawings = {description: {"tikz": tikz, "key": "4bcbe9aab5c2"}}
        section = {"number": "3.2", "stem": "03_02"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            out = root / run.ar_id / "11_book_figures" / f"{ar_key}.json"
            out.parent.mkdir(parents=True)
            filed = {
                "tikz": tikz,
                "settled": True,
                "provenance": {
                    "english_key": "4bcbe9aab5c2",
                    "section": "3.2",
                    "group": "group_64",
                    "exercise": "exercise_104",
                },
            }
            out.write_text(json_mod.dumps(filed), encoding="utf-8")
            with (
                patch.object(translate, "OUTPUT_DIR", root),
                patch.object(translate, "write_figure_data"),
            ):
                line = translate.adopt_section_figures(
                    run, section, document, document, drawings
                )
        self.assertIn("0 translated", line)
        self.assertIn("1 kept", line)

    def test_adopt_section_figures_unclashes_new_overlaps_instead_of_settling(self) -> None:
        import json as json_mod
        import tempfile
        from unittest.mock import MagicMock, patch

        import translate

        description = "isosceles triangle with forces"
        figure = {"description": description, "figure_type": "plane geometry"}
        document = {
            "exercises": {
                "groups": [
                    {
                        "id": "group_1",
                        "exercises": [
                            {"id": "exercise_1", "figures": [figure]}
                        ],
                    }
                ]
            }
        }
        en_tikz = r"\begin{tikzpicture}\node {$37.5\text{ N}$};\end{tikzpicture}"
        ar_tikz = r"\begin{tikzpicture}\node {$37.5\text{ نيوتن}$};\end{tikzpicture}"
        drawings = {description: {"tikz": en_tikz, "key": "englishkey12", "overlaps": 0}}
        run = self.make_run(
            client=object(),
            logger=None,
            model="gemini-3.8-flash",
            thinking="medium",
            progress=SimpleNamespace(
                asking=lambda *args, **kwargs: None,
                say=lambda *args, **kwargs: None,
            ),
        )
        overlap = RuntimeError("figkey: translated TikZ has 1 new label overlap(s)")
        adopted = []

        def fake_adopt(*args, **kwargs):
            adopted.append(kwargs.get("allowed_overlaps"))
            if kwargs.get("allowed_overlaps", 0) >= 32:
                return {"tikz": ar_tikz, "overlaps": 1}
            raise overlap

        unclash = MagicMock(return_value={"tikz": ar_tikz, "overlaps": 0, "settled": True})
        section = {"number": "1.3.2", "stem": "01_03_02"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (
                patch.object(translate, "OUTPUT_DIR", root),
                patch.object(translate, "write_figure_data"),
                patch.object(translate, "translate_tikz", return_value=ar_tikz),
                patch.object(translate, "adopt_tikz", side_effect=fake_adopt),
                patch.object(translate, "unclash_adopted", unclash),
                patch.object(translate, "bump_lengthmark_offsets", side_effect=lambda tikz: tikz),
            ):
                line = translate.adopt_section_figures(
                    run, section, document, document, drawings
                )
        unclash.assert_called_once()
        self.assertNotIn(32, adopted)
        self.assertIn("1 translated", line)

    def test_adopt_section_figures_retries_settled_extra_overlaps(self) -> None:
        import json as json_mod
        import tempfile
        from unittest.mock import MagicMock, patch

        import translate
        from src.book_map import glossary_fp

        description = "isosceles triangle with forces"
        figure = {"description": description, "figure_type": "plane geometry"}
        document = {
            "exercises": {
                "groups": [
                    {
                        "id": "group_1",
                        "exercises": [
                            {"id": "exercise_1", "figures": [figure]}
                        ],
                    }
                ]
            }
        }
        en_tikz = r"\begin{tikzpicture}\node {$N$};\end{tikzpicture}"
        ar_tikz = r"\begin{tikzpicture}\node {$نيوتن$};\end{tikzpicture}"
        run = self.make_run(
            client=object(),
            logger=None,
            model="gemini-3.8-flash",
            thinking="medium",
            progress=SimpleNamespace(
                asking=lambda *args, **kwargs: None,
                say=lambda *args, **kwargs: None,
            ),
        )
        ar_key = translate.translated_figure_key(
            "englishkey12",
            en_tikz,
            description,
            run.ar_meta,
            glossary_fp(run.book_map),
        )
        drawings = {description: {"tikz": en_tikz, "key": "englishkey12", "overlaps": 0}}
        unclash = MagicMock(return_value={"tikz": ar_tikz, "overlaps": 0, "settled": True})
        overlap = RuntimeError("figkey: translated TikZ has 1 new label overlap(s)")
        section = {"number": "1.3.2", "stem": "01_03_02"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            out = root / run.ar_id / "11_book_figures" / f"{ar_key}.json"
            out.parent.mkdir(parents=True)
            out.write_text(
                json_mod.dumps(
                    {
                        "tikz": ar_tikz,
                        "settled": True,
                        "overlaps": 1,
                        "provenance": {"english_key": "englishkey12"},
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(translate, "OUTPUT_DIR", root),
                patch.object(translate, "write_figure_data"),
                patch.object(translate, "tikz_problems", return_value=[]),
                patch.object(translate, "adopt_tikz", side_effect=overlap),
                patch.object(translate, "unclash_adopted", unclash),
                patch.object(translate, "bump_lengthmark_offsets", side_effect=lambda tikz: tikz),
            ):
                line = translate.adopt_section_figures(
                    run, section, document, document, drawings
                )
        unclash.assert_called_once()
        self.assertIn("1 copied", line)

    def test_adopt_section_figures_keeps_already_relabelled_overlaps(self) -> None:
        import json as json_mod
        import tempfile
        from unittest.mock import MagicMock, patch

        import translate
        from src.book_map import glossary_fp

        description = "isosceles triangle with forces"
        figure = {"description": description, "figure_type": "plane geometry"}
        document = {
            "exercises": {
                "groups": [
                    {
                        "id": "group_1",
                        "exercises": [
                            {"id": "exercise_1", "figures": [figure]}
                        ],
                    }
                ]
            }
        }
        en_tikz = r"\begin{tikzpicture}\node {$N$};\end{tikzpicture}"
        ar_tikz = r"\begin{tikzpicture}\node {$نيوتن$};\end{tikzpicture}"
        run = self.make_run()
        ar_key = translate.translated_figure_key(
            "englishkey12",
            en_tikz,
            description,
            run.ar_meta,
            glossary_fp(run.book_map),
        )
        drawings = {description: {"tikz": en_tikz, "key": "englishkey12", "overlaps": 0}}
        unclash = MagicMock()
        section = {"number": "1.3.2", "stem": "01_03_02"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            out = root / run.ar_id / "11_book_figures" / f"{ar_key}.json"
            out.parent.mkdir(parents=True)
            out.write_text(
                json_mod.dumps(
                    {
                        "tikz": ar_tikz,
                        "settled": True,
                        "overlaps": 1,
                        "provenance": {
                            "english_key": "englishkey12",
                            "relabelled": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(translate, "OUTPUT_DIR", root),
                patch.object(translate, "write_figure_data"),
                patch.object(translate, "tikz_problems", return_value=[]),
                patch.object(translate, "unclash_adopted", unclash),
            ):
                line = translate.adopt_section_figures(
                    run, section, document, document, drawings
                )
        unclash.assert_not_called()
        self.assertIn("1 kept", line)

    def test_accepted_run_names_the_papers_command(self) -> None:
        from translate import papers_not_rebuilt_line

        self.assertEqual(
            papers_not_rebuilt_line("mathematics_en_eg07_t1"),
            "papers not rebuilt; run: "
            "python3 -B scripts/pipeline.py mathematics_en_eg07_t1 --translate "
            "--from ar_frontmatter --to ar_groups",
        )

    def _group(self) -> dict:
        return {
            "id": "group_1",
            "objective": "objective_1",
            "stem": "مرحبا",
            "figures": [],
            "poems": [],
            "exercises": [
                {
                    "id": "exercise_1",
                    "text": "أوجد $2$.",
                    "options": [],
                    "answer": "$2$",
                    "figures": [],
                }
            ],
        }

    def _arabic(self) -> dict:
        return self._group()


def _hours_reply(text: str, answer: str):
    from translate import ObjectiveTranslateReply, TranslatedExercise, TranslatedGroupV2

    return ObjectiveTranslateReply(
        status="ok",
        abort_reason="",
        groups=[
            TranslatedGroupV2(
                id="group_109",
                stem="",
                figures=[],
                poems=[],
                exercises=[
                    TranslatedExercise(
                        id="exercise_155",
                        text=text,
                        options=[],
                        answer=answer,
                        figures=[],
                        poems=[],
                    )
                ],
                dropped_numbers=[],
            )
        ],
        aborted=[],
    )


class ReviewConvergenceTests(unittest.TestCase):
    def test_one_inflection_for_every_hour_is_rejected_before_review(self) -> None:
        from translate import translate_objective_batch

        english = {
            "id": "group_109",
            "objective": "objective_1",
            "stem": "",
            "figures": [],
            "poems": [],
            "exercises": [
                {
                    "id": "exercise_155",
                    "text": (
                        "Recorded $2$ hours and $3$ hours and $3$ hours "
                        "and $4$ hours and $13$ hours."
                    ),
                    "options": [],
                    "answer": "$3$ hours.",
                    "figures": [],
                    "poems": [],
                }
            ],
        }
        plural = _hours_reply(
            "سجلت ⟦M1⟧ ساعات و⟦M2⟧ ساعات و⟦M3⟧ ساعات و⟦M4⟧ ساعات و⟦M5⟧ ساعات.",
            "⟦M1⟧ ساعات",
        )
        singular = _hours_reply(
            "سجلت ⟦M1⟧ ساعة و⟦M2⟧ ساعة و⟦M3⟧ ساعة و⟦M4⟧ ساعة و⟦M5⟧ ساعة.",
            "⟦M1⟧ ساعة",
        )
        mixed = _hours_reply(
            "سجلت ⟦M1⟧ ساعة و⟦M2⟧ ساعات و⟦M3⟧ ساعات و⟦M4⟧ ساعات و⟦M5⟧ ساعةً.",
            "⟦M1⟧ ساعات",
        )
        script = iter((plural, singular, mixed))
        run = run_state()
        run.ar_id = "mathematics_ar_eg07_t1"
        run.ar_meta = {"notation": "latin", "language_code": "ar", "numerals": "western"}
        run.book_map = {"entries": []}
        run.thinking_by_step = {"exercise": "medium"}
        run.thinking = "medium"
        run.escalation_thinking = "high"
        run.progress = SimpleNamespace(asking=lambda *_a, **_k: None, say=lambda *_a, **_k: None)
        with (
            patch("translate.request_reply", side_effect=lambda *_a, **_k: next(script)) as call,
            patch("translate.system_prompt", return_value=SimpleNamespace(text="system", parts=())),
        ):
            merged, errors, _drops, _paths = translate_objective_batch(
                run,
                {"number": "3.2"},
                {"objectives": []},
                {"objectives": []},
                [],
                "objective_1",
                [english],
                escalate=False,
            )
        self.assertEqual(call.call_count, 1)
        self.assertNotIn("group_109", errors)
        text = merged["group_109"]["exercises"][0]["text"]
        self.assertIn("$13$ ساعات", text)
        self.assertNotIn("$13$ ساعةً", text)

    def test_correction_request_carries_the_value_table_and_current_arabic(self) -> None:
        from translate import ObjectiveTranslateReply, translate_objective

        english = {
            "id": "group_109",
            "stem": "",
            "figures": [],
            "poems": [],
            "exercises": [
                {
                    "id": "exercise_155",
                    "text": "The outlier is $13$ hours.",
                    "options": [],
                    "answer": "$13$",
                    "figures": [],
                    "poems": [],
                }
            ],
        }
        current = {
            "id": "group_109",
            "stem": "",
            "exercises": [
                {
                    "id": "exercise_155",
                    "text": "القيمة المتطرفة هي $13$ ساعات.",
                    "options": [],
                    "answer": "$13$",
                    "figures": [],
                }
            ],
        }
        seen: dict[str, str] = {}

        def capture(*_args, **kwargs):
            seen["parts"] = "\n".join(kwargs["parts"])
            return ObjectiveTranslateReply(
                status="abort", abort_reason="stop", groups=[], aborted=[]
            )

        run = run_state()
        run.ar_id = "mathematics_ar_eg07_t1"
        run.ar_meta = {"notation": "latin", "language_code": "ar", "numerals": "western"}
        run.book_map = {"entries": []}
        run.thinking_by_step = {"exercise": "medium"}
        run.thinking = "medium"
        run.prompt = SimpleNamespace(text="", parts=())
        run.progress = SimpleNamespace(asking=lambda *_a, **_k: None, say=lambda *_a, **_k: None)
        with (
            patch("translate.request_reply", side_effect=capture),
            patch("translate.system_prompt", return_value=SimpleNamespace(text="system", parts=())),
        ):
            translate_objective(
                run,
                {"number": "3.2"},
                {"objectives": []},
                {"objectives": []},
                [],
                "objective_1",
                [english],
                corrections={"group_109": "exercise_155.text: $13$ ساعات should be singular"},
                current_arabic={"group_109": current},
            )
        self.assertIn("⟦M1⟧ = $13$", seen["parts"])
        self.assertIn("القيمة المتطرفة هي $13$ ساعات.", seen["parts"])
        self.assertIn("word for word", seen["parts"])

    def test_last_review_round_does_not_correct(self) -> None:
        import json
        import tempfile

        from translate import TranslationIssueV2, group_keys, translate_exercises

        case = RedesignContractTests()
        case.setUp()
        run = case.make_run()
        run.progress = SimpleNamespace(say=lambda *_a, **_k: None, asking=lambda *_a, **_k: None)
        group = case._group()
        arabic = case._arabic()
        stored = {
            **group_keys(run, group, {}, {}),
            "status": "validated",
            "review_fp": "",
            "path": "batch",
            "first_path": "batch",
            "dropped_numbers": [],
        }
        section = {"number": "3.2", "stem": "03_02", "spec": {}}
        document = {
            "exercises": {
                "objectives": [{"id": "objective_1", "exercises": 1, "word_problems": 0}],
                "groups": [group],
            }
        }
        issue = TranslationIssueV2(
            group="group_1",
            exercise="exercise_1",
            field="text",
            category="unit",
            reason="$2$ hours",
        )
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            out_08 = root / "08.json"
            out_09 = root / "09.json"
            state_path = root / "state.json"
            out_08.write_text(
                json.dumps({"exercises": {"groups": [arabic]}}, ensure_ascii=False),
                encoding="utf-8",
            )
            state_path.write_text(
                json.dumps(
                    {
                        "version": 4,
                        "book_id": run.ar_id,
                        "section": "3.2",
                        "stem": "03_02",
                        "groups": {"group_1": stored},
                        "failed_groups": {},
                        "review": {"status": "pending", "rounds": []},
                        "exercises_accepted": False,
                        "accepted": False,
                        "failure": "",
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch("translate.review_translation", return_value=[issue]) as review,
                patch("translate.translate_objective_batch") as correct,
                patch("translate.validate_document"),
            ):
                correct.return_value = (
                    {"group_1": arabic},
                    {},
                    {},
                    {"group_1": "correction"},
                )
                translate_exercises(
                    run,
                    section,
                    "Title",
                    "module",
                    {},
                    {},
                    [],
                    document,
                    out_09,
                    out_08,
                    state_path,
                )
                self.assertEqual(review.call_count, 3)
                self.assertEqual(correct.call_count, 2)
                saved = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["groups"]["group_1"]["first_path"], "batch")
                self.assertEqual(saved["review"]["status"], "passed_with_issues")
                self.assertTrue(saved["exercises_accepted"])
                self.assertEqual(saved["review"]["open_issues"][0]["category"], "unit")

    def test_arabic_notation_rejects_a_latin_function_command(self) -> None:
        from translate import ObjectiveTranslateReply, TranslatedExercise, TranslatedGroupV2, translate_objective

        english = {
            "id": "group_2",
            "stem": "",
            "figures": [],
            "poems": [],
            "exercises": [
                {
                    "id": "exercise_1",
                    "text": r"Find $\sin x$.",
                    "options": [],
                    "answer": r"$\sin x$",
                    "figures": [],
                    "poems": [],
                }
            ],
        }

        def reply(text: str, answer: str) -> ObjectiveTranslateReply:
            return ObjectiveTranslateReply(
                status="ok",
                abort_reason="",
                groups=[
                    TranslatedGroupV2(
                        id="group_2",
                        stem="",
                        figures=[],
                        poems=[],
                        exercises=[
                            TranslatedExercise(
                                id="exercise_1",
                                text=text,
                                options=[],
                                answer=answer,
                                figures=[],
                                poems=[],
                            )
                        ],
                    )
                ],
                aborted=[],
            )

        run = run_state()
        run.ar_id = "mathematics_ar_eg07_t1"
        run.ar_meta = {"notation": "arabic", "language_code": "ar", "numerals": "western"}
        run.book_map = {"entries": []}
        run.thinking_by_step = {"exercise": "medium"}
        run.thinking = "medium"
        run.progress = SimpleNamespace(asking=lambda *_a, **_k: None, say=lambda *_a, **_k: None)
        bad = reply(r"أوجد ⟦E1⟧$\sin x$⟦/E1⟧.", r"⟦E1⟧$\sin x$⟦/E1⟧")
        good = reply(r"أوجد ⟦E1⟧$\text{جا} x$⟦/E1⟧.", r"⟦E1⟧$\text{جا} x$⟦/E1⟧")
        with (
            patch("translate.request_reply", return_value=bad),
            patch("translate.system_prompt", return_value=SimpleNamespace(text="system", parts=())),
        ):
            _merged, errors, _drops = translate_objective(
                run, {"number": "1.1"}, {}, {}, [], "", [english]
            )
        self.assertIn("sin", errors["group_2"])
        with (
            patch("translate.request_reply", return_value=good),
            patch("translate.system_prompt", return_value=SimpleNamespace(text="system", parts=())),
        ):
            merged, errors, _drops = translate_objective(
                run, {"number": "1.1"}, {}, {}, [], "", [english]
            )
        self.assertNotIn("group_2", errors)
        self.assertIn("جا", merged["group_2"]["exercises"][0]["text"])

    def test_failed_cache_delete_is_priced_and_not_an_error(self) -> None:
        import json
        import tempfile

        from src.api_logger import ApiLogger
        from translate import PromptCaches
        from translation_metrics import summarize

        class Usage:
            total_token_count = 1_000_000

        class Failing:
            def create(self, **kwargs):
                return SimpleNamespace(name="cached/2", usage_metadata=Usage())

            def delete(self, *, name: str) -> None:
                raise RuntimeError("still billing")

        with (
            tempfile.TemporaryDirectory() as folder,
            patch("src.api_logger.ApiLogger._compute_ntp_offset", return_value=0.0),
            patch("translate.OUTPUT_DIR", Path(folder)),
        ):
            root = Path(folder)
            logger = ApiLogger(root)
            caches = PromptCaches(
                SimpleNamespace(caches=Failing()),
                "gemini-3.8-flash",
                "book",
                True,
                logger=logger,
            )
            caches.name_for("review", "prompt")
            caches.close()
            summary = summarize(root)
            logged = json.loads(next((root / "00_api_call_logs").glob("*/call.json")).read_text())
        row = caches.storage_rows()[0]
        self.assertFalse(row["deleted"])
        self.assertAlmostEqual(row["usd"], 0.5)
        step = summary["steps"]["cache_storage"]
        self.assertEqual(step["errors"], 0)
        self.assertEqual(step.get("not_deleted"), 1)
        self.assertEqual(step.get("deleted", 0), 0)
        self.assertAlmostEqual(step["usd"], 0.5)
        self.assertEqual(logged["status"], "success")
        self.assertFalse(logged["cache_deleted"])
        self.assertIn("still billing", logged["error_message"])

    def test_shared_unit_rule_is_in_the_exercise_and_review_prompts(self) -> None:
        from translate import system_prompt

        case = RedesignContractTests()
        case.setUp()
        run = case.make_run()
        for task in ("exercises.md", "review.md"):
            prompt = system_prompt(run, task)
            self.assertIn("n mod 100", prompt.text)
            self.assertIn("11–99", prompt.text)
            self.assertTrue(any(part.endswith("unit_agreement.md") for part in prompt.parts))


class AcceptedSectionSkipTests(unittest.TestCase):
    def setUp(self) -> None:
        case = RedesignContractTests()
        case.setUp()
        self.make_run = case.make_run
        self._group = case._group
        self._arabic = case._arabic

    def _document(self, group: dict) -> dict:
        return {"exercises": {"groups": [group]}}

    def _write_accepted_tree(self, root: Path, run, en_group: dict, ar_group: dict) -> dict:
        from translate import TRANSLATION_MANIFEST_VERSION, write_json

        en_root = root / run.en_id
        ar_root = root / run.ar_id
        for folder in (
            "03_section_specs",
            "05_section_labels",
            "06_section_budgets",
            "08_section_new_questions",
            "09_section_questions_review",
            "00_translation_state",
        ):
            (ar_root / folder).mkdir(parents=True, exist_ok=True)
        (en_root / "05_section_labels").mkdir(parents=True, exist_ok=True)
        (en_root / "06_section_budgets").mkdir(parents=True, exist_ok=True)
        (en_root / "09_section_questions_review").mkdir(parents=True, exist_ok=True)
        spec = {"overview": "درس", "objectives": []}
        write_json(ar_root / "03_section_specs" / "01_01.json", {"spec": spec})
        write_json(
            ar_root / "05_section_labels" / "01_01.json",
            {"labels": {"objectives": [{"id": "objective_1", "label": "هدف"}]}},
        )
        write_json(ar_root / "06_section_budgets" / "01_01.json", {"budget": {}})
        write_json(
            en_root / "05_section_labels" / "01_01.json",
            {"labels": {"objectives": [{"id": "objective_1", "label": "Goal"}]}},
        )
        write_json(en_root / "06_section_budgets" / "01_01.json", {"budget": {}})
        en_doc = self._document(en_group)
        ar_doc = self._document(ar_group)
        write_json(en_root / "09_section_questions_review" / "01_01.json", en_doc)
        write_json(ar_root / "09_section_questions_review" / "01_01.json", ar_doc)
        write_json(ar_root / "08_section_new_questions" / "01_01.json", ar_doc)
        from translate import group_keys

        keys = group_keys(run, en_group, {}, {})
        state = {
            "version": TRANSLATION_MANIFEST_VERSION,
            "accepted": True,
            "exercises_accepted": True,
            "figures": {"status": "passed"},
            "groups": {
                en_group["id"]: {
                    **keys,
                    "status": "reviewed",
                    "review_fp": "rev",
                    "dropped_numbers": [],
                }
            },
            "review": {"status": "passed_with_issues", "rounds": [], "open_issues": []},
        }
        write_json(ar_root / "00_translation_state" / "01_01.json", state)
        return {
            "title": "عنوان",
            "module_path": "1 > 1",
            "en_document": en_doc,
            "line": "  1.1: terminology ready",
        }

    def test_accepted_section_makes_zero_model_calls(self) -> None:
        import translate

        run = self.make_run()
        run.progress = SimpleNamespace(say=lambda *a, **k: None, asking=lambda *a, **k: None)
        en_group = self._group()
        ar_group = self._arabic()
        section = {"number": "1.1", "stem": "01_01", "spec": {"overview": "درس"}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = self._write_accepted_tree(root, run, en_group, ar_group)
            calls: list[str] = []

            def boom(*_args, **_kwargs):
                calls.append("model")
                raise AssertionError("accepted section must not call the model")

            with (
                patch.object(translate, "OUTPUT_DIR", root),
                patch.object(translate, "request_reply", boom),
                patch.object(translate, "translate_labels", boom),
                patch.object(translate, "translate_exercises", boom),
                patch.object(translate, "adopt_section_figures", boom),
                patch.object(translate, "ensure_spec", boom),
            ):
                lines = translate.translate_section(run, section, {}, prepared)
            state = json.loads(
                (root / run.ar_id / "00_translation_state" / "01_01.json").read_text()
            )
        self.assertEqual(lines, ["  1.1: already accepted"])
        self.assertEqual(calls, [])
        self.assertTrue(state["accepted"])
        self.assertEqual(state["figures"]["status"], "passed")

    def test_ctrl_c_cleared_accepted_flag_still_skips_complete_section(self) -> None:
        import translate

        run = self.make_run()
        run.progress = SimpleNamespace(say=lambda *a, **k: None, asking=lambda *a, **k: None)
        en_group = self._group()
        ar_group = self._arabic()
        section = {"number": "1.1", "stem": "01_01", "spec": {"overview": "درس"}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = self._write_accepted_tree(root, run, en_group, ar_group)
            state_path = root / run.ar_id / "00_translation_state" / "01_01.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["accepted"] = False
            state["review"] = {"status": "pending", "rounds": [{"round": 1}]}
            state_path.write_text(json.dumps(state), encoding="utf-8")
            labels_before = (root / run.ar_id / "05_section_labels" / "01_01.json").read_text()
            accepted_before = (
                root / run.ar_id / "09_section_questions_review" / "01_01.json"
            ).read_text()
            calls: list[str] = []

            def boom(*_args, **_kwargs):
                calls.append("model")
                raise AssertionError("complete section must not call the model")

            with (
                patch.object(translate, "OUTPUT_DIR", root),
                patch.object(translate, "request_reply", boom),
                patch.object(translate, "translate_labels", boom),
                patch.object(translate, "translate_exercises", boom),
                patch.object(translate, "adopt_section_figures", boom),
                patch.object(translate, "ensure_spec", boom),
            ):
                lines = translate.translate_section(run, section, {}, prepared)
            after = json.loads(state_path.read_text(encoding="utf-8"))
            labels_after = (root / run.ar_id / "05_section_labels" / "01_01.json").read_text()
            accepted_after = (
                root / run.ar_id / "09_section_questions_review" / "01_01.json"
            ).read_text()
        self.assertEqual(lines, ["  1.1: already accepted"])
        self.assertEqual(calls, [])
        self.assertTrue(after["accepted"])
        self.assertEqual(after["review"]["status"], "passed")
        self.assertEqual(labels_before, labels_after)
        self.assertEqual(accepted_before, accepted_after)

    def test_force_flags_do_not_skip_an_accepted_section(self) -> None:
        import translate

        run = self.make_run()
        en_group = self._group()
        ar_group = self._arabic()
        section = {"number": "1.1", "stem": "01_01", "spec": {"overview": "درس"}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = self._write_accepted_tree(root, run, en_group, ar_group)
            with patch.object(translate, "OUTPUT_DIR", root):
                state = json.loads(
                    (root / run.ar_id / "00_translation_state" / "01_01.json").read_text()
                )
                self.assertTrue(
                    translate.accepted_section_reusable(run, section, state, prepared)
                )
                for flag in ("update", "rereview", "retranslate_on_prompt_change"):
                    forced = self.make_run()
                    setattr(forced, flag, True)
                    self.assertFalse(
                        translate.accepted_section_reusable(
                            forced, section, state, prepared
                        ),
                        flag,
                    )

    def test_accepted_section_is_not_reusable_when_09_is_incomplete(self) -> None:
        import translate

        run = self.make_run()
        en_group = self._group()
        ar_group = self._arabic()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = self._write_accepted_tree(root, run, en_group, ar_group)
            (root / run.ar_id / "09_section_questions_review" / "01_01.json").write_text(
                json.dumps({"exercises": {"groups": []}}),
                encoding="utf-8",
            )
            with patch.object(translate, "OUTPUT_DIR", root):
                state = json.loads(
                    (root / run.ar_id / "00_translation_state" / "01_01.json").read_text()
                )
                self.assertFalse(
                    translate.accepted_section_reusable(
                        run, {"number": "1.1", "stem": "01_01", "spec": {}}, state, prepared
                    )
                )

    def test_exercises_accepted_skips_review_and_keeps_open_issue_groups(self) -> None:
        import translate

        run = self.make_run()
        run.progress = SimpleNamespace(say=lambda *a, **k: None, asking=lambda *a, **k: None)
        run.workers = 1
        en_group = self._group()
        ar_group = self._arabic()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = self._write_accepted_tree(root, run, en_group, ar_group)
            state_path = root / run.ar_id / "00_translation_state" / "01_01.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["accepted"] = False
            state["groups"][en_group["id"]]["status"] = "validated"
            state["review"] = {"status": "pending", "rounds": [{"round": 1}]}
            state_path.write_text(json.dumps(state), encoding="utf-8")
            before = (root / run.ar_id / "09_section_questions_review" / "01_01.json").read_text()
            calls: list[str] = []

            def boom(*_args, **_kwargs):
                calls.append("model")
                raise AssertionError("must not re-review an accepted exercise set")

            with (
                patch.object(translate, "OUTPUT_DIR", root),
                patch.object(translate, "translate_objective_batch", boom),
                patch.object(translate, "review_translation", boom),
            ):
                line = translate.translate_exercises(
                    run,
                    {"number": "1.1", "stem": "01_01"},
                    prepared["title"],
                    prepared["module_path"],
                    {},
                    {},
                    [],
                    prepared["en_document"],
                    root / run.ar_id / "09_section_questions_review" / "01_01.json",
                    root / run.ar_id / "08_section_new_questions" / "01_01.json",
                    state_path,
                    {},
                )
            after = (root / run.ar_id / "09_section_questions_review" / "01_01.json").read_text()
            after_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(calls, [])
        self.assertIn("0 translated", line)
        self.assertIn("1 kept", line)
        self.assertEqual(before, after)
        self.assertEqual(after_state["review"]["status"], "passed")

    def test_reviewed_group_is_dropped_from_failed_groups(self) -> None:
        import translate

        run = self.make_run()
        run.progress = SimpleNamespace(say=lambda *a, **k: None, asking=lambda *a, **k: None)
        run.workers = 1
        en_group = self._group()
        ar_group = self._arabic()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = self._write_accepted_tree(root, run, en_group, ar_group)
            state_path = root / run.ar_id / "00_translation_state" / "01_01.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["accepted"] = False
            state["failed_groups"] = {en_group["id"]: "old checker rejection"}
            state["failed_groups"]["group_other"] = "still failing"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with (
                patch.object(translate, "OUTPUT_DIR", root),
                patch.object(translate, "translate_objective_batch"),
                patch.object(translate, "review_translation"),
            ):
                translate.translate_exercises(
                    run,
                    {"number": "1.1", "stem": "01_01"},
                    prepared["title"],
                    prepared["module_path"],
                    {},
                    {},
                    [],
                    prepared["en_document"],
                    root / run.ar_id / "09_section_questions_review" / "01_01.json",
                    root / run.ar_id / "08_section_new_questions" / "01_01.json",
                    state_path,
                    {},
                )
            after = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertNotIn(en_group["id"], after.get("failed_groups") or {})
        self.assertEqual(after["failed_groups"]["group_other"], "still failing")

    def test_forget_cleared_failures_drops_reviewed_only(self) -> None:
        from translate import forget_cleared_failures

        state = {
            "groups": {
                "group_22": {"status": "reviewed"},
                "group_9": {"status": "validated"},
            },
            "failed_groups": {
                "group_22": "old",
                "group_9": "old",
                "group_8": "still failing",
            },
        }
        forget_cleared_failures(state)
        self.assertEqual(state["failed_groups"], {"group_8": "still failing"})

    def test_failed_section_translates_only_the_missing_group(self) -> None:
        import translate

        run = self.make_run()
        run.progress = SimpleNamespace(say=lambda *a, **k: None, asking=lambda *a, **k: None)
        run.workers = 1
        kept_en = self._group()
        kept_ar = self._arabic()
        missing_en = json.loads(json.dumps(kept_en))
        missing_en["id"] = "group_2"
        missing_ar = json.loads(json.dumps(kept_ar))
        missing_ar["id"] = "group_2"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = self._write_accepted_tree(root, run, kept_en, kept_ar)
            en_doc = {"exercises": {"groups": [kept_en, missing_en]}}
            ar_doc = {"exercises": {"groups": [kept_ar]}}
            write = lambda path, doc: Path(path).write_text(
                json.dumps(doc, ensure_ascii=False), encoding="utf-8"
            )
            write(root / run.en_id / "09_section_questions_review" / "01_01.json", en_doc)
            write(root / run.ar_id / "09_section_questions_review" / "01_01.json", ar_doc)
            write(root / run.ar_id / "08_section_new_questions" / "01_01.json", ar_doc)
            state_path = root / run.ar_id / "00_translation_state" / "01_01.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["accepted"] = False
            state["exercises_accepted"] = False
            state_path.write_text(json.dumps(state), encoding="utf-8")
            seen: list[str] = []

            def fake_batch(_run, _section, _en_spec, _ar_spec, _term, _obj, groups, **_kwargs):
                seen.extend(str(group["id"]) for group in groups)
                got = {str(group["id"]): missing_ar for group in groups}
                return got, {}, {}, {gid: "batch" for gid in got}

            from concurrent.futures import ThreadPoolExecutor

            with (
                ThreadPoolExecutor(max_workers=1) as pool,
                patch.object(translate, "OUTPUT_DIR", root),
                patch.object(translate, "translate_objective_batch", fake_batch),
                patch.object(translate, "review_translation", return_value=[]),
                patch.object(translate, "validate_document"),
            ):
                run.call_executor = pool
                try:
                    translate.translate_exercises(
                        run,
                        {"number": "1.1", "stem": "01_01"},
                        prepared["title"],
                        prepared["module_path"],
                        {},
                        {},
                        [],
                        en_doc,
                        root / run.ar_id / "09_section_questions_review" / "01_01.json",
                        root / run.ar_id / "08_section_new_questions" / "01_01.json",
                        state_path,
                        {},
                    )
                except RuntimeError:
                    pass
        self.assertEqual(seen, ["group_2"])


if __name__ == "__main__":
    unittest.main()
