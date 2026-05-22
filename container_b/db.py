"""
PostgreSQL persistence layer for Red Notice records.

PSC-1: Every write method uses try/commit/except/rollback/raise.
Every read method ends with rollback() to release the implicit SELECT transaction.
The _cursor() helper clears INERROR state defensively.
"""

import logging
import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from psycopg2.extensions import TRANSACTION_STATUS_INERROR

logger = logging.getLogger("db")


class Database:
    """Thread-safe PostgreSQL database interface. Each instance owns its own connection."""

    def __init__(self, dsn: Optional[str] = None):
        self._dsn = dsn or os.environ.get("POSTGRES_DSN", "postgresql://postgres:postgres@postgres:5432/interpol")
        self._conn: Optional[psycopg2.extensions.connection] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self):
        """Establish connection and initialize schema."""
        self._conn = psycopg2.connect(self._dsn)
        self._conn.autocommit = False
        # Register JSONB adapter on this connection (NOT globally)
        psycopg2.extras.register_default_jsonb(conn_or_curs=self._conn, globally=False)
        self._init_schema()
        logger.info("Database connected and schema initialized")

    def _init_schema(self):
        """Create tables if they don't exist. All external API fields are nullable."""
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS red_notices (
                    notice_id       TEXT PRIMARY KEY,
                    forename        TEXT,
                    name            TEXT NOT NULL,
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
        self._conn.commit()

    # ------------------------------------------------------------------
    # Cursor helper (PSC-1: INERROR guard)
    # ------------------------------------------------------------------

    @contextmanager
    def _cursor(self):
        """
        Yield a psycopg2 cursor, pre-emptively rolling back if the connection
        is in TRANSACTION_STATUS_INERROR (PSC-1 defensive guard).
        """
        if self._conn is None:
            self.connect()
        try:
            status = self._conn.get_transaction_status()
        except Exception:
            # Connection dead — reconnect
            logger.warning("Connection lost — reconnecting")
            self.connect()
            status = self._conn.get_transaction_status()

        if status == TRANSACTION_STATUS_INERROR:
            logger.warning("Connection in INERROR state — rolling back before cursor handoff")
            self._conn.rollback()

        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Write operations (PSC-1: try/commit/except/rollback/raise)
    # ------------------------------------------------------------------

    def upsert_notice(self, record: Dict[str, Any]):
        """
        Insert or update a notice record.

        Sets is_alarm = TRUE only when meaningful fields change:
        name, forename, nationalities, arrest_warrants, date_of_birth, sex_id.

        PSC-1: try/commit/except/rollback/raise pattern.
        """
        notice_id = record["notice_id"]

        # Determine if this is an update and whether meaningful fields changed
        existing = self._get_notice_for_upsert(notice_id)
        is_new = existing is None

        alarm_fields = (
            "name", "forename", "nationalities", "arrest_warrants",
            "date_of_birth", "sex_id",
        )
        alarm_changed = False
        if not is_new:
            for field in alarm_fields:
                old_val = existing.get(field)
                new_val = record.get(field)
                if field in ("nationalities", "arrest_warrants", "languages", "eyes_colors_id", "hairs_id"):
                    # Compare lists/JSONB by value
                    if str(old_val) != str(new_val):
                        alarm_changed = True
                        break
                else:
                    if old_val != new_val:
                        alarm_changed = True
                        break

        try:
            with self._cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO red_notices (
                        notice_id, forename, name, date_of_birth, place_of_birth,
                        sex_id, height, weight, nationalities, languages,
                        eyes_colors_id, hairs_id, distinguishing_marks,
                        arrest_warrants, image_url, created_at, updated_at,
                        received_at, is_alarm
                    ) VALUES (
                        %(notice_id)s, %(forename)s, %(name)s, %(date_of_birth)s,
                        %(place_of_birth)s, %(sex_id)s, %(height)s, %(weight)s,
                        %(nationalities)s, %(languages)s, %(eyes_colors_id)s,
                        %(hairs_id)s, %(distinguishing_marks)s,
                        %(arrest_warrants)s::jsonb, %(image_url)s,
                        NOW(), NOW(), NOW(), FALSE
                    )
                    ON CONFLICT (notice_id) DO UPDATE SET
                        forename = EXCLUDED.forename,
                        name = EXCLUDED.name,
                        date_of_birth = EXCLUDED.date_of_birth,
                        place_of_birth = EXCLUDED.place_of_birth,
                        sex_id = EXCLUDED.sex_id,
                        height = EXCLUDED.height,
                        weight = EXCLUDED.weight,
                        nationalities = EXCLUDED.nationalities,
                        languages = EXCLUDED.languages,
                        eyes_colors_id = EXCLUDED.eyes_colors_id,
                        hairs_id = EXCLUDED.hairs_id,
                        distinguishing_marks = EXCLUDED.distinguishing_marks,
                        arrest_warrants = EXCLUDED.arrest_warrants,
                        image_url = EXCLUDED.image_url,
                        updated_at = NOW(),
                        received_at = NOW(),
                        is_alarm = CASE
                            WHEN red_notices.is_alarm = TRUE THEN TRUE
                            WHEN %(alarm_changed)s::boolean THEN TRUE
                            ELSE FALSE
                        END
                    """,
                    {
                        "notice_id": notice_id,
                        "forename": record.get("forename"),
                        "name": record.get("name", ""),
                        "date_of_birth": record.get("date_of_birth"),
                        "place_of_birth": record.get("place_of_birth"),
                        "sex_id": record.get("sex_id"),
                        "height": record.get("height"),
                        "weight": record.get("weight"),
                        "nationalities": record.get("nationalities", []),
                        "languages": record.get("languages", []),
                        "eyes_colors_id": record.get("eyes_colors_id", []),
                        "hairs_id": record.get("hairs_id", []),
                        "distinguishing_marks": record.get("distinguishing_marks"),
                        "arrest_warrants": psycopg2.extras.Json(record.get("arrest_warrants", [])),
                        "image_url": record.get("image_url"),
                        "alarm_changed": alarm_changed,
                    },
                )
            self._conn.commit()
            if is_new:
                logger.info("Inserted notice %s", notice_id)
            else:
                logger.info("Updated notice %s (alarm=%s)", notice_id, alarm_changed or existing.get("is_alarm"))
        except Exception:
            self._conn.rollback()
            logger.exception("Failed to upsert notice %s", notice_id)
            raise

    def _get_notice_for_upsert(self, notice_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a notice's current values for comparison during upsert.
        PSC-1: read — ends with rollback.
        """
        try:
            with self._cursor() as cur:
                cur.execute(
                    """
                    SELECT forename, name, date_of_birth, sex_id, nationalities, arrest_warrants, is_alarm
                    FROM red_notices WHERE notice_id = %s
                    """,
                    (notice_id,),
                )
                row = cur.fetchone()
        finally:
            if self._conn:
                self._conn.rollback()  # PSC-1: release implicit SELECT txn

        if row is None:
            return None
        return {
            "forename": row[0],
            "name": row[1],
            "date_of_birth": row[2],
            "sex_id": row[3],
            "nationalities": row[4],
            "arrest_warrants": row[5],
            "is_alarm": row[6],
        }

    # ------------------------------------------------------------------
    # Read operations (PSC-1: every read ends with rollback)
    # ------------------------------------------------------------------

    def get_notice(self, notice_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single notice by ID. PSC-1: ends with rollback."""
        try:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT row_to_json(t) FROM (SELECT * FROM red_notices WHERE notice_id = %s) t",
                    (notice_id,),
                )
                row = cur.fetchone()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._conn.rollback()
        return row[0] if row else None

    def count_notices(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Return the total number of notices matching optional filters. PSC-1: ends with rollback."""
        where_clause, params = self._build_filter_clause(filters)
        query = f"SELECT COUNT(*) FROM red_notices r {where_clause}"
        try:
            with self._cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._conn.rollback()
        return row[0] if row else 0

    def get_all_notices(
        self,
        filters: Optional[Dict[str, Any]] = None,
        offset: int = 0,
        limit: int = 20,
        order_by: str = "received_at",
        order_dir: str = "DESC",
    ) -> List[Dict[str, Any]]:
        """
        Return notices with optional filters, offset, limit, and sort.
        PSC-1: ends with rollback.

        PSC-4: accepts offset (derived from page/page_size).
        """
        valid_orders = {"received_at", "name", "nationality"}
        if order_by not in valid_orders:
            order_by = "received_at"
        order_dir_sql = "DESC" if order_dir.upper() == "DESC" else "ASC"

        where_clause, params = self._build_filter_clause(filters)

        query = f"""
            SELECT row_to_json(t) FROM (
                SELECT * FROM red_notices r
                {where_clause}
                ORDER BY
                    CASE WHEN %(order_by)s = 'name' THEN r.name END {order_dir_sql},
                    CASE WHEN %(order_by)s = 'received_at' THEN r.received_at END {order_dir_sql},
                    CASE WHEN %(order_by)s = 'nationality' THEN (r.nationalities)[1] END {order_dir_sql},
                    r.notice_id ASC
                OFFSET %(offset)s LIMIT %(limit)s
            ) t
        """
        params["order_by"] = order_by
        params["offset"] = offset
        params["limit"] = limit

        try:
            with self._cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._conn.rollback()

        return [r[0] for r in rows] if rows else []

    def get_filter_options(self) -> Dict[str, Any]:
        """
        Return distinct nationalities, issuing countries, sex options,
        and total_notices for dropdowns and live counter.
        PSC-1: ends with rollback.
        """
        options: Dict[str, Any] = {
            "nationalities": [],
            "issuing_countries": [],
            "sex_options": ["M", "F"],
            "total_notices": 0,
        }

        try:
            with self._cursor() as cur:
                # Distinct nationalities from the notices table
                cur.execute("""
                    SELECT DISTINCT unnest(nationalities) AS nat
                    FROM red_notices
                    WHERE nationalities IS NOT NULL
                    ORDER BY nat
                """)
                options["nationalities"] = [row[0] for row in cur.fetchall()]

                # Distinct issuing countries from arrest_warrants JSONB
                cur.execute("""
                    SELECT DISTINCT warrant->>'issuing_country_id' AS country
                    FROM red_notices,
                         jsonb_array_elements(COALESCE(arrest_warrants, '[]'::jsonb)) AS warrant
                    WHERE warrant->>'issuing_country_id' IS NOT NULL
                    ORDER BY country
                """)
                options["issuing_countries"] = [row[0] for row in cur.fetchall()]

                # Total notice count (unfiltered)
                cur.execute("SELECT COUNT(*) FROM red_notices")
                options["total_notices"] = cur.fetchone()[0]

        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._conn.rollback()

        return options

    # ------------------------------------------------------------------
    # Filter clause builder
    # ------------------------------------------------------------------

    def _build_filter_clause(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """Build a WHERE clause and parameter dict from a filter dict."""
        if not filters:
            return "", {}

        clauses: List[str] = []
        params: Dict[str, Any] = {}

        # Nationality filter
        if filters.get("nationality"):
            clauses.append("%(nationality)s = ANY(r.nationalities)")
            params["nationality"] = filters["nationality"]

        # Sex filter
        if filters.get("sex_id"):
            clauses.append("r.sex_id = %(sex_id)s")
            params["sex_id"] = filters["sex_id"]

        # Issuing country filter (from arrest_warrants JSONB)
        if filters.get("issuing_country"):
            clauses.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(r.arrest_warrants, '[]'::jsonb)) AS w "
                "WHERE w->>'issuing_country_id' = %(issuing_country)s)"
            )
            params["issuing_country"] = filters["issuing_country"]

        # Charges keyword filter
        if filters.get("charges"):
            clauses.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(r.arrest_warrants, '[]'::jsonb)) AS w "
                "WHERE w->>'charge' ILIKE %(charges)s)"
            )
            params["charges"] = f"%{filters['charges']}%"

        # Name search
        if filters.get("name"):
            clauses.append("(r.name ILIKE %(name)s OR r.forename ILIKE %(name)s)")
            params["name"] = f"%{filters['name']}%"

        # Alarm-only toggle
        if filters.get("is_alarm_only") in (True, "true", "1"):
            clauses.append("r.is_alarm = TRUE")

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        return where, params

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self):
        """Close the database connection."""
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
        logger.info("Database connection closed")
