#!/usr/bin/env python3
"""Summarize GPS photo locations by immediate subfolder.

The script reads GPS coordinates with exiftool, reverse-geocodes them, and
prints CSV rows like:

    "2026-06-02";"Matsumoto, Azumino"
"""

from __future__ import annotations

import argparse
import configparser
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import BytesIO
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
CONFIG_PATH = Path(__file__).with_name("get_image_locations.cfg")
CONFIG_SECTION = "defaults"

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
WEB_MERCATOR_MAX_LATITUDE = 85.05112878
TILE_SIZE = 256
HEATMAP_OUTPUT_EXTENSION = ".png"

HEATMAP_TILE_PROVIDERS = {
    "carto-light-nolabels": "https://basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
    "carto-light": "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
    "carto-dark-nolabels": "https://basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
    "carto-voyager": "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
    "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "none": "",
    "custom": "",
}

CONFIG_OPTION_TYPES = {
    "root": Path,
    "output": Path,
    "folder": list,
    "exclude_folder": list,
    "cache": Path,
    "coordinate_precision": int,
    "cluster_radius_meters": float,
    "min_photos_per_location": int,
    "language": str,
    "geocode_zoom": int,
    "name_detail": str,
    "allow_local_script": bool,
    "user_agent": str,
    "no_geocode": bool,
    "extensions": str,
    "include_empty": bool,
    "no_progress": bool,
    "exiftool_batch_size": int,
    "gpx_enabled": bool,
    "gpx_output_dir": Path,
    "gpx_only": bool,
    "gpx_max_points": int,
    "gpx_simplify_distance_meters": float,
    "gpx_simplify_time_seconds": int,
    "heatmap_enabled": bool,
    "heatmap_output": Path,
    "heatmap_only": bool,
    "heatmap_width": int,
    "heatmap_aspect_ratio": str,
    "heatmap_orientation": str,
    "heatmap_cluster_radius_meters": float,
    "heatmap_point_radius_pixels": int,
    "heatmap_blur_pixels": int,
    "heatmap_opacity": float,
    "heatmap_map_style": str,
    "heatmap_tile_url": str,
    "heatmap_tile_cache": Path,
    "heatmap_country": str,
    "heatmap_bounds": str,
    "heatmap_padding_ratio": float,
    "heatmap_min_zoom": int,
    "heatmap_max_zoom": int,
    "heatmap_trim_edge_outliers_km": float,
    "min_capture_date": str,
    "folder_date_tolerance_days": int,
    "allow_zero_coordinates": bool,
}


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
    coordinates: list[tuple[float, float]]
    clusters: list[LocationCluster]
    gpx_points: list[GpxPoint]


@dataclass(frozen=True)
class HeatmapBounds:
    south: float
    west: float
    north: float
    east: float


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


def split_config_list(value: str) -> list[str]:
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


def format_config_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value if str(item).strip())
    return str(value)


def parse_config_value(key: str, value: str) -> Any:
    value_type = CONFIG_OPTION_TYPES.get(key)
    if value_type is None:
        return value
    if value_type is bool:
        parser = configparser.ConfigParser()
        parser[CONFIG_SECTION] = {key: value}
        return parser.getboolean(CONFIG_SECTION, key)
    if value_type is list:
        return split_config_list(value)
    if value_type is Path:
        return Path(value)
    try:
        return value_type(value)
    except ValueError as exc:
        raise LocationError(f"Invalid value for config option '{key}': {value}") from exc


def load_config_defaults(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}

    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        raise LocationError(f"Could not read config file: {path}") from exc

    if not parser.has_section(CONFIG_SECTION):
        return {}

    defaults: dict[str, Any] = {}
    for key, value in parser.items(CONFIG_SECTION):
        normalized_key = key.replace("-", "_")
        if normalized_key not in CONFIG_OPTION_TYPES:
            continue
        if value.strip():
            defaults[normalized_key] = parse_config_value(normalized_key, value)
    return defaults


