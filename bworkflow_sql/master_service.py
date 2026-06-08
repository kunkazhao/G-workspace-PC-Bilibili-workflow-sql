from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .settings import DEFAULT_MASTER_API_BASE_URL, DEFAULT_MASTER_SERVICE_ROOT
from .utils import safe_text


def is_master_connection_error(exc: BaseException) -> bool:
    text = safe_text(exc)
    if "[WinError 10061]" in text or "由于目标计算机积极拒绝" in text:
        return True
    if "无法连接 master" in text:
        return True
    cause = getattr(exc, "__cause__", None)
    return is_master_connection_error(cause) if isinstance(cause, BaseException) else False


class MasterServiceManager:
    def __init__(self, *, api_base_url: str = DEFAULT_MASTER_API_BASE_URL, service_root: Path = DEFAULT_MASTER_SERVICE_ROOT):
        self.api_base_url = api_base_url.rstrip("/")
        self.service_root = Path(service_root)

    def is_running(self, *, timeout: float = 0.8) -> bool:
        try:
            with urllib.request.urlopen(f"{self.api_base_url}/api/health", timeout=timeout) as response:
                return 200 <= int(response.status) < 500
        except Exception:
            return False

    def start(self) -> subprocess.Popen:
        main_path = self.service_root / "backend" / "main.py"
        if not main_path.exists():
            raise ValueError(f"Master 服务启动文件不存在：{main_path}")
        startupinfo = None
        creationflags = 0
        if sys.platform.startswith("win"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(
            [sys.executable, str(main_path)],
            cwd=str(self.service_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=creationflags,
            close_fds=False,
        )

    def ensure_running(self, *, timeout_seconds: float = 20.0) -> None:
        if self.is_running(timeout=0.8):
            return
        self.start()
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self.is_running(timeout=1.0):
                return
            time.sleep(0.5)
        raise RuntimeError(f"Master 服务启动后仍不可用：{self.api_base_url}")
