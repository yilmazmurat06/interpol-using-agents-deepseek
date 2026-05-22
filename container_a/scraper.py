"""
Two-phase Interpol Red Notice scraper.

Phase 1: Nationality-sweep across ISO-2 country codes to collect all entity_ids,
         sub-slicing by sexId x ageMin/ageMax for high-volume countries.
Phase 2: Fetch full detail for each entity_id via the individual endpoint,
         publishing records as they are built via an on_record callback.

All outbound HTTP uses curl_cffi with browser TLS impersonation.
Circuit breaker pauses all requests after N consecutive 403s.
"""

import logging
import os
import random
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from curl_cffi import requests  # impersonate=chrome120 for Akamai TLS

logger = logging.getLogger("scraper")

# Comprehensive ISO-2 country codes for nationality sweep.
# Derived from ISO 3166-1 alpha-2, includes all sovereign states and
# territories that Interpol may recognise.
ISO2_COUNTRIES: List[str] = sorted([
    "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT", "AU", "AW", "AX",
    "AZ", "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN", "BO", "BQ",
    "BR", "BS", "BT", "BV", "BW", "BY", "BZ", "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK",
    "CL", "CM", "CN", "CO", "CR", "CU", "CV", "CW", "CX", "CY", "CZ", "DE", "DJ", "DK", "DM",
    "DO", "DZ", "EC", "EE", "EG", "EH", "ER", "ES", "ET", "FI", "FJ", "FK", "FM", "FO", "FR",
    "GA", "GB", "GD", "GE", "GF", "GG", "GH", "GI", "GL", "GM", "GN", "GP", "GQ", "GR", "GS",
    "GT", "GU", "GW", "GY", "HK", "HM", "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IM", "IN",
    "IO", "IQ", "IR", "IS", "IT", "JE", "JM", "JO", "JP", "KE", "KG", "KH", "KI", "KM", "KN",
    "KP", "KR", "KW", "KY", "KZ", "LA", "LB", "LC", "LI", "LK", "LR", "LS", "LT", "LU", "LV",
    "LY", "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK", "ML", "MM", "MN", "MO", "MP", "MQ",
    "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA", "NC", "NE", "NF", "NG", "NI",
    "NL", "NO", "NP", "NR", "NU", "NZ", "OM", "PA", "PE", "PF", "PG", "PH", "PK", "PL", "PM",
    "PN", "PR", "PS", "PT", "PW", "PY", "QA", "RE", "RO", "RS", "RU", "RW", "SA", "SB", "SC",
    "SD", "SE", "SG", "SH", "SI", "SJ", "SK", "SL", "SM", "SN", "SO", "SR", "SS", "ST", "SV",
    "SX", "SY", "SZ", "TC", "TD", "TF", "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO", "TR",
    "TT", "TV", "TW", "TZ", "UA", "UG", "UM", "US", "UY", "UZ", "VA", "VC", "VE", "VG", "VI",
    "VN", "VU", "WF", "WS", "XK", "YE", "YT", "ZA", "ZM", "ZW",
])


class CircuitBreaker:
    """Tracks consecutive HTTP 403 errors and enforces a global pause when the threshold is crossed."""

    def __init__(self, failure_threshold: int = 5, pause_seconds: float = 600.0):
        self._failure_threshold = failure_threshold
        self._pause_seconds = pause_seconds
        self._consecutive_failures: int = 0
        self._circuit_open_until: float = 0.0
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        with self._lock:
            if self._circuit_open_until == 0.0:
                return False
            if time.time() < self._circuit_open_until:
                return True
            # Pause expired — reset
            self._circuit_open_until = 0.0
            self._consecutive_failures = 0
            logger.info("Circuit breaker pause expired — resuming requests")
            return False

    def wait_if_open(self):
        """Block the caller until the circuit closes."""
        while True:
            with self._lock:
                if self._circuit_open_until == 0.0 or time.time() >= self._circuit_open_until:
                    return
                remaining = self._circuit_open_until - time.time()
            logger.warning("Circuit breaker open — pausing for %.0f more seconds", remaining)
            time.sleep(min(remaining, 30.0))

    def record_success(self):
        with self._lock:
            self._consecutive_failures = 0

    def record_403(self):
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._circuit_open_until = time.time() + self._pause_seconds
                logger.critical(
                    "Circuit breaker TRIPPED — %d consecutive 403s. Pausing all requests for %.0f seconds",
                    self._consecutive_failures,
                    self._pause_seconds,
                )