def save_config_defaults(values: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    parser = configparser.ConfigParser()
    parser[CONFIG_SECTION] = {}
    for key in CONFIG_OPTION_TYPES:
        value = values.get(key)
        if value is None or value == "" or value == []:
            continue
        parser[CONFIG_SECTION][key] = format_config_value(value)

    with path.open("w", encoding="utf-8") as handle:
        parser.write(handle)


def parse_args() -> argparse.Namespace:
    preliminary_parser = argparse.ArgumentParser(add_help=False)
    preliminary_parser.add_argument("--no-config", action="store_true")
    preliminary_args, _remaining_args = preliminary_parser.parse_known_args()
    config_defaults = {} if preliminary_args.no_config else load_config_defaults()

    def configured(name: str, fallback: Any = None) -> Any:
        return config_defaults.get(name, fallback)

    parser = argparse.ArgumentParser(
        description="Read GPS metadata from media files and summarize locations by folder."
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Ignore get_image_locations.cfg even if it exists.",
    )
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=configured("root"),
        help="Folder containing dated subfolders with photos/videos.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=configured("output"),
        help="Optional CSV output path. Rows are always printed to stdout too.",
    )
    parser.add_argument(
        "--folder",
        action="append",
        help="Only process this immediate subfolder name. Can be passed multiple times.",
    )
    parser.add_argument(
        "--exclude-folder",
        action="append",
        help="Skip this immediate subfolder name. Can be passed multiple times.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=configured("cache", Path(".geocode-cache.json")),
        help="JSON cache for reverse geocoding results. Default: .geocode-cache.json",
    )
    parser.add_argument(
        "--coordinate-precision",
        type=int,
        default=configured("coordinate_precision", 2),
        help="Decimals used when printing coordinates with --no-geocode. Default: 2.",
    )
    parser.add_argument(
        "--cluster-radius-meters",
        type=float,
        default=configured("cluster_radius_meters", 1000.0),
        help="Merge GPS points within this distance before geocoding. Use 0 to disable. Default: 1000.",
    )
    parser.add_argument(
        "--min-photos-per-location",
        type=int,
        default=configured("min_photos_per_location", 1),
        help="Hide clustered locations with fewer GPS media files than this. Default: 1.",
    )
    parser.add_argument(
        "--language",
        default=configured("language", "en"),
        help="Preferred language for location names. Default: en.",
    )
    parser.add_argument(
        "--geocode-zoom",
        type=int,
        default=configured("geocode_zoom", 12),
        help="Reverse geocoder detail level from 0 to 18. Higher is more specific. Default: 12.",
    )
    parser.add_argument(
        "--name-detail",
        choices=("balanced", "specific", "address"),
        default=configured("name_detail", "balanced"),
        help="How specific location names should be. Default: balanced.",
    )
    parser.add_argument(
        "--allow-local-script",
        action="store_true",
        default=configured("allow_local_script", False),
        help="Allow local-script names such as Japanese kanji/kana when no romanized name is available.",
    )
    parser.add_argument(
        "--user-agent",
        default=configured("user_agent", "get-image-locations/1.0"),
        help="User-Agent sent to the reverse geocoding service.",
    )
    parser.add_argument(
        "--no-geocode",
        action="store_true",
        default=configured("no_geocode", False),
        help="Print rounded coordinates instead of calling the reverse geocoder.",
    )
    parser.add_argument(
        "--extensions",
        default=configured("extensions", ",".join(DEFAULT_EXTENSIONS)),
        help="Comma-separated file extensions to scan.",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        default=configured("include_empty", False),
        help="Include folders that have no GPS locations.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        default=configured("no_progress", False),
        help="Disable progress messages. Progress is written to stderr, not stdout.",
    )
    parser.add_argument(
        "--exiftool-batch-size",
        type=int,
        default=configured("exiftool_batch_size", 100),
        help="Number of files passed to each exiftool call. Default: 100.",
    )
    parser.add_argument(
        "--gpx-output-dir",
        type=Path,
        default=configured("gpx_output_dir"),
        help="Optional folder where one GPX track per input subfolder will be written.",
    )
    parser.add_argument(
        "--gpx-only",
        action="store_true",
        default=configured("gpx_only", False),
        help="Generate GPX and skip CSV/geocoding summary output. Requires --gpx-output-dir.",
    )
    parser.add_argument(
        "--gpx-max-points",
        type=int,
        default=configured("gpx_max_points", 0),
        help="Maximum points per generated GPX. Use 0 for no hard limit. Default: 0.",
    )
    parser.add_argument(
        "--gpx-simplify-distance-meters",
        type=float,
        default=configured("gpx_simplify_distance_meters", 25.0),
        help="Collapse consecutive GPX points within this distance. Default: 25.",
    )
    parser.add_argument(
        "--gpx-simplify-time-seconds",
        type=int,
        default=configured("gpx_simplify_time_seconds", 300),
        help="Collapse consecutive GPX points within this time gap. Default: 300.",
    )
    parser.add_argument(
        "--heatmap-output",
        type=Path,
        default=configured("heatmap_output"),
        help="Optional PNG output path for a Google Photos-style photo heatmap.",
    )
    parser.add_argument(
        "--heatmap-only",
        action="store_true",
        default=configured("heatmap_only", False),
        help="Only generate the heatmap image. Requires --heatmap-output and skips CSV/GPX output.",
    )
    parser.add_argument(
        "--heatmap-width",
        type=int,
        default=configured("heatmap_width", 1600),
        help="Heatmap image width in pixels. Height is derived from aspect ratio. Default: 1600.",
    )
    parser.add_argument(
        "--heatmap-aspect-ratio",
        default=configured("heatmap_aspect_ratio", "16:9"),
        help="Heatmap aspect ratio, such as 1:1, 4:3, 3:2, 16:9, portrait, or landscape. Default: 16:9.",
    )
    parser.add_argument(
        "--heatmap-orientation",
        choices=("landscape", "portrait"),
        default=configured("heatmap_orientation", "landscape"),
        help="Image orientation applied to non-square aspect ratios. Default: landscape.",
    )
    parser.add_argument(
        "--heatmap-cluster-radius-meters",
        type=float,
        default=configured("heatmap_cluster_radius_meters", 250.0),
        help="Merge heatmap photo points within this distance before drawing. Use 0 to disable. Default: 250.",
    )
    parser.add_argument(
        "--heatmap-point-radius-pixels",
        type=int,
        default=configured("heatmap_point_radius_pixels", 6),
        help="Visual radius for each heatmap cluster before blur. Larger values make thicker heat spots. Default: 6.",
    )
    parser.add_argument(
        "--heatmap-blur-pixels",
        type=int,
        default=configured("heatmap_blur_pixels", 22),
        help="Gaussian blur radius for the heatmap overlay. Default: 22.",
    )
    parser.add_argument(
        "--heatmap-opacity",
        type=float,
        default=configured("heatmap_opacity", 0.78),
        help="Maximum heatmap overlay opacity from 0 to 1. Default: 0.78.",
    )
    parser.add_argument(
        "--heatmap-map-style",
        choices=tuple(HEATMAP_TILE_PROVIDERS),
        default=configured("heatmap_map_style", "carto-light"),
        help="Base map style. Default: carto-light.",
    )
    parser.add_argument(
        "--heatmap-tile-url",
        default=configured("heatmap_tile_url"),
        help="Custom raster tile URL template with {z}, {x}, and {y}. Use with --heatmap-map-style custom.",
    )
    parser.add_argument(
        "--heatmap-tile-cache",
        type=Path,
        default=configured("heatmap_tile_cache", Path(".tile-cache")),
        help="Folder used to cache downloaded map tiles. Default: .tile-cache",
    )
    parser.add_argument(
        "--heatmap-country",
        default=configured("heatmap_country"),
        help="Fit the map to this country name using Nominatim bounds instead of photo bounds.",
    )
    parser.add_argument(
        "--heatmap-bounds",
        default=configured("heatmap_bounds"),
        help="Fit the map to explicit bounds as south,west,north,east or lat1,lon1,lat2,lon2.",
    )
    parser.add_argument(
        "--heatmap-padding-ratio",
        type=float,
        default=configured("heatmap_padding_ratio", 0.08),
        help="Extra padding around automatic photo bounds. Default: 0.08.",
    )
    parser.add_argument(
        "--heatmap-min-zoom",
        type=int,
        default=configured("heatmap_min_zoom", 0),
        help="Minimum map tile zoom for heatmap rendering. Default: 0.",
    )
    parser.add_argument(
        "--heatmap-max-zoom",
        type=int,
        default=configured("heatmap_max_zoom", 12),
        help="Maximum map tile zoom for heatmap rendering. Default: 12.",
    )
    parser.add_argument(
        "--heatmap-trim-edge-outliers-km",
        type=float,
        default=configured("heatmap_trim_edge_outliers_km", 0.0),
        help="Trim chronological start/end trip segments separated by at least this distance. Use 0 to disable. Default: 0.",
    )
    parser.add_argument(
        "--min-capture-date",
        default=configured("min_capture_date", DEFAULT_MIN_CAPTURE_DATE),
        help=f"Ignore media captured before this date. Default: {DEFAULT_MIN_CAPTURE_DATE}.",
    )
    parser.add_argument(
        "--folder-date-tolerance-days",
        type=int,
        default=configured("folder_date_tolerance_days", 2),
        help="Ignore dated-folder media captured more than this many days away from the folder date. Use -1 to disable. Default: 2.",
    )
    parser.add_argument(
        "--allow-zero-coordinates",
        action="store_true",
        default=configured("allow_zero_coordinates", False),
        help="Keep GPS points at 0,0 instead of treating them as invalid.",
    )
    args = parser.parse_args()
    if args.folder is None:
        args.folder = config_defaults.get("folder")
    if args.exclude_folder is None:
        args.exclude_folder = config_defaults.get("exclude_folder")
    return args


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


