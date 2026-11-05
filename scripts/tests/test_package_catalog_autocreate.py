"""package.py autocreates a book_database.json row when none matches."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1]
PROJECT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import package  # noqa: E402


BOOK = "mathematics_ar_eg07_t1"
KEY = "Mathematics_G7_T1_Regular_ar_eg"
NEW_IDS = [
    "801111111111",
    "802222222222",
    "803333333333",
    "804444444444",
]
AR_SUBJECT_ID = "919174538509"
EN_SUBJECT_ID = "753147123653"
EXISTING_PRACTICE = "482187520926"

# First two rows of data/book_database.json, including the mixed 2-space first row.
MIXED_CATALOG = """\
{
  "History_G8_T0_Regular_ar_dz": {
  "book_id": "909107898414",
  "study_book_id": "714190143746",
  "practice_book_id": "482187520926",
  "exam_book_id": "528154631393",
  "academic_year": "26-27",
  "country": "Algeria",
  "country_code": "dz",
  "language": "Arabic",
  "language_code": "ar",
  "grade": "8",
  "subject": "التاريخ",
  "subject_id": "289169605804",
  "book_title": "التاريخ",
  "cover_illustration": "history_008"
  },
  "Mathematics_G8_T0_Regular_ar_dz": {
    "book_id": "783130976907",
    "study_book_id": "395158313693",
    "practice_book_id": "120182716203",
    "exam_book_id": "963162463275",
    "academic_year": "26-27",
    "country": "Algeria",
    "country_code": "dz",
    "language": "Arabic",
    "language_code": "ar",
    "grade": "8",
    "subject": "الرياضيات",
    "subject_id": "782127206843",
    "book_title": "الرياضيات",
    "cover_illustration": "mathematics_003"
  }
}
"""

CSV_HEADER = (
    "subject_id,stage_url_text,grade_title,subject_url_text,"
    "subject_order,subject_language,subject_name,subject_icon_s3_key"
)
DEFAULT_CSV_ROWS = (
    f"{AR_SUBJECT_ID},preparatory,EG07,mathematics-ar,118,ar,الرياضيات,/x.png",
    f"{EN_SUBJECT_ID},preparatory,EG07,mathematics-en,119,en,Mathematics,/x.png",
)

SHORT_NAMES = {
    "eg": "Egypt",
    "sa": "Saudi Arabia",
    "iq": "Iraq",
    "dz": "Algeria",
    "om": "Oman",
}


def ready_row(
    *,
    subject: str = "الرياضيات",
    grade: str = "7",
    language_code: str = "ar",
    country_code: str = "eg",
    cover: str = "mathematics_009",
    subject_id: str = AR_SUBJECT_ID,
    ids: tuple[str, str, str, str] = ("111111111111", "222222222222", "333333333333", "444444444444"),
    country: str = "Egypt",
    language: str = "Arabic",
    academic_year: str = "26-27",
    book_title: str | None = None,
) -> dict:
    return {
        "book_id": ids[0],
        "study_book_id": ids[1],
        "practice_book_id": ids[2],
        "exam_book_id": ids[3],
        "academic_year": academic_year,
        "country": country,
        "country_code": country_code,
        "language": language,
        "language_code": language_code,
        "grade": grade,
        "subject": subject,
        "subject_id": subject_id,
        "book_title": subject if book_title is None else book_title,
        "cover_illustration": cover,
    }


class CountryShortNameTests(unittest.TestCase):
    def test_short_names_match_country_names_and_catalog(self) -> None:
        wording = json.loads(
            (PROJECT / "frontmatter" / "wording.json").read_text(encoding="utf-8")
        )
        catalog = json.loads(
            (PROJECT / "data" / "book_database.json").read_text(encoding="utf-8")
        )
        names = wording["country_names"]
        short = wording["country_short_names_en"]
        self.assertEqual(set(names), set(short))
        by_code: dict[str, set[str]] = {}
        for row in catalog.values():
            if not isinstance(row, dict):
                continue
            code = str(row.get("country_code", "")).strip()
            country = str(row.get("country", "")).strip()
            if code:
                by_code.setdefault(code, set()).add(country)
        for code, countries in by_code.items():
            self.assertEqual(countries, {short[code]})


class CatalogAutocreateTests(unittest.TestCase):
    def setUp(self) -> None:
        package._catalog_validated = False
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.catalog_file = self.root / "book_database.json"
        self.wording_file = self.root / "wording.json"
        self.csv_file = self.root / "subjects.csv"
        self.input_dir = self.root / "input"
        self.fetch = mock.patch(
            "package._fetch_bulk_codes", return_value=list(NEW_IDS)
        ).start()
        mock.patch("package.catalog_path", lambda: self.catalog_file).start()
        mock.patch(
            "package.toc_path",
            lambda book_id: self.input_dir / book_id / f"{book_id}.xml",
        ).start()
        mock.patch("package.wording_path", lambda: self.wording_file).start()
        mock.patch("package.subjects_csv_path", lambda: self.csv_file).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._reset_validated)
        self.write_catalog()
        self.write_wording()
        self.write_csv()
        self.write_xml()

    def _reset_validated(self) -> None:
        package._catalog_validated = False

    def write_catalog(self, text: str = MIXED_CATALOG) -> None:
        self.catalog_file.write_text(text, encoding="utf-8")

    def write_wording(self, short: dict[str, str] | None = None) -> None:
        payload = {
            "country_names": {code: {"en": name} for code, name in SHORT_NAMES.items()},
            "country_short_names_en": dict(SHORT_NAMES if short is None else short),
        }
        self.wording_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_csv(self, rows: tuple[str, ...] = DEFAULT_CSV_ROWS) -> None:
        self.csv_file.write_text(
            CSV_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8"
        )

    def write_xml(self, book_id: str = BOOK, **attrs: str) -> None:
        fields = {
            "subject": "الرياضيات",
            "grade": "7",
            "term": "1",
            "language_code": "ar",
            "language": "Arabic",
            "academic_year": "26-27",
            "country_code": "eg",
            "country": "Arab Republic of Egypt",
            "cover_illustration": "mathematics_009",
        }
        fields.update(attrs)
        folder = self.input_dir / book_id
        folder.mkdir(parents=True, exist_ok=True)
        attr = " ".join(f'{name}="{value}"' for name, value in fields.items())
        (folder / f"{book_id}.xml").write_text(f"<book {attr}/>\n", encoding="utf-8")

    def add_row(self, key: str, row: dict) -> None:
        text = self.catalog_file.read_text(encoding="utf-8")
        self.catalog_file.write_text(
            package.splice_catalog_text(text, key, row), encoding="utf-8"
        )
        package._catalog_validated = False

    def catalog_bytes(self) -> bytes:
        return self.catalog_file.read_bytes()

    def catalog_dict(self) -> dict:
        return json.loads(self.catalog_file.read_text(encoding="utf-8"))

    def test_existing_row_is_unchanged_and_does_not_call_the_service(self) -> None:
        self.add_row(KEY, ready_row())
        before = self.catalog_bytes()
        row = package.catalog_record(BOOK)
        self.assertEqual(row["practice_book_id"], "333333333333")
        self.assertEqual(row["subject_id"], AR_SUBJECT_ID)
        self.assertEqual(self.catalog_bytes(), before)
        self.fetch.assert_not_called()

    def test_missing_arabic_row_is_appended_last_with_xml_subject(self) -> None:
        before = MIXED_CATALOG.encode("utf-8")
        first_row_end = before.index(b'\n  "Mathematics_G8_T0_Regular_ar_dz"')
        row = package.catalog_record(BOOK)
        self.assertEqual(list(row), list(package.CATALOG_ROW_FIELDS))
        self.assertEqual(row["subject"], "الرياضيات")
        self.assertEqual(row["book_title"], "الرياضيات")
        self.assertEqual(row["subject_id"], AR_SUBJECT_ID)
        self.assertNotEqual(row["subject_id"], EN_SUBJECT_ID)
        self.assertEqual(row["country"], "Egypt")
        self.assertEqual(row["language"], "Arabic")
        self.assertEqual(row["grade"], "7")
        self.assertEqual(
            [row[field] for field in package.CATALOG_ID_FIELDS],
            NEW_IDS,
        )
        loaded = self.catalog_dict()
        self.assertEqual(list(loaded)[-1], KEY)
        self.assertEqual(loaded[KEY], row)
        old = json.loads(MIXED_CATALOG)
        for key, old_row in old.items():
            self.assertEqual(loaded[key], old_row)
        after = self.catalog_bytes()
        self.assertEqual(after[:first_row_end], before[:first_row_end])
        self.assertTrue(after.endswith(b"\n  }\n}\n"))
        self.fetch.assert_called_once_with(4)

        package._catalog_validated = False
        self.fetch.reset_mock()
        again = package.catalog_record(BOOK)
        self.assertEqual(again, row)
        self.fetch.assert_not_called()
        self.assertEqual(self.catalog_bytes(), after)

    def test_service_errors_leave_the_file_unchanged(self) -> None:
        before = self.catalog_bytes()
        self.fetch.side_effect = RuntimeError("12-digit ID service: down")
        with self.assertRaisesRegex(RuntimeError, "12-digit ID service"):
            package.catalog_record(BOOK)
        self.assertEqual(self.catalog_bytes(), before)

        package._catalog_validated = False
        self.fetch.side_effect = None
        self.fetch.return_value = {"ids": NEW_IDS}
        with self.assertRaisesRegex(RuntimeError, "not a list"):
            package.catalog_record(BOOK)
        self.assertEqual(self.catalog_bytes(), before)

        package._catalog_validated = False
        self.fetch.return_value = NEW_IDS[:3]
        with self.assertRaisesRegex(RuntimeError, "expected 4 ids"):
            package.catalog_record(BOOK)
        self.assertEqual(self.catalog_bytes(), before)

    def test_duplicate_or_reused_ids_are_rejected(self) -> None:
        before = self.catalog_bytes()
        self.fetch.return_value = [NEW_IDS[0], NEW_IDS[1], NEW_IDS[0], NEW_IDS[3]]
        with self.assertRaisesRegex(RuntimeError, "duplicate ids"):
            package.catalog_record(BOOK)
        self.assertEqual(self.catalog_bytes(), before)

        package._catalog_validated = False
        self.fetch.return_value = ["909107898414", *NEW_IDS[1:]]
        with self.assertRaisesRegex(RuntimeError, "already in the catalog"):
            package.catalog_record(BOOK)
        self.assertEqual(self.catalog_bytes(), before)

    def test_missing_csv_subject_does_not_call_the_service(self) -> None:
        self.write_csv(rows=(DEFAULT_CSV_ROWS[1],))
        before = self.catalog_bytes()
        with self.assertRaisesRegex(RuntimeError, "add the row to book_database.json by hand"):
            package.catalog_record(BOOK)
        self.assertEqual(self.catalog_bytes(), before)
        self.fetch.assert_not_called()

    def test_ambiguous_csv_does_not_call_the_service(self) -> None:
        self.write_csv(
            rows=(
                DEFAULT_CSV_ROWS[0],
                "999999999999,preparatory,EG07,mathematics-ar,118,ar,الرياضيات,/x.png",
                DEFAULT_CSV_ROWS[1],
            )
        )
        before = self.catalog_bytes()
        with self.assertRaisesRegex(RuntimeError, "2 subject_id"):
            package.catalog_record(BOOK)
        self.assertEqual(self.catalog_bytes(), before)
        self.fetch.assert_not_called()

    def test_duplicate_csv_lines_with_the_same_id_are_ok(self) -> None:
        self.write_csv(rows=(DEFAULT_CSV_ROWS[0], DEFAULT_CSV_ROWS[0], DEFAULT_CSV_ROWS[1]))
        row = package.catalog_record(BOOK)
        self.assertEqual(row["subject_id"], AR_SUBJECT_ID)
        self.fetch.assert_called_once_with(4)

    def test_missing_country_short_name_does_not_call_the_service(self) -> None:
        self.write_wording(short={"sa": "Saudi Arabia"})
        before = self.catalog_bytes()
        with self.assertRaisesRegex(RuntimeError, "country_short_names_en"):
            package.catalog_record(BOOK)
        self.assertEqual(self.catalog_bytes(), before)
        self.fetch.assert_not_called()

    def test_draft_row_keeps_has_0_rows_and_does_not_mint(self) -> None:
        draft = ready_row(subject_id="", ids=("555555555555", "666666666666", "777777777777", "888888888888"))
        self.add_row(KEY, draft)
        before = self.catalog_bytes()
        with self.assertRaisesRegex(RuntimeError, "has 0 rows"):
            package.catalog_record(BOOK)
        self.assertEqual(self.catalog_bytes(), before)
        self.fetch.assert_not_called()

    def test_dry_run_missing_row_prints_placeholders(self) -> None:
        before = self.catalog_bytes()
        stdout = io.StringIO()
        with mock.patch("sys.argv", ["package.py", BOOK, "--dry-run-catalog"]):
            with mock.patch("sys.stdout", stdout):
                code = package.main()
        self.assertEqual(code, 0)
        text = stdout.getvalue()
        self.assertIn("would append", text)
        self.assertIn(KEY, text)
        self.assertIn(package.PLACEHOLDER_CATALOG_ID, text)
        self.assertIn("الرياضيات", text)
        self.assertEqual(self.catalog_bytes(), before)
        self.fetch.assert_not_called()

    def test_dry_run_existing_row_prints_the_row(self) -> None:
        self.add_row(KEY, ready_row())
        before = self.catalog_bytes()
        stdout = io.StringIO()
        with mock.patch("sys.argv", ["package.py", BOOK, "--dry-run-catalog"]):
            with mock.patch("sys.stdout", stdout):
                code = package.main()
        self.assertEqual(code, 0)
        text = stdout.getvalue()
        self.assertIn("already has", text)
        self.assertIn(KEY, text)
        self.assertNotIn("would append", text)
        self.assertEqual(self.catalog_bytes(), before)
        self.fetch.assert_not_called()

    def test_key_collision_does_not_overwrite(self) -> None:
        collision = ready_row(
            subject="Biology",
            ids=("121212121212", "131313131313", "141414141414", "151515151515"),
        )
        self.add_row(KEY, collision)
        before = self.catalog_bytes()
        with self.assertRaisesRegex(RuntimeError, "already has"):
            package.catalog_record(BOOK)
        self.assertEqual(self.catalog_bytes(), before)
        self.fetch.assert_not_called()

    def test_practice_md5_collision_does_not_write(self) -> None:
        before = self.catalog_bytes()
        real_md5 = package.book_md5

        def fake_md5(value: str) -> str:
            if value == NEW_IDS[2]:
                return real_md5(EXISTING_PRACTICE)
            return real_md5(value)

        with mock.patch("package.book_md5", side_effect=fake_md5):
            with self.assertRaisesRegex(RuntimeError, "practice md5"):
                package.catalog_record(BOOK)
        self.assertEqual(self.catalog_bytes(), before)

    def test_part_book_does_not_call_the_service(self) -> None:
        part_id = "mathematics_ar_eg07_t1_part1"
        self.write_xml(book_id=part_id)
        before = self.catalog_bytes()
        with self.assertRaisesRegex(RuntimeError, "part book"):
            package.catalog_record(part_id)
        self.assertEqual(self.catalog_bytes(), before)
        self.fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
