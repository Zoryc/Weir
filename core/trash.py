"""Deleting downloaded files: into the Recycle Bin (Windows) or the trash (Linux) when possible.

Files go to the Recycle Bin or trash so a mistake can be undone. Files on a network drive are
the exception: Windows can't keep them in the Recycle Bin and would delete them for good
without a word, so the window says so first and they are deleted directly.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QFile

from .browsers import is_network_path
from .paths import IS_WINDOWS

# What the user knows the place as.
TRASH_NAME = "Recycle Bin" if IS_WINDOWS else "trash"


def can_trash(path: str) -> bool:
    """False where the file couldn't be put back: on a network drive or share."""
    return not is_network_path(path)


def move_to_trash(path: str) -> bool:
    """Move a file into the Recycle Bin or trash. False if that didn't work (file in use,
    no trash on that drive…); the file is then left where it was."""
    result = QFile.moveToTrash(path)
    # Some PySide6 versions also return the file's new path: (ok, path_in_trash).
    ok = result[0] if isinstance(result, tuple) else result
    return bool(ok)


def in_use(path: str) -> bool:
    """Windows: True while another program (a video player, say) has the file open in a way
    that stops it being deleted. Always False elsewhere: Linux deletes open files."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        create = kernel32.CreateFileW
        create.restype = wintypes.HANDLE
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        delete_access, share_all, open_existing = 0x00010000, 0x7, 3
        # Asks only for the right to delete, sharing everything: that fails with a sharing
        # violation exactly when some other program's open file handle forbids deleting.
        handle = create(path, delete_access, share_all, None, open_existing, 0, None)
        if handle in (None, wintypes.HANDLE(-1).value):
            return ctypes.get_last_error() == 32  # ERROR_SHARING_VIOLATION  # type: ignore[attr-defined]
        kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        pass
    return False


def delete_for_good(path: str) -> bool:
    """Delete a file without the Recycle Bin or trash. False if that didn't work."""
    try:
        os.remove(path)
    except FileNotFoundError:
        return True  # gone already
    except OSError:
        return False
    return True
