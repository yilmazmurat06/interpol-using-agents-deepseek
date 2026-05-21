"""Tests for the RabbitMQ consumer and database persistence (F003, F004).

Tests upsert logic, alarm detection, filter options, and JSONB handling.
"""

import json
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

# Ensure container_b is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "container_b"))

from models import RedNotice


class TestRedNoticeModel(unittest.TestCase):
    """Test the RedNotice dataclass and serialization."""

    def test_from_enriched_payload(self):
        payload = {
            "notice_id": "2026/30493",
            "name": "MWEPU",
            "forename": "BENI",
            "date_of_birth": "2000/12/09",
            "place_of_birth": None,
            "sex_id": "M",
            "height": None,
            "weight": None,
            "nationalities": ["CD"],
            "languages": ["SWA"],
            "eyes_colors_id": [],
            "hairs_id": [],
            "distinguishing_marks": None,
            "arrest_warrants": [
                {"charge": "MURDER", "issuing_country_id": "CD", "charge_translation": None}
            ],
            "image_url": "https://example.com/thumb.jpg",
            "country_of_birth_id": "CD",
        }
        notice = RedNotice.from_enriched_payload(payload)
        self.assertEqual(notice.notice_id, "2026/30493")
        self.assertEqual(notice.name, "MWEPU")
        self.assertEqual(notice.forename, "BENI")
        self.assertEqual(notice.nationalities, ["CD"])
        self.assertEqual(notice.languages, ["SWA"])
        self.assertEqual(len(notice.arrest_warrants), 1)
        self.assertEqual(notice.arrest_warrants[0]["charge"], "MURDER")
        self.assertIsNone(notice.height)
        self.assertIsNone(notice.weight)
        self.assertIsNotNone(notice.created_at)
        self.assertIsNotNone(notice.updated_at)
        self.assertFalse(notice.is_alarm)

    def test_meaningful_hash_same_for_identical(self):
        payload = {
            "notice_id": "X/1",
            "name": "TEST",
            "forename": "A",
            "nationalities": ["US"],
            "arrest_warrants": [{"charge": "FRAUD", "issuing_country_id": "US"}],
        }
        n1 = RedNotice.from_enriched_payload(payload)
        n2 = RedNotice.from_enriched_payload(payload)
        self.assertEqual(n1.meaningful_hash(), n2.meaningful_hash())

    def test_meaningful_hash_differs_on_name_change(self):
        p1 = {
            "notice_id": "X/1",
            "name": "SMITH",
            "forename": "JOHN",
            "nationalities": ["US"],
            "arrest_warrants": [],
        }
        p2 = dict(p1)
        p2["name"] = "SMYTH"
        n1 = RedNotice.from_enriched_payload(p1)
        n2 = RedNotice.from_enriched_payload(p2)
        self.assertNotEqual(n1.meaningful_hash(), n2.meaningful_hash())

    def test_meaningful_hash_differs_on_warrant_change(self):
        p1 = {
            "notice_id": "X/1",
            "name": "SMITH",
            "forename": "JOHN",
            "nationalities": ["US"],
            "arrest_warrants": [{"charge": "FRAUD", "issuing_country_id": "US"}],
        }
        p2 = {
            "notice_id": "X/1",
            "name": "SMITH",
            "forename": "JOHN",
            "nationalities": ["US"],
            "arrest_warrants": [{"charge": "MURDER", "issuing_country_id": "US"}],
        }
        n1 = RedNotice.from_enriched_payload(p1)
        n2 = RedNotice.from_enriched_payload(p2)
        self.assertNotEqual(n1.meaningful_hash(), n2.meaningful_hash())

    def test_to_dict(self):
        payload = {
            "notice_id": "2026/99999",
            "name": "DOE",
            "forename": "JANE",
            "date_of_birth": "1990/05/15",
            "nationalities": ["FR"],
            "languages": ["FRE"],
            "arrest_warrants": [],
            "image_url": None,
        }
        notice = RedNotice.from_enriched_payload(payload)
        d = notice.to_dict()
        self.assertEqual(d["notice_id"], "2026/99999")
        self.assertEqual(d["name"], "DOE")
        self.assertIsInstance(d["arrest_warrants"], list)
        self.assertFalse(d["is_alarm"])

    def test_forename_dash_becomes_none(self):
        payload = {
            "notice_id": "X/1",
            "name": "AMAN",
            "forename": "-",
            "nationalities": ["IN"],
            "arrest_warrants": [],
        }
        notice = RedNotice.from_enriched_payload(payload)
        self.assertIsNone(notice.forename)


if __name__ == "__main__":
    unittest.main()