def discover_folders(root: Path, selected: set[str] | None, excluded: set[str]) -> list[Path]:
    if not root.exists():
        raise LocationError(f"Root folder does not exist: {root}")
    if not root.is_dir():
        raise LocationError(f"Root path is not a folder: {root}")

    folders = sorted(path for path in root.iterdir() if path.is_dir())
    by_name = {path.name: path for path in folders}

    missing_excluded = sorted(excluded - set(by_name))
    if missing_excluded:
        raise LocationError(f"Excluded subfolder not found under {root}: {', '.join(missing_excluded)}")

    if selected is None:
        return [folder for folder in folders if folder.name not in excluded]

    missing = sorted(selected - set(by_name))
    if missing:
        raise LocationError(f"Subfolder not found under {root}: {', '.join(missing)}")
    return [by_name[name] for name in sorted(selected) if name not in excluded]


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
            coordinates=raw_coordinates,
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
        original_points = analyses.get(folder.name, FolderAnalysis([], [], [])).gpx_points
        simplified_points = simplify_gpx_points(
            original_points,
            args.gpx_simplify_distance_meters,
            args.gpx_simplify_time_seconds,
        )
        limited_points = limit_gpx_points(simplified_points, args.gpx_max_points)
        write_gpx_file(output_dir / f"{safe_filename(folder.name)}.gpx", folder.name, limited_points)
        written[folder.name] = (len(original_points), len(limited_points))

    return written


