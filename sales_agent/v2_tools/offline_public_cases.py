"""Explicit synthetic homework lookup of coarse external public references.

This deliberately does not implement the institution-served Cases contract.
The source database is never copied or modified. Its raw values stay inside
this function; only enumerated categories cross into the tool envelope.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from collections import Counter
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from .cases import _valid_request
from .common import envelope

SOURCE_TYPE = "external_public_reference_unverified"
SOURCE_VERSION = "external-public-synthetic.v1"
LIMITATIONS = ["EXTERNAL_PUBLIC_REFERENCE", "SELF_REPORTED_OUTCOME_UNVERIFIED",
               "NOT_INSTITUTION_SERVED", "NOT_ADMISSION_PROBABILITY", "NOT_CAUSAL_EVIDENCE",
               "SOURCE_PRIVACY_REVIEW_NOT_ESTABLISHED"]
_COUNTRIES = {"Singapore": "SG", "Hong Kong": "HK", "United Kingdom": "UK", "Australia": "AU"}
_PUBLIC_SOURCE_HOSTS = {"m.compassedu.hk", "www.eic.org.cn", "m.hksg.org"}
_SCHOOLS = {name: name for name in (
    "National University of Singapore", "Nanyang Technological University",
    "University of Melbourne", "University of Sydney", "Chinese University of Hong Kong",
    "Hong Kong Polytechnic University", "Australian National University", "Imperial College London",
    "University of Leeds", "Hong Kong University of Science and Technology",
    "University of Edinburgh", "University of Adelaide", "Hong Kong Baptist University",
    "Monash University", "University of New South Wales", "University of Birmingham",
    "University College London", "King's College London")}
_DIRECTIONS = (
    (re.compile(r"computer|computing|software|artificial intelligence|data", re.I), "Computing and data"),
    (re.compile(r"finance|account|economic|business|management", re.I), "Business and economics"),
    (re.compile(r"engineering|electrical|mechanical|material", re.I), "Engineering"),
    (re.compile(r"education", re.I), "Education"),
    (re.compile(r"law", re.I), "Law"),
    (re.compile(r"media|communication", re.I), "Media and communication"),
)
_TIER = {"985": "985", "211": "211", "Double First Class": "DOUBLE_FIRST_CLASS",
         "DOUBLE_FIRST_CLASS": "DOUBLE_FIRST_CLASS"}
_MAJOR_FAMILIES = (
    (re.compile(r"computer|computing|software|artificial intelligence|data", re.I), "COMPUTING_DATA"),
    (re.compile(r"finance|account|economic|business|management", re.I), "BUSINESS_ECONOMICS"),
    (re.compile(r"engineering|electrical|mechanical|material", re.I), "ENGINEERING"),
    (re.compile(r"education", re.I), "EDUCATION"),
    (re.compile(r"law", re.I), "LAW"),
    (re.compile(r"media|communication", re.I), "MEDIA"),
)
_OUTCOME = {"offer": "External platform reports an offer; unverified",
            "reject": "External platform reports a rejection; unverified",
            "waitlist": "External platform reports a waitlist; unverified",
            "unknown": "Outcome unknown"}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _score_band(value: object) -> str:
    if isinstance(value, str):
        matched = re.fullmatch(r"\s*(\d{2}(?:\.\d+)?)\s*(?:/\s*100|%)?\s*", value)
        value = float(matched.group(1)) if matched else None
    if type(value) not in (int, float) or not 0 <= value <= 100:
        return "UNKNOWN"
    if value >= 90:
        return "90+"
    if value >= 85:
        return "85-89"
    if value >= 80:
        return "80-84"
    if value >= 75:
        return "75-79"
    return "UNKNOWN"


def _major_family(value: object) -> str:
    if not isinstance(value, str):
        return "UNKNOWN"
    return next((label for pattern, label in _MAJOR_FAMILIES if pattern.search(value)), "UNKNOWN")


class OfflinePublicCasesSource:
    """One pinned SQLite source for explicit, synthetic-only host opt-in."""

    def __init__(self, *, path: Path, expected_sha256: str,
                 source_version: str = SOURCE_VERSION, secret: bytes | None = None) -> None:
        self.path = Path(path)
        self.expected_sha256 = expected_sha256
        self.source_version = source_version
        self.secret = secret or secrets.token_bytes(32)
        if len(self.secret) < 32 or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("invalid_offline_case_source_configuration")

    def search(self, request: dict) -> dict:
        if not _valid_request(request):
            return envelope("INVALID_REQUEST", SOURCE_TYPE, limitations=LIMITATIONS,
                            error_code="INVALID_REQUEST")
        try:
            if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > 100_000_000:
                raise ValueError("source unavailable")
            if _sha(self.path) != self.expected_sha256:
                raise ValueError("source digest mismatch")
            started = time.monotonic()
            uri = self.path.absolute().as_uri() + "?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as db:
                db.execute("PRAGMA query_only=ON")
                db.set_progress_handler(lambda: 1 if time.monotonic() - started > 2.0 else 0, 1000)
                rows = db.execute(
                    "SELECT dedupe_key,country,institution,program_name,decision,payload_json,source_url "
                    "FROM cases WHERE source_url LIKE 'https://%' "
                    "AND json_extract(payload_json,'$.synthetic_flag')=1 "
                    "AND json_extract(payload_json,'$.source_quality')='traceable_structured' "
                    "ORDER BY id LIMIT 1000"
                ).fetchall()
            if _sha(self.path) != self.expected_sha256:
                raise ValueError("source changed during lookup")
            profile = request["student_profile"]
            wanted_country = profile["target_country"]
            wanted_school = profile["target_university"]
            wanted_direction = profile["target_program_or_major"]
            wanted_band = _score_band(profile["average_score"])
            wanted_major = _major_family(profile["undergraduate_major_raw"])
            if request["retrieval_mode"] == "SIMILAR_BACKGROUND" and wanted_band == "UNKNOWN" and wanted_major == "UNKNOWN":
                return envelope("NO_RESULTS", SOURCE_TYPE, source_version=self.source_version,
                                limitations=LIMITATIONS, data=[])
            candidates = []
            for record_key, country, school, program, decision, payload, source_url in rows:
                if not isinstance(record_key, str) or not record_key.strip():
                    continue
                parsed_url = urlsplit(source_url)
                if parsed_url.scheme != "https" or parsed_url.hostname not in _PUBLIC_SOURCE_HOSTS:
                    continue
                code = _COUNTRIES.get(country)
                safe_school = _SCHOOLS.get(school)
                if not code or not safe_school or decision not in _OUTCOME:
                    continue
                if wanted_country and wanted_country != code:
                    continue
                if wanted_school and wanted_school.casefold() not in (safe_school.casefold(),):
                    continue
                direction = next((label for pattern, label in _DIRECTIONS if pattern.search(program)), "UNKNOWN")
                if wanted_direction and wanted_direction != direction and wanted_direction not in program:
                    continue
                raw_profile = json.loads(payload).get("applicant_profile") or {}
                if not isinstance(raw_profile, dict):
                    raw_profile = {}
                raw_tier = raw_profile.get("university_tier")
                tier = _TIER.get(raw_tier, "UNKNOWN") if isinstance(raw_tier, str) else "UNKNOWN"
                band = _score_band(raw_profile.get("gpa"))
                major = _major_family(raw_profile.get("undergraduate_major"))
                if request["retrieval_mode"] == "SIMILAR_BACKGROUND":
                    if wanted_band != "UNKNOWN" and band != wanted_band:
                        continue
                    if wanted_major != "UNKNOWN" and major != wanted_major:
                        continue
                token = hmac.new(self.secret, f"{self.source_version}|{self.expected_sha256}|{record_key}".encode(),
                                 hashlib.sha256).hexdigest()[:32]
                candidates.append({"evidence_id": "ev_" + token,
                                "undergraduate_tier": tier, "score_band": band,
                                "major_family": major, "target": {"country": code,
                                "university": safe_school, "program_direction": direction},
                                "outcome": _OUTCOME[decision],
                                "evidence_label": "External public reference; platform-reported outcome unverified; not an institution-served case"})
            # Suppress rare visible combinations, including target plus outcome.
            # This is a disclosure threshold, not proof of privacy approval.
            def visible_key(item: dict) -> tuple:
                target = item["target"]
                return (target["country"], target["university"], target["program_direction"],
                        item["outcome"], item["undergraduate_tier"], item["score_band"],
                        item["major_family"])
            counts = Counter(visible_key(item) for item in candidates)
            results = [item for item in candidates if counts[visible_key(item)] >= 3][:request.get("limit", 2)]
            return envelope("OK" if results else "NO_RESULTS", SOURCE_TYPE,
                            source_version=self.source_version, limitations=LIMITATIONS, data=results)
        except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            return envelope("UNAVAILABLE", SOURCE_TYPE, source_version=self.source_version,
                            limitations=LIMITATIONS, error_code="OFFLINE_PUBLIC_CASE_SOURCE_UNAVAILABLE")


def search_offline_public_references(request: dict, *, source: OfflinePublicCasesSource | None) -> dict:
    if source is None:
        return envelope("UNAVAILABLE", SOURCE_TYPE, limitations=LIMITATIONS,
                        error_code="OFFLINE_PUBLIC_CASE_SOURCE_UNAVAILABLE")
    return source.search(request)
