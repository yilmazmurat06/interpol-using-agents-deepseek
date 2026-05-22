"""
Unit tests for the consumer module and database layer.

Tests RedNotice model, Database filter building, consumer message handling,
and MinIO storage operations.

Note: These tests do NOT require a live PostgreSQL or RabbitMQ.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "container_b"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "container_a"))

from models import RedNotice, ArrestWarrant, calculate_age


class TestRedNoticeModel:
    """Verify the RedNotice dataclass and related utilities."""

    def test_from_scraper_record_basic(self):
        record = {
            "notice_id": "2026/12345",
            "name": "DOE",
            "forename": "JOHN",
            "date_of_birth": "1990-05-15",
            "place_of_birth": "New York",
            "sex_id": "M",
            "height": 1.80,
            "weight": 75.0,
            "nationalities": ["US"],
            "languages": ["ENG"],
            "eyes_colors_id": ["BRO"],
            "hairs_id": ["BLA"],
            "distinguishing_marks": None,
            "arrest_warrants": [{"charge": "Murder", "issuing_country_id": "US"}],
            "image_url": "https://example.com/img.jpg",
            "fetched_at": "2026-05-22T12:00:00Z",
        }
        notice = RedNotice.from_scraper_record(record)
        assert notice.notice_id == "2026/12345"
        assert notice.name == "DOE"
        assert notice.forename == "JOHN"
        assert notice.date_of_birth == "1990-05-15"
        assert notice.height == 1.80
        assert notice.weight == 75.0
        assert notice.nationalities == ["US"]
        assert len(notice.arrest_warrants) == 1
        assert notice.arrest_warrants[0]["charge"] == "Murder"

    def test_from_scraper_record_zero_sentinels(self):
        """Height/weight = 0 should become None."""
        record = {
            "notice_id": "2026/99999",
            "name": "UNKNOWN",
            "forename": None,
            "height": 0,
            "weight": 0.0,
            "nationalities": [],
            "arrest_warrants": [],
        }
        notice = RedNotice.from_scraper_record(record)
        assert notice.height is None
        assert notice.weight is None

    def test_from_scraper_record_defaults(self):
        """Missing optional fields should get sensible defaults."""
        record = {"notice_id": "2026/minimal", "name": "MINIMAL"}
        notice = RedNotice.from_scraper_record(record)
        assert notice.forename is None
        assert notice.nationalities == []
        assert notice.languages == []
        assert notice.arrest_warrants == []
        assert notice.eyes_colors_id == []
        assert notice.hairs_id == []
        assert notice.is_alarm is False

    def test_to_dict(self):
        notice = RedNotice(
            notice_id="2026/12345",
            name="DOE",
            forename="JOHN",
            date_of_birth="1990-05-15",
            sex_id="M",
            nationalities=["US"],
            arrest_warrants=[{"charge": "Murder", "issuing_country_id": "US"}],
            is_alarm=True,
        )
        d = notice.to_dict()
        assert d["notice_id"] == "2026/12345"
        assert d["name"] == "DOE"
        assert d["is_alarm"] is True
        assert d["nationalities"] == ["US"]

    def test_calculate_age(self):
        from datetime import datetime
        # Test with a known birth date — age depends on current date,
        # so we verify it returns a positive integer
        dob = "1990-05-15"
        age = calculate_age(dob)
        assert isinstance(age, int)
        assert age > 0

    def test_calculate_age_year_only(self):
        dob = "1972-01-01"
        age = calculate_age(dob)
        assert isinstance(age, int)
        assert age > 0

    def test_calculate_age_none(self):
        assert calculate_age(None) is None


class TestArrestWarrant:
    """Verify arrest warrant serialisation."""

    def test_from_dict(self):
        aw = ArrestWarrant.from_dict({
            "charge": "Murder",
            "issuing_country_id": "US",
            "charge_translation": None,
        })
        assert aw.charge == "Murder"
        assert aw.issuing_country_id == "US"
        assert aw.charge_translation is None

    def test_to_dict(self):
        aw = ArrestWarrant(charge="Fraud", issuing_country_id="GB")
        d = aw.to_dict()
        assert d["charge"] == "Fraud"
        assert d["issuing_country_id"] == "GB"


class TestDatabaseFilterBuilder:
    """
    Test the _build_filter_clause method of the Database class.
    This doesn't require a live PostgreSQL connection — it only tests
    SQL clause generation.
    """

    def test_no_filters(self):
        from db import Database
        db = Database(dsn="postgresql://localhost/test")  # won't connect
        where, params = db._build_filter_clause(None)
        assert where == ""
        assert params == {}

    def test_empty_filters(self):
        from db import Database
        db = Database(dsn="postgresql://localhost/test")
        where, params = db._build_filter_clause({})
        assert where == ""
        assert params == {}

    def test_nationality_filter(self):
        from db import Database
        db = Database(dsn="postgresql://localhost/test")
        where, params = db._build_filter_clause({"nationality": "US"})
        assert "nationality" in where.lower()
        assert params["nationality"] == "US"

    def test_sex_filter(self):
        from db import Database
        db = Database(dsn="postgresql://localhost/test")
        where, params = db._build_filter_clause({"sex_id": "M"})
        assert "sex_id" in where.lower()
        assert params["sex_id"] == "M"

    def test_name_search(self):
        from db import Database
        db = Database(dsn="postgresql://localhost/test")
        where, params = db._build_filter_clause({"name": "SMITH"})
        assert "ILIKE" in where
        assert params["name"] == "%SMITH%"

    def test_combined_filters(self):
        from db import Database
        db = Database(dsn="postgresql://localhost/test")
        where, params = db._build_filter_clause({
            "nationality": "FR",
            "sex_id": "M",
            "is_alarm_only": True,
        })
        assert "AND" in where
        assert params["nationality"] == "FR"
        assert params["sex_id"] == "M"
        assert "is_alarm" in where.lower()

    def test_charges_keyword(self):
        from db import Database
        db = Database(dsn="postgresql://localhost/test")
        where, params = db._build_filter_clause({"charges": "murder"})
        assert "ILIKE" in where
        assert params["charges"] == "%murder%"

    def test_issuing_country(self):
        from db import Database
        db = Database(dsn="postgresql://localhost/test")
        where, params = db._build_filter_clause({"issuing_country": "US"})
        assert "issuing_country_id" in where
        assert params["issuing_country"] == "US"
