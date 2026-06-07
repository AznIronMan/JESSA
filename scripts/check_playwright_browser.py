#!/usr/bin/env python3
"""Check whether Playwright can find a browser for rendered and LinkedIn imports."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _dotenv_value(name: str) -> str:
    try:
        from dotenv import dotenv_values
    except Exception:
        return ""
    value = dotenv_values(ROOT_DIR / ".env").get(name)
    return str(value or "").strip()


def _resolve_executable(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    expanded = Path(value).expanduser()
    if expanded.exists():
        return str(expanded)
    return shutil.which(value) or ""


def _configured_browser() -> str:
    return _resolve_executable(os.getenv("PLAYWRIGHT_BROWSER_PATH", "") or _dotenv_value("PLAYWRIGHT_BROWSER_PATH"))


def _system_browser() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    if platform.system() == "Darwin":
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if chrome.exists():
            return str(chrome)
    return ""


def _bundled_browser() -> str:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return ""

    try:
        with sync_playwright() as playwright:
            path = Path(playwright.chromium.executable_path)
    except Exception:
        return ""
    return str(path) if path.exists() else ""


def find_browser() -> str:
    return _configured_browser() or _system_browser() or _bundled_browser()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when no browser is available.",
    )
    args = parser.parse_args()

    browser = find_browser()
    if browser:
        print(f"Playwright browser available: {browser}")
        return 0

    message = (
        "Playwright browser missing for rendered and LinkedIn URL imports.\n"
        "Install the browser in the environment that runs JESSA, not on the client opening the web UI.\n"
        "Try: python -m playwright install chromium\n"
        "If Playwright does not support this Linux distro, install Google Chrome or Chromium and set "
        "PLAYWRIGHT_BROWSER_PATH to the executable path."
    )
    print(message, file=sys.stderr if args.strict else sys.stdout)
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
