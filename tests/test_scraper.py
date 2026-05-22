"""
Unit tests for the scraper module.

Tests CircuitBreaker logic, _entity_id_to_url_path conversion,
_parse_dob normalisation, and Scraper initialisation from env.
"""

import os
import sys
import time
import threading

import pytest

# Add container_a to path so we can import scraper and producer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "container_a"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "container_b"))

from scraper import CircuitBreaker, Scraper, build_scraper_from_env


class TestCircuitBreaker:
    """Verify the circuit breaker state machine."""

    def test_initial_state_closed(self):
        cb = CircuitBreaker(failure_threshold=3, pause_seconds=60)
        assert not cb.is_open()

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, pause_seconds=60)
        cb.record_403()
        cb.record_403()
        assert not cb.is_open()
        cb.record_403()
        assert cb.is_open()

    def test_record_success_resets_counter(self):
        cb = CircuitBreaker(failure_threshold=3, pause_seconds=60)
        cb.record_403()
        cb.record_403()
        cb.record_success()
        cb.record_403()
        cb.record_403()
        assert not cb.is_open()  # only 2 consecutive 403s after reset

    def test_pause_expires(self):
        cb = CircuitBreaker(failure_threshold=2, pause_seconds=0.1)
        cb.record_403()
        cb.record_403()
        assert cb.is_open()
        time.sleep(0.15)
        assert not cb.is_open()

    def test_wait_if_open_blocks_then_returns(self):
        cb = CircuitBreaker(failure_threshold=2, pause_seconds=0.2)
        cb.record_403()
        cb.record_403()

        result = []

        def worker():
            cb.wait_if_open()
            result.append("proceeded")

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2)

        assert "proceeded" in result
        assert not cb.is_open()

    def test_thread_safety(self):
        """Concurrent 403 recordings should not leave the breaker in an inconsistent state."""
        cb = CircuitBreaker(failure_threshold=5, pause_seconds=60)
        errors = []

        def record_many():
            for _ in range(10):
                try:
                    cb.record_403()
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=record_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # After 4 * 10 = 40 recordings with threshold 5, breaker should be open
        assert cb.is_open()


class TestScraperHelpers:
    """Unit tests for Scraper utility methods."""

    def test_entity_id_to_url_path(self):
        scraper = Scraper()
        assert scraper._entity_id_to_url_path("2026/10847") == "2026-10847"
        assert scraper._entity_id_to_url_path("2003/36412") == "2003-36412"
        assert scraper._entity_id_to_url_path("2025/6928") == "2025-6928"
        # No slash — unchanged
        assert scraper._entity_id_to_url_path("2026") == "2026"

    def test_parse_dob_full(self):
        scraper = Scraper()
        assert scraper._parse_dob("1993/01/08") == "1993-01-08"
        assert scraper._parse_dob("2000/12/09") == "2000-12-09"

    def test_parse_dob_year_only(self):
        scraper = Scraper()
        assert scraper._parse_dob("1972") == "1972-01-01"
        assert scraper._parse_dob("2005") == "2005-01-01"

    def test_parse_dob_none(self):
        scraper = Scraper()
        assert scraper._parse_dob(None) is None
        assert scraper._parse_dob("") is None

    def test_parse_float_normal(self):
        scraper = Scraper()
        assert scraper._parse_float(1.68) == 1.68
        assert scraper._parse_float("1.72") == 1.72

    def test_parse_float_zero_sentinel(self):
        scraper = Scraper()
        assert scraper._parse_float(0) is None
        assert scraper._parse_float(0.0) is None

    def test_parse_float_none(self):
        scraper = Scraper()
        assert scraper._parse_float(None) is None

    def test_jitter_range(self):
        """Jitter should produce values in the configured range."""
        scraper = Scraper(jitter_min=0.1, jitter_max=0.3)
        import random
        random.seed(42)
        # We can't easily test _jitter directly (it sleeps), but we can verify
        # the constructor stores the values
        assert scraper._jitter_min == 0.1
        assert scraper._jitter_max == 0.3

    def test_build_scraper_from_env(self):
        """Verify scraper builds from environment variables with correct defaults."""
        # Save original env
        orig = dict(os.environ)
        try:
            # Unset all scraper env vars to test defaults
            for k in list(os.environ.keys()):
                if k in ("INTERPOL_SOURCE_URL", "JITTER_MIN_SECONDS", "JITTER_MAX_SECONDS",
                         "SCRAPE_CONCURRENCY", "SCRAPE_MAX_RETRIES",
                         "CIRCUIT_BREAKER_THRESHOLD", "CIRCUIT_BREAKER_PAUSE_SECONDS"):
                    del os.environ[k]

            s = build_scraper_from_env()
            assert s._base_url == "https://ws-public.interpol.int"
            assert s._jitter_min == 0.3
            assert s._jitter_max == 0.8
            assert s._concurrency == 4
            assert s._max_retries == 3
            assert s._circuit_breaker._failure_threshold == 5
            assert s._circuit_breaker._pause_seconds == 600.0
        finally:
            # Restore
            os.environ.clear()
            os.environ.update(orig)

    def test_build_scraper_custom_env(self):
        """Verify scraper respects custom env values."""
        orig = dict(os.environ)
        try:
            os.environ["INTERPOL_SOURCE_URL"] = "https://test.interpol.int"
            os.environ["JITTER_MIN_SECONDS"] = "0.5"
            os.environ["JITTER_MAX_SECONDS"] = "1.2"
            os.environ["SCRAPE_CONCURRENCY"] = "8"
            os.environ["SCRAPE_MAX_RETRIES"] = "5"
            os.environ["CIRCUIT_BREAKER_THRESHOLD"] = "10"
            os.environ["CIRCUIT_BREAKER_PAUSE_SECONDS"] = "300"

            s = build_scraper_from_env()
            assert s._base_url == "https://test.interpol.int"
            assert s._jitter_min == 0.5
            assert s._jitter_max == 1.2
            assert s._concurrency == 8
            assert s._max_retries == 5
            assert s._circuit_breaker._failure_threshold == 10
            assert s._circuit_breaker._pause_seconds == 300.0
        finally:
            os.environ.clear()
            os.environ.update(orig)


