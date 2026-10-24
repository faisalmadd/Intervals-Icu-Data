#!/usr/bin/env python3
"""
Intervals.icu → Google Drive + Git Sync Engine

Fetches all activities and wellness data from Intervals.icu,
generates structured CSVs and training status snapshot for Git,
and uploads raw .fit.gz files and CSVs to Google Drive.
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import pandas as pd

# ---------------------------------------------------------------------------
# Configuration & Environment Variables
# ---------------------------------------------------------------------------

INTERVALS_API_BASE = "https://intervals.icu/api/v1"

# Support both naming conventions (existing ATHLETE_ID / INTERVALS_KEY and new INTERVALS_*)
INTERVALS_API_KEY = os.environ.get("INTERVALS_API_KEY") or os.environ.get("INTERVALS_KEY")
INTERVALS_ATHLETE_ID = os.environ.get("INTERVALS_ATHLETE_ID") or os.environ.get("ATHLETE_ID", "0")

GDRIVE_SA_KEY_JSON = os.environ.get("GDRIVE_SERVICE_ACCOUNT_KEY")
GDRIVE_REFRESH_TOKEN = os.environ.get("GDRIVE_REFRESH_TOKEN")
GDRIVE_CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID")
GDRIVE_CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID")

FULL_BACKFILL = os.environ.get("FULL_BACKFILL", "false").lower() == "true"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LOOKBACK_DAYS = 7  # For incremental daily syncs
ACTIVITIES_CSV = DATA_DIR / "activities.csv"
WELLNESS_CSV = DATA_DIR / "wellness.csv"
SYNC_LOG = DATA_DIR / "sync_log.md"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("intervals-sync")

# ---------------------------------------------------------------------------
# Intervals.icu API Client
# ---------------------------------------------------------------------------


class IntervalsClient:
    """Wrapper around Intervals.icu REST API v1."""

    def __init__(self, api_key: str, athlete_id: str):
        self.session = requests.Session()
        self.session.auth = ("API_KEY", api_key)
        self.session.headers.update({"Accept": "application/json"})
        self.athlete_id = athlete_id
        self.base = INTERVALS_API_BASE

    def _url(self, path: str) -> str:
        return f"{self.base}/athlete/{self.athlete_id}/{path}"

    def get_activities(self, oldest: str, newest: str) -> list[dict]:
        """Fetch activity summaries in a date range (YYYY-MM-DD)."""
        url = self._url("activities")
        params = {"oldest": oldest, "newest": newest}
        log.info(f"Fetching activities {oldest} → {newest}")
        resp = self.session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        activities = resp.json()
        log.info(f"  → {len(activities)} activities returned")
        return activities

    def get_activity_detail(self, activity_id: str) -> dict:
        """Fetch a single activity with full interval details."""
        url = f"{self.base}/activity/{activity_id}"
        resp = self.session.get(url, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def download_activity_file(self, activity_id: str) -> bytes | None:
        """Download the raw original or processed FIT file for an activity."""
        # Try raw original file first
        url = f"{self.base}/activity/{activity_id}/file"
        try:
            resp = self.session.get(url, timeout=120)
            if resp.status_code == 200 and resp.content:
                return resp.content
        except requests.RequestException:
            pass

        # Fallback to processed FIT file
        fallback_url = f"{self.base}/activity/{activity_id}/fit-file"
        try:
            resp = self.session.get(fallback_url, timeout=120)
            if resp.status_code == 200 and resp.content:
                return resp.content
            log.warning(f"  No file found for activity {activity_id} (HTTP {resp.status_code})")
            return None
        except requests.RequestException as e:
            log.warning(f"  Failed downloading file for {activity_id}: {e}")
            return None

    def get_wellness(self, oldest: str, newest: str) -> list[dict]:
        """Fetch daily wellness records in a date range."""
        url = self._url("wellness")
        params = {"oldest": oldest, "newest": newest}
        log.info(f"Fetching wellness {oldest} → {newest}")
        resp = self.session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        records = resp.json()
        log.info(f"  → {len(records)} wellness records returned")
        return records


# ---------------------------------------------------------------------------
# Google Drive Client (Supports OAuth 2.0 and Service Account)
# ---------------------------------------------------------------------------


class GDriveClient:
    """Manages uploads to Google Drive with deduplication and error handling."""

    SCOPES = ["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]

    def __init__(
        self,
        root_folder_id: str,
        sa_key_json: str | None = None,
        oauth_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ):
        from googleapiclient.discovery import build

        if oauth_token and client_id:
            from google.oauth2.credentials import Credentials

            creds = Credentials(
                None,
                refresh_token=oauth_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=self.SCOPES,
            )
            log.info("Google Drive initialized using OAuth 2.0 User Token (Personal Storage Quota)")
        elif sa_key_json:
            from google.oauth2 import service_account

            creds_dict = json.loads(sa_key_json)
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=self.SCOPES
            )
            log.info("Google Drive initialized using Service Account")
        else:
            raise ValueError("No Google Drive credentials provided.")

        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.root_folder_id = root_folder_id
        self._folder_cache: dict[str, str] = {}

    def _ensure_folder(self, name: str, parent_id: str) -> str:
        """Create a folder if it doesn't exist, return its ID."""
        cache_key = f"{parent_id}/{name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        query = (
            f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
            f"and '{parent_id}' in parents and trashed=false"
        )
        results = (
            self.service.files()
            .list(q=query, spaces="drive", fields="files(id, name)", supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
        files = results.get("files", [])
        if files:
            folder_id = files[0]["id"]
        else:
            metadata = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            }
            folder = (
                self.service.files()
                .create(body=metadata, fields="id", supportsAllDrives=True)
                .execute()
            )
            folder_id = folder["id"]
            log.info(f"  Created Drive folder: {name}")

        self._folder_cache[cache_key] = folder_id
        return folder_id

    def ensure_year_folder(self, year: int) -> str:
        """Ensure files/YYYY/ folder structure exists."""
        files_folder = self._ensure_folder("files", self.root_folder_id)
        year_folder = self._ensure_folder(str(year), files_folder)
        return year_folder

    def file_exists(self, name: str, parent_id: str) -> bool:
        """Check if a file already exists in a folder."""
        query = f"name='{name}' and '{parent_id}' in parents and trashed=false"
        results = (
            self.service.files()
            .list(q=query, spaces="drive", fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
        return len(results.get("files", [])) > 0

    def upload_bytes(
        self, name: str, data: bytes, parent_id: str, mime_type: str = "application/octet-stream"
    ) -> str:
        """Upload a file (bytes) to a Drive folder."""
        from googleapiclient.http import MediaInMemoryUpload

        media = MediaInMemoryUpload(data, mimetype=mime_type)
        metadata = {"name": name, "parents": [parent_id]}
        f = (
            self.service.files()
            .create(body=metadata, media_body=media, fields="id", supportsAllDrives=True)
            .execute()
        )
        return f["id"]

    def upload_or_update(
        self, name: str, data: bytes, parent_id: str, mime_type: str = "text/csv"
    ) -> str:
        """Upload a file, overwriting if it already exists."""
        from googleapiclient.http import MediaInMemoryUpload

        query = f"name='{name}' and '{parent_id}' in parents and trashed=false"
        results = (
            self.service.files()
            .list(q=query, spaces="drive", fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
        existing = results.get("files", [])
        media = MediaInMemoryUpload(data, mimetype=mime_type)

        if existing:
            file_id = existing[0]["id"]
            self.service.files().update(
                fileId=file_id, media_body=media, supportsAllDrives=True
            ).execute()
            return file_id
        else:
            metadata = {"name": name, "parents": [parent_id]}
            f = (
                self.service.files()
                .create(body=metadata, media_body=media, fields="id", supportsAllDrives=True)
                .execute()
            )
            return f["id"]


# ---------------------------------------------------------------------------
# CSV Schemas & Transformations
# ---------------------------------------------------------------------------

ACTIVITY_COLUMNS = [
    "date", "name", "type", "distance_km", "duration_min", "elapsed_min",
    "elevation_gain_m", "avg_hr", "max_hr", "avg_pace_min_km", "gap_min_km",
    "avg_cadence_spm", "tss", "trimp", "hr_tss", "ctl", "atl", "tsb",
    "rpe", "feeling", "notes", "laps_json",
    "temperature_c", "humidity_pct", "wind_kph", "activity_id",
]

WELLNESS_COLUMNS = [
    "date", "ctl", "atl", "tsb", "resting_hr", "hrv_rmssd", "hrv_ln_rmssd",
    "sleep_duration_hr", "sleep_quality", "readiness", "mood", "motivation",
    "stress", "fatigue", "weight_kg", "body_fat_pct",
]


def _seconds_to_pace(speed_mps: float | None) -> str:
    """Convert m/s speed to min:sec/km pace string."""
    if not speed_mps or speed_mps <= 0:
        return ""
    pace_sec_per_km = 1000.0 / speed_mps
    minutes = int(pace_sec_per_km // 60)
    seconds = int(pace_sec_per_km % 60)
    return f"{minutes}:{seconds:02d}"


def _extract_laps_json(activity: dict) -> str:
    """Extract laps array as a compact JSON string."""
    laps = activity.get("laps") or activity.get("icu_laps") or []
    if not laps:
        return ""
    simplified = []
    for i, lap in enumerate(laps, 1):
        simplified.append({
            "lap": i,
            "distance_m": round(lap.get("distance", 0), 1),
            "duration_s": lap.get("moving_time") or lap.get("elapsed_time", 0),
            "avg_hr": lap.get("average_heartrate"),
            "avg_speed_mps": lap.get("average_speed"),
            "elevation_m": lap.get("total_elevation_gain"),
        })
    return json.dumps(simplified)


def activity_to_row(a: dict) -> dict:
    """Convert an Intervals.icu activity JSON object to a flat CSV row."""
    distance_m = a.get("distance") or 0
    moving_time_s = a.get("moving_time") or 0
    elapsed_time_s = a.get("elapsed_time") or 0

    return {
        "date": (a.get("start_date_local") or a.get("startDateLocal", ""))[:10],
        "name": a.get("name", ""),
        "type": a.get("type", ""),
        "distance_km": round(distance_m / 1000, 2) if distance_m else "",
        "duration_min": round(moving_time_s / 60, 1) if moving_time_s else "",
        "elapsed_min": round(elapsed_time_s / 60, 1) if elapsed_time_s else "",
        "elevation_gain_m": a.get("total_elevation_gain") or a.get("elevation_gain", ""),
        "avg_hr": a.get("average_heartrate", ""),
        "max_hr": a.get("max_heartrate", ""),
        "avg_pace_min_km": _seconds_to_pace(a.get("average_speed")),
        "gap_min_km": _seconds_to_pace(a.get("gap") or a.get("icu_average_gap")),
        "avg_cadence_spm": a.get("average_cadence", ""),
        "tss": a.get("icu_training_load", ""),
        "trimp": a.get("trimp", ""),
        "hr_tss": a.get("icu_hr_training_load", ""),
        "ctl": a.get("icu_ctl", ""),
        "atl": a.get("icu_atl", ""),
        "tsb": a.get("icu_tsb", ""),
        "rpe": a.get("icu_rpe", ""),
        "feeling": a.get("icu_feel", ""),
        "notes": (a.get("description") or "").replace("\n", " ").strip(),
        "laps_json": _extract_laps_json(a),
        "temperature_c": a.get("weather_temp", ""),
        "humidity_pct": a.get("weather_humidity", ""),
        "wind_kph": a.get("weather_wind", ""),
        "activity_id": a.get("id", ""),
    }


def wellness_to_row(w: dict) -> dict:
    """Convert an Intervals.icu wellness JSON to a flat CSV row."""
    return {
        "date": w.get("id", ""),
        "ctl": w.get("ctl", ""),
        "atl": w.get("atl", ""),
        "tsb": w.get("tsb", ""),
        "resting_hr": w.get("restingHR", ""),
        "hrv_rmssd": w.get("hrv", ""),
        "hrv_ln_rmssd": w.get("hrvSDNN", ""),
        "sleep_duration_hr": round(w.get("sleepTime", 0) / 3600, 1) if w.get("sleepTime") else "",
        "sleep_quality": w.get("sleepQuality", ""),
        "readiness": w.get("readiness", ""),
        "mood": w.get("mood", ""),
        "motivation": w.get("motivation", ""),
        "stress": w.get("stress", ""),
        "fatigue": w.get("fatigue", ""),
        "weight_kg": w.get("weight", ""),
        "body_fat_pct": w.get("bodyFat", ""),
    }


def merge_csv(existing_path: Path, new_rows: list[dict], columns: list[str], key: str = "date") -> pd.DataFrame:
    """Merge new rows into an existing CSV, deduplicating by activity_id or date."""
    if existing_path.exists():
        existing_df = pd.read_csv(existing_path, dtype=str)
    else:
        existing_df = pd.DataFrame(columns=columns)

    new_df = pd.DataFrame(new_rows, columns=columns)

    if key == "activity_id" and "activity_id" in new_df.columns:
        combined = pd.concat([existing_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["activity_id"], keep="last")
    else:
        combined = pd.concat([existing_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date"], keep="last")

    combined = combined.sort_values("date", ascending=True).reset_index(drop=True)
    return combined


# ---------------------------------------------------------------------------
# Status & Snapshot Generator
# ---------------------------------------------------------------------------


def generate_training_status(activities_df: pd.DataFrame, wellness_df: pd.DataFrame) -> str:
    """Generate training_status.md tailored for running, trail running, and hiking."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Numeric conversion
    for col in ["distance_km", "elevation_gain_m", "avg_hr", "tss", "ctl", "atl", "tsb", "duration_min"]:
        if col in activities_df.columns:
            activities_df[col] = pd.to_numeric(activities_df[col], errors="coerce")

    for col in ["ctl", "atl", "tsb", "resting_hr", "hrv_rmssd", "sleep_duration_hr", "weight_kg"]:
        if col in wellness_df.columns:
            wellness_df[col] = pd.to_numeric(wellness_df[col], errors="coerce")

    latest_w = wellness_df.iloc[-1] if len(wellness_df) > 0 else pd.Series(dtype=float)

    # Filter running & trail activities
    run_types = activities_df["type"].str.lower().str.contains("run|trail|hike|walk", na=False)
    running = activities_df[run_types].copy()

    today_dt = pd.Timestamp.now(tz="UTC").normalize()
    activities_df["_date"] = pd.to_datetime(activities_df["date"], errors="coerce")
    running["_date"] = pd.to_datetime(running["date"], errors="coerce")
    wellness_df["_date"] = pd.to_datetime(wellness_df["date"], errors="coerce")

    def window(df, days):
        cutoff = today_dt - pd.Timedelta(days=days)
        return df[df["_date"] >= cutoff]

    run_7 = window(running, 7)
    run_14 = window(running, 14)
    run_28 = window(running, 28)

    well_7 = window(wellness_df, 7)
    well_60 = window(wellness_df, 60)
    hrv_7_avg = well_7["hrv_rmssd"].mean() if "hrv_rmssd" in well_7.columns else float("nan")
    hrv_60_avg = well_60["hrv_rmssd"].mean() if "hrv_rmssd" in well_60.columns else float("nan")
    rhr_7_avg = well_7["resting_hr"].mean() if "resting_hr" in well_7.columns else float("nan")
    rhr_60_avg = well_60["resting_hr"].mean() if "resting_hr" in well_60.columns else float("nan")
    sleep_7_avg = well_7["sleep_duration_hr"].mean() if "sleep_duration_hr" in well_7.columns else float("nan")

    # ACWR
    all_28 = window(activities_df, 28)
    tss_7 = window(activities_df, 7)["tss"].sum()
    tss_28 = all_28["tss"].sum()
    acwr = round(tss_7 / (tss_28 / 4), 2) if tss_28 > 0 else 0

    acwr_icon = "✅" if acwr <= 1.3 else "⚠️" if acwr <= 1.5 else "🔴"
    hrv_icon = "✅" if pd.isna(hrv_7_avg) or pd.isna(hrv_60_avg) or hrv_7_avg >= hrv_60_avg else "⚠️"

    well_prev_7 = wellness_df[
        (wellness_df["_date"] >= today_dt - pd.Timedelta(days=14))
        & (wellness_df["_date"] < today_dt - pd.Timedelta(days=7))
    ]
    ctl_now = latest_w.get("ctl", float("nan"))
    ctl_prev = well_prev_7["ctl"].iloc[-1] if len(well_prev_7) > 0 else float("nan")
    ctl_delta = round(ctl_now - ctl_prev, 1) if pd.notna(ctl_now) and pd.notna(ctl_prev) else "N/A"
    ctl_arrow = "↑" if isinstance(ctl_delta, (int, float)) and ctl_delta > 0 else "↓" if isinstance(ctl_delta, (int, float)) and ctl_delta < 0 else "→"

    last5 = activities_df.tail(5).iloc[::-1]

    def _fmt(val, fmt=".0f"):
        if pd.isna(val) or val == "":
            return "—"
        try:
            return f"{float(val):{fmt}}"
        except (ValueError, TypeError):
            return str(val)

    last5_rows = []
    for _, row in last5.iterrows():
        last5_rows.append(
            f"| {str(row.get('date', ''))[-5:]} | {row.get('type', '')} | "
            f"{str(row.get('name', ''))[:25]} | {_fmt(row.get('distance_km'), '.1f')} km | "
            f"{_fmt(row.get('elevation_gain_m'))} m | "
            f"{row.get('avg_pace_min_km', '—')} | {_fmt(row.get('avg_hr'))} | "
            f"{_fmt(row.get('tss'))} | {_fmt(row.get('feeling'))}/5 |"
        )

    tsb_val = latest_w.get("tsb", float("nan"))
    tsb_status = ""
    if pd.notna(tsb_val):
        if tsb_val > 10:
            tsb_status = "ℹ️ Fresh / tapering"
        elif tsb_val > 0:
            tsb_status = "✅ Positive form — ready to perform"
        elif tsb_val > -15:
            tsb_status = "ℹ️ Productive training zone"
        else:
            tsb_status = "⚠️ Heavy fatigue accumulation"

    md = f"""# Training Status — {today}

## Current Load & Form
| Metric | Value | Trend (7d) |
|--------|-------|------------|
| CTL (Fitness) | {_fmt(ctl_now, '.1f')} | {ctl_arrow} {ctl_delta} |
| ATL (Fatigue) | {_fmt(latest_w.get('atl'), '.1f')} | — |
| TSB (Form) | {_fmt(tsb_val, '.1f')} | {tsb_status} |
| ACWR | {acwr} | {acwr_icon} {"Safe (<1.3)" if acwr <= 1.3 else "Caution zone (1.3-1.5)" if acwr <= 1.5 else "HIGH LOAD RISK (>1.5)"} |

## Running & Trail Volume
| Window | Distance (km) | Vert (m) | Sessions |
|--------|---------------|----------|----------|
| Last 7 days | {_fmt(run_7['distance_km'].sum(), '.1f')} | {_fmt(run_7['elevation_gain_m'].sum(), '.0f')} | {len(run_7)} |
| Last 14 days | {_fmt(run_14['distance_km'].sum(), '.1f')} | {_fmt(run_14['elevation_gain_m'].sum(), '.0f')} | {len(run_14)} |
| Last 28 days | {_fmt(run_28['distance_km'].sum(), '.1f')} | {_fmt(run_28['elevation_gain_m'].sum(), '.0f')} | {len(run_28)} |

## HRV & Recovery
| Metric | 7-day Avg | 60-day Baseline | Status |
|--------|-----------|-----------------|--------|
| HRV (rMSSD) | {_fmt(hrv_7_avg, '.1f')} ms | {_fmt(hrv_60_avg, '.1f')} ms | {hrv_icon} {"Above baseline" if hrv_7_avg >= hrv_60_avg else "Below baseline"} |
| Resting HR | {_fmt(rhr_7_avg, '.0f')} bpm | {_fmt(rhr_60_avg, '.0f')} bpm | {"✅ Stable" if rhr_7_avg <= rhr_60_avg + 2 else "⚠️ Elevated"} |
| Sleep (avg) | {_fmt(sleep_7_avg, '.1f')} hr | — | {"✅ Adequate" if pd.notna(sleep_7_avg) and sleep_7_avg >= 7 else "⚠️ Low" if pd.notna(sleep_7_avg) else "—"} |

## Last 5 Workouts
| Date | Type | Name | Dist | Vert | Pace | HR | TSS | Feel |
|------|------|------|------|------|------|-----|-----|------|
{chr(10).join(last5_rows) if last5_rows else "| — | — | No recent workouts | — | — | — | — | — | — |"}

## Coaching Flags
- {acwr_icon} ACWR is {acwr} — {"within optimal training range" if acwr <= 1.3 else "approaching overload caution threshold" if acwr <= 1.5 else "injury risk spike detected"}.
- {hrv_icon} HRV 7-day average ({_fmt(hrv_7_avg, '.1f')}) {"in green recovery range" if hrv_7_avg >= hrv_60_avg else "dipping below baseline"}.
- {"ℹ️" if pd.notna(tsb_val) else "—"} Form (TSB) is {_fmt(tsb_val, '.1f')}: {tsb_status}.
"""
    return md


# ---------------------------------------------------------------------------
# Main Execution Orchestrator
# ---------------------------------------------------------------------------


def main():
    start_time = time.time()
    errors: list[str] = []
    stats = {
        "activities_fetched": 0,
        "files_uploaded": 0,
        "files_skipped": 0,
        "wellness_records": 0,
    }

    if not INTERVALS_API_KEY:
        log.error("Missing INTERVALS_API_KEY or INTERVALS_KEY environment variable!")
        sys.exit(1)

    try:
        newest = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if FULL_BACKFILL:
            oldest = "2000-01-01"
            log.info("=== FULL BACKFILL MODE (account inception) ===")
        else:
            oldest = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
            log.info(f"=== INCREMENTAL SYNC (last {LOOKBACK_DAYS} days) ===")

        # 1. Fetch activities and wellness records from Intervals.icu
        icu = IntervalsClient(INTERVALS_API_KEY, INTERVALS_ATHLETE_ID)
        activities = icu.get_activities(oldest, newest)
        stats["activities_fetched"] = len(activities)

        wellness = icu.get_wellness(oldest, newest)
        stats["wellness_records"] = len(wellness)

        # 2. Build and save CSV files locally FIRST (guarantees Git data is updated)
        activity_rows = [activity_to_row(a) for a in activities]
        wellness_rows = [wellness_to_row(w) for w in wellness]

        activities_df = merge_csv(ACTIVITIES_CSV, activity_rows, ACTIVITY_COLUMNS, key="activity_id")
        wellness_df = merge_csv(WELLNESS_CSV, wellness_rows, WELLNESS_COLUMNS, key="date")

        activities_df.to_csv(ACTIVITIES_CSV, index=False)
        wellness_df.to_csv(WELLNESS_CSV, index=False)
        log.info(f"Saved {len(activities_df)} activities and {len(wellness_df)} wellness records to CSV.")

        # 3. Generate training_status.md locally
        status_md = generate_training_status(activities_df.copy(), wellness_df.copy())
        status_path = DATA_DIR / "training_status.md"
        status_path.write_text(status_md, encoding="utf-8")
        log.info("Generated local training_status.md.")

        # 4. Initialize Google Drive Client (if configured)
        gdrive = None
        if GDRIVE_FOLDER_ID and (GDRIVE_REFRESH_TOKEN or GDRIVE_SA_KEY_JSON):
            try:
                gdrive = GDriveClient(
                    root_folder_id=GDRIVE_FOLDER_ID,
                    sa_key_json=GDRIVE_SA_KEY_JSON,
                    oauth_token=GDRIVE_REFRESH_TOKEN,
                    client_id=GDRIVE_CLIENT_ID,
                    client_secret=GDRIVE_CLIENT_SECRET,
                )
            except Exception as e:
                log.warning(f"Google Drive initialization error: {e}")
                errors.append(f"GDrive Init: {e}")
        else:
            log.info("Google Drive secrets not configured. Skipping Drive upload.")

        # 5. Upload files to Google Drive (with individual error protection)
        if gdrive:
            # Upload .fit.gz files
            for act in activities:
                act_id = act.get("id", "")
                act_date = (act.get("start_date_local") or act.get("startDateLocal", ""))[:10]
                if not act_date:
                    continue

                year = int(act_date[:4])
                try:
                    year_folder = gdrive.ensure_year_folder(year)
                    filename = f"{act_date}_{act_id}.fit.gz"
                    if gdrive.file_exists(filename, year_folder):
                        stats["files_skipped"] += 1
                        continue

                    file_data = icu.download_activity_file(act_id)
                    if file_data:
                        gdrive.upload_bytes(filename, file_data, year_folder)
                        stats["files_uploaded"] += 1
                        log.info(f"  Uploaded to Drive: {filename}")
                    else:
                        stats["files_skipped"] += 1
                except Exception as e:
                    err_msg = str(e)
                    if "storageQuotaExceeded" in err_msg or "Service Accounts do not have storage quota" in err_msg:
                        log.warning(
                            "Google Drive upload skipped: Service Accounts cannot own files in personal Google Drive folders "
                            "(requires a Google Workspace Shared Drive or OAuth2 user token). "
                            "Local Git mirror will proceed normally."
                        )
                        errors.append("GDrive: Personal @gmail.com folders require OAuth2 token or Shared Drive (0-byte SA quota).")
                        break
                    else:
                        log.warning(f"  File upload failed for {act_id}: {e}")
                        stats["files_skipped"] += 1

                time.sleep(0.4)

            # Upload CSVs & status to Google Drive
            try:
                gdrive.upload_or_update("activities.csv", ACTIVITIES_CSV.read_bytes(), GDRIVE_FOLDER_ID, "text/csv")
                gdrive.upload_or_update("wellness.csv", WELLNESS_CSV.read_bytes(), GDRIVE_FOLDER_ID, "text/csv")
                gdrive.upload_or_update("training_status.md", status_md.encode("utf-8"), GDRIVE_FOLDER_ID, "text/markdown")
                log.info("Uploaded CSVs and training_status.md to Google Drive.")
            except Exception as e:
                log.warning(f"Could not upload CSVs to Google Drive: {e}")
                if "storageQuotaExceeded" not in str(e):
                    errors.append(f"GDrive CSV: {e}")

    except Exception as e:
        log.error(f"SYNC FATAL ERROR: {e}", exc_info=True)
        errors.append(str(e))

    # Write sync log
    elapsed = round(time.time() - start_time, 1)
    sync_log = f"""# Sync Log

| Field | Value |
|-------|-------|
| **Last Sync** | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} |
| **Mode** | {'Full Backfill' if FULL_BACKFILL else f'Incremental ({LOOKBACK_DAYS}-day lookback)'} |
| **Duration** | {elapsed}s |
| **Activities Fetched** | {stats['activities_fetched']} |
| **Files Uploaded to Drive** | {stats['files_uploaded']} |
| **Files Skipped** | {stats['files_skipped']} |
| **Wellness Records** | {stats['wellness_records']} |
| **Status** | {'✅ Success' if not errors else '⚠️ Completed with warnings'} |

"""
    if errors:
        sync_log += "## Notes & Warnings\n"
        for err in errors:
            sync_log += f"- `{err}`\n"

    SYNC_LOG.write_text(sync_log, encoding="utf-8")
    log.info(f"Sync complete in {elapsed}s.")

    # Only exit with error if core data extraction itself failed
    if stats["activities_fetched"] == 0 and stats["wellness_records"] == 0 and errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
