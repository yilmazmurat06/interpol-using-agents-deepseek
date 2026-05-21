"""
Two-phase Interpol Red Notice scraper.

Phase 1: Sweep notice IDs by ISO-2 nationality (the unfiltered API caps at 160 records).
    Sub-slice high-volume countries by sexId x ageMin/ageMax.
Phase 2: For each deduplicated ID, fetch full detail from the individual endpoint.
    Jitter between ALL requests. Circuit breaker on sustained 403s.

All HTTP against ws-public.interpol.int uses curl_cffi with impersonate="chrome120".
"""

import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Set

from curl_cffi import requests as curl_requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — pulled from env with safe defaults
# ---------------------------------------------------------------------------

INTERPOL_BASE_URL: str = os.environ.get(
    "INTERPOL_BASE_URL", "https://ws-public.interpol.int"
)

LIST_PATH: str = os.environ.get(
    "INTERPOL_LIST_PATH", "/notices/v1/red"
)
DETAIL_PATH_TEMPLATE: str = os.environ.get(
    "INTERPOL_DETAIL_PATH", "/notices/v1/red/{entity_id}"
)

JITTER_MIN: float = float(os.environ.get("JITTER_MIN_SECONDS", "1.0"))
JITTER_MAX: float = float(os.environ.get("JITTER_MAX_SECONDS", "3.5"))

SCRAPE_CONCURRENCY: int = int(os.environ.get("SCRAPE_CONCURRENCY", "4"))
MAX_RETRIES: int = int(os.environ.get("SCRAPE_MAX_RETRIES", "3"))
CIRCUIT_BREAKER_THRESHOLD: int = int(
    os.environ.get("CIRCUIT_BREAKER_THRESHOLD", "5")
)
CIRCUIT_BREAKER_PAUSE: int = int(
    os.environ.get("CIRCUIT_BREAKER_PAUSE_SECONDS", "600")
)

# Narrow age buckets for sub-slicing high-volume countries (~3 years wide)
AGE_BUCKET_WIDTH: int = 3
MIN_AGE: int = 18
MAX_AGE: int = 120

# ISO-2 country codes to sweep (comprehensive list)
COUNTRY_CODES: List[str] = [
    code.strip()
    for code in os.environ.get(
        "SCRAPE_NATIONALITY_CODES",
        (
            "AF,AL,DZ,AS,AD,AO,AI,AQ,AG,AR,AM,AW,AU,AT,AZ,BS,BH,BD,BB,BY,BE,"
            "BZ,BJ,BM,BT,BO,BQ,BA,BW,BR,VG,BN,BG,BF,BI,KH,CM,CA,CV,KY,CF,TD,"
            "CL,CN,CO,KM,CG,CK,CR,CI,HR,CU,CW,CY,CZ,CD,DK,DJ,DM,DO,EC,EG,SV,"
            "GQ,ER,EE,ET,FK,FO,FJ,FI,FR,GF,PF,GA,GM,GE,DE,GH,GI,GR,GL,GD,GP,"
            "GU,GT,GG,GN,GW,GY,HT,HN,HK,HU,IS,IN,ID,IR,IQ,IE,IL,IT,JM,JP,JE,"
            "JO,KZ,KE,KI,XK,KW,KG,LA,LV,LB,LS,LR,LY,LI,LT,LU,MO,MK,MG,MW,MY,"
            "MV,ML,MT,MQ,MR,MU,YT,MX,FM,MD,MC,MN,ME,MS,MA,MZ,MM,NA,NP,NL,NC,"
            "NZ,NI,NE,NG,NU,NO,OM,PK,PS,PA,PG,PY,PE,PH,PL,PT,PR,QA,RO,RU,RW,"
            "RE,KN,LC,VC,WS,SM,ST,SA,SN,RS,SC,SL,SG,SK,SI,SB,SO,ZA,KR,SS,ES,"
            "LK,SD,SR,SZ,SE,CH,SY,TW,TJ,TZ,TH,BS,GM,TL,TG,TO,TT,TN,TR,TM,TC,"
            "UG,UA,AE,GB,US,UY,UZ,VU,VA,VE,VN,YE,ZM,ZW"
        ),
    ).split(",")
    if code.strip()
]

