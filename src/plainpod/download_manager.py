from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from collections import deque
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, Signal, QRunnable, QThread, QThreadPool


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

    def _sanitize_basename(self, name: str) -> str:
        stem = Path(name).stem
        suffix = Path(name).suffix
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
        if not safe_stem:
            safe_stem = "episode"
        safe_suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix or "") else ".mp3"
        return f"{safe_stem}{safe_suffix}"

    def _filename_from_url(self) -> str:
        parsed = urlparse(self.req.url)
        basename = Path(parsed.path).name
        if basename:
            return f"{self.req.episode_id}-{self._sanitize_basename(basename)}"
        ext = Path(parsed.path).suffix
        safe_ext = ext if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", ext or "") else ".mp3"
        digest = hashlib.sha256(self.req.url.encode("utf-8")).hexdigest()[:24]
        return f"{self.req.episode_id}-{digest}{safe_ext}"

    def _path_belongs_to_another_episode(self, out_file: Path) -> bool:
        owner_lookup = getattr(self.manager, "_lookup_episode_for_path", None)
        if not callable(owner_lookup):
            return False
        owner = owner_lookup(out_file)
        return owner is not None and owner != self.req.episode_id

    def _choose_output_file(self) -> Path:
        candidate = self.target_dir / self._filename_from_url()
        counter = 1
        while True:
            if not self._path_belongs_to_another_episode(candidate):
                return candidate
            candidate = self.target_dir / f"{candidate.stem}-{counter}{candidate.suffix}"
            counter += 1

    def run(self) -> None:
        logger = logging.getLogger(__name__)
        out_file = self._choose_output_file()
        current_status: str | None = None

        def emit_status(status: str) -> None:
            nonlocal current_status
            if status == current_status:
                return
            current_status = status
            self._emit("download_status", self.req.episode_id, status)

        try:
            existing_size = out_file.stat().st_size if out_file.exists() else 0
            headers = {}
            if existing_size > 0:
                headers["Range"] = f"bytes={existing_size}-"
            req = Request(self.req.url, headers=headers)

            emit_status("downloading")
            logger.info("Starting download for episode_id=%s url=%s", self.req.episode_id, self.req.url)

            with urlopen(req, timeout=20) as resp:
                total = int(resp.headers.get("Content-Length", "0"))
                if existing_size > 0 and resp.status == 206:
                    total += existing_size
                elif existing_size > 0 and resp.status != 206:
                    existing_size = 0

                got = existing_size
                last_emit_bytes = got
                last_emit_ts = time.monotonic()
                progress_emit_interval = 0.25
                mode = "ab" if existing_size > 0 else "wb"
                with out_file.open(mode) as fh:
                    while True:
                        paused, canceled = self.control.snapshot()
                        if canceled:
                            if out_file.exists():
                                out_file.unlink()
                            emit_status("canceled")
                            self._emit("download_canceled", self.req.episode_id)
                            return
                        if paused:
                            emit_status("paused")
                            time.sleep(0.2)
                            continue
                        emit_status("downloading")

                        chunk = resp.read(1024 * 64)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)

                        now = time.monotonic()
                        if now - last_emit_ts >= progress_emit_interval:
                            speed_bps = int((got - last_emit_bytes) / max(now - last_emit_ts, 0.001))
                            last_emit_bytes = got
                            last_emit_ts = now
                            self._emit("download_progress", self.req.episode_id, got, total, speed_bps)

                now = time.monotonic()
                speed_bps = int((got - last_emit_bytes) / max(now - last_emit_ts, 0.001))
                self._emit("download_progress", self.req.episode_id, got, total, speed_bps)

            emit_status("completed")
            self._emit("download_finished", self.req.episode_id, str(out_file))
            logger.info("Completed download for episode_id=%s path=%s", self.req.episode_id, out_file)
        except Exception as exc:
            logger.exception("Download failed for episode_id=%s url=%s", self.req.episode_id, self.req.url)
            emit_status("failed")
            self._emit("download_failed", self.req.episode_id, str(exc))
        finally:
            self._emit("_task_finished_signal", self.req.episode_id)


