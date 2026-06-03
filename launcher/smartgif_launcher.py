from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from ctypes import windll
from pathlib import Path


APP_NAME = "SmartGIF"
APP_URL = "http://127.0.0.1:8765"
HEALTH_URL = f"{APP_URL}/api/health"
SERVER_SCRIPT = "animation_server.py"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def message_box(message: str) -> None:
    windll.user32.MessageBoxW(None, message, APP_NAME, 0x00000010)


def prepend_path(existing: str, *paths: Path) -> str:
    parts = [str(path) for path in paths if path.exists()]
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)


def is_server_running() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=0.8) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def choose_python(root: Path) -> str | None:
    bundled = root / "runtime" / "python" / "pythonw.exe"
    if bundled.exists():
        return str(bundled)
    return shutil.which("pythonw") or shutil.which("python")


def launch_server(root: Path) -> None:
    server = root / SERVER_SCRIPT
    if not server.exists():
        raise RuntimeError(f"Missing {SERVER_SCRIPT}. Please keep SmartGIF.exe in the extracted package folder.")

    python = choose_python(root)
    if not python:
        raise RuntimeError(
            "Python was not found. Use the Easy OneClick package, or install Python and add it to PATH."
        )

    env = os.environ.copy()
    runtime = root / "runtime"
    env["PATH"] = prepend_path(
        env.get("PATH", ""),
        runtime / "python",
        runtime / "ffmpeg" / "bin",
        runtime / "webp" / "bin",
    )

    subprocess.Popen(
        [python, str(server)],
        cwd=str(root),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
        close_fds=True,
    )


def main() -> int:
    root = app_dir()
    if is_server_running():
        webbrowser.open(APP_URL)
        return 0

    try:
        launch_server(root)
    except Exception as exc:
        message_box(str(exc))
        return 1

    for _ in range(30):
        if is_server_running():
            webbrowser.open(APP_URL)
            return 0
        time.sleep(0.2)

    webbrowser.open(APP_URL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
