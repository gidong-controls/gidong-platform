"""Restore the pywin32 import path omitted from frozen applications."""

import os
import sys

for relative_path in ("win32", os.path.join("win32", "lib")):
    win32_path = os.path.join(sys._MEIPASS, relative_path)
    if os.path.isdir(win32_path) and win32_path not in sys.path:
        sys.path.append(win32_path)
