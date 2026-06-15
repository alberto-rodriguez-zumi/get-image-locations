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
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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

LOCATION_FIELDS = (
    "city",
    "town",
    "village",
    "municipality",
    "city_district",
    "suburb",
    "county",
    "state",
)

EARTH_RADIUS_METERS = 6_371_008.8


class LocationError(RuntimeError):
    """Raised when the location workflow cannot continue."""


@dataclass(frozen=True)
class LocationCluster:
    latitude: float
    longitude: float
    count: int


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


def coordinates_from_records(records: list[dict[str, Any]]) -> list[tuple[float, float]]:
    coordinates: list[tuple[float, float]] = []

    for record in records:
        latitude = record.get("GPSLatitude")
        longitude = record.get("GPSLongitude")
        if latitude is None or longitude is None:
            continue
        coordinates.append((float(latitude), float(longitude)))

    return coordinates


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


def coordinates_by_folder(
    folders: list[Path],
    extensions: set[str],
    args: argparse.Namespace,
    progress: Progress,
) -> dict[str, list[LocationCluster]]:
    grouped: dict[str, list[LocationCluster]] = {}

    for folder in folders:
        files = discover_media_files(folder, extensions)
        raw_coordinates: list[tuple[float, float]] = []
        progress.update(folder.name, 0, len(files))

        for start in range(0, len(files), args.exiftool_batch_size):
            batch = files[start : start + args.exiftool_batch_size]
            records = read_gps_records(batch)
            raw_coordinates.extend(coordinates_from_records(records))
            progress.update(folder.name, min(start + len(batch), len(files)), len(files))

        progress.clear()
        clusters = cluster_coordinates(raw_coordinates, args.cluster_radius_meters)
        grouped[folder.name] = filter_clusters(clusters, args.min_photos_per_location)

    return grouped


def cache_key(latitude: float, longitude: float, language: str) -> str:
    return f"nominatim:{language}:{latitude:.6f},{longitude:.6f}"


def reverse_geocode(
    latitude: float,
    longitude: float,
    language: str,
    user_agent: str,
) -> str:
    query = urlencode(
        {
            "format": "jsonv2",
            "lat": f"{latitude:.6f}",
            "lon": f"{longitude:.6f}",
            "zoom": "10",
            "addressdetails": "1",
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

    address = data.get("address") if isinstance(data, dict) else None
    if isinstance(address, dict):
        for field in LOCATION_FIELDS:
            value = address.get(field)
            if value:
                return str(value)

    display_name = data.get("display_name") if isinstance(data, dict) else None
    if display_name:
        return str(display_name).split(",")[0].strip()
    return f"{latitude:.6f},{longitude:.6f}"


def location_for_point(
    latitude: float,
    longitude: float,
    args: argparse.Namespace,
    cache: dict[str, str],
) -> str:
    if args.no_geocode:
        return f"{latitude:.{args.coordinate_precision}f},{longitude:.{args.coordinate_precision}f}"

    key = cache_key(latitude, longitude, args.language)
    if key not in cache:
        cache[key] = reverse_geocode(latitude, longitude, args.language, args.user_agent)
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

        cache = load_cache(args.cache)
        folders = discover_folders(args.root, selected)
        coordinates = coordinates_by_folder(folders, extensions, args, progress)
        rows = build_rows(folders, coordinates, args, cache)
        write_csv(rows, args.output)
    except LocationError as exc:
        progress.clear()
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
