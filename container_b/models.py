"""
RedNotice data model — shared structure used by DB, consumer, and API.

All fields that originate from the Interpol API are documented with their
nullability constraints per research/interpol-api-constraints.md.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ArrestWarrant:
    """Single arrest warrant from the Interpol detail API."""
    charge: Optional[str] = None
    issuing_country_id: Optional[str] = None
    charge_translation: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ArrestWarrant":
        return cls(
            charge=d.get("charge"),
            issuing_country_id=d.get("issuing_country_id"),
            charge_translation=d.get("charge_translation"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "charge": self.charge,
            "issuing_country_id": self.issuing_country_id,
            "charge_translation": self.charge_translation,
        }


@dataclass
class RedNotice:
    """
    Full Red Notice record.

    NOT NULL: notice_id, name, created_at, updated_at, is_alarm
    All other fields from the external API are nullable (per research hard rules).
    """
    notice_id: str  # PK — format "YYYY/NNNNN" (with slash)
    name: str  # NOT NULL

    # From external API — all nullable
    forename: Optional[str] = None  # null for single-name individuals
    date_of_birth: Optional[str] = None  # stored as ISO date string or null
    place_of_birth: Optional[str] = None
    sex_id: Optional[str] = None  # "M" or "F"
    height: Optional[float] = None  # meters; 0 → None
    weight: Optional[float] = None  # kg; 0 → None
    nationalities: List[str] = field(default_factory=list)  # ISO-2 codes
    languages: List[str] = field(default_factory=list)  # 3-letter codes
    eyes_colors_id: List[str] = field(default_factory=list)  # e.g. ["BLA"]
    hairs_id: List[str] = field(default_factory=list)  # e.g. ["BLA"]
    distinguishing_marks: Optional[str] = None
    arrest_warrants: List[Dict[str, Any]] = field(default_factory=list)
    image_url: Optional[str] = None

    # Internal fields
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    received_at: Optional[str] = None
    is_alarm: bool = False

    @classmethod
    def from_scraper_record(cls, record: Dict[str, Any]) -> "RedNotice":
        """
        Build a RedNotice from the scraper's enriched dict.
        Converts known sentinel values (0 → None for height/weight).
        """
        def _coerce_float(val: Any) -> Optional[float]:
            if val is None:
                return None
            try:
                f = float(val)
            except (TypeError, ValueError):
                return None
            return f if f != 0.0 else None

        return cls(
            notice_id=record.get("notice_id", ""),
            name=record.get("name", ""),
            forename=record.get("forename"),
            date_of_birth=record.get("date_of_birth"),
            place_of_birth=record.get("place_of_birth"),
            sex_id=record.get("sex_id"),
            height=_coerce_float(record.get("height")),
            weight=_coerce_float(record.get("weight")),
            nationalities=record.get("nationalities") or [],
            languages=record.get("languages") or [],
            eyes_colors_id=record.get("eyes_colors_id") or [],
            hairs_id=record.get("hairs_id") or [],
            distinguishing_marks=record.get("distinguishing_marks"),
            arrest_warrants=record.get("arrest_warrants") or [],
            image_url=record.get("image_url"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict suitable for JSON API responses."""
        return {
            "notice_id": self.notice_id,
            "forename": self.forename,
            "name": self.name,
            "date_of_birth": self.date_of_birth,
            "place_of_birth": self.place_of_birth,
            "sex_id": self.sex_id,
            "height": self.height,
            "weight": self.weight,
            "nationalities": self.nationalities,
            "languages": self.languages,
            "eyes_colors_id": self.eyes_colors_id,
            "hairs_id": self.hairs_id,
            "distinguishing_marks": self.distinguishing_marks,
            "arrest_warrants": self.arrest_warrants,
            "image_url": self.image_url,
            "created_at": str(self.created_at) if self.created_at else None,
            "updated_at": str(self.updated_at) if self.updated_at else None,
            "received_at": str(self.received_at) if self.received_at else None,
            "is_alarm": self.is_alarm,
        }


def calculate_age(date_of_birth: Optional[str]) -> Optional[int]:
    """Calculate age in years from an ISO date string or YYYY-MM-DD."""
    if not date_of_birth:
        return None
    try:
        # Try ISO date
        dob = datetime.strptime(date_of_birth, "%Y-%m-%d")
    except ValueError:
        # Just year (e.g. "1972-01-01" from scraper normalisation, or bare "1972")
        if len(date_of_birth) >= 4 and date_of_birth[:4].isdigit():
            dob = datetime(int(date_of_birth[:4]), 1, 1)
        else:
            return None
    today = datetime.now(timezone.utc)
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age