def parse_heatmap_aspect_ratio(value: str) -> tuple[int, int]:
    normalized = value.strip().lower()
    if normalized == "landscape":
        return 4, 3
    if normalized == "portrait":
        return 3, 4

    match = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", normalized)
    if not match:
        raise LocationError(
            "Invalid --heatmap-aspect-ratio. Use values like 1:1, 4:3, 3:2, 16:9, portrait, or landscape."
        )

    width_ratio = float(match.group(1))
    height_ratio = float(match.group(2))
    if width_ratio <= 0 or height_ratio <= 0:
        raise LocationError("--heatmap-aspect-ratio values must be greater than zero.")

    return int(width_ratio * 1000), int(height_ratio * 1000)


def heatmap_dimensions(width: int, aspect_ratio: str, orientation: str) -> tuple[int, int]:
    ratio_width, ratio_height = parse_heatmap_aspect_ratio(aspect_ratio)
    if orientation == "portrait" and ratio_width > ratio_height:
        ratio_width, ratio_height = ratio_height, ratio_width
    if orientation == "landscape" and ratio_height > ratio_width:
        ratio_width, ratio_height = ratio_height, ratio_width

    height = max(1, round(width * ratio_height / ratio_width))
    return width, height


def validate_heatmap_output_path(path: Path) -> None:
    if path.suffix.casefold() == HEATMAP_OUTPUT_EXTENSION:
        return

    hint = ""
    if re.fullmatch(r"\d+(?:\.\d+)?:\d+(?:\.\d+)?", str(path)):
        hint = " Did you mean to use --heatmap-aspect-ratio and pass a PNG path to --heatmap-output?"
    raise LocationError(f"--heatmap-output must be a .png file path.{hint}")


def parse_heatmap_bounds(value: str) -> HeatmapBounds:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise LocationError("--heatmap-bounds must contain four comma-separated numbers.")

    try:
        first_lat, first_lon, second_lat, second_lon = (float(part) for part in parts)
    except ValueError as exc:
        raise LocationError("--heatmap-bounds must contain valid numbers.") from exc

    south = min(first_lat, second_lat)
    north = max(first_lat, second_lat)
    west = min(first_lon, second_lon)
    east = max(first_lon, second_lon)
    return normalized_heatmap_bounds(HeatmapBounds(south=south, west=west, north=north, east=east))


