"""Data model for a Red Notice record.

Lives here so both db.py and app.py can import without circular deps.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class RedNotice:
    """Full Interpol Red Notice record.

    All external API fields are nullable except notice_id and name.
    """
    notice_id: str
    name: str
    forename: Optional[str] = None
    date_of_birth: Optional[str] = None
    place_of_birth: Optional[str] = None
    sex_id: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    nationalities: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    eyes_colors_id: List[str] = field(default_factory=list)
    hairs_id: List[str] = field(default_factory=list)
    distinguishing_marks: Optional[str] = None
    arrest_warrants: List[Dict[str, Any]] = field(default_factory=list)
    image_url: Optional[str] = None
    country_of_birth_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    is_alarm: bool = False

    @classmethod
    def from_enriched_payload(cls, payload: Dict[str, Any]) -> "RedNotice":
        """Build a RedNotice from the scraper's enriched payload dict."""
        now = datetime.now(timezone.utc)
        forename = payload.get("forename")
        if forename == "-" or forename == "" or forename is None:
            forename = None
        return cls(
            notice_id=payload["notice_id"],
            name=payload.get("name", ""),
            forename=forename,
            date_of_birth=payload.get("date_of_birth"),
            place_of_birth=payload.get("place_of_birth"),
            sex_id=payload.get("sex_id"),
            height=float(payload["height"]) if payload.get("height") else None,
            weight=float(payload["weight"]) if payload.get("weight") else None,
            nationalities=payload.get("nationalities") or [],
            languages=payload.get("languages") or [],
            eyes_colors_id=payload.get("eyes_colors_id") or [],
            hairs_id=payload.get("hairs_id") or [],
            distinguishing_marks=payload.get("distinguishing_marks"),
            arrest_warrants=payload.get("arrest_warrants") or [],
            image_url=payload.get("image_url"),
            country_of_birth_id=payload.get("country_of_birth_id"),
            created_at=now,
            updated_at=now,
            received_at=now,
            is_alarm=False,
        )

    def meaningful_hash(self) -> str:
        """Return a hash of fields that matter for alarm detection.

        Excludes image_url, created_at, updated_at, received_at, is_alarm.
        """
        import hashlib
        import json

        parts = json.dumps(
            {
                "name": self.name,
                "forename": self.forename,
                "nationalities": sorted(self.nationalities),
                "arrest_warrants": sorted(
                    (w.get("charge", ""), w.get("issuing_country_id", ""))
                    for w in self.arrest_warrants
                ),
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(parts.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dict suitable for JSON responses."""
        return {
            "notice_id": self.notice_id,
            "name": self.name,
            "forename": self.forename,
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
            "country_of_birth_id": self.country_of_birth_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "is_alarm": self.is_alarm,
        }
