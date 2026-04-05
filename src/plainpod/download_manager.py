from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool


@dataclass
class DownloadRequest:
    episode_id: int
    url: str


@dataclass
class _DownloadControl:
    paused: bool = False
    canceled: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> tuple[bool, bool]:
        with self.lock:
            return self.paused, self.canceled

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.paused = paused

    def set_canceled(self) -> None:
        with self.lock:
            self.canceled = True


class _DownloadTask(QRunnable):
    def __init__(
        self,
        manager: "DownloadManager",
        req: DownloadRequest,
        target_dir: Path,
        control: _DownloadControl | None = None,
    ):
        super().__init__()
        self.manager = manager
        self.req = req
        self.target_dir = target_dir
        self.control = control or _DownloadControl()

    def _emit(self, signal_name: str, *args: object) -> None:
        signal = getattr(self.manager, signal_name, None)
        if signal is None:
            return
        signal.emit(*args)

    def run(self) -> None:
        logger = logging.getLogger(__name__)
        parsed = urlparse(self.req.url)
        filename = Path(parsed.path).name or f"episode-{self.req.episode_id}.mp3"
        out_file = self.target_dir / filename

        try:
            existing_size = out_file.stat().st_size if out_file.exists() else 0
            headers = {}
            if existing_size > 0:
                headers["Range"] = f"bytes={existing_size}-"
            req = Request(self.req.url, headers=headers)

            self._emit("download_status", self.req.episode_id, "downloading")
            logger.info("Starting download for episode_id=%s url=%s", self.req.episode_id, self.req.url)

            with urlopen(req, timeout=20) as resp:
                total = int(resp.headers.get("Content-Length", "0"))
                if existing_size > 0 and resp.status == 206:
                    total += existing_size
                elif existing_size > 0 and resp.status != 206:
                    existing_size = 0

                got = existing_size
                last_bytes = got
                last_ts = time.monotonic()
                mode = "ab" if existing_size > 0 else "wb"
                with out_file.open(mode) as fh:
                    while True:
                        paused, canceled = self.control.snapshot()
                        if canceled:
                            if out_file.exists():
                                out_file.unlink()
                            self._emit("download_status", self.req.episode_id, "canceled")
                            self._emit("download_canceled", self.req.episode_id)
                            return
                        if paused:
                            self._emit("download_status", self.req.episode_id, "paused")
                            time.sleep(0.2)
                            continue

                        chunk = resp.read(1024 * 64)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)

                        now = time.monotonic()
                        elapsed = max(now - last_ts, 0.001)
                        speed_bps = int((got - last_bytes) / elapsed)
                        last_ts = now
                        last_bytes = got
                        self._emit("download_progress", self.req.episode_id, got, total, speed_bps)
                        self._emit("download_status", self.req.episode_id, "downloading")

            self._emit("download_status", self.req.episode_id, "completed")
            self._emit("download_finished", self.req.episode_id, str(out_file))
            logger.info("Completed download for episode_id=%s path=%s", self.req.episode_id, out_file)
        except Exception as exc:
            logger.exception("Download failed for episode_id=%s url=%s", self.req.episode_id, self.req.url)
            self._emit("download_status", self.req.episode_id, "failed")
            self._emit("download_failed", self.req.episode_id, str(exc))
        finally:
            task_finished = getattr(self.manager, "_task_finished", None)
            if callable(task_finished):
                task_finished(self.req.episode_id)


class DownloadManager(QObject):
    download_progress = Signal(int, int, int, int)  # episode_id, bytes_received, bytes_total, speed_bps
    download_status = Signal(int, str)  # episode_id, status
    download_finished = Signal(int, str)  # episode_id, file_path
    download_failed = Signal(int, str)    # episode_id, reason
    download_canceled = Signal(int)       # episode_id

    def __init__(self, target_dir: Path):
        super().__init__()
        self.pool = QThreadPool.globalInstance()
        self.target_dir = target_dir
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.auto_download_policy = "off"
        self.notifications_enabled = True
        self._controls: dict[int, _DownloadControl] = {}

    def queue(self, episode_id: int, url: str) -> None:
        control = self._controls.get(episode_id)
        if control is not None:
            control.set_paused(False)
            self.download_status.emit(episode_id, "downloading")
            return
        control = _DownloadControl()
        self._controls[episode_id] = control
        task = _DownloadTask(self, DownloadRequest(episode_id=episode_id, url=url), self.target_dir, control)
        self.pool.start(task)

    def pause(self, episode_id: int) -> None:
        control = self._controls.get(episode_id)
        if control is None:
            return
        control.set_paused(True)
        self.download_status.emit(episode_id, "paused")

    def resume(self, episode_id: int) -> None:
        control = self._controls.get(episode_id)
        if control is None:
            return
        control.set_paused(False)
        self.download_status.emit(episode_id, "downloading")

    def cancel(self, episode_id: int) -> None:
        control = self._controls.get(episode_id)
        if control is None:
            return
        control.set_canceled()

    def _task_finished(self, episode_id: int) -> None:
        self._controls.pop(episode_id, None)


    def set_target_dir(self, target_dir: Path) -> None:
        self.target_dir = target_dir
        self.target_dir.mkdir(parents=True, exist_ok=True)

    def set_auto_download_policy(self, policy: str) -> None:
        self.auto_download_policy = policy

    def set_notifications_enabled(self, enabled: bool) -> None:
        self.notifications_enabled = enabled