def normalized_heatmap_bounds(bounds: HeatmapBounds) -> HeatmapBounds:
    south = max(-WEB_MERCATOR_MAX_LATITUDE, min(WEB_MERCATOR_MAX_LATITUDE, bounds.south))
    north = max(-WEB_MERCATOR_MAX_LATITUDE, min(WEB_MERCATOR_MAX_LATITUDE, bounds.north))
    west = max(-180.0, min(180.0, bounds.west))
    east = max(-180.0, min(180.0, bounds.east))

    if south > north:
        south, north = north, south
    if west > east:
        west, east = east, west
    if math.isclose(south, north):
        south -= 0.01
        north += 0.01
    if math.isclose(west, east):
        west -= 0.01
        east += 0.01

    return HeatmapBounds(
        south=max(-WEB_MERCATOR_MAX_LATITUDE, south),
        west=max(-180.0, west),
        north=min(WEB_MERCATOR_MAX_LATITUDE, north),
        east=min(180.0, east),
    )


def bounds_from_coordinates(
    coordinates: list[tuple[float, float]],
    padding_ratio: float,
) -> HeatmapBounds:
    if not coordinates:
        raise LocationError("No valid GPS coordinates were found for heatmap generation.")

    latitudes = [latitude for latitude, _ in coordinates]
    longitudes = [longitude for _, longitude in coordinates]
    south = min(latitudes)
    north = max(latitudes)
    west = min(longitudes)
    east = max(longitudes)

    lat_span = max(north - south, 0.02)
    lon_span = max(east - west, 0.02)
    padding = max(0.0, padding_ratio)
    return normalized_heatmap_bounds(
        HeatmapBounds(
            south=south - (lat_span * padding),
            west=west - (lon_span * padding),
            north=north + (lat_span * padding),
            east=east + (lon_span * padding),
        )
    )


def heatmap_country_cache_key(country: str, language: str) -> str:
    return f"nominatim:country-bounds:v1:{language}:{country.casefold()}"


