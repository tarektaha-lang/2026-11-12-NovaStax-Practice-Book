from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from review_probe import unit_agreement_issue, unit_agreement_messages  # noqa: E402


class CountedNounAgreementTests(unittest.TestCase):
    def test_the_four_probe_phrases(self) -> None:
        self.assertTrue(unit_agreement_issue("4 ساعة"))
        self.assertTrue(unit_agreement_issue("3 كوب"))
        self.assertFalse(unit_agreement_issue("210 جنيهات"))
        self.assertFalse(unit_agreement_issue("120 جنيهًا"))

    def test_hundreds_are_singular_and_abbreviations_are_not_inflected(self) -> None:
        self.assertFalse(unit_agreement_issue("200 جنيه"))
        self.assertTrue(unit_agreement_issue("200 جنيهات"))
        self.assertFalse(unit_agreement_issue("12 سم"))
        self.assertTrue(unit_agreement_issue("12 لتر"))
        self.assertFalse(unit_agreement_issue("12 لترًا"))

    def test_dollar_and_text_units_use_the_same_rule(self) -> None:
        self.assertTrue(unit_agreement_issue("$13$ ساعات"))
        self.assertTrue(unit_agreement_issue(r"$13\text{ ساعات}$"))
        self.assertTrue(unit_agreement_issue("$3$ ساعة"))
        self.assertFalse(unit_agreement_issue("$3$ ساعات"))
        self.assertFalse(unit_agreement_issue("$12$ سم"))
        self.assertFalse(unit_agreement_issue(r"$4.8\text{ سم}$"))
        self.assertTrue(unit_agreement_issue("$12$ لتر"))
        self.assertFalse(unit_agreement_issue("$12$ لترًا"))
        self.assertFalse(unit_agreement_issue(r"$13\text{ ساعةً}$"))
        self.assertFalse(unit_agreement_issue("$13$ ساعةً،"))
        self.assertFalse(unit_agreement_messages("من $3$ إلى $5$ ساعات"))

    def test_hours_list_rejects_one_inflection_and_passes_a_mix(self) -> None:
        plural = "$2$ ساعات و$3$ ساعات و$3$ ساعات و$4$ ساعات و$13$ ساعات"
        singular = "$2$ ساعة و$3$ ساعة و$3$ ساعة و$4$ ساعة و$13$ ساعة"
        mixed = "$2$ ساعة و$3$ ساعات و$3$ ساعات و$4$ ساعات و$13$ ساعةً"
        self.assertTrue(unit_agreement_messages(plural))
        joined = " ".join(unit_agreement_messages(plural))
        self.assertIn("check number–noun agreement after $13$", joined)
        self.assertNotIn("ساعة", joined)
        self.assertTrue(unit_agreement_messages(singular))
        singular_hints = " ".join(unit_agreement_messages(singular))
        self.assertIn("check number–noun agreement after $3$", singular_hints)
        self.assertNotRegex(singular_hints, r"[\u0600-\u06FF]")
        self.assertFalse(unit_agreement_messages(mixed))
        self.assertFalse(unit_agreement_issue(mixed))