class DownloadManager(QObject):
    download_progress = Signal(int, int, int, int)  # episode_id, bytes_received, bytes_total, speed_bps
    download_status = Signal(int, str)  # episode_id, status
    download_finished = Signal(int, str)  # episode_id, file_path
    download_failed = Signal(int, str)    # episode_id, reason
    download_canceled = Signal(int)       # episode_id
    _task_finished_signal = Signal(int)   # episode_id

    def __init__(self, target_dir: Path, repository: object | None = None):
        super().__init__()
        self.pool = QThreadPool.globalInstance()
        self.target_dir = target_dir
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.repository = repository
        self.auto_download_policy = "off"
        self.notifications_enabled = True
        self.max_concurrent_downloads = 3
        self._controls: dict[int, _DownloadControl] = {}
        self._pending: deque[tuple[int, str, _DownloadControl]] = deque()
        self._active: set[int] = set()
        self._task_finished_signal.connect(self._task_finished)

    def _assert_manager_thread(self) -> None:
        if QThread.currentThread() != self.thread():
            raise RuntimeError("DownloadManager state must be mutated on its Qt thread")

    def queue(self, episode_id: int, url: str) -> None:
        self._assert_manager_thread()
        control = self._controls.get(episode_id)
        if control is not None:
            control.set_paused(False)
            status = "downloading" if episode_id in self._active else "queued"
            self.download_status.emit(episode_id, status)
            return
        control = _DownloadControl()
        self._controls[episode_id] = control
        if len(self._active) >= self.max_concurrent_downloads:
            self._pending.append((episode_id, url, control))
            self.download_status.emit(episode_id, "queued")
            return
        self._start_task(episode_id, url, control)

    def _start_task(self, episode_id: int, url: str, control: _DownloadControl) -> None:
        self._assert_manager_thread()
        self._active.add(episode_id)
        task = _DownloadTask(self, DownloadRequest(episode_id=episode_id, url=url), self.target_dir, control)
        self.pool.start(task)

    def pause(self, episode_id: int) -> None:
        self._assert_manager_thread()
        control = self._controls.get(episode_id)
        if control is None:
            return
        control.set_paused(True)
        self.download_status.emit(episode_id, "paused")

    def resume(self, episode_id: int) -> None:
        self._assert_manager_thread()
        control = self._controls.get(episode_id)
        if control is None:
            return
        control.set_paused(False)
        self.download_status.emit(episode_id, "downloading")

    def cancel(self, episode_id: int) -> None:
        self._assert_manager_thread()
        control = self._controls.get(episode_id)
        if control is None:
            return
        control.set_canceled()
        if episode_id not in self._active:
            self._pending = deque(item for item in self._pending if item[0] != episode_id)
            self._controls.pop(episode_id, None)
            self.download_status.emit(episode_id, "canceled")
            self.download_canceled.emit(episode_id)

    def _task_finished(self, episode_id: int) -> None:
        self._assert_manager_thread()
        self._active.discard(episode_id)
        self._controls.pop(episode_id, None)
        self._start_next_pending()

    def _start_next_pending(self) -> None:
        self._assert_manager_thread()
        while self._pending and len(self._active) < self.max_concurrent_downloads:
            episode_id, url, control = self._pending.popleft()
            _, canceled = control.snapshot()
            if canceled:
                self._controls.pop(episode_id, None)
                continue
            self._start_task(episode_id, url, control)

    def _lookup_episode_for_path(self, path: Path) -> int | None:
        if self.repository is None:
            return None
        owner_lookup = getattr(self.repository, "episode_id_for_local_path", None)
        if not callable(owner_lookup):
            return None
        return owner_lookup(str(path))

    def set_target_dir(self, target_dir: Path) -> None:
        self.target_dir = target_dir
        self.target_dir.mkdir(parents=True, exist_ok=True)

    def set_auto_download_policy(self, policy: str) -> None:
        self.auto_download_policy = policy

    def set_notifications_enabled(self, enabled: bool) -> None:
        self.notifications_enabled = enabled

    def set_max_concurrent_downloads(self, count: int) -> None:
        self._assert_manager_thread()
        self.max_concurrent_downloads = max(1, int(count))
        self._start_next_pending()
