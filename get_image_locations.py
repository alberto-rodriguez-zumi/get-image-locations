#!/usr/bin/env python3
"""Summarize GPS photo locations by immediate subfolder.

The script reads GPS coordinates with exiftool, reverse-geocodes them, and
prints CSV rows like:

    "2026-06-02";"Matsumoto, Azumino"
"""

from __future__ import annotations

import argparse
import csv
import json
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


class LocationError(RuntimeError):
    """Raised when the location workflow cannot continue."""


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
        help="Decimals used to group nearby coordinates before geocoding. Default: 2.",
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


def read_gps_records(paths: list[Path], extensions: list[str]) -> list[dict[str, Any]]:
    command = [
        "exiftool",
        "-json",
        "-n",
        "-r",
        "-GPSLatitude",
        "-GPSLongitude",
        "-FileType",
    ]
    for extension in extensions:
        command.extend(["-ext", extension.lower().lstrip(".")])
    command.extend(str(path) for path in paths)

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


def record_folder_name(root: Path, source_file: str) -> str | None:
    try:
        relative = Path(source_file).resolve().relative_to(root.resolve())
    except ValueError:
        return None
    if len(relative.parts) < 2:
        return None
    return relative.parts[0]


def grouped_coordinates(
    root: Path,
    folder_names: set[str],
    records: list[dict[str, Any]],
    precision: int,
) -> dict[str, list[tuple[float, float]]]:
    grouped: dict[str, list[tuple[float, float]]] = {name: [] for name in folder_names}
    seen_by_folder: dict[str, set[tuple[float, float]]] = {name: set() for name in folder_names}

    for record in records:
        source_file = record.get("SourceFile")
        latitude = record.get("GPSLatitude")
        longitude = record.get("GPSLongitude")
        if not source_file or latitude is None or longitude is None:
            continue

        folder_name = record_folder_name(root, str(source_file))
        if folder_name not in folder_names:
            continue

        point = (round(float(latitude), precision), round(float(longitude), precision))
        if point not in seen_by_folder[folder_name]:
            seen_by_folder[folder_name].add(point)
            grouped[folder_name].append(point)

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
    coordinates_by_folder: dict[str, list[tuple[float, float]]],
    args: argparse.Namespace,
    cache: dict[str, str],
) -> list[list[str]]:
    rows: list[list[str]] = []
    for folder in folders:
        locations = [
            location_for_point(latitude, longitude, args, cache)
            for latitude, longitude in coordinates_by_folder.get(folder.name, [])
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
    extensions = [item.strip() for item in args.extensions.split(",") if item.strip()]
    selected = set(args.folder) if args.folder else None

    try:
        cache = load_cache(args.cache)
        folders = discover_folders(args.root, selected)
        folder_names = {folder.name for folder in folders}
        records = read_gps_records(folders if selected else [args.root], extensions)
        coordinates = grouped_coordinates(args.root, folder_names, records, args.coordinate_precision)
        rows = build_rows(folders, coordinates, args, cache)
        write_csv(rows, args.output)
    except LocationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
