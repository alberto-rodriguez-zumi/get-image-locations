#!/usr/bin/env python3
"""Small Tkinter launcher for get_image_locations.py."""

from __future__ import annotations

from pathlib import Path
import queue
import shlex
import subprocess
import sys
import threading
from typing import Callable

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except ModuleNotFoundError as exc:
    print(
        "error: Tkinter is required for the graphical launcher.\n"
        "Install the Tkinter package for your Python version, then run this launcher again.",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


SCRIPT_PATH = Path(__file__).with_name("get_image_locations.py")
DEFAULT_EXTENSIONS = "heic,heif,jpg,jpeg,png,tif,tiff,dng,cr2,cr3,nef,arw,raf,rw2,orf,mov,mp4"
MAP_STYLES = (
    "carto-light",
    "carto-light-nolabels",
    "carto-dark-nolabels",
    "carto-voyager",
    "osm",
    "none",
    "custom",
)
NAME_DETAILS = ("balanced", "specific", "address")
ORIENTATIONS = ("landscape", "portrait")


class ScrollFrame(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=12)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.inner.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._update_inner_width)

    def _update_scroll_region(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _update_inner_width(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)


class ImageLocationGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Get Image Locations")
        self.geometry("980x760")
        self.minsize(820, 620)

        self.process: subprocess.Popen[str] | None = None
        self.output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()

        self.root_path = tk.StringVar()
        self.csv_output = tk.StringVar()
        self.folders = tk.StringVar()
        self.cache_path = tk.StringVar(value=".geocode-cache.json")
        self.language = tk.StringVar(value="en")
        self.geocode_zoom = tk.StringVar(value="12")
        self.name_detail = tk.StringVar(value="balanced")
        self.coordinate_precision = tk.StringVar(value="2")
        self.cluster_radius = tk.StringVar(value="1000")
        self.min_photos = tk.StringVar(value="1")
        self.no_geocode = tk.BooleanVar(value=False)
        self.include_empty = tk.BooleanVar(value=False)
        self.allow_local_script = tk.BooleanVar(value=False)

        self.extensions = tk.StringVar(value=DEFAULT_EXTENSIONS)
        self.exiftool_batch_size = tk.StringVar(value="100")
        self.min_capture_date = tk.StringVar(value="2000-01-01")
        self.folder_date_tolerance = tk.StringVar(value="2")
        self.allow_zero_coordinates = tk.BooleanVar(value=False)

        self.gpx_enabled = tk.BooleanVar(value=False)
        self.gpx_output_dir = tk.StringVar()
        self.gpx_only = tk.BooleanVar(value=False)
        self.gpx_max_points = tk.StringVar(value="0")
        self.gpx_simplify_distance = tk.StringVar(value="25")
        self.gpx_simplify_time = tk.StringVar(value="300")

        self.heatmap_enabled = tk.BooleanVar(value=False)
        self.heatmap_output = tk.StringVar()
        self.heatmap_only = tk.BooleanVar(value=False)
        self.heatmap_width = tk.StringVar(value="1600")
        self.heatmap_aspect_ratio = tk.StringVar(value="16:9")
        self.heatmap_orientation = tk.StringVar(value="landscape")
        self.heatmap_cluster_radius = tk.StringVar(value="250")
        self.heatmap_point_radius = tk.StringVar(value="6")
        self.heatmap_blur = tk.StringVar(value="22")
        self.heatmap_opacity = tk.StringVar(value="0.78")
        self.heatmap_map_style = tk.StringVar(value="carto-light")
        self.heatmap_tile_url = tk.StringVar()
        self.heatmap_tile_cache = tk.StringVar(value=".tile-cache")
        self.heatmap_use_country = tk.BooleanVar(value=False)
        self.heatmap_country = tk.StringVar()
        self.heatmap_use_bounds = tk.BooleanVar(value=False)
        self.heatmap_bounds = tk.StringVar()
        self.heatmap_padding = tk.StringVar(value="0.08")
        self.heatmap_min_zoom = tk.StringVar(value="0")
        self.heatmap_max_zoom = tk.StringVar(value="12")
        self.heatmap_trim_outliers = tk.StringVar(value="0")

        self.command_preview = tk.StringVar()

        self._build_ui()
        self._trace_command_variables()
        self.refresh_command_preview()
        self.after(100, self._drain_output_queue)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        title = ttk.Label(self, text="Get Image Locations", font=("", 18, "bold"))
        title.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))

        notebook = ttk.Notebook(self)
        notebook.grid(row=1, column=0, sticky="nsew", padx=12, pady=8)

        self.input_tab = ScrollFrame(notebook)
        self.location_tab = ScrollFrame(notebook)
        self.gpx_tab = ScrollFrame(notebook)
        self.heatmap_tab = ScrollFrame(notebook)
        self.advanced_tab = ScrollFrame(notebook)

        notebook.add(self.input_tab, text="Input / CSV")
        notebook.add(self.location_tab, text="Locations")
        notebook.add(self.gpx_tab, text="GPX")
        notebook.add(self.heatmap_tab, text="Heatmap")
        notebook.add(self.advanced_tab, text="Advanced")

        self._build_input_tab(self.input_tab.inner)
        self._build_location_tab(self.location_tab.inner)
        self._build_gpx_tab(self.gpx_tab.inner)
        self._build_heatmap_tab(self.heatmap_tab.inner)
        self._build_advanced_tab(self.advanced_tab.inner)
        self._build_bottom_panel()

    def _build_input_tab(self, frame: ttk.Frame) -> None:
        self._entry_row(frame, 0, "Photo root folder", self.root_path, self._choose_root_folder)
        self._entry_row(frame, 1, "Optional CSV output file", self.csv_output, self._choose_csv_output)
        self._entry_row(frame, 2, "Only these folders", self.folders)

        ttk.Label(
            frame,
            text="Use comma-separated folder names, for example: 2026-06-01, 2026-06-02",
            foreground="#555",
        ).grid(row=3, column=1, columnspan=2, sticky="w", pady=(0, 8))

        self._check_row(frame, 4, "Print coordinates instead of place names", self.no_geocode)
        self._check_row(frame, 5, "Include folders without GPS locations", self.include_empty)

    def _build_location_tab(self, frame: ttk.Frame) -> None:
        self._entry_row(frame, 0, "Language", self.language)
        self._entry_row(frame, 1, "Geocode zoom", self.geocode_zoom)
        self._combo_row(frame, 2, "Name detail", self.name_detail, NAME_DETAILS)
        self._entry_row(frame, 3, "Coordinate precision", self.coordinate_precision)
        self._entry_row(frame, 4, "Cluster radius meters", self.cluster_radius)
        self._entry_row(frame, 5, "Min photos per location", self.min_photos)
        self._check_row(frame, 6, "Allow local-script names", self.allow_local_script)

    def _build_gpx_tab(self, frame: ttk.Frame) -> None:
        self._check_row(frame, 0, "Generate GPX files", self.gpx_enabled)
        self._entry_row(frame, 1, "GPX output folder", self.gpx_output_dir, self._choose_gpx_output_dir)
        self._check_row(frame, 2, "Skip CSV/geocoding summary", self.gpx_only)
        self._entry_row(frame, 3, "Max GPX points", self.gpx_max_points)
        self._entry_row(frame, 4, "Simplify distance meters", self.gpx_simplify_distance)
        self._entry_row(frame, 5, "Simplify time seconds", self.gpx_simplify_time)

    def _build_heatmap_tab(self, frame: ttk.Frame) -> None:
        self._check_row(frame, 0, "Generate heatmap image", self.heatmap_enabled)
        self._entry_row(frame, 1, "Heatmap PNG output", self.heatmap_output, self._choose_heatmap_output)
        self._check_row(frame, 2, "Only generate heatmap", self.heatmap_only)
        self._entry_row(frame, 3, "Image width", self.heatmap_width)
        self._entry_row(frame, 4, "Aspect ratio", self.heatmap_aspect_ratio)
        self._combo_row(frame, 5, "Orientation", self.heatmap_orientation, ORIENTATIONS)
        self._entry_row(frame, 6, "Cluster radius meters", self.heatmap_cluster_radius)
        self._entry_row(frame, 7, "Point radius pixels", self.heatmap_point_radius)
        self._entry_row(frame, 8, "Blur pixels", self.heatmap_blur)
        self._entry_row(frame, 9, "Opacity", self.heatmap_opacity)
        self._combo_row(frame, 10, "Map style", self.heatmap_map_style, MAP_STYLES)
        self._entry_row(frame, 11, "Custom tile URL", self.heatmap_tile_url)
        self._entry_row(frame, 12, "Tile cache folder", self.heatmap_tile_cache, self._choose_tile_cache_dir)
        self._check_row(frame, 13, "Fit heatmap to a country", self.heatmap_use_country)
        self._entry_row(frame, 14, "Country name", self.heatmap_country)
        self._check_row(frame, 15, "Use explicit bounds", self.heatmap_use_bounds)
        self._entry_row(frame, 16, "Explicit bounds", self.heatmap_bounds)
        self._entry_row(frame, 17, "Padding ratio", self.heatmap_padding)
        self._entry_row(frame, 18, "Min zoom", self.heatmap_min_zoom)
        self._entry_row(frame, 19, "Max zoom", self.heatmap_max_zoom)
        self._entry_row(frame, 20, "Trim edge outliers km", self.heatmap_trim_outliers)

    def _build_advanced_tab(self, frame: ttk.Frame) -> None:
        self._entry_row(frame, 0, "Cache file", self.cache_path, self._choose_cache_file)
        self._entry_row(frame, 1, "Extensions", self.extensions)
        self._entry_row(frame, 2, "ExifTool batch size", self.exiftool_batch_size)
        self._entry_row(frame, 3, "Min capture date", self.min_capture_date)
        self._entry_row(frame, 4, "Folder date tolerance days", self.folder_date_tolerance)
        self._check_row(frame, 5, "Allow 0,0 coordinates", self.allow_zero_coordinates)

    def _build_bottom_panel(self) -> None:
        panel = ttk.Frame(self, padding=(12, 0, 12, 12))
        panel.grid(row=2, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)

        preview_frame = ttk.LabelFrame(panel, text="Command preview", padding=8)
        preview_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        preview_frame.columnconfigure(0, weight=1)
        ttk.Entry(preview_frame, textvariable=self.command_preview, state="readonly").grid(row=0, column=0, sticky="ew")

        output_frame = ttk.LabelFrame(panel, text="Output", padding=8)
        output_frame.grid(row=1, column=0, sticky="nsew")
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)
        self.output_text = scrolledtext.ScrolledText(output_frame, height=12, wrap="word")
        self.output_text.grid(row=0, column=0, sticky="nsew")

        button_frame = ttk.Frame(panel)
        button_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        button_frame.columnconfigure(0, weight=1)
        self.run_button = ttk.Button(button_frame, text="Run", command=self.run_command)
        self.run_button.grid(row=0, column=1, padx=(0, 8))
        self.cancel_button = ttk.Button(button_frame, text="Cancel", command=self.cancel_command, state="disabled")
        self.cancel_button.grid(row=0, column=2)

    def _entry_row(
        self,
        frame: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        browse_command: Callable[[], None] | None = None,
    ) -> None:
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        if browse_command:
            ttk.Button(frame, text="Browse", command=browse_command).grid(row=row, column=2, sticky="e", padx=(8, 0), pady=5)

    def _combo_row(
        self,
        frame: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        values: tuple[str, ...],
    ) -> None:
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Combobox(frame, textvariable=variable, values=values, state="readonly").grid(row=row, column=1, sticky="ew", pady=5)

    def _check_row(self, frame: ttk.Frame, row: int, label: str, variable: tk.BooleanVar) -> None:
        ttk.Checkbutton(frame, text=label, variable=variable).grid(row=row, column=1, sticky="w", pady=5)

    def _trace_command_variables(self) -> None:
        for variable in self._all_variables():
            variable.trace_add("write", lambda *_args: self.refresh_command_preview())

    def _all_variables(self) -> list[tk.Variable]:
        return [
            self.root_path,
            self.csv_output,
            self.folders,
            self.cache_path,
            self.language,
            self.geocode_zoom,
            self.name_detail,
            self.coordinate_precision,
            self.cluster_radius,
            self.min_photos,
            self.no_geocode,
            self.include_empty,
            self.allow_local_script,
            self.extensions,
            self.exiftool_batch_size,
            self.min_capture_date,
            self.folder_date_tolerance,
            self.allow_zero_coordinates,
            self.gpx_enabled,
            self.gpx_output_dir,
            self.gpx_only,
            self.gpx_max_points,
            self.gpx_simplify_distance,
            self.gpx_simplify_time,
            self.heatmap_enabled,
            self.heatmap_output,
            self.heatmap_only,
            self.heatmap_width,
            self.heatmap_aspect_ratio,
            self.heatmap_orientation,
            self.heatmap_cluster_radius,
            self.heatmap_point_radius,
            self.heatmap_blur,
            self.heatmap_opacity,
            self.heatmap_map_style,
            self.heatmap_tile_url,
            self.heatmap_tile_cache,
            self.heatmap_use_country,
            self.heatmap_country,
            self.heatmap_use_bounds,
            self.heatmap_bounds,
            self.heatmap_padding,
            self.heatmap_min_zoom,
            self.heatmap_max_zoom,
            self.heatmap_trim_outliers,
        ]

    def build_command(self) -> list[str]:
        root = self.root_path.get().strip()
        command = [sys.executable, str(SCRIPT_PATH)]
        if root:
            command.append(root)

        self._append_option(command, "--output", self.csv_output)
        for folder in self._folder_values():
            command.extend(["--folder", folder])
        self._append_option(command, "--cache", self.cache_path, default=".geocode-cache.json")
        self._append_option(command, "--coordinate-precision", self.coordinate_precision, default="2")
        self._append_option(command, "--cluster-radius-meters", self.cluster_radius, default="1000")
        self._append_option(command, "--min-photos-per-location", self.min_photos, default="1")
        self._append_option(command, "--language", self.language, default="en")
        self._append_option(command, "--geocode-zoom", self.geocode_zoom, default="12")
        self._append_option(command, "--name-detail", self.name_detail, default="balanced")
        self._append_flag(command, "--allow-local-script", self.allow_local_script)
        self._append_flag(command, "--no-geocode", self.no_geocode)
        self._append_option(command, "--extensions", self.extensions, default=DEFAULT_EXTENSIONS)
        self._append_flag(command, "--include-empty", self.include_empty)
        self._append_option(command, "--exiftool-batch-size", self.exiftool_batch_size, default="100")

        if self.gpx_enabled.get():
            self._append_option(command, "--gpx-output-dir", self.gpx_output_dir)
            self._append_flag(command, "--gpx-only", self.gpx_only)
            self._append_option(command, "--gpx-max-points", self.gpx_max_points, default="0")
            self._append_option(command, "--gpx-simplify-distance-meters", self.gpx_simplify_distance, default="25")
            self._append_option(command, "--gpx-simplify-time-seconds", self.gpx_simplify_time, default="300")

        if self.heatmap_enabled.get():
            self._append_option(command, "--heatmap-output", self.heatmap_output)
            self._append_flag(command, "--heatmap-only", self.heatmap_only)
            self._append_option(command, "--heatmap-width", self.heatmap_width, default="1600")
            self._append_option(command, "--heatmap-aspect-ratio", self.heatmap_aspect_ratio, default="16:9")
            self._append_option(command, "--heatmap-orientation", self.heatmap_orientation, default="landscape")
            self._append_option(command, "--heatmap-cluster-radius-meters", self.heatmap_cluster_radius, default="250")
            self._append_option(command, "--heatmap-point-radius-pixels", self.heatmap_point_radius, default="6")
            self._append_option(command, "--heatmap-blur-pixels", self.heatmap_blur, default="22")
            self._append_option(command, "--heatmap-opacity", self.heatmap_opacity, default="0.78")
            self._append_option(command, "--heatmap-map-style", self.heatmap_map_style, default="carto-light")
            self._append_option(command, "--heatmap-tile-url", self.heatmap_tile_url)
            self._append_option(command, "--heatmap-tile-cache", self.heatmap_tile_cache, default=".tile-cache")
            if self.heatmap_use_country.get():
                self._append_option(command, "--heatmap-country", self.heatmap_country)
            if self.heatmap_use_bounds.get():
                self._append_option(command, "--heatmap-bounds", self.heatmap_bounds)
            self._append_option(command, "--heatmap-padding-ratio", self.heatmap_padding, default="0.08")
            self._append_option(command, "--heatmap-min-zoom", self.heatmap_min_zoom, default="0")
            self._append_option(command, "--heatmap-max-zoom", self.heatmap_max_zoom, default="12")
            self._append_option(command, "--heatmap-trim-edge-outliers-km", self.heatmap_trim_outliers, default="0")

        self._append_option(command, "--min-capture-date", self.min_capture_date, default="2000-01-01")
        self._append_option(command, "--folder-date-tolerance-days", self.folder_date_tolerance, default="2")
        self._append_flag(command, "--allow-zero-coordinates", self.allow_zero_coordinates)
        return command

    def _append_option(
        self,
        command: list[str],
        flag: str,
        variable: tk.StringVar,
        default: str | None = None,
    ) -> None:
        value = variable.get().strip()
        if value and value != default:
            command.extend([flag, value])

    def _append_flag(self, command: list[str], flag: str, variable: tk.BooleanVar) -> None:
        if variable.get():
            command.append(flag)

    def _folder_values(self) -> list[str]:
        return [folder.strip() for folder in self.folders.get().split(",") if folder.strip()]

    def refresh_command_preview(self) -> None:
        self.command_preview.set(shlex.join(self.build_command()))

    def run_command(self) -> None:
        if self.process:
            return
        if not self.root_path.get().strip():
            messagebox.showerror("Missing root folder", "Choose the folder containing your photo subfolders.")
            return
        if self.gpx_only.get() and not self.gpx_enabled.get():
            messagebox.showerror("GPX not enabled", "Enable GPX output before using 'Skip CSV/geocoding summary'.")
            return
        if self.gpx_enabled.get() and not self.gpx_output_dir.get().strip():
            messagebox.showerror("Missing GPX output folder", "Choose a folder for generated GPX files.")
            return
        if self.heatmap_only.get() and not self.heatmap_enabled.get():
            messagebox.showerror("Heatmap not enabled", "Enable heatmap output before using 'Only generate heatmap'.")
            return
        if self.heatmap_enabled.get() and not self.heatmap_output.get().strip():
            messagebox.showerror("Missing heatmap output", "Choose a PNG output path for the heatmap image.")
            return
        if self.heatmap_only.get() and self.gpx_only.get():
            messagebox.showerror("Incompatible modes", "Heatmap-only and GPX-only cannot be enabled together.")
            return
        if self.heatmap_enabled.get() and self.heatmap_use_country.get() and not self.heatmap_country.get().strip():
            messagebox.showerror("Missing country", "Enter a country name or disable 'Fit heatmap to a country'.")
            return
        if self.heatmap_enabled.get() and self.heatmap_use_bounds.get() and not self.heatmap_bounds.get().strip():
            messagebox.showerror("Missing bounds", "Enter explicit bounds or disable 'Use explicit bounds'.")
            return
        if self.heatmap_enabled.get() and self.heatmap_use_country.get() and self.heatmap_use_bounds.get():
            messagebox.showerror("Incompatible heatmap bounds", "Use either country bounds or explicit bounds, not both.")
            return

        command = self.build_command()
        self.output_text.delete("1.0", "end")
        self._append_output("$ " + shlex.join(command) + "\n\n")
        self.run_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        thread = threading.Thread(target=self._run_process, args=(command,), daemon=True)
        thread.start()

    def _run_process(self, command: list[str]) -> None:
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.output_queue.put(("output", line))
            return_code = self.process.wait()
            self.output_queue.put(("done", f"\nFinished with exit code {return_code}\n"))
        except OSError as exc:
            self.output_queue.put(("done", f"\nCould not run command: {exc}\n"))

    def cancel_command(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self._append_output("\nCancellation requested...\n")

    def _drain_output_queue(self) -> None:
        try:
            while True:
                kind, text = self.output_queue.get_nowait()
                if text:
                    self._append_output(text)
                if kind == "done":
                    self.process = None
                    self.run_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_output_queue)

    def _append_output(self, text: str) -> None:
        self.output_text.insert("end", text)
        self.output_text.see("end")

    def _choose_root_folder(self) -> None:
        path = filedialog.askdirectory(title="Choose photo root folder")
        if path:
            self.root_path.set(path)

    def _choose_csv_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Choose CSV output file",
            defaultextension=".csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if path:
            self.csv_output.set(path)

    def _choose_gpx_output_dir(self) -> None:
        path = filedialog.askdirectory(title="Choose GPX output folder")
        if path:
            self.gpx_output_dir.set(path)
            self.gpx_enabled.set(True)

    def _choose_heatmap_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Choose heatmap PNG output file",
            defaultextension=".png",
            filetypes=(("PNG files", "*.png"), ("All files", "*.*")),
        )
        if path:
            self.heatmap_output.set(path)
            self.heatmap_enabled.set(True)

    def _choose_tile_cache_dir(self) -> None:
        path = filedialog.askdirectory(title="Choose tile cache folder")
        if path:
            self.heatmap_tile_cache.set(path)

    def _choose_cache_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Choose cache file",
            defaultextension=".json",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if path:
            self.cache_path.set(path)


def main() -> int:
    app = ImageLocationGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
