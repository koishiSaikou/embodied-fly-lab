"""Explicit user-selected Windows destinations for exported experiment data.

Importing this module does not open windows or probe any destination. The native
picker is invoked only by choose_directory(), on its own COM STA thread.
"""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import threading
import uuid


_PICKER_LOCK = threading.Lock()


def validate_destination(value) -> Path:
    """Require an existing writable absolute Windows directory; never create it."""
    try:
        value = os.fspath(value)
    except TypeError as exc:
        raise ValueError("请输入现有文件夹的 Windows 绝对路径") from exc
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("保存目录不能为空，且必须是有效的 Windows 文件夹路径")
    text = value.strip()
    if not PureWindowsPath(text).is_absolute():
        raise ValueError("请选择或输入完整的 Windows 绝对目录，例如 E:\\实验记录")
    if os.name != "nt":
        raise ValueError("此保存目录功能当前面向 Windows，请在 Windows 上选择现有目录")
    directory = Path(text)
    if not directory.is_dir():
        raise ValueError("保存目录不存在或不是文件夹；请先创建文件夹后再选择")
    try:
        directory = directory.resolve(strict=True)
    except OSError as exc:
        raise OSError(f"无法访问保存目录：{exc}") from exc
    probe = directory / f".fly-experiment-write-check-{uuid.uuid4().hex}.tmp"
    created = False
    try:
        # A private zero-byte probe checks actual write permission, rather than
        # relying on os.access() ACL guesses. O_EXCL never overwrites a file.
        descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        os.close(descriptor)
        probe.unlink()
        created = False
    except OSError as exc:
        raise OSError(f"保存目录不可写或无法清理写入检查文件：{exc}") from exc
    finally:
        if created:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                # Preserve the original error; only our own unique empty probe
                # may remain if Windows revoked deletion permission meanwhile.
                pass
    return directory


def _browse_windows_directory() -> str | None:
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.UINT,
                                     wintypes.LPARAM, wintypes.LPARAM)

    class BrowseInfo(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND), ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR), ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT), ("lpfn", callback_type),
            ("lParam", wintypes.LPARAM), ("iImage", ctypes.c_int),
        ]

    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BrowseInfo)]
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL

    @callback_type
    def on_dialog(hwnd, message, _parameter, _data):
        if message == 1:  # BFFM_INITIALIZED: an explicit user action opened it.
            user32.SetForegroundWindow(hwnd)
        return 0

    result = ole32.CoInitializeEx(None, 0x2 | 0x4)  # STA + disable legacy OLE DDE.
    if result not in (0, 1):
        raise OSError(f"无法初始化 Windows 文件夹选择器（HRESULT 0x{result & 0xffffffff:08X}）；请手动输入目录")
    pidl = None
    try:
        display = ctypes.create_unicode_buffer(32768)
        info = BrowseInfo()
        info.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
        info.lpszTitle = "选择保存实验包的文件夹（不移动原始实验记录）"
        info.ulFlags = 0x1 | 0x10 | 0x40  # filesystem dirs + edit box + new UI.
        info.lpfn = on_dialog
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
        if not pidl:
            return None
        selected = ctypes.create_unicode_buffer(32768)
        get_path = getattr(shell32, "SHGetPathFromIDListEx", None)
        if get_path is not None:
            get_path.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
            get_path.restype = wintypes.BOOL
            succeeded = get_path(pidl, selected, len(selected), 0)
        else:
            get_path = shell32.SHGetPathFromIDListW
            get_path.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
            get_path.restype = wintypes.BOOL
            succeeded = get_path(pidl, selected)
        if not succeeded or not selected.value:
            raise OSError("无法读取所选文件夹的本地路径；请手动输入目录")
        return selected.value
    finally:
        if pidl:
            ole32.CoTaskMemFree(pidl)
        ole32.CoUninitialize()


def choose_directory() -> str | None:
    """Open the native directory picker only for an explicit user request."""
    if os.name != "nt":
        raise ValueError("当前系统不支持 Windows 文件夹选择器，请手动输入保存目录")
    if not _PICKER_LOCK.acquire(blocking=False):
        raise ValueError("文件夹选择器已经打开，请先完成或取消当前选择")
    selected = []
    errors = []

    def worker():
        try:
            selected.append(_browse_windows_directory())
        except Exception as exc:
            errors.append(exc)
        finally:
            # Keep the guard held even if the waiting HTTP request is cancelled
            # or interrupted while the native dialog is still open.
            _PICKER_LOCK.release()

    thread = threading.Thread(target=worker, name="experiment-directory-picker-sta", daemon=True)
    try:
        thread.start()
    except BaseException:
        _PICKER_LOCK.release()
        raise
    thread.join()
    if errors:
        raise OSError(f"Windows 文件夹选择失败：{errors[0]}；也可手动输入目录") from errors[0]
    return selected[0] if selected else None