class Scraper:
    """Two-phase scraper for Interpol Red Notice data."""

    def __init__(
        self,
        base_url: str = "https://ws-public.interpol.int",
        jitter_min: float = 0.5,
        jitter_max: float = 1.2,
        concurrency: int = 4,
        max_retries: int = 3,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._jitter_min = jitter_min
        self._jitter_max = jitter_max
        self._concurrency = concurrency
        self._max_retries = max_retries
        self._circuit_breaker = circuit_breaker or CircuitBreaker()

        # curl_cffi session with browser TLS impersonation.
        # IMPORTANT: do NOT set a User-Agent header — the impersonation sets one
        # automatically that matches the spoofed TLS fingerprint.
        self._session = requests.Session(impersonate="chrome120")
        self._session.timeout = 30.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _jitter(self):
        """Sleep for a random interval between jitter_min and jitter_max seconds."""
        delay = random.uniform(self._jitter_min, self._jitter_max)
        time.sleep(delay)

    def _entity_id_to_url_path(self, entity_id: str) -> str:
        """Convert '2026/10847' → '2026-10847' for the API URL path."""
        return entity_id.replace("/", "-")

    def _fetch_json(
        self, url: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        GET *url*, return parsed JSON, with jitter, retry, and circuit breaker.

        Returns None on unrecoverable failure (after max retries).
        Raises on unexpected error types.
        """
        self._circuit_breaker.wait_if_open()

        last_exception: Optional[Exception] = None
        for attempt in range(1, self._max_retries + 1):
            # Jitter before EVERY attempt (including first) — callers
            # must NOT add their own jitter or the rate will double.
            self._jitter()
            try:
                resp = self._session.get(url, params=params)
                status = resp.status_code

                if status == 403:
                    logger.warning("HTTP 403 on %s (attempt %d/%d)", url, attempt, self._max_retries)
                    # Only count the FIRST 403 per request toward the circuit breaker.
                    # Retries on the same blocked request artificially inflate the count.
                    if attempt == 1:
                        self._circuit_breaker.record_403()
                    if attempt < self._max_retries:
                        # Longer backoff after a 403 — IP may be temporarily blocked
                        backoff = 3.0 + random.uniform(1.0, 3.0)
                        logger.debug("Backing off %.1fs after 403", backoff)
                        time.sleep(backoff)
                        continue
                    return None

                if status == 502:
                    # 502 = non-existent notice ID (see research constraints)
                    logger.debug("HTTP 502 on %s — treating as not-found", url)
                    return None

                # Any 2xx is success
                if 200 <= status < 300:
                    self._circuit_breaker.record_success()
                    try:
                        data = resp.json()
                    except Exception:
                        logger.error("Failed to parse JSON from %s", url)
                        return None
                    return data

                # Other 4xx/5xx
                logger.warning(
                    "HTTP %d on %s (attempt %d/%d)", status, url, attempt, self._max_retries
                )
                if attempt < self._max_retries:
                    backoff = 2 ** attempt
                    logger.debug("Retrying in %ds", backoff)
                    time.sleep(backoff)
                last_exception = Exception(f"HTTP {status} from {url}")

            except Exception as exc:
                # curl_cffi raises generic Exception — inspect for embedded response
                code = getattr(getattr(exc, "response", None), "status_code", None)
                if code == 403:
                    logger.warning("HTTP 403 (via exception) on %s (attempt %d/%d)", url, attempt, self._max_retries)
                    if attempt == 1:
                        self._circuit_breaker.record_403()
                    if attempt < self._max_retries:
                        backoff = 3.0 + random.uniform(1.0, 3.0)
                        time.sleep(backoff)
                        continue
                else:
                    logger.warning(
                        "Request error on %s (attempt %d/%d): %s", url, attempt, self._max_retries, exc
                    )
                    if attempt < self._max_retries:
                        continue
                last_exception = exc

        if last_exception:
            logger.error("All %d retries exhausted for %s", self._max_retries, url)
        return None

    def _parse_float(self, val: Any) -> Optional[float]:
        """Coerce to float, treating 0 as unknown (returns None)."""
        if val is None:
            return None
        try:
            f = float(val)
        except (TypeError, ValueError):
            return None
        if f == 0.0:
            return None
        return f

    def _parse_dob(self, val: Any) -> Optional[str]:
        """Normalise date_of_birth string — 'YYYY/MM/DD' or 'YYYY'."""
        if val is None:
            return None
        s = str(val).strip()
        if not s:
            return None
        # Handle year-only: "1972"
        if s.isdigit() and len(s) == 4:
            return f"{s}-01-01"
        # Convert slashes to dashes
        s = s.replace("/", "-")
        return s

    def _normalize_forename(self, val: Any) -> Optional[str]:
        """Normalise forename — treat dash placeholder '-' and empty string as None."""
        if val is None:
            return None
        s = str(val).strip()
        if s in ("", "-"):
            return None
        return s

    # ------------------------------------------------------------------
    # Phase 1: Nationality sweep
    # ------------------------------------------------------------------

    def _fetch_list_page(
        self,
        nationality: str,
        sex_id: Optional[str] = None,
        age_min: Optional[int] = None,
        age_max: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch ONE page of the list API for a specific nationality (+ optional sub-slice).
        Returns a list of notice summary dicts from _embedded.notices.
        Handles jitter and circuit breaker internally.
        """
        params: Dict[str, Any] = {
            "nationality": nationality,
            "resultPerPage": 160,
            "page": 1,
        }
        if sex_id:
            params["sexId"] = sex_id
        if age_min is not None:
            params["ageMin"] = age_min
        if age_max is not None:
            params["ageMax"] = age_max

        url = f"{self._base_url}/notices/v1/red"

        data = self._fetch_json(url, params)
        if data is None:
            return []

        embedded = data.get("_embedded", {})
        notices = embedded.get("notices", [])
        return notices

    def _sub_slice_country(
        self, nationality: str
    ) -> List[Dict[str, Any]]:
        """
        For a country that may have >160 notices, sub-slice by sex × age buckets
        to collect all records under the 160 cap.

        Uses sexId (M, F) × age buckets of 5-year windows.
        """
        all_notices: List[Dict[str, Any]] = []

        # Try unfiltered first
        notices = self._fetch_list_page(nationality)
        if len(notices) < 160:
            return notices

        logger.info(
            "Country %s returned %d records (at cap) — sub-slicing by sex×age",
            nationality, len(notices),
        )

        # We cannot trust the total from the query when at cap.
        # Sub-slice: sexId × ageMin/ageMax (5-year buckets, age 15–100)
        age_buckets = [(i, i + 4) for i in range(15, 100, 5)]  # 15-19, 20-24, ..., 95-99

        seen: Set[str] = set()
        for sex_id in ("M", "F"):
            for age_min, age_max in age_buckets:
                bucket_notices = self._fetch_list_page(
                    nationality, sex_id=sex_id, age_min=age_min, age_max=age_max
                )
                new_in_bucket = 0
                for n in bucket_notices:
                    eid = n.get("entity_id", "")
                    if eid and eid not in seen:
                        seen.add(eid)
                        all_notices.append(n)
                        new_in_bucket += 1

                logger.debug(
                    "  %s sex=%s age=%d-%d → %d (%d new)",
                    nationality, sex_id, age_min, age_max, len(bucket_notices), new_in_bucket,
                )

        # Also try without sex filter for any remaining edge cases (age-only)
        # This catches records where sex_id might be missing
        for age_min, age_max in age_buckets:
            bucket_notices = self._fetch_list_page(
                nationality, age_min=age_min, age_max=age_max
            )
            for n in bucket_notices:
                eid = n.get("entity_id", "")
                if eid and eid not in seen:
                    seen.add(eid)
                    all_notices.append(n)

        logger.info(
            "Country %s: collected %d unique notices via sub-slicing",
            nationality, len(all_notices),
        )
        return all_notices

    def _sweep_nationalities(self) -> List[Dict[str, Any]]:
        """
        Phase 1: Sweep all ISO-2 nationality codes and collect notice summaries.
        Returns a de-duplicated list of dicts with keys:
          entity_id, name, forename, nationalities, thumbnail_url, date_of_birth
        """
        all_notices: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        total_codes = len(ISO2_COUNTRIES)
        for idx, code in enumerate(ISO2_COUNTRIES, 1):

            logger.info("Sweeping nationality %s (%d/%d)", code, idx, total_codes)
            notices = self._sub_slice_country(code)

            new_count = 0
            for notice in notices:
                eid = notice.get("entity_id", "")
                if not eid or eid in seen:
                    continue
                seen.add(eid)

                thumbnail_url = ""
                tlink = notice.get("_links", {}).get("thumbnail", {})
                if isinstance(tlink, dict):
                    thumbnail_url = tlink.get("href", "") or ""

                all_notices.append({
                    "entity_id": eid,
                    "name": notice.get("name", ""),
                    "forename": self._normalize_forename(notice.get("forename")),
                    "nationalities": notice.get("nationalities", []),
                    "thumbnail_url": thumbnail_url,
                    "date_of_birth": notice.get("date_of_birth"),
                })
                new_count += 1

            logger.info(
                "  %s: %d new notices (running total: %d)",
                code, new_count, len(all_notices),
            )

        logger.info("Phase 1 complete: %d unique notices across %d nationalities", len(all_notices), total_codes)
        return all_notices

    # ------------------------------------------------------------------
    # Phase 2: Detail fetch
    # ------------------------------------------------------------------

    def _fetch_detail(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch full detail for a single notice.

        Returns a fully enriched dict, or None if unrecoverable.
        """
        url_path = self._entity_id_to_url_path(entity_id)
        url = f"{self._base_url}/notices/v1/red/{url_path}"

        data = self._fetch_json(url)
        if data is None:
            return None

        # Build enriched record
        arrest_warrants = data.get("arrest_warrants", [])
        if not isinstance(arrest_warrants, list):
            arrest_warrants = []

        # Extract thumbnail URL from detail response too (may differ from list)
        thumbnail_url = ""
        tlink = data.get("_links", {}).get("thumbnail", {})
        if isinstance(tlink, dict):
            thumbnail_url = tlink.get("href", "") or ""

        languages = data.get("languages_spoken_ids")
        if languages is None:
            languages = []
        elif not isinstance(languages, list):
            languages = [languages]

        eyes = data.get("eyes_colors_id")
        if not isinstance(eyes, list):
            eyes = []

        hairs = data.get("hairs_id")
        if not isinstance(hairs, list):
            hairs = []

        record: Dict[str, Any] = {
            "notice_id": entity_id,
            "forename": self._normalize_forename(data.get("forename")),
            "name": data.get("name", ""),
            "date_of_birth": self._parse_dob(data.get("date_of_birth")),
            "place_of_birth": data.get("place_of_birth"),
            "sex_id": data.get("sex_id"),
            "height": self._parse_float(data.get("height")),
            "weight": self._parse_float(data.get("weight")),
            "nationalities": data.get("nationalities", []),
            "languages": languages,
            "eyes_colors_id": eyes,
            "hairs_id": hairs,
            "distinguishing_marks": data.get("distinguishing_marks"),
            "arrest_warrants": arrest_warrants,
            "image_url": thumbnail_url,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        return record

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def scrape(
        self,
        on_record: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run the full two-phase scrape.

        Parameters:
            on_record: Called for each successfully fetched detail record as it
                       becomes available (streaming publish). Called under a lock
                       if the caller uses threading.

        Returns:
            All records collected (for testing / fallback).
        """
        # Phase 1
        logger.info("=== Phase 1: Nationality sweep ===")
        summaries = self._sweep_nationalities()
        logger.info("Phase 1 produced %d unique entity_ids", len(summaries))

        if not summaries:
            logger.warning("Phase 1 returned zero notices — aborting")
            return []

        # Phase 2
        logger.info("=== Phase 2: Detail fetch (%d notices, concurrency=%d) ===",
                     len(summaries), self._concurrency)

        records: List[Dict[str, Any]] = []
        completed = 0
        skipped = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=self._concurrency) as executor:
            future_to_eid = {
                executor.submit(self._fetch_detail, s["entity_id"]): s
                for s in summaries
            }

            for future in as_completed(future_to_eid):
                summary = future_to_eid[future]
                eid = summary["entity_id"]
                completed += 1

                try:
                    detail = future.result()
                except Exception as exc:
                    logger.error("Unhandled exception fetching %s: %s", eid, exc)
                    failed += 1
                    continue

                if detail is None:
                    logger.warning("Skipping %s — all retries exhausted", eid)
                    skipped += 1
                    continue

                # Merge list-level thumbnail_url if detail didn't provide one
                if not detail.get("image_url") and summary.get("thumbnail_url"):
                    detail["image_url"] = summary["thumbnail_url"]

                # Merge list-level nationalities if detail returned empty
                if not detail.get("nationalities") and summary.get("nationalities"):
                    detail["nationalities"] = summary["nationalities"]

                records.append(detail)

                if on_record:
                    on_record(detail)

                if completed % 50 == 0:
                    logger.info(
                        "Phase 2 progress: %d/%d (skipped=%d, failed=%d)",
                        completed, len(summaries), skipped, failed,
                    )

        logger.info(
            "Phase 2 complete: %d records, %d skipped, %d failed",
            len(records), skipped, failed,
        )
        return records


# ------------------------------------------------------------------
# Convenience: build from environment
# ------------------------------------------------------------------

def build_scraper_from_env() -> Scraper:
    """Instantiate a Scraper configured from environment variables."""
    return Scraper(
        base_url=os.environ.get("INTERPOL_SOURCE_URL", "https://ws-public.interpol.int"),
        jitter_min=float(os.environ.get("JITTER_MIN_SECONDS", "0.5")),
        jitter_max=float(os.environ.get("JITTER_MAX_SECONDS", "1.2")),
        concurrency=int(os.environ.get("SCRAPE_CONCURRENCY", "4")),
        max_retries=int(os.environ.get("SCRAPE_MAX_RETRIES", "3")),
        circuit_breaker=CircuitBreaker(
            failure_threshold=int(os.environ.get("CIRCUIT_BREAKER_THRESHOLD", "20")),
            pause_seconds=float(os.environ.get("CIRCUIT_BREAKER_PAUSE_SECONDS", "300")),
        ),
    )
