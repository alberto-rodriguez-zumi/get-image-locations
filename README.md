# Get Image Locations

CLI script to read GPS coordinates from photos and videos organized in subfolders, turn them into place names, and print a per-folder summary.

Example output:

```csv
"2026-06-02";"Matsumoto, Azumino"
```

## Parameters

| Parameter | Default | Description |
| --- | --- | --- |
| `root` | Required | Root folder containing the photo/video subfolders. |
| `-o`, `--output` | None | Optional CSV output path. Rows are always printed to stdout too. |
| `--folder` | None | Only process this immediate subfolder name. Can be passed multiple times. |
| `--cache` | `.geocode-cache.json` | JSON cache for reverse geocoding and country-bound lookup results. |
| `--coordinate-precision` | `2` | Decimals used when printing coordinates with `--no-geocode`. |
| `--cluster-radius-meters` | `1000` | Merge GPS points within this distance before reverse geocoding. Use `0` to disable. |
| `--min-photos-per-location` | `1` | Hide clustered locations with fewer GPS media files than this. |
| `--language` | `en` | Preferred language for location names. |
| `--geocode-zoom` | `12` | Reverse geocoder detail level from `0` to `18`. Higher is more specific. |
| `--name-detail` | `balanced` | Location-name specificity. Choices: `balanced`, `specific`, `address`. |
| `--allow-local-script` | `false` | Allow local-script names such as Japanese kanji/kana when no romanized name is available. |
| `--user-agent` | `get-image-locations/1.0` | User-Agent sent to map/geocoding services. |
| `--no-geocode` | `false` | Print rounded coordinates instead of calling the reverse geocoder. |
| `--extensions` | `heic,heif,jpg,jpeg,png,tif,tiff,dng,cr2,cr3,nef,arw,raf,rw2,orf,mov,mp4` | Comma-separated file extensions to scan. |
| `--include-empty` | `false` | Include folders that have no GPS locations. |
| `--no-progress` | `false` | Disable progress messages on stderr. |
| `--exiftool-batch-size` | `100` | Number of files passed to each `exiftool` call. |
| `--gpx-output-dir` | None | Optional folder where one GPX track per input subfolder will be written. |
| `--gpx-only` | `false` | Generate GPX and skip CSV/geocoding summary output. Requires `--gpx-output-dir`. |
| `--gpx-max-points` | `0` | Maximum points per generated GPX. Use `0` for no hard limit. |
| `--gpx-simplify-distance-meters` | `25` | Collapse consecutive GPX points within this distance. |
| `--gpx-simplify-time-seconds` | `300` | Collapse consecutive GPX points within this time gap. |
| `--heatmap-output` | None | Optional `.png` output path for a Google Photos-style photo heatmap. |
| `--heatmap-only` | `false` | Only generate the heatmap image. Requires `--heatmap-output` and skips CSV/GPX output. |
| `--heatmap-width` | `1600` | Heatmap image width in pixels. Height is derived from aspect ratio. |
| `--heatmap-aspect-ratio` | `16:9` | Heatmap aspect ratio, such as `1:1`, `4:3`, `3:2`, `16:9`, `portrait`, or `landscape`. |
| `--heatmap-orientation` | `landscape` | Image orientation applied to non-square aspect ratios. Choices: `landscape`, `portrait`. |
| `--heatmap-cluster-radius-meters` | `250` | Merge heatmap photo points within this distance before drawing. Use `0` to disable. |
| `--heatmap-point-radius-pixels` | `6` | Visual radius for each heatmap cluster before blur. Larger values make thicker heat spots. |
| `--heatmap-blur-pixels` | `22` | Gaussian blur radius for the heatmap overlay. |
| `--heatmap-opacity` | `0.78` | Maximum heatmap overlay opacity from `0` to `1`. |
| `--heatmap-map-style` | `carto-light` | Base map style. Choices: `carto-light-nolabels`, `carto-light`, `carto-dark-nolabels`, `carto-voyager`, `osm`, `none`, `custom`. |
| `--heatmap-tile-url` | None | Custom raster tile URL template with `{z}`, `{x}`, and `{y}`. Use with `--heatmap-map-style custom`. |
| `--heatmap-tile-cache` | `.tile-cache` | Folder used to cache downloaded map tiles. |
| `--heatmap-country` | None | Fit the map to this country name using Nominatim bounds instead of photo bounds. |
| `--heatmap-bounds` | None | Fit the map to explicit bounds as `south,west,north,east` or `lat1,lon1,lat2,lon2`. |
| `--heatmap-padding-ratio` | `0.08` | Extra padding around automatic photo bounds. |
| `--heatmap-min-zoom` | `0` | Minimum map tile zoom for heatmap rendering. |
| `--heatmap-max-zoom` | `12` | Maximum map tile zoom for heatmap rendering. |
| `--heatmap-trim-edge-outliers-km` | `0` | Trim chronological start/end trip segments separated by at least this distance. Use `0` to disable. |
| `--min-capture-date` | `2000-01-01` | Ignore media captured before this date. |
| `--folder-date-tolerance-days` | `2` | Ignore dated-folder media captured more than this many days away from the folder date. Use `-1` to disable. |
| `--allow-zero-coordinates` | `false` | Keep GPS points at `0,0` instead of treating them as invalid. |

