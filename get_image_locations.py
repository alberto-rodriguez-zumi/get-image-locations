#!/usr/bin/env python3
"""Summarize GPS photo locations by immediate subfolder.

The script reads GPS coordinates with exiftool, reverse-geocodes them, and
prints CSV rows like:

    "2026-06-02";"Matsumoto, Azumino"
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


DEFAULT_EXTENSIONS = (
    "heic",
    "heif",
    "jpg",
    "jpeg",
    "png",
    "tif",
    "tiff",
    "dng",
    "cr2",
    "cr3",
    "nef",
    "arw",
    "raf",
    "rw2",
    "orf",
    "mov",
    "mp4",
)
DEFAULT_MIN_CAPTURE_DATE = "2000-01-01"

BASIC_SPECIFIC_LOCATION_FIELDS = (
    "tourism",
    "attraction",
    "historic",
    "heritage",
)

LOCAL_PLACE_FIELDS = (
    "locality",
    "hamlet",
)

ADDRESS_DETAIL_FIELDS = (
    "neighbourhood",
    "quarter",
    "suburb",
    "city_district",
)

BROAD_LOCATION_FIELDS = (
    "village",
    "town",
    "city",
    "municipality",
    "county",
    "state",
)

PLACE_NAME_CATEGORIES = {"tourism", "historic", "place", "natural", "leisure"}
LOCAL_SCRIPT_RANGES = (
    ("\u3040", "\u30ff"),  # Hiragana and Katakana
    ("\u3400", "\u9fff"),  # CJK ideographs
)
CHOME_MARKERS = ("chome", "-cho", "丁目")

EARTH_RADIUS_METERS = 6_371_008.8


class LocationError(RuntimeError):
    """Raised when the location workflow cannot continue."""


@dataclass(frozen=True)
class LocationCluster:
    latitude: float
    longitude: float
    count: int


@dataclass(frozen=True)
class GpxPoint:
    latitude: float
    longitude: float
    captured_at: datetime
    source_file: str


@dataclass(frozen=True)
class FolderAnalysis:
    clusters: list[LocationCluster]
    gpx_points: list[GpxPoint]


class Progress:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.is_tty = sys.stderr.isatty()
        self.last_width = 0

    def update(self, folder_name: str, processed: int, total: int) -> None:
        if not self.enabled:
            return

        message = f"Processing {folder_name}: {processed}/{total} files analyzed"
        if self.is_tty:
            padding = " " * max(0, self.last_width - len(message))
            print(f"\r{message}{padding}", end="", file=sys.stderr, flush=True)
            self.last_width = len(message)
        else:
            print(message, file=sys.stderr, flush=True)

    def clear(self) -> None:
        if self.enabled and self.is_tty and self.last_width:
            print(f"\r{' ' * self.last_width}\r", end="", file=sys.stderr, flush=True)
            self.last_width = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read GPS metadata from media files and summarize locations by folder."
    )
    parser.add_argument(
        "root",
        type=Path,
        help="Folder containing dated subfolders with photos/videos.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional CSV output path. Rows are always printed to stdout too.",
    )
    parser.add_argument(
        "--folder",
        action="append",
        help="Only process this immediate subfolder name. Can be passed multiple times.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(".geocode-cache.json"),
        help="JSON cache for reverse geocoding results. Default: .geocode-cache.json",
    )
    parser.add_argument(
        "--coordinate-precision",
        type=int,
        default=2,
        help="Decimals used when printing coordinates with --no-geocode. Default: 2.",
    )
    parser.add_argument(
        "--cluster-radius-meters",
        type=float,
        default=1000.0,
        help="Merge GPS points within this distance before geocoding. Use 0 to disable. Default: 1000.",
    )
    parser.add_argument(
        "--min-photos-per-location",
        type=int,
        default=1,
        help="Hide clustered locations with fewer GPS media files than this. Default: 1.",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Preferred language for location names. Default: en.",
    )
    parser.add_argument(
        "--geocode-zoom",
        type=int,
        default=12,
        help="Reverse geocoder detail level from 0 to 18. Higher is more specific. Default: 12.",
    )
    parser.add_argument(
        "--name-detail",
        choices=("balanced", "specific", "address"),
        default="balanced",
        help="How specific location names should be. Default: balanced.",
    )
    parser.add_argument(
        "--allow-local-script",
        action="store_true",
        help="Allow local-script names such as Japanese kanji/kana when no romanized name is available.",
    )
    parser.add_argument(
        "--user-agent",
        default="get-image-locations/1.0",
        help="User-Agent sent to the reverse geocoding service.",
    )
    parser.add_argument(
        "--no-geocode",
        action="store_true",
        help="Print rounded coordinates instead of calling the reverse geocoder.",
    )
    parser.add_argument(
        "--extensions",
        default=",".join(DEFAULT_EXTENSIONS),
        help="Comma-separated file extensions to scan.",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Include folders that have no GPS locations.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress messages. Progress is written to stderr, not stdout.",
    )
    parser.add_argument(
        "--exiftool-batch-size",
        type=int,
        default=100,
        help="Number of files passed to each exiftool call. Default: 100.",
    )
    parser.add_argument(
        "--gpx-output-dir",
        type=Path,
        help="Optional folder where one GPX track per input subfolder will be written.",
    )
    parser.add_argument(
        "--gpx-only",
        action="store_true",
        help="Only generate GPX files. Requires --gpx-output-dir and skips CSV/geocoding output.",
    )
    parser.add_argument(
        "--gpx-max-points",
        type=int,
        default=0,
        help="Maximum points per generated GPX. Use 0 for no hard limit. Default: 0.",
    )
    parser.add_argument(
        "--gpx-simplify-distance-meters",
        type=float,
        default=25.0,
        help="Collapse consecutive GPX points within this distance. Default: 25.",
    )
    parser.add_argument(
        "--gpx-simplify-time-seconds",
        type=int,
        default=300,
        help="Collapse consecutive GPX points within this time gap. Default: 300.",
    )
    parser.add_argument(
        "--min-capture-date",
        default=DEFAULT_MIN_CAPTURE_DATE,
        help=f"Ignore media captured before this date. Default: {DEFAULT_MIN_CAPTURE_DATE}.",
    )
    parser.add_argument(
        "--folder-date-tolerance-days",
        type=int,
        default=2,
        help="Ignore dated-folder media captured more than this many days away from the folder date. Use -1 to disable. Default: 2.",
    )
    parser.add_argument(
        "--allow-zero-coordinates",
        action="store_true",
        help="Keep GPS points at 0,0 instead of treating them as invalid.",
    )
    return parser.parse_args()


def load_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LocationError(f"Cache file is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise LocationError(f"Cache file must contain a JSON object: {path}")
    return {str(key): str(value) for key, value in data.items()}


def save_cache(path: Path, cache: dict[str, str]) -> None:
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def discover_folders(root: Path, selected: set[str] | None) -> list[Path]:
    if not root.exists():
        raise LocationError(f"Root folder does not exist: {root}")
    if not root.is_dir():
        raise LocationError(f"Root path is not a folder: {root}")

    folders = sorted(path for path in root.iterdir() if path.is_dir())
    if selected is None:
        return folders

    by_name = {path.name: path for path in folders}
    missing = sorted(selected - set(by_name))
    if missing:
        raise LocationError(f"Subfolder not found under {root}: {', '.join(missing)}")
    return [by_name[name] for name in sorted(selected)]


def discover_media_files(folder: Path, extensions: set[str]) -> list[Path]:
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower().lstrip(".") in extensions
    )


def read_gps_records(files: list[Path]) -> list[dict[str, Any]]:
    if not files:
        return []

    command = [
        "exiftool",
        "-json",
        "-n",
        "-GPSLatitude",
        "-GPSLongitude",
        "-GPSDateTime",
        "-SubSecDateTimeOriginal",
        "-DateTimeOriginal",
        "-OffsetTimeOriginal",
        "-CreateDate",
        "-MediaCreateDate",
        "-TrackCreateDate",
        "-FileModifyDate",
        "-FileType",
    ]
    command.extend(str(path) for path in files)

    try:
        completed = subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise LocationError("exiftool is required but was not found in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise LocationError(exc.stderr.strip() or "exiftool failed.") from exc

    try:
        records = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LocationError("exiftool returned invalid JSON.") from exc
    if not isinstance(records, list):
        raise LocationError("exiftool returned an unexpected JSON shape.")
    return records


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LocationError(f"Invalid date '{value}'. Expected YYYY-MM-DD.") from exc


def folder_date(folder_name: str) -> date | None:
    try:
        return date.fromisoformat(folder_name)
    except ValueError:
        return None


def valid_coordinates(latitude: float, longitude: float, allow_zero_coordinates: bool) -> bool:
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return False
    if not allow_zero_coordinates and latitude == 0 and longitude == 0:
        return False
    return True


def valid_capture_date(
    captured_at: datetime,
    minimum_capture_date: date,
    current_folder_date: date | None,
    folder_date_tolerance_days: int,
) -> bool:
    captured_date = captured_at.astimezone(timezone.utc).date()
    if captured_date < minimum_capture_date:
        return False
    if current_folder_date and folder_date_tolerance_days >= 0:
        return abs((captured_date - current_folder_date).days) <= folder_date_tolerance_days
    return True


def coordinates_from_records(
    records: list[dict[str, Any]],
    allow_zero_coordinates: bool,
    minimum_capture_date: date,
    current_folder_date: date | None,
    folder_date_tolerance_days: int,
) -> list[tuple[float, float]]:
    coordinates: list[tuple[float, float]] = []

    for record in records:
        latitude = record.get("GPSLatitude")
        longitude = record.get("GPSLongitude")
        if latitude is None or longitude is None:
            continue
        latitude = float(latitude)
        longitude = float(longitude)
        if not valid_coordinates(latitude, longitude, allow_zero_coordinates):
            continue
        captured_at = captured_at_from_record(record)
        if captured_at and not valid_capture_date(captured_at, minimum_capture_date, current_folder_date, folder_date_tolerance_days):
            continue
        coordinates.append((latitude, longitude))

    return coordinates


def parse_exif_datetime(value: Any, offset: str | None = None, assume_utc: bool = False) -> datetime | None:
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if offset and re.match(r"^[+-]\d\d:\d\d$", offset) and not re.search(r"[+-]\d\d:\d\d$", text):
        text = f"{text}{offset}"

    for pattern in ("%Y:%m:%d %H:%M:%S.%f%z", "%Y:%m:%d %H:%M:%S%z", "%Y:%m:%d %H:%M:%S.%f", "%Y:%m:%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue

        if parsed.tzinfo is None:
            if assume_utc:
                return parsed.replace(tzinfo=timezone.utc)
            return None
        return parsed.astimezone(timezone.utc)

    return None


def captured_at_from_record(record: dict[str, Any]) -> datetime | None:
    offset = record.get("OffsetTimeOriginal")
    candidates = (
        ("GPSDateTime", None, True),
        ("SubSecDateTimeOriginal", offset, False),
        ("DateTimeOriginal", offset, False),
        ("CreateDate", None, True),
        ("MediaCreateDate", None, True),
        ("TrackCreateDate", None, True),
        ("FileModifyDate", None, False),
    )

    for field, field_offset, assume_utc in candidates:
        parsed = parse_exif_datetime(record.get(field), field_offset, assume_utc)
        if parsed:
            return parsed

    return None


def gpx_points_from_records(
    records: list[dict[str, Any]],
    allow_zero_coordinates: bool,
    minimum_capture_date: date,
    current_folder_date: date | None,
    folder_date_tolerance_days: int,
) -> list[GpxPoint]:
    points: list[GpxPoint] = []

    for record in records:
        latitude = record.get("GPSLatitude")
        longitude = record.get("GPSLongitude")
        source_file = record.get("SourceFile")
        captured_at = captured_at_from_record(record)
        if latitude is None or longitude is None or not source_file or captured_at is None:
            continue
        latitude = float(latitude)
        longitude = float(longitude)
        if not valid_coordinates(latitude, longitude, allow_zero_coordinates):
            continue
        if not valid_capture_date(captured_at, minimum_capture_date, current_folder_date, folder_date_tolerance_days):
            continue

        points.append(
            GpxPoint(
                latitude=latitude,
                longitude=longitude,
                captured_at=captured_at,
                source_file=str(source_file),
            )
        )

    return sorted(points, key=lambda point: (point.captured_at, point.source_file))


def distance_meters(first: tuple[float, float], second: tuple[float, float]) -> float:
    first_lat, first_lon = (math.radians(value) for value in first)
    second_lat, second_lon = (math.radians(value) for value in second)
    delta_lat = second_lat - first_lat
    delta_lon = second_lon - first_lon

    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(first_lat) * math.cos(second_lat) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(haversine))


def unique_points(points: list[tuple[float, float]]) -> list[LocationCluster]:
    counts: dict[tuple[float, float], int] = {}
    result: list[LocationCluster] = []
    for latitude, longitude in points:
        point = (round(latitude, 6), round(longitude, 6))
        counts[point] = counts.get(point, 0) + 1

    for (latitude, longitude), count in counts.items():
        result.append(LocationCluster(latitude=latitude, longitude=longitude, count=count))

    return result


def cluster_coordinates(
    points: list[tuple[float, float]],
    radius_meters: float,
) -> list[LocationCluster]:
    if radius_meters <= 0:
        return unique_points(points)

    clusters: list[dict[str, float]] = []
    for latitude, longitude in points:
        closest_index: int | None = None
        closest_distance = radius_meters

        for index, cluster in enumerate(clusters):
            center = (cluster["latitude"], cluster["longitude"])
            distance = distance_meters((latitude, longitude), center)
            if distance <= closest_distance:
                closest_index = index
                closest_distance = distance

        if closest_index is None:
            clusters.append({"latitude": latitude, "longitude": longitude, "count": 1.0})
            continue

        cluster = clusters[closest_index]
        count = cluster["count"] + 1
        cluster["latitude"] = ((cluster["latitude"] * cluster["count"]) + latitude) / count
        cluster["longitude"] = ((cluster["longitude"] * cluster["count"]) + longitude) / count
        cluster["count"] = count

    return [
        LocationCluster(
            latitude=round(cluster["latitude"], 6),
            longitude=round(cluster["longitude"], 6),
            count=int(cluster["count"]),
        )
        for cluster in clusters
    ]


def filter_clusters(
    clusters: list[LocationCluster],
    min_photos_per_location: int,
) -> list[LocationCluster]:
    return [cluster for cluster in clusters if cluster.count >= min_photos_per_location]


def simplify_gpx_points(
    points: list[GpxPoint],
    distance_meters_threshold: float,
    time_seconds_threshold: int,
) -> list[GpxPoint]:
    if len(points) <= 2 or distance_meters_threshold <= 0 or time_seconds_threshold <= 0:
        return points

    simplified: list[GpxPoint] = []
    index = 0
    while index < len(points):
        group = [points[index]]
        index += 1

        while index < len(points):
            first = group[0]
            candidate = points[index]
            seconds = (candidate.captured_at - first.captured_at).total_seconds()
            distance = distance_meters(
                (first.latitude, first.longitude),
                (candidate.latitude, candidate.longitude),
            )
            if seconds < 0 or seconds > time_seconds_threshold or distance > distance_meters_threshold:
                break
            group.append(candidate)
            index += 1

        simplified.append(group[0])
        if len(group) > 1:
            simplified.append(group[-1])

    return unique_gpx_points(simplified)


def unique_gpx_points(points: list[GpxPoint]) -> list[GpxPoint]:
    result: list[GpxPoint] = []
    seen: set[tuple[str, float, float, str]] = set()
    for point in points:
        key = (
            point.captured_at.isoformat(),
            round(point.latitude, 7),
            round(point.longitude, 7),
            point.source_file,
        )
        if key not in seen:
            seen.add(key)
            result.append(point)
    return result


def limit_gpx_points(points: list[GpxPoint], max_points: int) -> list[GpxPoint]:
    if max_points <= 0 or len(points) <= max_points:
        return points
    if max_points == 1:
        return [points[0]]
    if max_points == 2:
        return [points[0], points[-1]]

    step = (len(points) - 1) / (max_points - 1)
    indexes = {round(index * step) for index in range(max_points)}
    indexes.add(0)
    indexes.add(len(points) - 1)
    return [points[index] for index in sorted(indexes)[:max_points]]


def analyze_folders(
    folders: list[Path],
    extensions: set[str],
    args: argparse.Namespace,
    progress: Progress,
) -> dict[str, FolderAnalysis]:
    analyses: dict[str, FolderAnalysis] = {}

    for folder in folders:
        files = discover_media_files(folder, extensions)
        raw_coordinates: list[tuple[float, float]] = []
        gpx_points: list[GpxPoint] = []
        current_folder_date = folder_date(folder.name)
        progress.update(folder.name, 0, len(files))

        for start in range(0, len(files), args.exiftool_batch_size):
            batch = files[start : start + args.exiftool_batch_size]
            records = read_gps_records(batch)
            raw_coordinates.extend(
                coordinates_from_records(
                    records,
                    args.allow_zero_coordinates,
                    args.minimum_capture_date,
                    current_folder_date,
                    args.folder_date_tolerance_days,
                )
            )
            gpx_points.extend(
                gpx_points_from_records(
                    records,
                    args.allow_zero_coordinates,
                    args.minimum_capture_date,
                    current_folder_date,
                    args.folder_date_tolerance_days,
                )
            )
            progress.update(folder.name, min(start + len(batch), len(files)), len(files))

        progress.clear()
        clusters = cluster_coordinates(raw_coordinates, args.cluster_radius_meters)
        analyses[folder.name] = FolderAnalysis(
            clusters=filter_clusters(clusters, args.min_photos_per_location),
            gpx_points=sorted(gpx_points, key=lambda point: (point.captured_at, point.source_file)),
        )

    return analyses


def cache_key(
    latitude: float,
    longitude: float,
    language: str,
    geocode_zoom: int,
    name_detail: str,
    allow_local_script: bool,
) -> str:
    script_mode = "local" if allow_local_script else "romanized"
    return (
        f"nominatim:v3:zoom{geocode_zoom}:{name_detail}:{script_mode}:"
        f"{language}:{latitude:.6f},{longitude:.6f}"
    )


def has_local_script(value: str) -> bool:
    return any(start <= char <= end for char in value for start, end in LOCAL_SCRIPT_RANGES)


def looks_like_chome(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in CHOME_MARKERS) or bool(
        re.search(r"(?:^|[\s-])\d+(?:-?chome)?$", lowered)
    )


def clean_location_value(value: Any, allow_local_script: bool, allow_chome: bool) -> str | None:
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None
    if not allow_local_script and has_local_script(text):
        return None
    if not allow_chome and looks_like_chome(text):
        return None

    return text


def first_address_value(
    address: dict[str, Any],
    fields: tuple[str, ...],
    allow_local_script: bool,
    allow_chome: bool,
) -> str | None:
    for field in fields:
        value = clean_location_value(address.get(field), allow_local_script, allow_chome)
        if value:
            return value
    return None


def namedetail_value(
    namedetails: Any,
    language: str,
    allow_local_script: bool,
    allow_chome: bool,
) -> str | None:
    if not isinstance(namedetails, dict):
        return None

    keys = (
        f"name:{language}",
        "name:en",
        "int_name",
        "name:latin",
        "name:romaji",
        "name:ja-Latn",
    )
    for key in keys:
        value = clean_location_value(namedetails.get(key), allow_local_script, allow_chome)
        if value:
            return value

    return None


def location_name_from_response(
    data: Any,
    language: str,
    name_detail: str,
    allow_local_script: bool,
) -> str | None:
    if not isinstance(data, dict):
        return None

    allow_chome = name_detail == "address"
    namedetails_name = namedetail_value(
        data.get("namedetails"),
        language,
        allow_local_script,
        allow_chome,
    )
    if namedetails_name:
        return namedetails_name

    name = data.get("name")
    category = data.get("category")
    if name and category in PLACE_NAME_CATEGORIES:
        clean_name = clean_location_value(name, allow_local_script, allow_chome)
        if clean_name:
            return clean_name

    address = data.get("address")
    if isinstance(address, dict):
        specific_name = first_address_value(
            address,
            BASIC_SPECIFIC_LOCATION_FIELDS,
            allow_local_script,
            allow_chome,
        )
        if specific_name:
            return specific_name

        if name_detail in {"specific", "address"}:
            local_name = first_address_value(
                address,
                LOCAL_PLACE_FIELDS,
                allow_local_script,
                allow_chome,
            )
            if local_name:
                return local_name

        if name_detail == "address":
            address_name = first_address_value(
                address,
                ADDRESS_DETAIL_FIELDS,
                allow_local_script,
                allow_chome,
            )
            if address_name:
                return address_name

        broad_name = first_address_value(address, BROAD_LOCATION_FIELDS, allow_local_script, allow_chome)
        if broad_name:
            return broad_name

    display_name = data.get("display_name")
    if display_name:
        first_part = str(display_name).split(",")[0].strip()
        return clean_location_value(first_part, allow_local_script, allow_chome)

    return None


def reverse_geocode(
    latitude: float,
    longitude: float,
    language: str,
    geocode_zoom: int,
    name_detail: str,
    allow_local_script: bool,
    user_agent: str,
) -> str:
    query = urlencode(
        {
            "format": "jsonv2",
            "lat": f"{latitude:.6f}",
            "lon": f"{longitude:.6f}",
            "zoom": str(geocode_zoom),
            "addressdetails": "1",
            "namedetails": "1",
        }
    )
    request = Request(
        f"https://nominatim.openstreetmap.org/reverse?{query}",
        headers={
            "Accept": "application/json",
            "Accept-Language": language,
            "User-Agent": user_agent,
        },
    )

    try:
        with urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LocationError(f"Reverse geocoder HTTP error for {latitude}, {longitude}: {exc}") from exc
    except URLError as exc:
        raise LocationError(f"Could not reach reverse geocoder: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LocationError("Reverse geocoder timed out.") from exc
    except json.JSONDecodeError as exc:
        raise LocationError("Reverse geocoder returned invalid JSON.") from exc

    location_name = location_name_from_response(
        data,
        language,
        name_detail,
        allow_local_script,
    )
    if location_name:
        return location_name

    return f"{latitude:.6f},{longitude:.6f}"


def location_for_point(
    latitude: float,
    longitude: float,
    args: argparse.Namespace,
    cache: dict[str, str],
) -> str:
    if args.no_geocode:
        return f"{latitude:.{args.coordinate_precision}f},{longitude:.{args.coordinate_precision}f}"

    key = cache_key(
        latitude,
        longitude,
        args.language,
        args.geocode_zoom,
        args.name_detail,
        args.allow_local_script,
    )
    if key not in cache:
        cache[key] = reverse_geocode(
            latitude,
            longitude,
            args.language,
            args.geocode_zoom,
            args.name_detail,
            args.allow_local_script,
            args.user_agent,
        )
        save_cache(args.cache, cache)
        time.sleep(1.0)
    return cache[key]


def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def build_rows(
    folders: list[Path],
    coordinates_by_folder: dict[str, list[LocationCluster]],
    args: argparse.Namespace,
    cache: dict[str, str],
) -> list[list[str]]:
    rows: list[list[str]] = []
    for folder in folders:
        locations = [
            location_for_point(cluster.latitude, cluster.longitude, args, cache)
            for cluster in coordinates_by_folder.get(folder.name, [])
        ]
        locations = unique_preserving_order(locations)
        if locations or args.include_empty:
            rows.append([folder.name, ", ".join(locations)])
    return rows


def gpx_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return safe.strip("._") or "track"


def write_gpx_file(path: Path, folder_name: str, points: list[GpxPoint]) -> None:
    gpx = ET.Element(
        "gpx",
        {
            "version": "1.1",
            "creator": "get-image-locations",
            "xmlns": "http://www.topografix.com/GPX/1/1",
        },
    )
    metadata = ET.SubElement(gpx, "metadata")
    ET.SubElement(metadata, "name").text = folder_name
    track = ET.SubElement(gpx, "trk")
    ET.SubElement(track, "name").text = folder_name
    segment = ET.SubElement(track, "trkseg")

    for point in points:
        track_point = ET.SubElement(
            segment,
            "trkpt",
            {
                "lat": f"{point.latitude:.8f}",
                "lon": f"{point.longitude:.8f}",
            },
        )
        ET.SubElement(track_point, "time").text = gpx_time(point.captured_at)
        ET.SubElement(track_point, "name").text = Path(point.source_file).name

    tree = ET.ElementTree(gpx)
    ET.indent(tree, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def write_gpx_files(
    folders: list[Path],
    analyses: dict[str, FolderAnalysis],
    output_dir: Path,
    args: argparse.Namespace,
) -> dict[str, tuple[int, int]]:
    written: dict[str, tuple[int, int]] = {}
    output_dir.mkdir(parents=True, exist_ok=True)

    for folder in folders:
        original_points = analyses.get(folder.name, FolderAnalysis([], [])).gpx_points
        simplified_points = simplify_gpx_points(
            original_points,
            args.gpx_simplify_distance_meters,
            args.gpx_simplify_time_seconds,
        )
        limited_points = limit_gpx_points(simplified_points, args.gpx_max_points)
        write_gpx_file(output_dir / f"{safe_filename(folder.name)}.gpx", folder.name, limited_points)
        written[folder.name] = (len(original_points), len(limited_points))

    return written


def write_csv(rows: list[list[str]], output: Path | None) -> None:
    stdout_writer = csv.writer(
        sys.stdout,
        delimiter=";",
        quoting=csv.QUOTE_ALL,
        lineterminator="\n",
    )
    stdout_writer.writerows(rows)

    if output:
        with output.open("w", encoding="utf-8", newline="") as handle:
            file_writer = csv.writer(
                handle,
                delimiter=";",
                quoting=csv.QUOTE_ALL,
                lineterminator="\n",
            )
            file_writer.writerows(rows)


def main() -> int:
    args = parse_args()
    extensions = {item.strip().lower().lstrip(".") for item in args.extensions.split(",") if item.strip()}
    selected = set(args.folder) if args.folder else None
    progress = Progress(enabled=not args.no_progress)

    try:
        if args.exiftool_batch_size < 1:
            raise LocationError("--exiftool-batch-size must be at least 1.")
        if args.cluster_radius_meters < 0:
            raise LocationError("--cluster-radius-meters cannot be negative.")
        if args.min_photos_per_location < 1:
            raise LocationError("--min-photos-per-location must be at least 1.")
        if not 0 <= args.geocode_zoom <= 18:
            raise LocationError("--geocode-zoom must be between 0 and 18.")
        if args.gpx_only and not args.gpx_output_dir:
            raise LocationError("--gpx-only requires --gpx-output-dir.")
        if args.gpx_max_points < 0:
            raise LocationError("--gpx-max-points cannot be negative.")
        if args.gpx_simplify_distance_meters < 0:
            raise LocationError("--gpx-simplify-distance-meters cannot be negative.")
        if args.gpx_simplify_time_seconds < 0:
            raise LocationError("--gpx-simplify-time-seconds cannot be negative.")
        if args.folder_date_tolerance_days < -1:
            raise LocationError("--folder-date-tolerance-days must be -1 or greater.")
        args.minimum_capture_date = parse_iso_date(args.min_capture_date)

        cache = {} if args.gpx_only else load_cache(args.cache)
        folders = discover_folders(args.root, selected)
        analyses = analyze_folders(folders, extensions, args, progress)

        if args.gpx_output_dir:
            written = write_gpx_files(folders, analyses, args.gpx_output_dir, args)
            for folder_name, (original_count, written_count) in written.items():
                print(f"GPX {folder_name}: {written_count}/{original_count} points written", file=sys.stderr)

        if not args.gpx_only:
            coordinates = {folder_name: analysis.clusters for folder_name, analysis in analyses.items()}
            rows = build_rows(folders, coordinates, args, cache)
            write_csv(rows, args.output)
    except LocationError as exc:
        progress.clear()
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
