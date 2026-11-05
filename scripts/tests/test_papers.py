"""Papers helpers refuse a holey bound book without calling the engine."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from papers import (  # noqa: E402
    PROJECT_ROOT,
    bound_book_paths,
    discard_bound_book,
    display_path,
    missing_papers,
)


def order(*pairs: tuple[str, str]) -> list[tuple[str, dict]]:
    return [(stem, {"number": number}) for stem, number in pairs]


class PapersHelperTests(unittest.TestCase):
    def test_missing_papers_is_empty_when_every_stem_has_a_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            papers_dir = Path(folder)
            (papers_dir / "01_01.pdf").write_bytes(b"%PDF")
            (papers_dir / "02_01.pdf").write_bytes(b"%PDF")
            self.assertEqual(
                missing_papers(order(("01_01", "1.1"), ("02_01", "2.1")), papers_dir),
                [],
            )

    def test_missing_papers_lists_absent_stems_in_book_order(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            papers_dir = Path(folder)
            (papers_dir / "01_01.pdf").write_bytes(b"%PDF")
            self.assertEqual(
                missing_papers(
                    order(("01_01", "1.1"), ("02_01", "2.1"), ("03_02", "3.2")),
                    papers_dir,
                ),
                ["2.1", "3.2"],
            )

    def test_discard_bound_book_removes_both_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output_dir = Path(folder)
            book_id = "mathematics_en_eg10_t1"
            book_pdf, grid_pdf = bound_book_paths(book_id, output_dir)
            book_pdf.parent.mkdir(parents=True)
            book_pdf.write_bytes(b"%PDF")
            grid_pdf.write_bytes(b"%PDF")
            discard_bound_book(book_id, output_dir)
            self.assertFalse(book_pdf.exists())
            self.assertFalse(grid_pdf.exists())

    def test_display_path_falls_back_outside_the_project(self) -> None:
        inside = PROJECT_ROOT / "scripts" / "papers.py"
        self.assertEqual(display_path(inside), Path("scripts/papers.py"))
        outside = Path("/tmp/novastax-output-dir")
        self.assertEqual(display_path(outside), outside)

    def test_discard_bound_book_is_fine_when_pdfs_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            discard_bound_book("mathematics_en_eg10_t1", Path(folder))


if __name__ == "__main__":
    unittest.main()