# ---------------------------------------------------------------------------
# Circuit breaker — shared state across all workers
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Tracks consecutive 403s and opens the circuit when threshold is reached."""

    def __init__(self, threshold: int = 5, pause_seconds: int = 600) -> None:
        self._threshold = threshold
        self._pause = pause_seconds
        self._lock = threading.Lock()
        self._consecutive_403s: int = 0
        self._open_until: float = 0.0

    def is_open(self) -> bool:
        with self._lock:
            if self._open_until > time.time():
                return True
            if time.time() >= self._open_until and self._open_until > 0:
                # circuit closed after pause — reset counter
                self._consecutive_403s = 0
                self._open_until = 0.0
            return False

    def record_403(self) -> None:
        with self._lock:
            self._consecutive_403s += 1
            if self._consecutive_403s >= self._threshold:
                logger.warning(
                    "Circuit breaker OPEN — %d consecutive 403s. "
                    "Pausing all requests for %d seconds.",
                    self._consecutive_403s,
                    self._pause,
                )
                self._open_until = time.time() + self._pause

    def record_success(self) -> None:
        with self._lock:
            if self._open_until <= 0:
                self._consecutive_403s = 0

    @property
    def remaining_pause_seconds(self) -> float:
        with self._lock:
            return max(0.0, self._open_until - time.time())


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------


class RedNoticeScraper:
    """Two-phase Interpol red notice scraper.

    Phase 1: Sweep nationality codes, sub-slicing by sex + age for high-volume
              countries, deduplicating by entity_id.
    Phase 2: For each entity_id, fetch full detail from individual endpoint.
              Jitter between ALL calls. Circuit breaker on sustained 403s.

    The scraper accepts an ``on_record`` callback that receives each
    enriched notice dict as soon as it is built (streaming publish).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        list_path: Optional[str] = None,
        detail_path_template: Optional[str] = None,
        jitter_min: Optional[float] = None,
        jitter_max: Optional[float] = None,
        concurrency: Optional[int] = None,
        max_retries: Optional[int] = None,
        circuit_breaker_threshold: Optional[int] = None,
        circuit_breaker_pause: Optional[int] = None,
        country_codes: Optional[List[str]] = None,
    ) -> None:
        self._base_url = (base_url or INTERPOL_BASE_URL).rstrip("/")
        self._list_path = list_path or LIST_PATH
        self._detail_path_template = detail_path_template or DETAIL_PATH_TEMPLATE
        self._jitter_min = jitter_min if jitter_min is not None else JITTER_MIN
        self._jitter_max = jitter_max if jitter_max is not None else JITTER_MAX
        self._concurrency = concurrency if concurrency is not None else SCRAPE_CONCURRENCY
        self._max_retries = max_retries if max_retries is not None else MAX_RETRIES
        self._circuit_breaker = CircuitBreaker(
            threshold=circuit_breaker_threshold
            if circuit_breaker_threshold is not None
            else CIRCUIT_BREAKER_THRESHOLD,
            pause_seconds=circuit_breaker_pause
            if circuit_breaker_pause is not None
            else CIRCUIT_BREAKER_PAUSE,
        )
        self._country_codes = country_codes if country_codes is not None else COUNTRY_CODES
        self._session = curl_requests.Session(impersonate="chrome120")
        # Shared set for deduplication across workers
        self._seen_ids: Set[str] = set()
        self._seen_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _jitter(self) -> None:
        """Sleep for a random duration between jitter_min and jitter_max seconds."""
        delay = random.uniform(self._jitter_min, self._jitter_max)
        time.sleep(delay)

    def _wait_if_circuit_open(self) -> None:
        """Block until the circuit breaker closes (or time passes)."""
        while self._circuit_breaker.is_open():
            remaining = self._circuit_breaker.remaining_pause_seconds
            logger.info(
                "Circuit breaker is open — waiting %.0f seconds ...", remaining
            )
            time.sleep(min(10, remaining))

    def _is_already_seen(self, entity_id: str) -> bool:
        with self._seen_lock:
            if entity_id in self._seen_ids:
                return True
            self._seen_ids.add(entity_id)
            return False

    # ------------------------------------------------------------------
    # HTTP with retry + circuit breaker
    # ------------------------------------------------------------------

    def _http_get(
        self, url: str
    ) -> Optional[Dict[str, Any]]:
        """GET *url* with retry, circuit breaker, and jitter.

        Returns parsed JSON dict on success, None on permanent failure.
        """
        for attempt in range(1, self._max_retries + 1):
            self._wait_if_circuit_open()
            self._jitter()

            try:
                resp = self._session.get(url, timeout=30)
                status_code = getattr(resp, "status_code", 0)

                if status_code == 403:
                    logger.warning(
                        "HTTP 403 on %s (attempt %d/%d)", url, attempt, self._max_retries
                    )
                    self._circuit_breaker.record_403()
                    if attempt < self._max_retries:
                        time.sleep(2 ** attempt)
                        continue
                    return None

                self._circuit_breaker.record_success()

                if status_code == 200:
                    try:
                        return resp.json()  # type: ignore[no-any-return]
                    except Exception:
                        logger.warning("Failed to parse JSON from %s", url)
                        return None

                # Non-200, non-403
                logger.warning(
                    "HTTP %d on %s (attempt %d/%d)",
                    status_code,
                    url,
                    attempt,
                    self._max_retries,
                )
                if attempt < self._max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return None

            except Exception as exc:
                logger.warning(
                    "Request error on %s (attempt %d/%d): %s",
                    url,
                    attempt,
                    self._max_retries,
                    exc,
                )
                if attempt < self._max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return None

        return None

    # ------------------------------------------------------------------
    # API URL helpers
    # ------------------------------------------------------------------

    def _list_url(self, extra_params: Optional[Dict[str, str]] = None) -> str:
        base = f"{self._base_url}{self._list_path}?resultPerPage=160"
        if extra_params:
            for key, value in extra_params.items():
                base += f"&{key}={value}"
        return base

    def _detail_url(self, entity_id: str) -> str:
        """Build detail URL — entity_id uses dashes in the URL path."""
        dashed_id = entity_id.replace("/", "-")
        path = self._detail_path_template.format(entity_id=dashed_id)
        return f"{self._base_url}{path}"

    # ------------------------------------------------------------------
    # Phase 1 helpers
    # ------------------------------------------------------------------

    def _list_filtered(
        self, nationality: Optional[str] = None,
        sex_id: Optional[str] = None,
        age_min: Optional[int] = None,
        age_max: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Call the list endpoint with optional filters. Returns notices list."""
        extra: Dict[str, str] = {}
        if nationality:
            extra["nationality"] = nationality
        if sex_id:
            extra["sexId"] = sex_id
        if age_min is not None:
            extra["ageMin"] = str(age_min)
        if age_max is not None:
            extra["ageMax"] = str(age_max)

        url = self._list_url(extra)
        data = self._http_get(url)
        if not data:
            return []

        notices = (
            data.get("_embedded", {}).get("notices", [])
            if isinstance(data, dict)
            else []
        )
        total = data.get("total", 0) if isinstance(data, dict) else 0
        logger.debug(
            "List query nationality=%s sex=%s age=[%s,%s] → total=%d returned=%d",
            nationality, sex_id, age_min, age_max, total, len(notices),
        )
        return notices

    def _extract_list_fields(
        self, raw: Dict[str, Any]
    ) -> Optional[str]:
        """Extract entity_id from a list record. Returns entity_id or None."""
        entity_id = raw.get("entity_id")
        if not entity_id:
            return None
        return str(entity_id)

    def _collect_entity_ids(self) -> List[str]:
        """Phase 1: sweep nationality codes, sub-slice by sex+age, dedupe.

        Returns a deduplicated list of entity_ids.
        """
        all_ids: List[str] = []
        total_countries = len(self._country_codes)
        logger.info(
            "Phase 1: sweeping %d nationalities for entity IDs ...",
            total_countries,
        )

        for idx, country in enumerate(self._country_codes):
            logger.info(
                "Phase 1: nationality %s (%d/%d)", country, idx + 1, total_countries
            )
            data = self._http_get(self._list_url({"nationality": country}))
            if not data or not isinstance(data, dict):
                logger.warning("  Failed to fetch list for %s — skipping", country)
                continue

            total = data.get("total", 0)
            notices = (
                data.get("_embedded", {}).get("notices", [])
                if isinstance(data, dict)
                else []
            )

            logger.debug("  %s: total=%d, in_first_page=%d", country, total, len(notices))

            if total == 0:
                continue

            if total <= 160:
                # Fits in one page — collect all returned IDs
                for raw in notices:
                    eid = self._extract_list_fields(raw)
                    if eid and not self._is_already_seen(eid):
                        all_ids.append(eid)
            else:
                # More than 160 — need to sub-slice
                logger.info(
                    "  %s has total=%d > 160 — sub-slicing by sex × age buckets",
                    country, total,
                )
                self._sub_slice_country(country, all_ids)

        logger.info("Phase 1 complete: collected %d unique entity IDs", len(all_ids))
        return all_ids

    def _sub_slice_country(
        self, country: str, collector: List[str]
    ) -> None:
        """Sub-slice a high-volume country by sexId × ageMin/ageMax buckets."""
        for sex_id in ("M", "F"):
            for age_start in range(MIN_AGE, MAX_AGE, AGE_BUCKET_WIDTH):
                age_end = age_start + AGE_BUCKET_WIDTH - 1
                notices = self._list_filtered(
                    nationality=country,
                    sex_id=sex_id,
                    age_min=age_start,
                    age_max=age_end,
                )
                count = 0
                for raw in notices:
                    eid = self._extract_list_fields(raw)
                    if eid and not self._is_already_seen(eid):
                        collector.append(eid)
                        count += 1
                if count > 0:
                    logger.debug(
                        "    %s/%s age %d-%d → %d new IDs",
                        country, sex_id, age_start, age_end, count,
                    )
                if len(notices) >= 160:
                    logger.warning(
                        "    %s/%s age %d-%d still returned ≥160 records — "
                        "some notices may be unreachable",
                        country, sex_id, age_start, age_end,
                    )

    # ------------------------------------------------------------------
    # Phase 2: detail fetching
    # ------------------------------------------------------------------

    def _fetch_detail(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Fetch full detail for a single notice. Returns dict or None."""
        url = self._detail_url(entity_id)
        data = self._http_get(url)
        if not data or not isinstance(data, dict):
            return None
        return data

    def _enrich_notice(
        self, entity_id: str, detail: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build the enriched notice payload from detail data."""
        arrest_warrants = detail.get("arrest_warrants", [])
        if not isinstance(arrest_warrants, list):
            arrest_warrants = []

        # Normalise languages_spoken_ids → languages
        languages = detail.get("languages_spoken_ids", [])
        if not isinstance(languages, list):
            languages = []

        nationalities = detail.get("nationalities", [])
        if not isinstance(nationalities, list):
            nationalities = []

        # Forename: pass through raw value from API. models.RedNotice handles
        # "-" / "" / None → None during construction.
        forename = detail.get("forename")

        # Handle height/weight 0 → None
        height = detail.get("height")
        if isinstance(height, (int, float)) and height == 0:
            height = None
        weight = detail.get("weight")
        if isinstance(weight, (int, float)) and weight == 0:
            weight = None

        # Handle eyes_colors_id / hairs_id — arrays or null
        eyes_colors = detail.get("eyes_colors_id")
        if not isinstance(eyes_colors, list):
            eyes_colors = []
        hairs = detail.get("hairs_id")
        if not isinstance(hairs, list):
            hairs = []

        # thumbnail
        thumbnail_url = None
        links = detail.get("_links", {})
        if isinstance(links, dict):
            thumb = links.get("thumbnail", {})
            if isinstance(thumb, dict):
                thumbnail_url = thumb.get("href")

        return {
            "notice_id": entity_id,
            "forename": forename,
            "name": detail.get("name", ""),
            "date_of_birth": detail.get("date_of_birth"),
            "place_of_birth": detail.get("place_of_birth"),
            "sex_id": detail.get("sex_id"),
            "height": height,
            "weight": weight,
            "nationalities": nationalities,
            "languages": languages,
            "eyes_colors_id": eyes_colors,
            "hairs_id": hairs,
            "distinguishing_marks": detail.get("distinguishing_marks"),
            "arrest_warrants": arrest_warrants,
            "image_url": thumbnail_url,
            "country_of_birth_id": detail.get("country_of_birth_id"),
        }

    def _phase2_worker(
        self,
        entity_id: str,
        publish_lock: threading.Lock,
        on_record: Optional[Callable[[Dict[str, Any]], None]],
    ) -> int:
        """Fetch detail for one entity_id and optionally publish via callback.

        Returns 1 on success, 0 on failure (individual failure is never fatal).
        """
        detail = self._fetch_detail(entity_id)
        if not detail:
            logger.warning("Failed to fetch detail for %s after %d retries — skipping",
                           entity_id, self._max_retries)
            return 0

        enriched = self._enrich_notice(entity_id, detail)
        if on_record is not None:
            with publish_lock:
                try:
                    on_record(enriched)
                except Exception:
                    logger.exception("on_record callback failed for %s", entity_id)

        return 1

    # ------------------------------------------------------------------
    # Main public API
    # ------------------------------------------------------------------

    def scrape(
        self,
        on_record: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Run a complete scrape cycle.

        Args:
            on_record: Called for each enriched notice dict as soon as it is
                       built.  The caller MUST provide a lock if multiple
                       workers call this callback (the scraper handles the
                       lock internally via *publish_lock*).

        Returns:
            Dict with stats: ``{"total_collected": N, "success": N, "failed": N}``.
        """
        entity_ids = self._collect_entity_ids()
        total_collected = len(entity_ids)
        if total_collected == 0:
            logger.warning("Phase 1 returned zero entity IDs — nothing to scrape.")
            return {"total_collected": 0, "success": 0, "failed": 0}

        logger.info(
            "Phase 2: fetching details for %d notices (concurrency=%d) ...",
            total_collected, self._concurrency,
        )

        publish_lock = threading.Lock()
        success = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=self._concurrency) as executor:
            future_map = {}
            for eid in entity_ids:
                fut = executor.submit(
                    self._phase2_worker, eid, publish_lock, on_record
                )
                future_map[fut] = eid

            for fut in as_completed(future_map):
                try:
                    result = fut.result()
                    if result:
                        success += 1
                    else:
                        failed += 1
                except Exception:
                    logger.exception(
                        "Unexpected exception in worker for %s", future_map[fut]
                    )
                    failed += 1

        summary = {
            "total_collected": total_collected,
            "success": success,
            "failed": failed,
        }
        logger.info("Scrape cycle complete: %s", summary)
        return summary

    def close(self) -> None:
        """Close the curl_cffi session to release resources."""
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Standalone entrypoint (for testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    scraper = RedNoticeScraper()
    result = scraper.scrape(on_record=lambda r: print("RECORD:", r.get("notice_id")))
    print("Done:", result)
