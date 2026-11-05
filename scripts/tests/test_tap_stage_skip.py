"""Pipeline --translate skip for transcripts/audios/align uses complete tap sets."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import pipeline  # noqa: E402
from src import twin  # noqa: E402


BOOK_ID = "fixture_en_eg_t1"
MD5_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MD5_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def make_book() -> dict:
    return {
        "pages": {
            "1": [
                {
                    "id": "g1",
                    "kind": "exercise",
                    "section": "1.1",
                    "exercises": [{"id": "g1"}],
                    "taps": [{"id": "g1", "md5": MD5_A}],
                    "transcript": {"paragraphs": [{"sentences": ["Hello."]}]},
                },
                {
                    "id": "g2",
                    "kind": "exercise",
                    "section": "1.1",
                    "exercises": [{"id": "g2"}],
                    "taps": [{"id": "g2", "md5": MD5_B}],
                    "transcript": {"paragraphs": [{"sentences": ["World."]}]},
                },
            ]
        }
    }


def write_book(root: Path, book: dict) -> Path:
    book_dir = root / BOOK_ID
    taps = book_dir / "14_taps"
    taps.mkdir(parents=True)
    (book_dir / f"{BOOK_ID}.json").write_text(
        json.dumps(book, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return taps


def touch(path: Path, name: str, data: bytes = b"x") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_bytes(data)


class TapStageSkipTests(unittest.TestCase):
    def _skip(self, canonical: str, output_dir: Path) -> bool:
        stage = pipeline.STAGES_BY_NAME[canonical]
        return pipeline.should_skip(
            stage,
            BOOK_ID,
            None,
            translate=True,
            update=False,
            updateboth=False,
            output_dir=output_dir,
        )

    def test_audios_skip_when_every_wav_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            taps = write_book(root, make_book())
            touch(taps / MD5_A, "01.wav")
            touch(taps / MD5_B, "01.wav")
            self.assertEqual(
                twin.audio_wav_counts(BOOK_ID, None, output_dir=root), (2, 2)
            )
            self.assertTrue(self._skip("audios", root))

    def test_audios_run_when_one_wav_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            taps = write_book(root, make_book())
            touch(taps / MD5_A, "01.wav")
            self.assertEqual(
                twin.audio_wav_counts(BOOK_ID, None, output_dir=root), (1, 2)
            )
            self.assertFalse(self._skip("audios", root))
            self.assertFalse(
                twin.stage_output_exists("audios", BOOK_ID, None, output_dir=root)
            )

    def test_audios_run_when_only_wav_tmp_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            taps = write_book(root, make_book())
            touch(taps / MD5_A, "01.wav.tmp")
            touch(taps / MD5_B, "01.wav.tmp")
            self.assertEqual(
                twin.audio_wav_counts(BOOK_ID, None, output_dir=root), (0, 2)
            )
            self.assertFalse(self._skip("audios", root))

    def test_align_skip_when_every_wav_has_json(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            taps = write_book(root, make_book())
            touch(taps / MD5_A, "01.wav")
            touch(taps / MD5_A, "01.json", b"{}")
            touch(taps / MD5_B, "01.wav")
            touch(taps / MD5_B, "01.json", b"{}")
            self.assertEqual(
                twin.align_json_counts(BOOK_ID, None, output_dir=root), (2, 2)
            )
            self.assertTrue(self._skip("align", root))

    def test_align_run_when_one_json_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            taps = write_book(root, make_book())
            touch(taps / MD5_A, "01.wav")
            touch(taps / MD5_A, "01.json", b"{}")
            touch(taps / MD5_B, "01.wav")
            self.assertEqual(
                twin.align_json_counts(BOOK_ID, None, output_dir=root), (1, 2)
            )
            self.assertFalse(self._skip("align", root))

    def test_align_run_when_only_wav_tmp_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            taps = write_book(root, make_book())
            touch(taps / MD5_A, "01.wav.tmp")
            touch(taps / MD5_B, "01.wav.tmp")
            self.assertEqual(
                twin.align_json_counts(BOOK_ID, None, output_dir=root), (0, 0)
            )
            self.assertFalse(self._skip("align", root))

    def test_transcripts_skip_when_every_txt_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            taps = write_book(root, make_book())
            (taps / MD5_A / "01.txt").parent.mkdir(parents=True)
            (taps / MD5_A / "01.txt").write_text("Hello.", encoding="utf-8")
            (taps / MD5_B / "01.txt").parent.mkdir(parents=True)
            (taps / MD5_B / "01.txt").write_text("World.", encoding="utf-8")
            self.assertEqual(
                twin.transcript_txt_counts(BOOK_ID, None, output_dir=root), (2, 2)
            )
            self.assertTrue(self._skip("transcripts", root))

    def test_transcripts_run_when_one_txt_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            taps = write_book(root, make_book())
            (taps / MD5_A).mkdir(parents=True)
            (taps / MD5_A / "01.txt").write_text("Hello.", encoding="utf-8")
            self.assertEqual(
                twin.transcript_txt_counts(BOOK_ID, None, output_dir=root), (1, 2)
            )
            self.assertFalse(self._skip("transcripts", root))

    def test_audio_counts_do_not_write_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            taps = write_book(root, make_book())
            twin.audio_wav_counts(BOOK_ID, None, output_dir=root)
            self.assertFalse((taps / MD5_A / "01.txt").exists())
            self.assertFalse((taps / MD5_B / "01.txt").exists())

    def test_progress_line_names_the_gap(self) -> None:
        self.assertEqual(
            twin.tap_progress_line("EN", "audios", 798, 2146),
            "EN audios: 798/2146 on disk, 1348 to generate",
        )


if __name__ == "__main__":
    unittest.main()