class TestScraperDetailParsing:
    """Test the _fetch_detail return shape (mock-based)."""

    def test_detail_record_shape(self):
        """Verify that _fetch_detail produces the expected record keys."""
        # We test the shape by mocking _fetch_json
        scraper = Scraper()

        mock_response = {
            "date_of_birth": "1990/05/15",
            "distinguishing_marks": None,
            "weight": 75,
            "nationalities": ["US"],
            "entity_id": "2026/12345",
            "eyes_colors_id": ["BRO"],
            "sex_id": "M",
            "place_of_birth": "New York, USA",
            "forename": "JOHN",
            "arrest_warrants": [
                {"charge": "Murder", "issuing_country_id": "US", "charge_translation": None}
            ],
            "country_of_birth_id": "US",
            "hairs_id": ["BLA"],
            "name": "DOE",
            "languages_spoken_ids": ["ENG"],
            "height": 1.80,
            "_embedded": {"links": []},
            "_links": {
                "self": {"href": "https://ws-public.interpol.int/notices/v1/red/2026-12345"},
                "images": {"href": "https://ws-public.interpol.int/notices/v1/red/2026-12345/images"},
                "thumbnail": {"href": "https://ws-public.interpol.int/notices/v1/red/2026-12345/images/12345678"},
            },
        }

        # Monkey-patch _fetch_json
        original_fetch = scraper._fetch_json
        scraper._fetch_json = lambda url, params=None: mock_response
        try:
            record = scraper._fetch_detail("2026/12345")
            assert record is not None
            assert record["notice_id"] == "2026/12345"
            assert record["forename"] == "JOHN"
            assert record["name"] == "DOE"
            assert record["date_of_birth"] == "1990-05-15"
            assert record["height"] == 1.80
            assert record["weight"] == 75.0
            assert record["nationalities"] == ["US"]
            assert record["languages"] == ["ENG"]
            assert record["eyes_colors_id"] == ["BRO"]
            assert record["hairs_id"] == ["BLA"]
            assert record["sex_id"] == "M"
            assert len(record["arrest_warrants"]) == 1
            assert record["arrest_warrants"][0]["charge"] == "Murder"
            assert record["image_url"] == "https://ws-public.interpol.int/notices/v1/red/2026-12345/images/12345678"
            assert "fetched_at" in record
        finally:
            scraper._fetch_json = original_fetch

    def test_detail_with_nulls(self):
        """Verify detail with many null fields."""
        scraper = Scraper()

        mock_response = {
            "date_of_birth": None,
            "distinguishing_marks": None,
            "weight": 0,  # sentinel
            "nationalities": [],
            "entity_id": "2026/99999",
            "eyes_colors_id": None,
            "sex_id": "M",
            "place_of_birth": None,
            "forename": None,
            "arrest_warrants": [],
            "country_of_birth_id": None,
            "hairs_id": None,
            "name": "UNKNOWN",
            "languages_spoken_ids": None,
            "height": 0,  # sentinel
            "_embedded": {"links": []},
            "_links": {"self": {"href": "..."}},
        }

        original_fetch = scraper._fetch_json
        scraper._fetch_json = lambda url, params=None: mock_response
        try:
            record = scraper._fetch_detail("2026/99999")
            assert record is not None
            assert record["date_of_birth"] is None
            assert record["height"] is None  # zero sentinel
            assert record["weight"] is None  # zero sentinel
            assert record["forename"] is None
            assert record["arrest_warrants"] == []
            assert record["nationalities"] == []
            assert record["languages"] == []
            assert record["eyes_colors_id"] == []
            assert record["hairs_id"] == []
        finally:
            scraper._fetch_json = original_fetch


class TestISO2Codes:
    """Verify the nationality sweep code list is non-empty and valid."""

    def test_iso2_codes_non_empty(self):
        from scraper import ISO2_COUNTRIES
        assert len(ISO2_COUNTRIES) > 200
        assert "US" in ISO2_COUNTRIES
        assert "GB" in ISO2_COUNTRIES
        assert "RU" in ISO2_COUNTRIES
        assert "FR" in ISO2_COUNTRIES
        assert "DE" in ISO2_COUNTRIES

    def test_iso2_codes_all_two_chars(self):
        from scraper import ISO2_COUNTRIES
        for code in ISO2_COUNTRIES:
            assert len(code) == 2, f"Expected 2-char ISO code, got '{code}'"
            assert code == code.upper(), f"Expected uppercase, got '{code}'"