def heatmap_bounds_from_country(
    country: str,
    args: argparse.Namespace,
    cache: dict[str, str],
) -> HeatmapBounds:
    key = heatmap_country_cache_key(country, args.language)
    if key in cache:
        return parse_heatmap_bounds(cache[key])

    query = urlencode({"format": "jsonv2", "q": country, "limit": "1"})
    request = Request(
        f"https://nominatim.openstreetmap.org/search?{query}",
        headers={
            "Accept": "application/json",
            "Accept-Language": args.language,
            "User-Agent": args.user_agent,
        },
    )

    try:
        with urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LocationError(f"Country lookup HTTP error for '{country}': {exc}") from exc
    except URLError as exc:
        raise LocationError(f"Could not reach country lookup service: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LocationError("Country lookup timed out.") from exc
    except json.JSONDecodeError as exc:
        raise LocationError("Country lookup returned invalid JSON.") from exc

    if not isinstance(data, list) or not data:
        raise LocationError(f"Country not found for heatmap bounds: {country}")

    bounding_box = data[0].get("boundingbox")
    if not isinstance(bounding_box, list) or len(bounding_box) != 4:
        raise LocationError(f"Country lookup did not return usable bounds for: {country}")

    south, north, west, east = (float(value) for value in bounding_box)
    bounds = normalized_heatmap_bounds(HeatmapBounds(south=south, west=west, north=north, east=east))
    cache[key] = f"{bounds.south},{bounds.west},{bounds.north},{bounds.east}"
    save_cache(args.cache, cache)
    time.sleep(1.0)
    return bounds


def heatmap_bounds(
    coordinates: list[tuple[float, float]],
    args: argparse.Namespace,
    cache: dict[str, str],
) -> HeatmapBounds:
    if args.heatmap_bounds:
        return parse_heatmap_bounds(args.heatmap_bounds)
    if args.heatmap_country:
        return heatmap_bounds_from_country(args.heatmap_country, args, cache)
    return bounds_from_coordinates(coordinates, args.heatmap_padding_ratio)


def clamp_mercator_latitude(latitude: float) -> float:
    return max(-WEB_MERCATOR_MAX_LATITUDE, min(WEB_MERCATOR_MAX_LATITUDE, latitude))


def lat_lon_to_world_pixels(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
    latitude = clamp_mercator_latitude(latitude)
    scale = TILE_SIZE * (2**zoom)
    x = (longitude + 180.0) / 360.0 * scale
    lat_rad = math.radians(latitude)
    y = (1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * scale
    return x, y


def choose_heatmap_zoom(bounds: HeatmapBounds, width: int, height: int, min_zoom: int, max_zoom: int) -> int:
    for zoom in range(max_zoom, min_zoom - 1, -1):
        west_x, north_y = lat_lon_to_world_pixels(bounds.north, bounds.west, zoom)
        east_x, south_y = lat_lon_to_world_pixels(bounds.south, bounds.east, zoom)
        if abs(east_x - west_x) <= width * 0.9 and abs(south_y - north_y) <= height * 0.9:
            return zoom
    return min_zoom


def heatmap_tile_template(args: argparse.Namespace) -> str:
    if args.heatmap_tile_url:
        template = args.heatmap_tile_url
    else:
        template = HEATMAP_TILE_PROVIDERS[args.heatmap_map_style]

    if args.heatmap_map_style == "custom" and not template:
        raise LocationError("--heatmap-map-style custom requires --heatmap-tile-url.")
    if template and not all(token in template for token in ("{z}", "{x}", "{y}")):
        raise LocationError("--heatmap-tile-url must contain {z}, {x}, and {y}.")
    return template


def require_pillow() -> tuple[Any, Any, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError as exc:
        raise LocationError("Pillow is required for heatmap rendering. Run: python3 -m pip install -r requirements.txt") from exc
    return Image, ImageDraw, ImageFilter


def load_heatmap_tile(
    x: int,
    y: int,
    zoom: int,
    template: str,
    args: argparse.Namespace,
    Image: Any,
) -> Any:
    tile_count = 2**zoom
    if y < 0 or y >= tile_count:
        return Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (242, 242, 238, 255))

    wrapped_x = x % tile_count
    cache_path = args.heatmap_tile_cache / args.heatmap_map_style / str(zoom) / str(wrapped_x) / f"{y}.png"
    if cache_path.exists():
        return Image.open(cache_path).convert("RGBA")

    url = template.format(z=zoom, x=wrapped_x, y=y)
    request = Request(url, headers={"User-Agent": args.user_agent})
    try:
        with urlopen(request, timeout=30) as response:
            tile_bytes = response.read()
    except HTTPError as exc:
        raise LocationError(f"Map tile HTTP error for {url}: {exc}") from exc
    except URLError as exc:
        raise LocationError(f"Could not download map tile {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LocationError(f"Map tile download timed out: {url}") from exc

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(tile_bytes)
    return Image.open(BytesIO(tile_bytes)).convert("RGBA")


def render_base_heatmap_map(
    bounds: HeatmapBounds,
    width: int,
    height: int,
    args: argparse.Namespace,
) -> tuple[Any, int, float, float, Any, Any, Any]:
    Image, ImageDraw, ImageFilter = require_pillow()
    template = heatmap_tile_template(args)
    base = Image.new("RGBA", (width, height), (248, 248, 244, 255))

    zoom = choose_heatmap_zoom(bounds, width, height, args.heatmap_min_zoom, args.heatmap_max_zoom)
    west_x, north_y = lat_lon_to_world_pixels(bounds.north, bounds.west, zoom)
    east_x, south_y = lat_lon_to_world_pixels(bounds.south, bounds.east, zoom)
    center_x = (west_x + east_x) / 2.0
    center_y = (north_y + south_y) / 2.0
    top_left_x = center_x - (width / 2.0)
    top_left_y = center_y - (height / 2.0)

    if not template:
        return base, zoom, top_left_x, top_left_y, Image, ImageDraw, ImageFilter

    first_tile_x = math.floor(top_left_x / TILE_SIZE)
    last_tile_x = math.floor((top_left_x + width) / TILE_SIZE)
    first_tile_y = math.floor(top_left_y / TILE_SIZE)
    last_tile_y = math.floor((top_left_y + height) / TILE_SIZE)

    for tile_x in range(first_tile_x, last_tile_x + 1):
        for tile_y in range(first_tile_y, last_tile_y + 1):
            tile = load_heatmap_tile(tile_x, tile_y, zoom, template, args, Image)
            paste_x = round((tile_x * TILE_SIZE) - top_left_x)
            paste_y = round((tile_y * TILE_SIZE) - top_left_y)
            base.alpha_composite(tile, (paste_x, paste_y))

    return base, zoom, top_left_x, top_left_y, Image, ImageDraw, ImageFilter


def heatmap_cluster_coordinates(
    coordinates: list[tuple[float, float]],
    cluster_radius_meters: float,
) -> list[LocationCluster]:
    return cluster_coordinates(coordinates, cluster_radius_meters)


def heatmap_color(value: int, opacity: float) -> tuple[int, int, int, int]:
    if value <= 0:
        return 0, 0, 0, 0

    stops = (
        (0, (0, 80, 255)),
        (70, (0, 210, 255)),
        (135, (255, 235, 0)),
        (200, (255, 98, 0)),
        (255, (220, 0, 0)),
    )
    for index in range(len(stops) - 1):
        start_value, start_color = stops[index]
        end_value, end_color = stops[index + 1]
        if start_value <= value <= end_value:
            ratio = (value - start_value) / (end_value - start_value)
            red = round(start_color[0] + ((end_color[0] - start_color[0]) * ratio))
            green = round(start_color[1] + ((end_color[1] - start_color[1]) * ratio))
            blue = round(start_color[2] + ((end_color[2] - start_color[2]) * ratio))
            alpha = round((60 + (195 * (value / 255))) * opacity)
            return red, green, blue, max(0, min(255, alpha))

    alpha = round(255 * opacity)
    return 220, 0, 0, max(0, min(255, alpha))


def render_heatmap_overlay(
    coordinates: list[tuple[float, float]],
    width: int,
    height: int,
    zoom: int,
    top_left_x: float,
    top_left_y: float,
    args: argparse.Namespace,
    Image: Any,
    ImageDraw: Any,
    ImageFilter: Any,
) -> tuple[Any, int]:
    clusters = heatmap_cluster_coordinates(coordinates, args.heatmap_cluster_radius_meters)
    heat = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(heat, "L")
    radius = args.heatmap_point_radius_pixels

    for cluster in clusters:
        world_x, world_y = lat_lon_to_world_pixels(cluster.latitude, cluster.longitude, zoom)
        point_x = world_x - top_left_x
        point_y = world_y - top_left_y
        if point_x < -radius or point_x > width + radius or point_y < -radius or point_y > height + radius:
            continue
        intensity = max(28, min(255, round(52 * math.sqrt(cluster.count))))
        draw.ellipse(
            (point_x - radius, point_y - radius, point_x + radius, point_y + radius),
            fill=intensity,
        )

    if args.heatmap_blur_pixels > 0:
        heat = heat.filter(ImageFilter.GaussianBlur(args.heatmap_blur_pixels))

    max_value = heat.getextrema()[1]
    if max_value > 0:
        heat = heat.point(lambda value: min(255, round(value * 255 / max_value)))

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    heat_values = heat.get_flattened_data() if hasattr(heat, "get_flattened_data") else heat.getdata()
    overlay.putdata([heatmap_color(value, args.heatmap_opacity) for value in heat_values])
    return overlay, len(clusters)


def chronological_heatmap_points(analyses: dict[str, FolderAnalysis]) -> list[GpxPoint]:
    points = [point for analysis in analyses.values() for point in analysis.gpx_points]
    return sorted(points, key=lambda point: (point.captured_at, point.source_file))


def trim_edge_heatmap_outliers(points: list[GpxPoint], threshold_meters: float) -> list[GpxPoint]:
    if threshold_meters <= 0 or len(points) < 3:
        return points

    edge_limit = max(1, math.ceil(len(points) * 0.2))
    start = 0
    end = len(points)

    for index in range(0, len(points) - 1):
        distance = distance_meters(
            (points[index].latitude, points[index].longitude),
            (points[index + 1].latitude, points[index + 1].longitude),
        )
        if distance >= threshold_meters:
            if index < edge_limit:
                start = index + 1
            break

    for index in range(len(points) - 2, start - 1, -1):
        distance = distance_meters(
            (points[index].latitude, points[index].longitude),
            (points[index + 1].latitude, points[index + 1].longitude),
        )
        if distance >= threshold_meters:
            if index >= len(points) - 1 - edge_limit:
                end = index + 1
            break

    return points[start:end]


def heatmap_coordinates_from_analyses(
    analyses: dict[str, FolderAnalysis],
    args: argparse.Namespace,
) -> tuple[list[tuple[float, float]], int]:
    raw_coordinates = [coordinate for analysis in analyses.values() for coordinate in analysis.coordinates]
    if args.heatmap_trim_edge_outliers_km <= 0:
        return raw_coordinates, 0

    timed_points = chronological_heatmap_points(analyses)
    if len(timed_points) < 3:
        return raw_coordinates, 0

    trimmed_points = trim_edge_heatmap_outliers(timed_points, args.heatmap_trim_edge_outliers_km * 1000.0)
    if len(trimmed_points) == len(timed_points):
        return raw_coordinates, 0

    coordinates = [(point.latitude, point.longitude) for point in trimmed_points]
    return coordinates, len(timed_points) - len(trimmed_points)


def write_heatmap_image(
    analyses: dict[str, FolderAnalysis],
    args: argparse.Namespace,
    cache: dict[str, str],
) -> tuple[int, int, int, int, HeatmapBounds]:
    coordinates, trimmed_count = heatmap_coordinates_from_analyses(analyses, args)
    if not coordinates:
        raise LocationError("No valid GPS coordinates were found for heatmap generation.")

    width, height = heatmap_dimensions(args.heatmap_width, args.heatmap_aspect_ratio, args.heatmap_orientation)
    bounds = heatmap_bounds(coordinates, args, cache)
    base, zoom, top_left_x, top_left_y, Image, ImageDraw, ImageFilter = render_base_heatmap_map(
        bounds,
        width,
        height,
        args,
    )
    overlay, cluster_count = render_heatmap_overlay(
        coordinates,
        width,
        height,
        zoom,
        top_left_x,
        top_left_y,
        args,
        Image,
        ImageDraw,
        ImageFilter,
    )
    image = Image.alpha_composite(base, overlay).convert("RGB")
    args.heatmap_output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.heatmap_output, format="PNG")
    return len(coordinates), cluster_count, trimmed_count, zoom, bounds


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
    excluded = set(args.exclude_folder) if args.exclude_folder else set()
    progress = Progress(enabled=not args.no_progress)

    try:
        if not args.root:
            raise LocationError("Root folder is required. Pass it as an argument or set root in get_image_locations.cfg.")
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
        if args.heatmap_only and not args.heatmap_output:
            raise LocationError("--heatmap-only requires --heatmap-output.")
        if args.heatmap_output:
            validate_heatmap_output_path(args.heatmap_output)
        if args.heatmap_only and args.gpx_only:
            raise LocationError("--heatmap-only cannot be combined with --gpx-only.")
        if args.gpx_max_points < 0:
            raise LocationError("--gpx-max-points cannot be negative.")
        if args.gpx_simplify_distance_meters < 0:
            raise LocationError("--gpx-simplify-distance-meters cannot be negative.")
        if args.gpx_simplify_time_seconds < 0:
            raise LocationError("--gpx-simplify-time-seconds cannot be negative.")
        if args.heatmap_width < 1:
            raise LocationError("--heatmap-width must be at least 1.")
        if args.heatmap_cluster_radius_meters < 0:
            raise LocationError("--heatmap-cluster-radius-meters cannot be negative.")
        if args.heatmap_point_radius_pixels < 1:
            raise LocationError("--heatmap-point-radius-pixels must be at least 1.")
        if args.heatmap_blur_pixels < 0:
            raise LocationError("--heatmap-blur-pixels cannot be negative.")
        if not 0 <= args.heatmap_opacity <= 1:
            raise LocationError("--heatmap-opacity must be between 0 and 1.")
        if not 0 <= args.heatmap_min_zoom <= args.heatmap_max_zoom <= 19:
            raise LocationError("--heatmap-min-zoom and --heatmap-max-zoom must be between 0 and 19.")
        if args.heatmap_bounds and args.heatmap_country:
            raise LocationError("--heatmap-bounds and --heatmap-country cannot be used together.")
        if args.heatmap_trim_edge_outliers_km < 0:
            raise LocationError("--heatmap-trim-edge-outliers-km cannot be negative.")
        if args.folder_date_tolerance_days < -1:
            raise LocationError("--folder-date-tolerance-days must be -1 or greater.")
        args.minimum_capture_date = parse_iso_date(args.min_capture_date)

        needs_geocode_cache = not args.gpx_only and not args.heatmap_only
        needs_geocode_cache = needs_geocode_cache or bool(args.heatmap_country)
        cache = load_cache(args.cache) if needs_geocode_cache else {}
        folders = discover_folders(args.root, selected, excluded)
        analyses = analyze_folders(folders, extensions, args, progress)

        if args.heatmap_output:
            point_count, cluster_count, trimmed_count, zoom, bounds = write_heatmap_image(analyses, args, cache)
            trim_text = f", {trimmed_count} edge outliers trimmed" if trimmed_count else ""
            print(
                "Heatmap "
                f"{args.heatmap_output}: {point_count} points, {cluster_count} clusters, zoom {zoom}{trim_text} "
                f"({bounds.south:.4f},{bounds.west:.4f},{bounds.north:.4f},{bounds.east:.4f})",
                file=sys.stderr,
            )

        if args.gpx_output_dir and not args.heatmap_only:
            written = write_gpx_files(folders, analyses, args.gpx_output_dir, args)
            for folder_name, (original_count, written_count) in written.items():
                print(f"GPX {folder_name}: {written_count}/{original_count} points written", file=sys.stderr)

        if not args.gpx_only and not args.heatmap_only:
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