## Dependencies

You need:

- Python 3.10 or newer
- `exiftool`, to read GPS metadata from HEIC, JPEG, MOV, RAW, and similar formats
- Pillow, to render heatmap images
- Tkinter, only if you want to use the graphical launcher
- An internet connection if you want to convert coordinates into place names
  or render heatmap base maps from online tiles

Install the Python dependency:

```bash
python3 -m pip install -r requirements.txt
```

### Install on macOS

```bash
brew install exiftool
```

Python 3 is often already available on macOS. If you need to install it:

```bash
brew install python
```

Tkinter is included with the official Python.org macOS installer. If you use a
package manager Python, verify it with:

```bash
python3 -m tkinter
```

For Homebrew Python, install the matching Tkinter package if that command fails:

```bash
brew install python-tk@3.14
```

Adjust the version suffix to match your Homebrew Python version.

### Install on Debian/Ubuntu

```bash
sudo apt update
sudo apt install libimage-exiftool-perl python3 python3-tk
```

For WSL, use a distro with GUI support such as WSLg, then install the same
Debian/Ubuntu packages.

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

## Graphical Launcher

For users who are less comfortable with the terminal, there is a small Tkinter
launcher:

```bash
./get_image_locations_gui.py
```

The GUI lets you choose the photo root folder, CSV output, GPX options, heatmap
options, metadata filters, and map settings. It shows the command it will run,
then launches `get_image_locations.py` in the background and streams the output
inside the window.

The launcher is intentionally a wrapper around the CLI script. That keeps the
command-line workflow and the graphical workflow using the same implementation.

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

You can combine `--gpx-only` with `--heatmap-output` if you want GPX files plus
a heatmap image, but no CSV output.

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

## Generate a Photo Heatmap Image

You can generate a Google Photos-style heatmap image showing where the photos
were taken:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --heatmap-output japan-heatmap.png
```

This keeps the regular CSV output on stdout and writes the image to
`japan-heatmap.png`. If you only want the image:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --heatmap-output japan-heatmap.png \
  --heatmap-only
```

By default, the heatmap:

- Uses the same GPS metadata filters as CSV and GPX generation
- Fits the map to the photo locations
- Uses a `16:9` landscape image
- Uses `carto-light` as the base map
- Caches downloaded map tiles in `.tile-cache/`

`--heatmap-output` must be a `.png` file path. Use
`--heatmap-aspect-ratio` for values such as `16:9`.

### Heatmap Cluster Size

The heatmap first groups nearby photo points before drawing. A smaller cluster
radius is more precise; a larger one creates broader heat areas:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --heatmap-output japan-heatmap.png \
  --heatmap-cluster-radius-meters 100
```

You can also make the visual heat spots thicker or thinner:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --heatmap-output japan-heatmap.png \
  --heatmap-point-radius-pixels 36 \
  --heatmap-blur-pixels 28
```

### Heatmap Aspect Ratio and Orientation

Set the output width and aspect ratio:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --heatmap-output japan-square.png \
  --heatmap-width 1800 \
  --heatmap-aspect-ratio 1:1
```

For portrait images:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --heatmap-output japan-portrait.png \
  --heatmap-aspect-ratio 4:3 \
  --heatmap-orientation portrait
```

Common values include `1:1`, `4:3`, `3:2`, `16:9`, `portrait`, and
`landscape`.

### Heatmap Bounds

Automatic bounds are based on the photo locations. You can instead fit the map
to a country:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --heatmap-output japan-country.png \
  --heatmap-country Japan
```

Country bounds use Nominatim/OpenStreetMap and are cached in the same geocode
cache file.

You can also pass explicit bounds as `south,west,north,east`:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --heatmap-output japan-bounds.png \
  --heatmap-bounds 30.0,129.0,46.0,146.0
```

### Ignore Trip Edge Outliers

If the first or last part of the trip is very far from the main travel area,
for example airport photos from another country, you can trim those chronological
edge segments:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --heatmap-output japan-heatmap.png \
  --heatmap-trim-edge-outliers-km 1000
```

This only trims large jumps near the start or end of the chronological photo
sequence. Use a high value for international-trip cleanup and `0` to disable it.

### Heatmap Base Maps

Available built-in base map styles:

- `carto-light-nolabels`, default and low-label
- `carto-light`
- `carto-dark-nolabels`
- `carto-voyager`
- `osm`
- `none`, useful for testing without internet access
- `custom`

Example:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --heatmap-output japan-heatmap.png \
  --heatmap-map-style carto-dark-nolabels
```

For providers such as MapTiler, pass a raster tile URL template:

```bash
./get_image_locations.py "/Volumes/External Drive/Japan Travel Photos 2026" \
  --heatmap-output japan-heatmap.png \
  --heatmap-map-style custom \
  --heatmap-tile-url "https://api.maptiler.com/maps/YOUR_STYLE/256/{z}/{x}/{y}.png?key=YOUR_KEY"
```

The URL must contain `{z}`, `{x}`, and `{y}` placeholders.

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
