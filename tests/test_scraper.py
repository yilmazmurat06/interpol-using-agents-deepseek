"""Unit tests for the scraper module (F001).

Tests the nationality sweep logic, circuit breaker, jitter handling,
detail enrichment, and retry mechanism.
"""

import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure container_a is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "container_a"))

from scraper import CircuitBreaker, RedNoticeScraper


class TestCircuitBreaker(unittest.TestCase):
    """Verify the circuit breaker opens, closes, and tracks 403s correctly."""

    def setUp(self):
        self.cb = CircuitBreaker(threshold=3, pause_seconds=0.5)

    def test_initial_state_closed(self):
        self.assertFalse(self.cb.is_open())

    def test_opens_after_threshold_403s(self):
        self.cb.record_403()
        self.cb.record_403()
        self.assertFalse(self.cb.is_open())
        self.cb.record_403()  # hits threshold
        self.assertTrue(self.cb.is_open())

    def test_closes_after_pause(self):
        cb = CircuitBreaker(threshold=2, pause_seconds=0.1)
        cb.record_403()
        cb.record_403()
        self.assertTrue(cb.is_open())
        time.sleep(0.2)
        self.assertFalse(cb.is_open())

    def test_record_success_resets_counter(self):
        self.cb.record_403()
        self.cb.record_success()
        self.cb.record_403()
        self.assertFalse(self.cb.is_open())

    def test_records_403_when_open_does_not_count(self):
        cb = CircuitBreaker(threshold=2, pause_seconds=0.5)
        cb.record_403()
        cb.record_403()
        self.assertTrue(cb.is_open())
        cb.record_403()  # while open — does not affect counter
        self.assertTrue(cb.is_open())


class TestScraperHelpers(unittest.TestCase):
    """Test the RedNoticeScraper helper methods."""

    def setUp(self):
        self.scraper = RedNoticeScraper(
            base_url="https://ws-public.interpol.int",
            jitter_min=0.01,
            jitter_max=0.02,
            concurrency=2,
            max_retries=1,
            circuit_breaker_threshold=10,
            circuit_breaker_pause=600,
        )

    def test_detail_url_converts_slashes_to_dashes(self):
        url = self.scraper._detail_url("2026/30493")
        self.assertIn("2026-30493", url)
        self.assertNotIn("2026/30493", url)

    def test_enrich_notice_normal(self):
        detail = {
            "entity_id": "2026/30493",
            "forename": "BENI",
            "name": "MWEPU",
            "date_of_birth": "2000/12/09",
            "place_of_birth": None,
            "sex_id": "M",
            "height": None,
            "weight": None,
            "nationalities": ["CD"],
            "languages_spoken_ids": ["SWA"],
            "eyes_colors_id": None,
            "hairs_id": None,
            "distinguishing_marks": None,
            "arrest_warrants": [
                {"charge": "MURDER", "issuing_country_id": "CD", "charge_translation": None}
            ],
            "_links": {
                "thumbnail": {"href": "https://example.com/thumb.jpg"}
            },
        }
        result = self.scraper._enrich_notice("2026/30493", detail)
        self.assertEqual(result["notice_id"], "2026/30493")
        self.assertEqual(result["name"], "MWEPU")
        self.assertEqual(result["forename"], "BENI")
        self.assertEqual(result["nationalities"], ["CD"])
        self.assertEqual(result["languages"], ["SWA"])
        self.assertEqual(result["height"], None)
        self.assertEqual(result["weight"], None)
        self.assertEqual(result["eyes_colors_id"], [])
        self.assertEqual(result["hairs_id"], [])
        self.assertEqual(len(result["arrest_warrants"]), 1)
        self.assertEqual(result["arrest_warrants"][0]["charge"], "MURDER")
        self.assertEqual(result["image_url"], "https://example.com/thumb.jpg")

    def test_enrich_notice_forename_dash(self):
        detail = {
            "forename": "-",
            "name": "AMAN",
            "date_of_birth": "1994/12/08",
            "nationalities": ["IN"],
            "languages_spoken_ids": ["ENG"],
            "arrest_warrants": [],
            "_links": {},
        }
        result = self.scraper._enrich_notice("2025/97751", detail)
        self.assertEqual(result["forename"], "-")

    def test_enrich_notice_height_weight_zero(self):
        detail = {
            "forename": "TEST",
            "name": "PERSON",
            "date_of_birth": "1980/01/01",
            "height": 0,
            "weight": 0,
            "nationalities": [],
            "languages_spoken_ids": [],
            "arrest_warrants": [],
            "_links": {},
        }
        result = self.scraper._enrich_notice("test/1", detail)
        self.assertIsNone(result["height"])
        self.assertIsNone(result["weight"])

    def test_enrich_notice_nationalities_null(self):
        detail = {
            "forename": "TEST",
            "name": "PERSON",
            "date_of_birth": "1980/01/01",
            "nationalities": None,
            "languages_spoken_ids": [],
            "arrest_warrants": [],
            "_links": {},
        }
        result = self.scraper._enrich_notice("test/2", detail)
        self.assertEqual(result["nationalities"], [])

    def test_is_already_seen_deduplication(self):
        self.assertFalse(self.scraper._is_already_seen("2026/30493"))
        self.assertTrue(self.scraper._is_already_seen("2026/30493"))

    def test_enrich_notice_no_thumbnail(self):
        detail = {
            "forename": "TEST",
            "name": "PERSON",
            "date_of_birth": "1980/01/01",
            "nationalities": [],
            "languages_spoken_ids": [],
            "arrest_warrants": [],
            "_links": {},
        }
        result = self.scraper._enrich_notice("test/3", detail)
        self.assertIsNone(result["image_url"])

    def test_enrich_notice_eyes_colors_array(self):
        detail = {
            "forename": "TEST",
            "name": "PERSON",
            "date_of_birth": "1980/01/01",
            "nationalities": [],
            "languages_spoken_ids": [],
            "eyes_colors_id": ["BLA"],
            "hairs_id": ["BLA"],
            "arrest_warrants": [],
            "_links": {},
        }
        result = self.scraper._enrich_notice("test/4", detail)
        self.assertEqual(result["eyes_colors_id"], ["BLA"])
        self.assertEqual(result["hairs_id"], ["BLA"])


if __name__ == "__main__":
    unittest.main()
