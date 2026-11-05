"""Catalog lookup ignores draft rows and cover-filename drift."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import package  # noqa: E402
import pipeline  # noqa: E402
from package import (  # noqa: E402
    catalog_number,
    match_catalog_row,
    validate_catalog,
)


def row(
    *,
    subject: str,
    grade: str,
    language_code: str,
    country_code: str,
    cover: str,
    subject_id: str,
    practice_book_id: str,
    book_id: str = "111111111111",
    study_book_id: str = "222222222222",
    exam_book_id: str = "333333333333",
    book_title: str = "Title",
    country: str = "Saudi Arabia",
    academic_year: str = "26-27",
) -> dict:
    return {
        "book_id": book_id,
        "study_book_id": study_book_id,
        "practice_book_id": practice_book_id,
        "exam_book_id": exam_book_id,
        "academic_year": academic_year,
        "country": country,
        "country_code": country_code,
        "language_code": language_code,
        "grade": grade,
        "subject": subject,
        "subject_id": subject_id,
        "book_title": book_title,
        "cover_illustration": cover,
    }


MATH_SA07 = (
    "Mathematics_G7_T1_Regular_ar_sa",
    row(
        subject="الرياضيات",
        grade="7",
        language_code="ar",
        country_code="sa",
        cover="mathematics_024",
        subject_id="705153769849",
        practice_book_id="348121352580",
        book_id="946109630310",
        study_book_id="953183632018",
        exam_book_id="598108789398",
    ),
)
BIOLOGY_STUB = (
    "Biology_G12_T0_Regular_ar_sa",
    row(
        subject="الأحياء",
        grade="12",
        language_code="ar",
        country_code="sa",
        cover="biology_002",
        subject_id="",
        practice_book_id="573139347570",
        book_id="375104851823",
        study_book_id="250191814871",
        exam_book_id="509123751746",
    ),
)


class PackageCatalogTests(unittest.TestCase):
    def test_draft_biology_row_does_not_block_catalog_validate(self) -> None:
        catalog = dict([BIOLOGY_STUB, MATH_SA07])
        validate_catalog(catalog, path_name="book_database.json")

    def test_part_suffix_is_a_catalog_key(self) -> None:
        catalog = {
            "Chemistry_G11_T0_Regular_ar_eg_Part1": row(
                subject="الكيمياء",
                grade="11",
                language_code="ar",
                country_code="eg",
                cover="chemistry_001",
                subject_id="748165923273",
                practice_book_id="953187469464",
                book_id="789142318903",
                study_book_id="125173071479",
                exam_book_id="368186980416",
                country="Egypt",
            )
        }
        validate_catalog(catalog, path_name="book_database.json")
        key, matched = match_catalog_row(
            catalog,
            ("الكيمياء", "11", "0", "ar", "eg"),
            "chemistry_001",
        )
        self.assertEqual(key, "Chemistry_G11_T0_Regular_ar_eg_Part1")
        self.assertEqual(matched["subject_id"], "748165923273")

    def test_draft_row_is_ignored_when_matching_a_ready_math_book(self) -> None:
        catalog = dict([BIOLOGY_STUB, MATH_SA07])
        key, matched = match_catalog_row(
            catalog,
            ("الرياضيات", "7", "1", "ar", "sa"),
            "mathematics_068",
        )
        self.assertEqual(key, "Mathematics_G7_T1_Regular_ar_sa")
        self.assertEqual(matched["practice_book_id"], "348121352580")

    def test_unique_row_matches_when_xml_cover_has_drifted(self) -> None:
        catalog = dict([MATH_SA07])
        key, _matched = match_catalog_row(
            catalog,
            ("الرياضيات", "7", "1", "ar", "sa"),
            "mathematics_068",
        )
        self.assertEqual(key, "Mathematics_G7_T1_Regular_ar_sa")

    def test_cover_disambiguates_applied_and_pure(self) -> None:
        catalog = {
            "Applied_Mathematics_G12_T0_Regular_ar_eg": row(
                subject="الرياضيات",
                grade="12",
                language_code="ar",
                country_code="eg",
                cover="applied_mathematics_001",
                subject_id="187147528686",
                practice_book_id="165148368139",
                book_id="846138790821",
                study_book_id="830182609232",
                exam_book_id="646151393407",
                country="Egypt",
            ),
            "Pure_Mathematics_G12_T0_Regular_ar_eg": row(
                subject="الرياضيات",
                grade="12",
                language_code="ar",
                country_code="eg",
                cover="pure_mathematics_002",
                subject_id="284165165462",
                practice_book_id="906125745380",
                book_id="405198724345",
                study_book_id="636175690476",
                exam_book_id="535109013653",
                country="Egypt",
            ),
        }
        key, matched = match_catalog_row(
            catalog,
            ("الرياضيات", "12", "0", "ar", "eg"),
            "pure_mathematics_002",
        )
        self.assertEqual(key, "Pure_Mathematics_G12_T0_Regular_ar_eg")
        self.assertEqual(matched["subject_id"], "284165165462")

    def test_book_title_matches_when_xml_subject_is_title(self) -> None:
        catalog = {
            "Applied_Mathematics_G12_T0_Regular_en_eg": row(
                subject="Mathematics",
                book_title="Applied Mathematics",
                grade="12",
                language_code="en",
                country_code="eg",
                cover="applied_mathematics_001",
                subject_id="284165165462",
                practice_book_id="906125745380",
                book_id="846138790821",
                study_book_id="830182609232",
                exam_book_id="646151393407",
                country="Egypt",
            ),
            "Pure_Mathematics_G12_T0_Regular_en_eg": row(
                subject="Mathematics",
                book_title="Pure Mathematics",
                grade="12",
                language_code="en",
                country_code="eg",
                cover="mathematics_081",
                subject_id="284165165462",
                practice_book_id="937102620124",
                book_id="386187134632",
                study_book_id="259161026159",
                exam_book_id="202192093167",
                country="Egypt",
            ),
        }
        key, matched = match_catalog_row(
            catalog,
            ("Pure Mathematics", "12", "0", "en", "eg"),
            "mathematics_081",
        )
        self.assertEqual(key, "Pure_Mathematics_G12_T0_Regular_en_eg")
        self.assertEqual(matched["practice_book_id"], "937102620124")

    def test_incomplete_part_stub_validates_without_grade(self) -> None:
        catalog = {
            "Mathematics_G11_T0_Regular_ar_eg_Part2": {
                "book_id": "352183590705",
                "study_book_id": "146137083869",
                "practice_book_id": "563162589568",
                "exam_book_id": "313171314251",
                "language_code": "ar",
                "language": "Arabic",
            }
        }
        validate_catalog(catalog, path_name="book_database.json")

    def test_catalog_number_equates_07_and_7(self) -> None:
        self.assertEqual(catalog_number("07"), "7")
        self.assertEqual(catalog_number("7"), "7")
        self.assertEqual(catalog_number("T1"), "T1")
        catalog = dict([MATH_SA07])
        key, _matched = match_catalog_row(
            catalog,
            ("الرياضيات", "07", "1", "ar", "sa"),
            "mathematics_068",
        )
        self.assertEqual(key, "Mathematics_G7_T1_Regular_ar_sa")

    def test_draft_only_identity_still_fails(self) -> None:
        catalog = dict([BIOLOGY_STUB])
        with self.assertRaisesRegex(RuntimeError, "has 0 rows"):
            match_catalog_row(
                catalog,
                ("الأحياء", "12", "0", "ar", "sa"),
                "biology_002",
            )


STAGE_KWARGS = dict(
    section=None,
    update=False,
    model=None,
    thinking_level=None,
    workers=None,
    tts_model=None,
)


class OutputBackupFlagTests(unittest.TestCase):
    def test_package_book_skips_dated_archive_when_disabled(self) -> None:
        working = mock.MagicMock()
        working.is_file.return_value = True
        working.name = "book.json"
        book_pdf = mock.MagicMock()
        book_pdf.is_file.return_value = True
        book_dir = mock.MagicMock()
        book_dir.__truediv__.return_value = book_pdf
        output_dir = mock.MagicMock()
        output_dir.__truediv__.return_value = book_dir
        served = mock.MagicMock()
        served.stat.return_value = mock.Mock(st_size=1024)
        served.relative_to.return_value = Path("served.zip")
        out = mock.MagicMock()
        out.relative_to.return_value = Path("package.json")
        backup = mock.MagicMock()
        backup.stat.return_value = mock.Mock(st_size=2048)
        backup.relative_to.return_value = Path("backup.zip")
        reader = mock.MagicMock()
        reader.pages = [object()]

        with (
            mock.patch.object(package, "working_json", return_value=working),
            mock.patch.object(package, "OUTPUT_DIR", output_dir),
            mock.patch.object(package, "PdfReader", return_value=reader),
            mock.patch.object(package, "load_json", return_value={"pages": {"0001": []}}),
            mock.patch.object(package, "pages_for_section", return_value={"0001": []}),
            mock.patch.object(package, "build_pages_dict", return_value=({}, 0)),
            mock.patch.object(package, "export_tap_transcripts", return_value=0),
            mock.patch.object(
                package,
                "catalog_record",
                return_value={
                    "practice_book_id": "123456789012",
                    "subject_id": "s",
                    "country": "Egypt",
                    "academic_year": "26-27",
                },
            ),
            mock.patch.object(package, "build_document_meta", return_value={}),
            mock.patch.object(package, "package_json_path", return_value=out),
            mock.patch.object(package, "cover_pdf_path", return_value=Path("cover.pdf")),
            mock.patch.object(package, "served_zip_path", return_value=served),
            mock.patch.object(
                package, "build_served_zip", return_value=(served, 1)
            ) as build_served,
            mock.patch.object(
                package, "build_output_backup_zip", return_value=backup
            ) as build_backup,
            mock.patch("builtins.print"),
        ):
            package.package_book("book_id", output_backup=False)
            build_served.assert_called_once()
            build_backup.assert_not_called()

            package.package_book("book_id")
            build_backup.assert_called_once_with("book_id", "123456789012")

    def test_cli_forwards_no_output_backup(self) -> None:
        with (
            mock.patch.object(
                sys, "argv", ["package.py", "mathematics_ar_eg07_t1", "--no-output-backup"]
            ),
            mock.patch.object(package, "package_book") as build,
        ):
            self.assertEqual(package.main(), 0)
            build.assert_called_once_with(
                "mathematics_ar_eg07_t1",
                None,
                output_backup=False,
            )

    def test_cli_default_writes_output_backup(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["package.py", "mathematics_ar_eg07_t1"]),
            mock.patch.object(package, "package_book") as build,
        ):
            self.assertEqual(package.main(), 0)
            build.assert_called_once_with(
                "mathematics_ar_eg07_t1",
                None,
                output_backup=True,
            )

    def test_pipeline_forwards_only_to_package_stages(self) -> None:
        package_cmd = pipeline.stage_command(
            pipeline.STAGES_BY_NAME["package"],
            "mathematics_en_eg07_t1",
            **STAGE_KWARGS,
            no_output_backup=True,
        )
        self.assertIn("--no-output-backup", package_cmd)
        ar_package = next(
            row for row in pipeline.TRANSLATE_STAGES if row.name == "ar_package"
        )
        ar_cmd = pipeline.stage_command(
            ar_package,
            "mathematics_ar_eg07_t1",
            **STAGE_KWARGS,
            no_output_backup=True,
        )
        self.assertIn("--no-output-backup", ar_cmd)
        groups = pipeline.stage_command(
            pipeline.STAGES_BY_NAME["groups"],
            "mathematics_en_eg07_t1",
            **STAGE_KWARGS,
            no_output_backup=True,
        )
        self.assertNotIn("--no-output-backup", groups)
        omitted = pipeline.stage_command(
            pipeline.STAGES_BY_NAME["package"],
            "mathematics_en_eg07_t1",
            **STAGE_KWARGS,
        )
        self.assertNotIn("--no-output-backup", omitted)


if __name__ == "__main__":
    unittest.main()
