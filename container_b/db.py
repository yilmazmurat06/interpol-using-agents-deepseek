"""PostgreSQL database layer for red notices.

Implements PSC-1: every write method wraps its body in
try/commit/except/rollback/raise.  Read methods rollback the implicit
SELECT txn.  _cursor() clears INERROR state defensively.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.extensions import TRANSACTION_STATUS_INERROR

from models import RedNotice

logger = logging.getLogger(__name__)

# Defaults
POSTGRES_DSN: str = os.environ.get(
    "POSTGRES_DSN",
    "host=postgres port=5432 dbname=interpol user=interpol password=interpol",
)


class Database:
    """Manages PostgreSQL connections and red-notice CRUD operations.

    Each instance owns its own psycopg2 connection — DO NOT share across
    threads.  The consumer thread and Flask request handlers each get
    their own ``Database`` instance.
    """

    def __init__(self, dsn: Optional[str] = None) -> None:
        self._dsn: str = dsn if dsn is not None else POSTGRES_DSN
        self._conn: Optional[psycopg2.extensions.connection] = None

    def connect(self) -> None:
        """Create the connection and ensure the schema exists."""
        self._conn = psycopg2.connect(self._dsn)
        self._conn.autocommit = False
        logger.info("Database connected: %s", self._dsn.split("password")[0] if "password" in self._dsn else self._dsn)
        self._ensure_schema()

    def close(self) -> None:
        """Close the connection gracefully."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            logger.info("Database connection closed.")

    # ------------------------------------------------------------------
    # PSC-1 helpers
    # ------------------------------------------------------------------

    def _cursor(self) -> Any:
        """Return a cursor, clearing INERROR state first if needed.

        This is THE defensive guard that prevents one bad row from
        poisoning the entire connection.
        """
        if self._conn is None or self._conn.closed:
            raise RuntimeError("Database not connected — call connect() first.")
        if self._conn.get_transaction_status() == TRANSACTION_STATUS_INERROR:
            logger.warning("Connection in INERROR state — rolling back before cursor.")
            self._conn.rollback()
        return self._conn.cursor()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Create tables if they don't exist.  All external API fields nullable."""
        cur = self._cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS red_notices (
                    notice_id       TEXT PRIMARY KEY,
                    name            TEXT NOT NULL,
                    forename        TEXT,
                    date_of_birth   TEXT,
                    place_of_birth  TEXT,
                    sex_id          TEXT,
                    height          DOUBLE PRECISION,
                    weight          DOUBLE PRECISION,
                    nationalities   TEXT[],
                    languages       TEXT[],
                    eyes_colors_id  TEXT[],
                    hairs_id        TEXT[],
                    distinguishing_marks TEXT,
                    arrest_warrants JSONB,
                    image_url       TEXT,
                    country_of_birth_id TEXT,
                    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
                    received_at     TIMESTAMP NOT NULL DEFAULT NOW(),
                    is_alarm        BOOLEAN NOT NULL DEFAULT FALSE
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_notices_nationalities
                    ON red_notices USING GIN (nationalities)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_notices_arrest_warrants
                    ON red_notices USING GIN (arrest_warrants)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_notices_is_alarm
                    ON red_notices (is_alarm)
            """)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Write operations (PSC-1: try/commit/except/rollback/raise)
    # ------------------------------------------------------------------

    def upsert_notice(
        self, payload: Dict[str, Any], raw_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Insert a new notice or update an existing one.

        Sets ``is_alarm = True`` ONLY when meaningful fields change
        (name, nationalities, arrest_warrants) — NOT image_url.

        Returns a dict with ``action`` ("insert" or "update") and ``is_alarm``.
        """
        # PSC-1: rollback on exception in except block below
        notice = RedNotice.from_enriched_payload(payload)
        cur = self._cursor()
        try:
            # Check if it already exists
            cur.execute(
                "SELECT arrest_warrants, nationalities, name, forename FROM red_notices WHERE notice_id = %s",
                (notice.notice_id,),
            )
            existing = cur.fetchone()

            if existing is None:
                # Insert new record
                cur.execute(
                    """
                    INSERT INTO red_notices (
                        notice_id, name, forename, date_of_birth, place_of_birth,
                        sex_id, height, weight, nationalities, languages,
                        eyes_colors_id, hairs_id, distinguishing_marks,
                        arrest_warrants, image_url, country_of_birth_id,
                        created_at, updated_at, received_at, is_alarm
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    """,
                    (
                        notice.notice_id,
                        notice.name,
                        notice.forename,
                        notice.date_of_birth,
                        notice.place_of_birth,
                        notice.sex_id,
                        notice.height,
                        notice.weight,
                        notice.nationalities,
                        notice.languages,
                        notice.eyes_colors_id,
                        notice.hairs_id,
                        notice.distinguishing_marks,
                        json.dumps(notice.arrest_warrants),
                        notice.image_url,
                        notice.country_of_birth_id,
                        notice.created_at,
                        notice.updated_at,
                        notice.received_at,
                        False,  # new records are not alarms
                    ),
                )
                self._conn.commit()
                logger.info("Inserted new notice: %s", notice.notice_id)
                return {"action": "insert", "notice_id": notice.notice_id, "is_alarm": False}
            else:
                # Check if meaningful fields changed
                old_warrants = existing[0] if existing[0] else []
                if isinstance(old_warrants, str):
                    old_warrants = json.loads(old_warrants)
                old_nationalities = existing[1] or []
                old_name = existing[2] or ""
                old_forename = existing[3]

                new_hash = notice.meaningful_hash()

                # Build a comparable hash from existing data
                import hashlib as hl
                import json as jmod

                existing_hash_parts = jmod.dumps(
                    {
                        "name": old_name,
                        "forename": old_forename,
                        "nationalities": sorted(old_nationalities),
                        "arrest_warrants": sorted(
                            (w.get("charge", ""), w.get("issuing_country_id", ""))
                            for w in old_warrants
                        ) if old_warrants else [],
                    },
                    sort_keys=True,
                    default=str,
                )
                old_hash = hl.sha256(existing_hash_parts.encode()).hexdigest()

                is_alarm = new_hash != old_hash

                cur.execute(
                    """
                    UPDATE red_notices SET
                        name = %s, forename = %s, date_of_birth = %s,
                        place_of_birth = %s, sex_id = %s, height = %s,
                        weight = %s, nationalities = %s, languages = %s,
                        eyes_colors_id = %s, hairs_id = %s,
                        distinguishing_marks = %s, arrest_warrants = %s,
                        image_url = %s, country_of_birth_id = %s,
                        updated_at = %s, received_at = %s,
                        is_alarm = %s
                    WHERE notice_id = %s
                    """,
                    (
                        notice.name,
                        notice.forename,
                        notice.date_of_birth,
                        notice.place_of_birth,
                        notice.sex_id,
                        notice.height,
                        notice.weight,
                        notice.nationalities,
                        notice.languages,
                        notice.eyes_colors_id,
                        notice.hairs_id,
                        notice.distinguishing_marks,
                        json.dumps(notice.arrest_warrants),
                        notice.image_url,
                        notice.country_of_birth_id,
                        notice.updated_at,
                        notice.received_at,
                        is_alarm,
                        notice.notice_id,
                    ),
                )
                self._conn.commit()
                action_label = "updated (ALARM)" if is_alarm else "updated"
                logger.info(
                    "%s notice: %s", action_label, notice.notice_id
                )
                return {
                    "action": "update",
                    "notice_id": notice.notice_id,
                    "is_alarm": is_alarm,
                }
        except Exception:
            self._conn.rollback()
            logger.exception("upsert_notice failed for %s", notice.notice_id)
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Read operations (rollback implicit SELECT txn afterward)
    # ------------------------------------------------------------------

    def get_notice(self, notice_id: str) -> Optional[Dict[str, Any]]:
        """Return a single notice as a dict, or None."""
        cur = self._cursor()
        try:
            cur.execute(
                "SELECT * FROM red_notices WHERE notice_id = %s",
                (notice_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return self._row_to_dict(row, cur)
        finally:
            self._conn.rollback()
            cur.close()

    def get_all_notices(
        self,
        page: int = 1,
        page_size: int = 20,
        nationality: Optional[str] = None,
        sex_id: Optional[str] = None,
        issuing_country: Optional[str] = None,
        charges: Optional[str] = None,
        is_alarm_only: bool = False,
        sort: str = "newest",
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return filtered, paginated notice list.

        Sorting supports: newest (received_at DESC), name_asc, nationality_asc.
        """
        cur = self._cursor()
        try:
            conditions: List[str] = []
            params: List[Any] = []

            if nationality:
                conditions.append("%s = ANY(nationalities)")
                params.append(nationality)

            if sex_id:
                conditions.append("sex_id = %s")
                params.append(sex_id)

            if issuing_country:
                # Check inside arrest_warrants JSONB array
                conditions.append(
                    "EXISTS (SELECT 1 FROM jsonb_array_elements(arrest_warrants) AS w WHERE w->>'issuing_country_id' = %s)"
                )
                params.append(issuing_country)

            if charges:
                conditions.append(
                    "EXISTS (SELECT 1 FROM jsonb_array_elements(arrest_warrants) AS w WHERE w->>'charge' ILIKE %s)"
                )
                params.append(f"%{charges}%")

            if is_alarm_only:
                conditions.append("is_alarm = TRUE")

            if search:
                conditions.append(
                    "(name ILIKE %s OR forename ILIKE %s)"
                )
                params.extend([f"%{search}%", f"%{search}%"])

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            # Order
            order_clause = "ORDER BY received_at DESC"
            if sort == "name_asc":
                order_clause = "ORDER BY name ASC"
            elif sort == "nationality_asc":
                order_clause = "ORDER BY nationalities[1] ASC NULLS LAST"

            offset = (page - 1) * page_size
            query = f"""
                SELECT * FROM red_notices
                {where_clause}
                {order_clause}
                LIMIT %s OFFSET %s
            """
            params.extend([page_size, offset])

            cur.execute(query, params)
            rows = cur.fetchall()
            return [self._row_to_dict(row, cur) for row in rows]
        finally:
            self._conn.rollback()
            cur.close()

    def count_notices(
        self,
        nationality: Optional[str] = None,
        sex_id: Optional[str] = None,
        issuing_country: Optional[str] = None,
        charges: Optional[str] = None,
        is_alarm_only: bool = False,
        search: Optional[str] = None,
    ) -> int:
        """Return count of notices matching filters (for pagination totals)."""
        cur = self._cursor()
        try:
            conditions: List[str] = []
            params: List[Any] = []

            if nationality:
                conditions.append("%s = ANY(nationalities)")
                params.append(nationality)

            if sex_id:
                conditions.append("sex_id = %s")
                params.append(sex_id)

            if issuing_country:
                conditions.append(
                    "EXISTS (SELECT 1 FROM jsonb_array_elements(arrest_warrants) AS w WHERE w->>'issuing_country_id' = %s)"
                )
                params.append(issuing_country)

            if charges:
                conditions.append(
                    "EXISTS (SELECT 1 FROM jsonb_array_elements(arrest_warrants) AS w WHERE w->>'charge' ILIKE %s)"
                )
                params.append(f"%{charges}%")

            if is_alarm_only:
                conditions.append("is_alarm = TRUE")

            if search:
                conditions.append(
                    "(name ILIKE %s OR forename ILIKE %s)"
                )
                params.extend([f"%{search}%", f"%{search}%"])

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            query = f"SELECT COUNT(*) FROM red_notices {where_clause}"
            cur.execute(query, params)
            result = cur.fetchone()
            return result[0] if result else 0
        finally:
            self._conn.rollback()
            cur.close()

    def get_filter_options(self) -> Dict[str, Any]:
        """Return distinct nationalities, issuing countries, sex options, and total_notices."""
        cur = self._cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM red_notices")
            total_notices = cur.fetchone()[0] if cur.rowcount is not None else 0

            # Distinct nationalities from nationalities array
            cur.execute(
                """
                SELECT DISTINCT unnest(nationalities) AS nat
                FROM red_notices
                WHERE nationalities IS NOT NULL
                ORDER BY nat
                """
            )
            nationalities = [row[0] for row in cur.fetchall()]

            # Distinct issuing countries from arrest_warrants JSONB
            cur.execute(
                """
                SELECT DISTINCT w->>'issuing_country_id' AS ic
                FROM red_notices, jsonb_array_elements(arrest_warrants) AS w
                WHERE w->>'issuing_country_id' IS NOT NULL
                ORDER BY ic
                """
            )
            issuing_countries = [row[0] for row in cur.fetchall()]

            return {
                "nationalities": nationalities,
                "issuing_countries": issuing_countries,
                "sex_options": [{"value": "", "label": "All"}, {"value": "M", "label": "Male"}, {"value": "F", "label": "Female"}],
                "total_notices": total_notices,
            }
        finally:
            self._conn.rollback()
            cur.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: Any, cur: Any) -> Dict[str, Any]:
        """Convert a psycopg2 row to a dictionary using column names."""
        cols = [desc[0] for desc in cur.description]
        d = dict(zip(cols, row))
        # Ensure JSONB field is parsed
        if isinstance(d.get("arrest_warrants"), str):
            try:
                d["arrest_warrants"] = json.loads(d["arrest_warrants"])
            except (json.JSONDecodeError, TypeError):
                d["arrest_warrants"] = []
        if d["arrest_warrants"] is None:
            d["arrest_warrants"] = []
        # Serialise datetime fields
        for dt_field in ("created_at", "updated_at", "received_at"):
            val = d.get(dt_field)
            if isinstance(val, datetime):
                d[dt_field] = val.isoformat()
        return d
