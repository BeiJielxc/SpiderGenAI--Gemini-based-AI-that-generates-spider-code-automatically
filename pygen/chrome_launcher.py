"""
Chrome launcher module for starting/reusing a browser in CDP mode.
"""

from __future__ import annotations

import glob
import os
import platform
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

import requests


class ChromeLauncher:
    """Launch/reuse Chrome or Chromium with remote debugging enabled."""

    def __init__(
        self,
        debug_port: int = 9222,
        user_data_dir: Optional[str] = None,
        headless: bool = True,
        auto_select_port: bool = True,
    ):
        self.debug_port = debug_port
        self.user_data_dir = user_data_dir or self._get_default_profile_dir()
        self.headless = headless
        self.auto_select_port = auto_select_port
        self.chrome_process: Optional[subprocess.Popen] = None
        self.actual_port: Optional[int] = None

    def _get_default_profile_dir(self) -> str:
        profile_dir = Path(__file__).parent / "chrome-profile"
        profile_dir.mkdir(exist_ok=True)
        return str(profile_dir)

    def _find_chrome_executable(self) -> Optional[str]:
        """Find browser executable on Windows/macOS/Linux and Playwright containers."""
        system = platform.system()

        if system == "Windows":
            possible_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            ]
        elif system == "Darwin":
            possible_paths = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            ]
        else:
            possible_paths = [
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
                "/usr/bin/microsoft-edge",
            ]

            # Playwright images / bundles
            pw_override = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "").strip()
            if pw_override:
                possible_paths.insert(0, pw_override)

            possible_paths.extend(sorted(glob.glob("/ms-playwright/chromium-*/chrome-linux/chrome"), reverse=True))
            possible_paths.extend(sorted(glob.glob("/ms-playwright/chromium-*/chrome-linux/headless_shell"), reverse=True))

        for path in possible_paths:
            if path and os.path.exists(path):
                return path

        return None

    def _is_port_in_use(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return False
            except OSError:
                return True

    def _find_available_port(self, start_port: int = 9222, max_attempts: int = 20) -> int:
        for i in range(max_attempts):
            port = start_port + i
            if not self._is_port_in_use(port):
                return port
        raise RuntimeError(f"No available port found in range {start_port}-{start_port + max_attempts}")

    def _check_cdp_ready(self, port: int, timeout: int = 15) -> bool:
        url = f"http://127.0.0.1:{port}/json/version"
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                response = requests.get(url, timeout=2)
                if response.status_code == 200:
                    data = response.json()
                    print(f"[OK] CDP ready: {data.get('Browser', 'Unknown')}")
                    return True
            except Exception:
                pass
            time.sleep(0.5)

        return False

    def _check_existing_instance(self, port: int) -> bool:
        if not self._is_port_in_use(port):
            return False

        try:
            response = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
            if response.status_code == 200:
                print(f"[OK] Reusing existing browser instance on port {port}")
                return True
        except Exception:
            pass

        return False

    def launch(self) -> Tuple[bool, int]:
        if self._check_existing_instance(self.debug_port):
            self.actual_port = self.debug_port
            return True, self.debug_port

        if self._is_port_in_use(self.debug_port):
            if self.auto_select_port:
                self.debug_port = self._find_available_port(self.debug_port)
                print(f"[INFO] Port occupied, auto-switch to {self.debug_port}")
            else:
                raise RuntimeError(f"Port {self.debug_port} is already in use")

        chrome_path = self._find_chrome_executable()
        if not chrome_path:
            raise FileNotFoundError(
                "Chrome/Chromium executable not found. "
                "Install browser or set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH."
            )

        print(f"[INFO] Browser executable: {chrome_path}")

        args = [
            chrome_path,
            f"--remote-debugging-port={self.debug_port}",
            f"--user-data-dir={self.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-infobars",
            "--excludeSwitches=enable-automation",
            "--use-mock-keychain",
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]

        if self.headless:
            args.extend(["--headless=new", "--disable-gpu", "--hide-scrollbars"])

        if platform.system() == "Linux":
            args.extend(["--no-sandbox", "--disable-dev-shm-usage"])

        print(f"[INFO] Starting browser (port {self.debug_port})...")
        try:
            if platform.system() == "Windows":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                self.chrome_process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    startupinfo=startupinfo,
                )
            else:
                self.chrome_process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as exc:
            raise RuntimeError(f"Failed to start browser: {exc}") from exc

        print("[INFO] Waiting for CDP...")
        if not self._check_cdp_ready(self.debug_port):
            self.terminate()
            raise RuntimeError(f"CDP not ready before timeout on port {self.debug_port}")

        self.actual_port = self.debug_port
        print(f"[OK] Browser started on port {self.debug_port}")
        return True, self.debug_port

    def terminate(self):
        if self.chrome_process:
            try:
                self.chrome_process.terminate()
                self.chrome_process.wait(timeout=5)
                print("[OK] Browser process terminated")
            except Exception as exc:
                print(f"[WARN] Failed to terminate browser process cleanly: {exc}")
                try:
                    self.chrome_process.kill()
                except Exception:
                    pass

    def get_ws_endpoint(self) -> str:
        if not self.actual_port:
            raise RuntimeError("Browser not started or actual port unknown")

        try:
            response = requests.get(f"http://127.0.0.1:{self.actual_port}/json/version", timeout=5)
            data = response.json()
            ws_url = data.get("webSocketDebuggerUrl")
            if ws_url:
                return ws_url
        except Exception as exc:
            print(f"[WARN] Failed to read ws endpoint from /json/version: {exc}")

        return f"ws://127.0.0.1:{self.actual_port}/devtools/browser"

    def __enter__(self):
        self.launch()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.terminate()
