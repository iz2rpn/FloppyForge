from __future__ import annotations

import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from floppyforge_core import (
    FloppyForgeCore,
    FLOPPY_720K,
    FLOPPY_1440K,
    FLOPPY_2880K,
    AMIGA_ADF_880K, # experimental support for ADF files
)


# Dark theme colors (unchanged)
COLORS = {
    "bg_dark": "#1a1a1a",
    "bg_medium": "#2d2d2d",
    "bg_light": "#3a3a3a",
    "fg_primary": "#e0e0e0",
    "fg_secondary": "#a0a0a0",
    "accent": "#00d4ff",
    "accent_hover": "#00a8cc",
    "success": "#00ff88",
    "error": "#ff4444",
    "warning": "#ffaa00",
}


class ModernButton(tk.Canvas):
    """Custom modern button with hover effects."""

    def __init__(
        self,
        parent,
        text,
        command,
        bg=COLORS["accent"],
        fg=COLORS["bg_dark"],
        hover_bg=COLORS["accent_hover"],
        width=120,
        height=36,
        **kwargs,
    ):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=COLORS["bg_medium"],
            highlightthickness=0,
            **kwargs,
        )
        self.command = command
        self.bg_color = bg
        self.hover_bg = hover_bg
        self.fg_color = fg
        self.text = text
        self.is_hovered = False
        self.is_disabled = False

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

        self._draw()

    def _draw(self):
        self.delete("all")

        if self.is_disabled:
            color = COLORS["bg_light"]
            text_color = COLORS["fg_secondary"]
        else:
            color = self.hover_bg if self.is_hovered else self.bg_color
            text_color = self.fg_color

        self.create_rectangle(
            0,
            0,
            self.winfo_reqwidth(),
            self.winfo_reqheight(),
            fill=color,
            outline="",
            tags="bg",
        )
        self.create_text(
            self.winfo_reqwidth() // 2,
            self.winfo_reqheight() // 2,
            text=self.text,
            fill=text_color,
            font=("Segoe UI", 10, "bold"),
        )

    def _on_enter(self, _e):
        if not self.is_disabled:
            self.is_hovered = True
            self._draw()

    def _on_leave(self, _e):
        self.is_hovered = False
        self._draw()

    def _on_click(self, _e):
        if not self.is_disabled and self.command:
            self.command()

    def config_state(self, state: str):
        self.is_disabled = (state == "disabled")
        self._draw()


class FloppyForgeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title("FloppyForge")
        self.geometry("820x620")
        self.minsize(820, 620)
        self.configure(bg=COLORS["bg_dark"])

        self._configure_ttk_theme()

        # Core instance (NEW)
        self.core = FloppyForgeCore(chunk_size=64 * 1024)

        self.img_path_var = tk.StringVar(value="")
        self.drive_var = tk.StringVar(value="A")
        self.format_size_var = tk.StringVar(value="Auto")  # Auto / 720KB / 1.44MB

        self.status_var = tk.StringVar(value=f"Ready — OS: {self.core.platform_name()}")
        self.bytes_written_var = tk.StringVar(value="0 B / 0 B")
        self.speed_var = tk.StringVar(value="—")

        self._stop_requested = False
        self._worker_thread: threading.Thread | None = None
        self._current_op = "idle"  # "write" | "format" | "idle"

        self._build_ui()
        self._apply_app_icon()

    # ---------------- UI ----------------

    def _configure_ttk_theme(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Dark.TFrame", background=COLORS["bg_dark"])
        style.configure("Card.TFrame", background=COLORS["bg_medium"])
        style.configure(
            "Dark.TLabel",
            background=COLORS["bg_dark"],
            foreground=COLORS["fg_primary"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Card.TLabel",
            background=COLORS["bg_medium"],
            foreground=COLORS["fg_primary"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Title.TLabel",
            background=COLORS["bg_dark"],
            foreground=COLORS["accent"],
            font=("Segoe UI", 18, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=COLORS["bg_dark"],
            foreground=COLORS["fg_secondary"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Status.TLabel",
            background=COLORS["bg_medium"],
            foreground=COLORS["accent"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Dark.Horizontal.TProgressbar",
            background=COLORS["accent"],
            troughcolor=COLORS["bg_light"],
            borderwidth=0,
            thickness=8,
        )

    def _apply_app_icon(self) -> None:
        """
        Optional icon support:
        - assets/icon.ico preferred on Windows
        - assets/icon.png used via iconphoto when available
        No errors if missing (silent best-effort).
        """
        try:
            base = Path(__file__).resolve().parent
            ico = base / "assets" / "icon.ico"
            png = base / "assets" / "icon.png"

            if ico.exists():
                try:
                    self.iconbitmap(str(ico))
                except Exception:
                    pass

            if png.exists():
                try:
                    img = tk.PhotoImage(file=str(png))
                    self.iconphoto(True, img)
                except Exception:
                    pass
        except Exception:
            pass

    def _build_ui(self) -> None:
        main_frame = ttk.Frame(self, style="Dark.TFrame", padding=20)
        main_frame.pack(fill="both", expand=True)

        header_frame = ttk.Frame(main_frame, style="Dark.TFrame")
        header_frame.pack(fill="x", pady=(0, 20))

        ttk.Label(header_frame, text="⚡ FloppyForge", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header_frame,
            text="Modern Floppy Disk Writer",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        card = ttk.Frame(main_frame, style="Card.TFrame", padding=20)
        card.pack(fill="both", expand=True)

        # File selection
        file_section = ttk.Frame(card, style="Card.TFrame")
        file_section.pack(fill="x", pady=(0, 15))

        ttk.Label(file_section, text="Image File (.img / .adf)", style="Card.TLabel").pack(anchor="w", pady=(0, 5))

        file_input_frame = ttk.Frame(file_section, style="Card.TFrame")
        file_input_frame.pack(fill="x")

        self.file_entry = tk.Entry(
            file_input_frame,
            textvariable=self.img_path_var,
            bg=COLORS["bg_light"],
            fg=COLORS["fg_primary"],
            font=("Segoe UI", 10),
            relief="flat",
            bd=0,
        )
        self.file_entry.pack(side="left", fill="x", expand=True, ipady=8, ipadx=10)

        browse_btn = ModernButton(file_input_frame, "Browse", self.pick_img, width=100, height=36)
        browse_btn.pack(side="left", padx=(10, 0))

        # Drive selection
        drive_section = ttk.Frame(card, style="Card.TFrame")
        drive_section.pack(fill="x", pady=(0, 10))

        ttk.Label(drive_section, text="Target Drive", style="Card.TLabel").pack(anchor="w", pady=(0, 5))

        drive_frame = ttk.Frame(drive_section, style="Card.TFrame")
        drive_frame.pack(fill="x")

        drive_combo = ttk.Combobox(
            drive_frame,
            textvariable=self.drive_var,
            values=["A", "B"],
            width=8,
            state="readonly",
            font=("Segoe UI", 10),
        )
        drive_combo.pack(side="left")

        ttk.Label(
            drive_frame,
            text="(Windows uses A/B directly — Linux/macOS uses auto-detect when needed)",
            style="Card.TLabel",
            foreground=COLORS["fg_secondary"],
        ).pack(side="left", padx=(15, 0))

        # Format size
        format_section = ttk.Frame(card, style="Card.TFrame")
        format_section.pack(fill="x", pady=(0, 20))

        ttk.Label(format_section, text="Format Size", style="Card.TLabel").pack(anchor="w", pady=(0, 5))

        format_frame = ttk.Frame(format_section, style="Card.TFrame")
        format_frame.pack(fill="x")

        fmt_combo = ttk.Combobox(
            format_frame,
            textvariable=self.format_size_var,
            values=["Auto", "720KB", "1.44MB"],
            width=10,
            state="readonly",
            font=("Segoe UI", 10),
        )
        fmt_combo.pack(side="left")

        ttk.Label(
            format_frame,
            text="Auto = uses selected image size if available, else defaults to 1.44MB.",
            style="Card.TLabel",
            foreground=COLORS["fg_secondary"],
        ).pack(side="left", padx=(15, 0))

        # Buttons
        btn_section = ttk.Frame(card, style="Card.TFrame")
        btn_section.pack(fill="x", pady=(0, 20))

        self.write_btn = ModernButton(btn_section, "⚡ Write Image", self.start_write, width=150, height=40)
        self.write_btn.pack(side="left", padx=(0, 10))

        # Rename "Zero Fill" -> Format
        self.format_btn = ModernButton(
            btn_section,
            "🔄 Format",
            self.start_format,
            bg=COLORS["bg_light"],
            hover_bg=COLORS["accent"],
            fg=COLORS["fg_primary"],
            width=120,
            height=40,
        )
        self.format_btn.pack(side="left", padx=(0, 10))

        self.stop_btn = ModernButton(
            btn_section,
            "⏹ Stop",
            self.request_stop,
            bg=COLORS["error"],
            hover_bg="#cc0000",
            fg="white",
            width=100,
            height=40,
        )
        self.stop_btn.pack(side="left")
        self.stop_btn.config_state("disabled")

        clear_btn = ModernButton(
            btn_section,
            "Clear Log",
            self.clear_log,
            bg=COLORS["bg_light"],
            hover_bg=COLORS["accent"],
            fg=COLORS["fg_primary"],
            width=100,
            height=40,
        )
        clear_btn.pack(side="right")

        # Progress
        progress_section = ttk.Frame(card, style="Card.TFrame")
        progress_section.pack(fill="x", pady=(0, 15))

        self.progress = ttk.Progressbar(
            progress_section,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            style="Dark.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x", pady=(0, 8))

        info_frame = ttk.Frame(progress_section, style="Card.TFrame")
        info_frame.pack(fill="x")

        ttk.Label(info_frame, textvariable=self.bytes_written_var, style="Card.TLabel").pack(side="left")
        ttk.Label(info_frame, textvariable=self.speed_var, style="Card.TLabel").pack(side="right")

        # Status
        status_frame = ttk.Frame(card, style="Card.TFrame")
        status_frame.pack(fill="x", pady=(0, 15))
        ttk.Label(status_frame, textvariable=self.status_var, style="Status.TLabel").pack(fill="x")

        # Log
        log_frame = tk.Frame(card, bg=COLORS["bg_light"])
        log_frame.pack(fill="both", expand=True)

        self.log = tk.Text(
            log_frame,
            height=10,
            wrap="word",
            bg=COLORS["bg_light"],
            fg=COLORS["fg_primary"],
            font=("Consolas", 9),
            relief="flat",
            insertbackground=COLORS["accent"],
            selectbackground=COLORS["accent"],
            selectforeground=COLORS["bg_dark"],
        )
        self.log.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        scroll = tk.Scrollbar(
            log_frame,
            command=self.log.yview,
            bg=COLORS["bg_light"],
            troughcolor=COLORS["bg_dark"],
            activebackground=COLORS["accent"],
        )
        scroll.pack(side="right", fill="y", pady=10, padx=(0, 5))
        self.log.configure(yscrollcommand=scroll.set)

        # Footer tips
        footer = ttk.Frame(main_frame, style="Dark.TFrame")
        footer.pack(fill="x", pady=(15, 0))

        tip = (
            "• Close Explorer/Finder windows on the floppy drive while writing\n"
            "• Make sure the disk is inserted and NOT write-protected\n"
            "• Format overwrites sectors with 0x00 (deep wipe) — it does NOT create a filesystem"
        )
        ttk.Label(
            footer,
            text=tip,
            style="Dark.TLabel",
            foreground=COLORS["fg_secondary"],
            font=("Segoe UI", 8),
        ).pack(fill="x")

    # ---------------- Logging + helpers ----------------

    def log_line(self, msg: str, level: str = "info") -> None:
        """
        level: info | ok | warn | err
        """
        ts = time.strftime("%H:%M:%S")

        color = None
        if level == "ok":
            color = COLORS["success"]
        elif level == "warn":
            color = COLORS["warning"]
        elif level == "err":
            color = COLORS["error"]
        elif level == "info":
            color = COLORS["accent"]

        tag = None
        if color:
            tag = f"color_{color}"
            self.log.tag_config(tag, foreground=color)

        if tag:
            self.log.insert("end", f"[{ts}] ", tag)
            self.log.insert("end", f"{msg}\n", tag)
        else:
            self.log.insert("end", f"[{ts}] {msg}\n")

        self.log.see("end")

    def clear_log(self) -> None:
        self.log.delete("1.0", "end")

    def pick_img(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Image File",
            filetypes=[
                ("Floppy Images", "*.img *.adf"),
                ("IMG Disk Image", "*.img"),
                ("ADF Amiga Image (experimental)", "*.adf"),
                ("All Files", "*.*"),
            ],
        )
        if path:
            self.img_path_var.set(path)

    def request_stop(self) -> None:
        self._stop_requested = True
        self.status_var.set("Stop requested…")
        self.log_line("Stop requested by user", "warn")

    def _set_ui_busy(self, busy: bool) -> None:
        self.write_btn.config_state("disabled" if busy else "normal")
        self.format_btn.config_state("disabled" if busy else "normal")
        self.stop_btn.config_state("normal" if busy else "disabled")

    def _stop_cb(self) -> bool:
        return self._stop_requested

    def _progress_cb(self, written: int, total: int, t0: float) -> None:
        self.after(0, self._update_progress, written, total, t0)

    def _core_log_cb(self, msg: str, level: str = "info") -> None:
        """
        core can emit logs with levels:
        info | warn | err | ok
        """
        if level not in ("info", "warn", "err", "ok"):
            level = "info"
        self.after(0, self.log_line, msg, level)

    def _resolve_format_size(self) -> int:
        opt = self.format_size_var.get().strip()
        if opt == "720KB":
            return FLOPPY_720K
        if opt == "1.44MB":
            return FLOPPY_1440K

        # Auto = use selected image size if exists
        p = Path(self.img_path_var.get().strip())
        if p.exists() and p.is_file():
            s = p.stat().st_size
            if s > 0:
                return s
        return FLOPPY_1440K

    def _set_op_status(self, pct: float) -> None:
        if self._current_op == "write":
            self.status_var.set(f"Writing… {pct:.1f}%")
        elif self._current_op == "format":
            self.status_var.set(f"Formatting… {pct:.1f}%")
        else:
            self.status_var.set(f"Ready — OS: {self.core.platform_name()}")

    def _check_device_access(self, drive: str) -> bool:
        """
        Cross-platform device resolution check (via core instance).
        """
        try:
            dev = self.core.resolve_device_path(drive)
            self.log_line(f"Resolved device: {dev}", "info")
            return True
        except Exception as e:
            msg = self.core.format_error(e)
            messagebox.showerror("Device Error", f"Cannot resolve target drive {drive}.\n\n{msg}")
            self.log_line(f"ERROR: {msg}", "err")
            return False

    # ---------------- Actions ----------------

    def start_write(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showwarning("Busy", "An operation is already in progress.")
            return

        image_path = Path(self.img_path_var.get().strip())
        if not image_path.exists() or not image_path.is_file():
            messagebox.showerror("Error", "Please select a valid .img or .adf file.")
            return

        drive = self.drive_var.get().strip().upper()
        if drive not in ("A", "B"):
            messagebox.showerror("Error", "Please select a valid drive (A or B).")
            return

        if not self._check_device_access(drive):
            return

        size = image_path.stat().st_size
        if size == 0:
            messagebox.showerror("Error", "Selected image file is empty.")
            return

        suffix = image_path.suffix.lower()
        is_adf = (suffix == ".adf")

        typical_sizes = {FLOPPY_720K, FLOPPY_1440K, FLOPPY_2880K, AMIGA_ADF_880K}
        if size not in typical_sizes:
            if not messagebox.askyesno(
                "Unusual Image Size",
                f"The image is {size} bytes.\n"
                "This is not a typical floppy image size.\n\nProceed anyway?",
            ):
                return

        if is_adf:
            messagebox.showwarning(
                "ADF Support (Experimental)",
                "ADF writing is currently experimental/testing.\n\n"
                "Many USB floppy drives only support PC formats (720KB/1.44MB).\n"
                "If it fails or the disk won't boot on Amiga, it's likely a hardware limitation.",
            )

        if not messagebox.askyesno(
            "Confirm Write",
            f"Write:\n  {image_path.name}\n"
            f"Size:\n  {self.core.human_bytes(size)}\n"
            f"To drive:\n  {drive}:\n\n"
            "This will overwrite the entire disk.",
        ):
            return

        self._stop_requested = False
        self._set_ui_busy(True)
        self.progress["value"] = 0
        self.bytes_written_var.set(f"0 B / {self.core.human_bytes(size)}")
        self.speed_var.set("—")

        self._current_op = "write"
        self._set_op_status(0.0)

        self.log_line(f"Image: {image_path}", "info")
        self.log_line(f"Size: {size} bytes ({self.core.human_bytes(size)})", "info")
        self.log_line("Starting write operation…", "info")

        self._worker_thread = threading.Thread(
            target=self._write_worker,
            args=(image_path, drive),
            daemon=True,
        )
        self._worker_thread.start()
        self.after(100, self._poll_thread)

    def start_format(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showwarning("Busy", "An operation is already in progress.")
            return

        drive = self.drive_var.get().strip().upper()
        if drive not in ("A", "B"):
            messagebox.showerror("Error", "Please select a valid drive (A or B).")
            return

        if not self._check_device_access(drive):
            return

        size = self._resolve_format_size()

        if not messagebox.askyesno(
            "Confirm Format",
            f"Format drive {drive}: ({self.core.human_bytes(size)})?\n\n"
            "This operation overwrites the disk with 0x00.\n"
            "It is a deep wipe, NOT a filesystem format.",
        ):
            return

        self._stop_requested = False
        self._set_ui_busy(True)
        self.progress["value"] = 0
        self.bytes_written_var.set(f"0 B / {self.core.human_bytes(size)}")
        self.speed_var.set("—")

        self._current_op = "format"
        self._set_op_status(0.0)

        self.log_line(f"Format on drive {drive}: {self.core.human_bytes(size)}", "warn")
        self.log_line("Starting format operation…", "warn")

        self._worker_thread = threading.Thread(
            target=self._format_worker,
            args=(drive, size),
            daemon=True,
        )
        self._worker_thread.start()
        self.after(100, self._poll_thread)

    # ---------------- Thread workers ----------------

    def _write_worker(self, image_path: Path, drive: str) -> None:
        t0 = time.time()

        def progress_cb(written: int, total: int) -> None:
            self._progress_cb(written, total, t0)

        try:
            self.core.write_image(
                image_path=image_path,
                drive_letter=drive,
                stop_cb=self._stop_cb,
                progress_cb=progress_cb,
                log_cb=self._core_log_cb,
            )
            self.after(0, self._finish_ok, "Write completed successfully.")
        except Exception as e:
            err = self.core.format_error(e)
            self.after(0, self._finish_err, err)

    def _format_worker(self, drive: str, size: int) -> None:
        t0 = time.time()

        def progress_cb(written: int, total: int) -> None:
            self._progress_cb(written, total, t0)

        try:
            self.core.format_zero_fill(
                size=size,
                drive_letter=drive,
                stop_cb=self._stop_cb,
                progress_cb=progress_cb,
                log_cb=self._core_log_cb,
            )
            self.after(0, self._finish_ok, "Format completed successfully.")
        except Exception as e:
            err = self.core.format_error(e)
            self.after(0, self._finish_err, err)

    def _poll_thread(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            self.after(150, self._poll_thread)
        else:
            self._set_ui_busy(False)
            self._current_op = "idle"
            self._set_op_status(0.0)

    # ---------------- UI updates ----------------

    def _update_progress(self, written: int, total: int, t0: float) -> None:
        if total <= 0:
            return

        pct = (written / total) * 100.0
        pct = min(max(pct, 0.0), 100.0)

        self.progress["value"] = pct
        self.bytes_written_var.set(f"{self.core.human_bytes(written)} / {self.core.human_bytes(total)}")

        dt = max(time.time() - t0, 0.001)
        speed = written / dt
        self.speed_var.set(f"{self.core.human_bytes(int(speed))}/s")

        self._set_op_status(pct)

    def _finish_ok(self, msg: str) -> None:
        self.progress["value"] = 100
        self.status_var.set("✓ Completed Successfully")
        self.log_line(f"SUCCESS: {msg}", "ok")
        messagebox.showinfo("Success", msg)

    def _finish_err(self, err: str) -> None:
        self.status_var.set("✗ Error occurred")
        self.log_line(f"ERROR: {err}", "err")
        messagebox.showerror(
            "Error",
            "Operation failed.\n\n"
            f"Details:\n{err}\n\n"
            "Tip:\n"
            "• Insert disk and retry\n"
            "• Close file manager windows on the floppy\n"
            "• Try a different floppy disk\n"
            "• On Linux/macOS you may need elevated permissions",
        )
