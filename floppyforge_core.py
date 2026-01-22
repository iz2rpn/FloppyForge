from __future__ import annotations

import ctypes
import os
import sys
import subprocess
import plistlib
from pathlib import Path


FLOPPY_720K = 737_280
FLOPPY_1440K = 1_474_560
FLOPPY_2880K = 2_949_120
AMIGA_ADF_880K = 901_120


class FloppyForgeCore:
    """
    FloppyForgeCore
    --------------
    Cross-platform raw floppy writer + deep wipe (zero fill).

    It supports:
      - Windows raw device writing via WinAPI 
      - Linux/macOS raw device writing via /dev/... 

    Callbacks:
      - stop_cb() -> bool
      - progress_cb(written: int, total: int) -> None
      - log_cb(message: str, level: str) -> None   level: "info" | "warn" | "err" | "ok"
    """

    def __init__(self, chunk_size: int = 64 * 1024) -> None:
        self.chunk_size = int(chunk_size)

        # WinAPI initialized only if running on Windows
        self._kernel32 = None
        self._wintypes = None

        # Win constants
        self._GENERIC_READ = 0
        self._GENERIC_WRITE = 0
        self._FILE_SHARE_READ = 0
        self._FILE_SHARE_WRITE = 0
        self._OPEN_EXISTING = 0
        self._FILE_ATTRIBUTE_NORMAL = 0
        self._INVALID_HANDLE_VALUE = 0

        self._FSCTL_LOCK_VOLUME = 0
        self._FSCTL_UNLOCK_VOLUME = 0
        self._FSCTL_DISMOUNT_VOLUME = 0

        if self.is_windows():
            self._init_windows_api()

    # ------------------ BASIC UTILS ------------------

    @staticmethod
    def human_bytes(n: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"] # Units for human-readable sizes
        size = float(n)
        for u in units:
            if size < 1024.0 or u == units[-1]:
                return f"{size:.0f} {u}" if u == "B" else f"{size:.2f} {u}"
            size /= 1024.0
        return f"{n} B"

    @staticmethod
    def platform_name() -> str:
        if sys.platform.startswith("win"):
            return "windows"
        if sys.platform == "darwin":
            return "macos"
        return "linux"

    @classmethod
    def is_windows(cls) -> bool:
        return cls.platform_name() == "windows"

    @staticmethod
    def drive_device_path_windows(drive: str) -> str:
        return fr"\\.\{drive}:"

    @staticmethod
    def format_error(e: Exception) -> str:
        if isinstance(e, PermissionError):
            return "Permission denied. Elevated privileges may be required."
        if isinstance(e, FileNotFoundError):
            return "Device not found / not ready. Insert disk and retry."
        return str(e)

    # ------------------ PUBLIC API ------------------

    def resolve_device_path(self, drive_letter: str) -> str:
        """
        Resolve drive letter (A/B) to a device path:
        - Windows: \\\\.\\A:
        - Linux: /dev/fd0, /dev/fd1 or removable floppy-sized block device
        - macOS: diskutil-based auto-detect -> /dev/rdiskN
        """
        drive_letter = drive_letter.upper().strip()
        plat = self.platform_name()

        if plat == "windows":
            return self.drive_device_path_windows(drive_letter)

        if plat == "linux":
            candidates = ["/dev/fd0", "/dev/floppy/0"] if drive_letter == "A" else ["/dev/fd1", "/dev/floppy/1"]
            for dev in candidates:
                if os.path.exists(dev):
                    return dev

            dev = self._linux_find_floppy_sized_block_device()
            if dev:
                return dev
            raise FileNotFoundError("No suitable floppy device found on Linux.")

        if plat == "macos":
            dev = self._mac_find_floppy_sized_disk()
            if dev:
                return dev
            raise FileNotFoundError("No suitable floppy device found on macOS.")

        raise FileNotFoundError("Unsupported platform.")

    def write_image(
        self,
        image_path: Path,
        drive_letter: str,
        stop_cb,
        progress_cb,
        log_cb=None,
    ) -> None:
        """
        Write raw image to floppy device (cross-platform).
        """
        device = self.resolve_device_path(drive_letter)
        total = image_path.stat().st_size

        if log_cb:
            log_cb(f"Target device: {device}", "info")

        if self.platform_name() == "windows":
            self._write_windows(image_path, device, total, stop_cb, progress_cb, log_cb)
        else:
            self._write_unix(image_path, device, total, stop_cb, progress_cb, log_cb)

    def format_zero_fill(
        self,
        size: int,
        drive_letter: str,
        stop_cb,
        progress_cb,
        log_cb=None,
    ) -> None:
        """
        Deep wipe (format) = overwrite with 0x00 (NOT filesystem format).
        """
        device = self.resolve_device_path(drive_letter)

        if log_cb:
            log_cb(f"Target device: {device}", "info")

        if self.platform_name() == "windows":
            self._format_windows(size, device, stop_cb, progress_cb, log_cb)
        else:
            self._format_unix(size, device, stop_cb, progress_cb, log_cb)

    # ------------------ WINDOWS BACKEND ------------------

    def _init_windows_api(self) -> None:
        """
        Initialize WinAPI handles + function signatures.
        Called once in __init__ only if platform is Windows.
        """
        from ctypes import wintypes

        self._wintypes = wintypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._GENERIC_READ = 0x80000000
        self._GENERIC_WRITE = 0x40000000
        self._FILE_SHARE_READ = 0x00000001
        self._FILE_SHARE_WRITE = 0x00000002
        self._OPEN_EXISTING = 3
        self._FILE_ATTRIBUTE_NORMAL = 0x00000080

        self._INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

        self._FSCTL_LOCK_VOLUME = 0x00090018
        self._FSCTL_UNLOCK_VOLUME = 0x0009001C
        self._FSCTL_DISMOUNT_VOLUME = 0x00090020

        k = self._kernel32

        k.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        k.CreateFileW.restype = wintypes.HANDLE

        k.DeviceIoControl.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        k.DeviceIoControl.restype = wintypes.BOOL

        k.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        k.WriteFile.restype = wintypes.BOOL

        k.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        k.FlushFileBuffers.restype = wintypes.BOOL

        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL

    def _winerr(self, prefix: str) -> OSError:
        err = ctypes.get_last_error()
        return OSError(err, f"{prefix} (WinError {err})")

    def _open_device_handle(self, device: str):
        if not self.is_windows() or self._kernel32 is None:
            raise RuntimeError("WinAPI handle open called on non-Windows")

        k = self._kernel32
        h = k.CreateFileW(
            device,
            self._GENERIC_READ | self._GENERIC_WRITE,
            self._FILE_SHARE_READ | self._FILE_SHARE_WRITE,
            None,
            self._OPEN_EXISTING,
            self._FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if h == self._INVALID_HANDLE_VALUE:
            raise self._winerr("CreateFileW failed")
        return h

    def _device_ioctl(self, handle, code: int) -> None:
        br = self._wintypes.DWORD(0)
        ok = self._kernel32.DeviceIoControl(handle, code, None, 0, None, 0, ctypes.byref(br), None)
        if not ok:
            raise self._winerr(f"DeviceIoControl failed (code={hex(code)})")

    def _writefile(self, handle, data: bytes) -> int:
        written = self._wintypes.DWORD(0)
        ok = self._kernel32.WriteFile(handle, data, len(data), ctypes.byref(written), None)
        if not ok:
            raise self._winerr("WriteFile failed")
        return int(written.value)

    def _flush(self, handle) -> None:
        ok = self._kernel32.FlushFileBuffers(handle)
        if not ok:
            err = ctypes.get_last_error()
            # WinError 1 = INVALID_FUNCTION: common on USB floppy drivers -> ignore
            if err == 1:
                return
            raise OSError(err, f"FlushFileBuffers failed (WinError {err})")

    def _close_handle(self, handle) -> None:
        try:
            self._kernel32.CloseHandle(handle)
        except Exception:
            pass

    def _write_windows(
        self,
        image_path: Path,
        device: str,
        total: int,
        stop_cb,
        progress_cb,
        log_cb=None,
    ) -> None:
        handle = self._open_device_handle(device)
        try:
            self._device_ioctl(handle, self._FSCTL_LOCK_VOLUME)
            self._device_ioctl(handle, self._FSCTL_DISMOUNT_VOLUME)

            written_total = 0
            with image_path.open("rb") as f:
                while True:
                    if stop_cb():
                        raise RuntimeError("Interrupted by user.")
                    buf = f.read(self.chunk_size)
                    if not buf:
                        break

                    w = self._writefile(handle, buf)
                    written_total += w
                    progress_cb(written_total, total)

            self._flush(handle)
            if log_cb:
                log_cb("WinAPI flush complete (or safely ignored)", "info")

        finally:
            try:
                self._device_ioctl(handle, self._FSCTL_UNLOCK_VOLUME)
            except Exception:
                pass
            self._close_handle(handle)

    def _format_windows(
        self,
        size: int,
        device: str,
        stop_cb,
        progress_cb,
        log_cb=None,
    ) -> None:
        handle = self._open_device_handle(device)
        try:
            self._device_ioctl(handle, self._FSCTL_LOCK_VOLUME)
            self._device_ioctl(handle, self._FSCTL_DISMOUNT_VOLUME)

            written_total = 0
            while written_total < size:
                if stop_cb():
                    raise RuntimeError("Interrupted by user.")
                remaining = size - written_total
                n = min(self.chunk_size, remaining)
                buf = b"\x00" * n
                w = self._writefile(handle, buf)
                written_total += w
                progress_cb(written_total, size)

            self._flush(handle)
            if log_cb:
                log_cb("WinAPI flush complete (or safely ignored)", "info")

        finally:
            try:
                self._device_ioctl(handle, self._FSCTL_UNLOCK_VOLUME)
            except Exception:
                pass
            self._close_handle(handle)

    # ------------------ UNIX BACKEND (Linux/macOS) ------------------

    def _unix_unmount_best_effort(self, dev: str) -> None:
        plat = self.platform_name()
        try:
            if plat == "linux":
                subprocess.run(["umount", dev], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif plat == "macos":
                base = dev.replace("/dev/rdisk", "/dev/disk")
                subprocess.run(["diskutil", "unmountDisk", base], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    @staticmethod
    def _unix_open_flags() -> int:
        flags = os.O_WRONLY
        if hasattr(os, "O_SYNC"):
            flags |= os.O_SYNC
        return flags

    def _write_unix(
        self,
        image_path: Path,
        device: str,
        total: int,
        stop_cb,
        progress_cb,
        log_cb=None,
    ) -> None:
        self._unix_unmount_best_effort(device)

        fd = os.open(device, self._unix_open_flags())
        try:
            written_total = 0
            with image_path.open("rb") as f:
                while True:
                    if stop_cb():
                        raise RuntimeError("Interrupted by user.")
                    buf = f.read(self.chunk_size)
                    if not buf:
                        break

                    n = os.write(fd, buf)
                    written_total += n
                    progress_cb(written_total, total)

            os.fsync(fd)
            if log_cb:
                log_cb("fsync complete", "info")

        finally:
            os.close(fd)

    def _format_unix(
        self,
        size: int,
        device: str,
        stop_cb,
        progress_cb,
        log_cb=None,
    ) -> None:
        self._unix_unmount_best_effort(device)

        fd = os.open(device, self._unix_open_flags())
        try:
            written_total = 0
            while written_total < size:
                if stop_cb():
                    raise RuntimeError("Interrupted by user.")
                remaining = size - written_total
                n = min(self.chunk_size, remaining)
                buf = b"\x00" * n

                w = os.write(fd, buf)
                written_total += w
                progress_cb(written_total, size)

            os.fsync(fd)
            if log_cb:
                log_cb("fsync complete", "info")

        finally:
            os.close(fd)

    # ------------------ DEVICE DISCOVERY ------------------

    def _linux_find_floppy_sized_block_device(self) -> str | None:
        try:
            out = subprocess.check_output(
                ["lsblk", "-b", "-o", "NAME,SIZE,RM,TYPE,RO"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            return None

        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if len(lines) < 2:
            return None

        for ln in lines[1:]:
            parts = ln.split()
            if len(parts) < 5:
                continue

            name, size_s, rm_s, typ, ro_s = parts[:5]
            try:
                size = int(size_s)
                rm = int(rm_s)
                ro = int(ro_s)
            except Exception:
                continue

            if typ != "disk" or rm != 1 or ro != 0:
                continue

            if size in (FLOPPY_720K, FLOPPY_1440K, FLOPPY_2880K, AMIGA_ADF_880K):
                return f"/dev/{name}"

        return None

    def _mac_find_floppy_sized_disk(self) -> str | None:
        try:
            raw = subprocess.check_output(["diskutil", "list", "-plist"], stderr=subprocess.DEVNULL)
            data = plistlib.loads(raw)
        except Exception:
            return None

        for disk in data.get("AllDisksAndPartitions", []):
            disk_id = disk.get("DeviceIdentifier")
            size = disk.get("Size")

            if not disk_id or not isinstance(size, int):
                continue

            if size in (FLOPPY_720K, FLOPPY_1440K, FLOPPY_2880K, AMIGA_ADF_880K):
                return f"/dev/r{disk_id}"

        return None
