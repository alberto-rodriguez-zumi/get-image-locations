# Get Image Locations

CLI script to read GPS coordinates from photos and videos organized in subfolders, turn them into place names, and print a per-folder summary.

Example output:

```csv
"2026-06-02";"Matsumoto, Azumino"
```

## Dependencies

No external Python packages are required. The script uses only the Python standard library.

You need:

- Python 3.10 or newer
- `exiftool`, to read GPS metadata from HEIC, JPEG, MOV, RAW, and similar formats
- An internet connection if you want to convert coordinates into place names

### Install on macOS

```bash
brew install exiftool
```

Python 3 is often already available on macOS. If you need to install it:

```bash
brew install python
```

### Install on Debian/Ubuntu

```bash
sudo apt update
sudo apt install libimage-exiftool-perl python3
```

## Usage

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026"
```

Privacy note: this mode sends rounded GPS coordinates to Nominatim/OpenStreetMap in order to convert them into place names. If you want a fully local workflow, use `--no-geocode`.

By default the script:

- Reads immediate subfolders under the root folder
- Scans HEIC, HEIF, JPG, JPEG, PNG, TIFF, DNG, several RAW formats, MOV, and MP4
- Groups nearby coordinates within a 1000 meter radius before geocoding
- Can hide locations that have very few photos
- Uses Nominatim/OpenStreetMap reverse geocoding with medium detail
- Prints CSV to stdout
- Shows progress on stderr so it does not pollute CSV output

## Export to CSV

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --output locations.csv
```

The script always prints the result to stdout and, if you pass `--output`, also writes it to a file.

## Generate GPX for Lightroom

You can generate one GPX file per subfolder using GPS coordinates and capture time:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --gpx-output-dir gpx
```

This keeps CSV output on stdout and writes files such as `gpx/2026-06-02.gpx`. If you only want GPX files and do not want CSV or place-name lookup:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --gpx-output-dir gpx \
  --gpx-only
```

To stay within Lightroom point limits:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --gpx-output-dir gpx \
  --gpx-only \
  --gpx-max-points 500
```

Before applying that hard limit, the script simplifies runs of very close consecutive points: if several consecutive points stay within the configured distance and time window, it keeps only the first and last point of that run.

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --gpx-output-dir gpx \
  --gpx-only \
  --gpx-simplify-distance-meters 25 \
  --gpx-simplify-time-seconds 300
```

Default values:

- `--gpx-simplify-distance-meters 25`
- `--gpx-simplify-time-seconds 300`
- `--gpx-max-points 0`, meaning no hard limit

## Ignore bad metadata

By default, the script discards obviously suspicious points:

- Coordinates outside valid latitude/longitude ranges
- Coordinates at `0,0`, which are common in broken metadata
- Capture dates earlier than `2000-01-01`
- In folders whose name matches `YYYY-MM-DD`, files whose capture date is more than 2 days away from that folder date

If your folders do not contain dates in their names, that last filter does not apply. It is only enabled when the folder name is exactly a date such as `2026-06-02`.

You can tune these filters:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --min-capture-date 2020-01-01 \
  --folder-date-tolerance-days 5
```

To disable the folder-date check entirely:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --folder-date-tolerance-days -1
```

If you really want to keep `0,0` coordinates:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --allow-zero-coordinates
```

## Process a Single Folder

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --folder 2026-06-02
```

You can pass more than one folder:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --folder 2026-06-01 \
  --folder 2026-06-02
```

## Show Coordinates Without Internet Access

This is useful to verify that GPS metadata is being read correctly:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --folder 2026-06-02 \
  --no-geocode
```

Example:

```csv
"2026-06-02";"36.047,138.119, 36.048,138.122"
```

## Location Cache

Reverse geocoding results are stored in `.geocode-cache.json` so the script does not repeat the same lookups.

You can change the cache path:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --cache cache-japan.json
```

## Progress

While processing, the script shows the current folder and the number of analyzed files:

```text
Processing 2026-06-02: 100/248 files analyzed
```

Progress is written to stderr, not stdout. That means you can redirect CSV output without contaminating it:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --output locations.csv
```

To hide progress:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --no-progress
```

## Group Nearby Points

To reduce calls to the map service, nearby GPS coordinates are grouped before geocoding. By default, points within 1000 meters are grouped together.

You can adjust the radius:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --cluster-radius-meters 2500
```

To disable this grouping:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --cluster-radius-meters 0
```

## Hide Locations With Very Few Photos

You can require a minimum number of GPS-tagged photos or videos for a location to appear in the output:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --min-photos-per-location 3
```

This is useful for removing logistical stops, hotels, or shops where you only took one or two stray pictures. The filter is applied after distance-based grouping, so those locations are dropped before any reverse geocoding call is made.

## Coordinate Precision

The `--coordinate-precision` parameter controls how many decimals are printed when you use `--no-geocode`:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --coordinate-precision 2
```

Useful values:

- `2`: compact output, good for quick inspection
- `3`: more detail in printed coordinates
- `4`: much more detail if you need to audit specific points

## Language

You can ask for localized names:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --language es
```

By default it uses `en`, which tends to produce more stable export-friendly names.

## Name Detail

By default, the script asks Nominatim for medium-detail names. It tries to avoid postal-style outputs such as `Ginza 2`, `Kuramae 2-chome`, or `Oshiage 1`, while still prioritizing tourist or historic places when OpenStreetMap provides them by name.

You can adjust the level with `--geocode-zoom`, from `0` to `18`:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --geocode-zoom 12
```

Useful values:

- `10`: broader output, usually province/region/municipality
- `12` or `14`: a good balance between city, town, and area; `12` is the default
- `16`: more specific, useful in some cases but more likely to return numbered neighborhoods
- `18`: very specific, and may end up returning buildings, streets, or other address-like objects

You can also control what style of name is preferred:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --name-detail specific
```

Modes:

- `balanced`: default; avoids address-like `chome` names
- `specific`: allows smaller localities such as `hamlet` or `locality`, but still avoids address-style names
- `address`: allows address-style names, numbered neighborhoods, and `chome`

By default, the script tries to avoid local-script names such as kanji or kana when a romanized or broader alternative is available. If you want to keep local-script names:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --allow-local-script
```

The cache key includes zoom level, naming mode, and script preference, so changing those parameters triggers fresh lookups rather than reusing older results that were too broad or too specific.

## Notes About Nominatim/OpenStreetMap

The script uses the public Nominatim endpoint and waits 1 second between new reverse-geocoding requests, following the service recommendations. For large libraries, the first run may take a while; later runs will be faster thanks to the cache.

If you plan to process thousands of coordinates regularly, it is worth considering your own geocoding service or a commercial reverse-geocoding provider.
