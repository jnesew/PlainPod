from __future__ import annotations

from pathlib import Path
import argparse
import logging
import os
import sys

from PySide6.QtCore import QObject, Slot, QUrl
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QFileDialog

from .download_manager import DownloadManager
from .opml import export_opml, import_opml
from .paths import db_path, downloads_dir
from .player import PlayerController
from .repository import Repository
from .settings import SettingsStore
from .viewmodel import AppViewModel
from .logging_utils import configure_logging


logger = logging.getLogger(__name__)


class OpmlController(QObject):
    def __init__(self, vm: AppViewModel, repo: Repository):
        super().__init__()
        self.vm = vm
        self.repo = repo

    @Slot()
    def import_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(None, "Import OPML", "", "OPML Files (*.opml *.xml)")
        if not filename:
            return
        try:
            for url in import_opml(Path(filename)):
                self.vm.add_feed(url)
        except Exception:
            logger.exception("OPML import failed for file: %s", filename)
            self.vm.error.emit(f"OPML import failed: {filename}")

    @Slot()
    def export_file(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(None, "Export OPML", "subscriptions.opml", "OPML Files (*.opml)")
        if not filename:
            return
        try:
            export_opml(self.repo.list_podcasts(), Path(filename))
        except Exception:
            logger.exception("OPML export failed for file: %s", filename)
            self.vm.error.emit(f"OPML export failed: {filename}")


def _has_gui_session() -> bool:
    # On Linux, Qt needs a display server (Wayland or X11) to show windows.
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="plainpod", description="PlainPod desktop podcast player")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Print GUI/session diagnostics before launching the UI",
    )
    return parser.parse_args(argv)


def _print_diagnostics(qml_file: Path) -> None:
    print("[plainpod] Diagnostics")
    print(f"[plainpod] QML file: {qml_file}")
    print(f"[plainpod] DISPLAY={os.environ.get('DISPLAY', '')}")
    print(f"[plainpod] WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '')}")
    print(f"[plainpod] XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE', '')}")
    print(f"[plainpod] QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '')}")


def main(argv: list[str] | None = None) -> None:

    project_root = Path(__file__).resolve().parents[2]
    icon_path = project_root / "resources" / "plainpod.svg"
    

    args = _parse_args(argv or sys.argv[1:])
    log_file = configure_logging(db_path().parent)

    qml_file = Path(__file__).resolve().parents[1] / "qml" / "Main.qml"
    if args.diagnose:
        _print_diagnostics(qml_file)
    logger.info("PlainPod startup. Logs at: %s", log_file)

    forced_platform = os.environ.get("QT_QPA_PLATFORM", "")
    if not _has_gui_session() and forced_platform not in {"offscreen", "minimal"}:
        print(
            "[plainpod] No GUI display session detected. "
            "Launch from a KDE desktop terminal (Konsole) or export DISPLAY/WAYLAND_DISPLAY.",
            file=sys.stderr,
        )
        sys.exit(2)

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(icon_path)))
    if QGuiApplication.primaryScreen() is None and forced_platform not in {"offscreen", "minimal"}:
        print(
            "[plainpod] Qt started but no primary screen was detected. "
            "Check your Wayland/X11 session.",
            file=sys.stderr,
        )

    repo = Repository(db_path())
    player = PlayerController()
    downloads = DownloadManager(downloads_dir())
    settings = SettingsStore()
    vm = AppViewModel(repo, downloads, player, settings)
    opml = OpmlController(vm, repo)
    vm.error.connect(lambda msg: logger.error("UI error: %s", msg))
    vm.info.connect(lambda msg: logger.info("UI info: %s", msg))

    engine = QQmlApplicationEngine()

    def _log_qml_warnings(warnings: list) -> None:
        for warning in warnings:
            logger.error("QML warning: %s:%s: %s", warning.url().toString(), warning.line(), warning.description())

    engine.warnings.connect(_log_qml_warnings)
    ctx = engine.rootContext()
    ctx.setContextProperty("vm", vm)
    ctx.setContextProperty("opml", opml)

    engine.load(QUrl.fromLocalFile(str(qml_file)))

    if not engine.rootObjects():
        print(f"[plainpod] Failed to load QML UI from {qml_file}", file=sys.stderr)
        sys.exit(1)

    app.aboutToQuit.connect(repo.close)
    sys.exit(app.exec())
