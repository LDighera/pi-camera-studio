from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic

import numpy as np
from PIL import Image
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import QProcess, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from libcamera import controls
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput
from picamera2.previews.qt import QGlPicamera2, QPicamera2

from . import __version__
from .audio import AudioSource, detect_audio_sources
from .config import (
    PREVIEW_SIZE,
    VIDEO_SIZE,
    CapturePaths,
    inspect_model,
    timestamped_filename,
)
from .detector import (
    DetectionResult,
    NanoDetDetector,
    annotate_frame,
    cv2,
    render_overlay,
)
from .diagnostics import diagnostics_json
from .lifecycle import DeferredClose
from .media import (
    StopMotionSession,
    build_stop_motion_ffmpeg_args,
    probe_media_file,
    rollback_partial_encoder,
    validate_image_file,
)


APP_TITLE = "Pi Camera Studio"


class DetectionWorker(QtCore.QObject):
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, model_path: Path):
        super().__init__()
        self.model_path = model_path
        self.detector: NanoDetDetector | None = None

    @pyqtSlot(object, float)
    def process(self, frame: np.ndarray, confidence: float) -> None:
        try:
            if self.detector is None:
                self.detector = NanoDetDetector(self.model_path, confidence=confidence)
            result = self.detector.detect(frame, confidence=confidence)
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.result_ready.emit(result)


class MainWindow(QMainWindow):
    detection_requested = pyqtSignal(object, float)
    mux_error_received = pyqtSignal(str)

    def __init__(self, camera_number: int = 0):
        super().__init__()
        self.camera_number = camera_number
        self.paths = CapturePaths.for_current_user()
        self.paths.ensure()
        self.model_inspection = inspect_model()
        self.ui_scale = self._calculate_ui_scale()

        self.picam2 = Picamera2(camera_number)
        self.video_config = self.picam2.create_video_configuration(
            main={"size": VIDEO_SIZE, "format": "YUV420"},
            lores={"size": PREVIEW_SIZE, "format": "RGB888"},
            raw=None,
            display="lores",
            encode="main",
            buffer_count=6,
            queue=True,
            controls={"FrameRate": 30.0},
        )
        self.still_config = self.picam2.create_still_configuration(
            main={"size": self.picam2.sensor_resolution, "format": "RGB888"},
            raw=None,
            display="main",
            buffer_count=3,
            queue=False,
        )
        self.picam2.configure(self.video_config)

        preview_class = QPicamera2 if os.environ.get("WAYLAND_DISPLAY") else QGlPicamera2
        self.preview_backend = "Qt software" if preview_class is QPicamera2 else "OpenGL"
        try:
            self.preview = preview_class(
                self.picam2,
                width=self._scaled(640),
                height=self._scaled(360),
                keep_ar=True,
                bg_colour=(12, 15, 18),
            )
        except Exception:
            self.picam2.close()
            raise
        self.preview.setMinimumSize(self._scaled(480), self._scaled(270))
        self.preview.done_signal.connect(self._camera_job_finished)

        self.pending_job = None
        self.pending_action: str | None = None
        self.pending_path: Path | None = None
        self.pending_final_path: Path | None = None
        self.pending_stop_session: StopMotionSession | None = None
        self.recording = False
        self.video_encoder = None
        self.video_output = None
        self.video_path: Path | None = None
        self.video_temp_path: Path | None = None
        self.video_requires_audio = False
        self.recording_started = 0.0
        self.audio_sources: list[AudioSource] = []
        self.stop_session: StopMotionSession | None = None
        self.render_process: QProcess | None = None
        self.render_output: Path | None = None
        self.render_temp_output: Path | None = None
        self.render_requires_audio = False
        self.detection_busy = False
        self.pending_detection_job = None
        self.detection_frame_inflight: np.ndarray | None = None
        self.last_detection_frame: np.ndarray | None = None
        self.last_detection_result: DetectionResult | None = None
        self.detection_thread: QThread | None = None
        self.detection_worker: DetectionWorker | None = None
        self._closing = False
        self._deferred_close = DeferredClose()

        self._build_ui()
        self._build_timers()
        self._refresh_audio_sources()
        self._refresh_detection_availability()

        self.mux_error_received.connect(
            self._handle_mux_error, type=QtCore.Qt.QueuedConnection
        )
        try:
            self.picam2.start()
            if (
                cv2 is not None
                and self.model_inspection.integrity_ok
                and self.model_inspection.manifest is not None
            ):
                self._build_detection_thread(self.model_inspection.manifest.model_path)
        except Exception:
            try:
                self.preview.close()
            except Exception:
                pass
            try:
                self.picam2.stop()
            except Exception:
                pass
            self.picam2.close()
            raise
        self._enable_continuous_autofocus()
        self.statusBar().showMessage("Camera ready")

    @staticmethod
    def _calculate_ui_scale() -> float:
        try:
            input_inventory = Path("/proc/bus/input/devices").read_text(errors="replace")
        except OSError:
            input_inventory = ""
        screen = QApplication.primaryScreen()
        logical_width = screen.availableGeometry().width() if screen else 1024
        if "ILITEK-TOUCH" in input_inventory or "ILITEK       ILITEK-TOUCH" in input_inventory:
            return max(1.0, min(2.0, logical_width / 1024.0))
        return 1.0

    def _scaled(self, value: int) -> int:
        return max(1, round(value * self.ui_scale))

    def _build_ui(self) -> None:
        self.setWindowTitle(f"{APP_TITLE} {__version__}")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(screen.width(), self._scaled(1024)), min(screen.height(), self._scaled(600)))
        self.setMinimumSize(
            min(screen.width(), self._scaled(900)), min(screen.height(), self._scaled(520))
        )

        central = QWidget(self)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(*([self._scaled(6)] * 4))
        outer.setSpacing(self._scaled(8))

        preview_column = QVBoxLayout()
        preview_column.addWidget(self.preview, 1)

        camera_bar = QHBoxLayout()
        camera_model = str(
            self.picam2.camera_properties.get("Model") or f"Camera {self.camera_number}"
        )
        self.camera_label = QLabel(
            f"{camera_model} • {VIDEO_SIZE[0]}×{VIDEO_SIZE[1]} • {self.preview_backend}"
        )
        self.camera_label.setObjectName("cameraLabel")
        camera_bar.addWidget(self.camera_label, 1)
        self.refocus_button = QPushButton("Refocus")
        self.refocus_button.clicked.connect(self._refocus)
        camera_bar.addWidget(self.refocus_button)
        self.exposure_slider = QSlider(QtCore.Qt.Horizontal)
        self.exposure_slider.setRange(-20, 20)
        self.exposure_slider.setValue(0)
        self.exposure_slider.setToolTip("Exposure compensation: -2.0 to +2.0 EV")
        self.exposure_slider.valueChanged.connect(self._set_exposure_compensation)
        camera_bar.addWidget(QLabel("Exposure"))
        camera_bar.addWidget(self.exposure_slider)
        preview_column.addLayout(camera_bar)

        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(self._scaled(350))
        self.tabs.setMaximumWidth(self._scaled(410))
        self.tabs.addTab(self._scrollable(self._build_still_tab()), "Still")
        self.tabs.addTab(self._scrollable(self._build_video_tab()), "Video")
        self.tabs.addTab(self._scrollable(self._build_stop_motion_tab()), "Stop")
        self.tabs.addTab(self._scrollable(self._build_detection_tab()), "Detect")
        self.tabs.setTabToolTip(1, "Video with optional microphone sound")
        self.tabs.setTabToolTip(2, "Stop-motion capture and rendering")
        self.tabs.setTabToolTip(3, "Live common-object detection")
        self.tabs.currentChanged.connect(self._tab_changed)

        outer.addLayout(preview_column, 7)
        outer.addWidget(self.tabs, 3)
        self.setCentralWidget(central)
        self._build_menu()
        self._apply_style()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("View")
        fullscreen_action = QAction("Full Screen", self)
        fullscreen_action.setShortcut("F11")
        fullscreen_action.setCheckable(True)
        fullscreen_action.toggled.connect(lambda enabled: self.showFullScreen() if enabled else self.showNormal())
        view_menu.addAction(fullscreen_action)

        help_menu = self.menuBar().addMenu("Help")
        diagnostics_action = QAction("Diagnostics", self)
        diagnostics_action.triggered.connect(self._show_diagnostics)
        help_menu.addAction(diagnostics_action)
        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _apply_style(self) -> None:
        font_size = self._scaled(13)
        tab_vertical = self._scaled(10)
        tab_horizontal = self._scaled(9)
        button_height = self._scaled(40)
        button_horizontal = self._scaled(9)
        field_padding = self._scaled(5)
        slider_height = self._scaled(5)
        slider_width = self._scaled(16)
        slider_margin = -self._scaled(6)
        group_margin = self._scaled(16)
        group_title_left = self._scaled(8)
        group_title_padding = self._scaled(4)
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{ background: #171b20; color: #eef2f5; font-size: {font_size}px; }}
            QTabWidget::pane {{ border: 1px solid #39434d; border-radius: 6px; }}
            QGroupBox {{ border: 1px solid #39434d; border-radius: 6px; margin-top: {group_margin}px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: {group_title_left}px; padding: 0 {group_title_padding}px; }}
            QTabBar::tab {{ background: #252c33; padding: {tab_vertical}px {tab_horizontal}px; margin-right: 1px; }}
            QTabBar::tab:selected {{ background: #176b87; }}
            QPushButton {{ background: #176b87; border: 0; border-radius: 5px; min-height: {button_height}px; padding: 4px {button_horizontal}px; }}
            QPushButton:hover {{ background: #2184a5; }}
            QPushButton:disabled {{ background: #343b42; color: #87919a; }}
            QPushButton#dangerButton {{ background: #a53d45; }}
            QLineEdit, QComboBox, QSpinBox, QListWidget {{ background: #22282e; border: 1px solid #46515c; border-radius: 4px; padding: {field_padding}px; min-height: {self._scaled(32)}px; }}
            QSlider::groove:horizontal {{ background: #3c4650; height: {slider_height}px; }}
            QSlider::handle:horizontal {{ background: #40b7d9; width: {slider_width}px; margin: {slider_margin}px 0; border-radius: {self._scaled(8)}px; }}
            QLabel#cameraLabel {{ color: #9ed9ea; font-weight: 600; }}
            QLabel#mutedLabel {{ color: #aab3bb; }}
            QStatusBar {{ background: #101317; }}
            """
        )

    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidget(page)
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        QtWidgets.QScroller.grabGesture(
            area.viewport(), QtWidgets.QScroller.LeftMouseButtonGesture
        )
        return area

    @staticmethod
    def _directory_row(line_edit: QLineEdit, callback) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit, 1)
        button = QPushButton("Browse…")
        button.clicked.connect(callback)
        layout.addWidget(button)
        return widget

    def _build_still_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.still_directory = QLineEdit(str(self.paths.stills))
        form.addRow("Save in", self._directory_row(self.still_directory, self._browse_still_directory))
        self.still_format = QComboBox()
        self.still_format.addItem("JPEG", ".jpg")
        self.still_format.addItem("PNG", ".png")
        form.addRow("Format", self.still_format)
        layout.addLayout(form)
        self.still_capture_button = QPushButton("Capture Full-Resolution Still")
        self.still_capture_button.clicked.connect(self._capture_still)
        layout.addWidget(self.still_capture_button)
        self.still_last_label = QLabel("No photograph captured in this session.")
        self.still_last_label.setWordWrap(True)
        self.still_last_label.setObjectName("mutedLabel")
        layout.addWidget(self.still_last_label)
        layout.addStretch(1)
        return tab

    def _build_video_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.video_directory = QLineEdit(str(self.paths.videos))
        form.addRow("Save in", self._directory_row(self.video_directory, self._browse_video_directory))
        self.video_bitrate = QComboBox()
        self.video_bitrate.addItem("Standard (8 Mb/s)", 8_000_000)
        self.video_bitrate.addItem("High (12 Mb/s)", 12_000_000)
        self.video_bitrate.addItem("Very high (20 Mb/s)", 20_000_000)
        self.video_bitrate.setCurrentIndex(1)
        form.addRow("Quality", self.video_bitrate)
        layout.addLayout(form)

        audio_group = QGroupBox("Sound")
        audio_layout = QVBoxLayout(audio_group)
        self.audio_checkbox = QCheckBox("Record microphone audio (AAC)")
        audio_layout.addWidget(self.audio_checkbox)
        self.audio_combo = QComboBox()
        audio_layout.addWidget(self.audio_combo)
        self.audio_refresh_button = QPushButton("Refresh Audio Inputs")
        self.audio_refresh_button.clicked.connect(self._refresh_audio_sources)
        audio_layout.addWidget(self.audio_refresh_button)
        self.audio_status_label = QLabel()
        self.audio_status_label.setWordWrap(True)
        self.audio_status_label.setObjectName("mutedLabel")
        audio_layout.addWidget(self.audio_status_label)
        layout.addWidget(audio_group)

        self.video_record_button = QPushButton("Start MP4 Recording")
        self.video_record_button.clicked.connect(self._toggle_video_recording)
        layout.addWidget(self.video_record_button)
        self.video_timer_label = QLabel("00:00:00")
        self.video_timer_label.setAlignment(QtCore.Qt.AlignCenter)
        font = self.video_timer_label.font()
        font.setPointSize(20)
        font.setBold(True)
        self.video_timer_label.setFont(font)
        layout.addWidget(self.video_timer_label)
        self.video_last_label = QLabel("No video recorded in this session.")
        self.video_last_label.setWordWrap(True)
        self.video_last_label.setObjectName("mutedLabel")
        layout.addWidget(self.video_last_label)
        layout.addStretch(1)
        return tab

    def _build_stop_motion_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.stop_session_label = QLabel("No sequence is open.")
        self.stop_session_label.setWordWrap(True)
        layout.addWidget(self.stop_session_label)

        session_buttons = QHBoxLayout()
        self.stop_new_button = QPushButton("New Sequence")
        self.stop_new_button.clicked.connect(self._new_stop_session)
        session_buttons.addWidget(self.stop_new_button)
        self.stop_open_button = QPushButton("Open Sequence")
        self.stop_open_button.clicked.connect(self._open_stop_session)
        session_buttons.addWidget(self.stop_open_button)
        layout.addLayout(session_buttons)

        form = QFormLayout()
        self.stop_fps = QSpinBox()
        self.stop_fps.setRange(1, 60)
        self.stop_fps.setValue(12)
        self.stop_fps.valueChanged.connect(self._stop_fps_changed)
        form.addRow("Playback fps", self.stop_fps)
        self.onion_checkbox = QCheckBox("Show previous frame")
        self.onion_checkbox.setChecked(True)
        self.onion_checkbox.toggled.connect(self._refresh_onion_overlay)
        form.addRow("Onion skin", self.onion_checkbox)
        self.onion_alpha = QSlider(QtCore.Qt.Horizontal)
        self.onion_alpha.setRange(5, 80)
        self.onion_alpha.setValue(32)
        self.onion_alpha.valueChanged.connect(self._refresh_onion_overlay)
        form.addRow("Opacity", self.onion_alpha)
        layout.addLayout(form)

        self.stop_capture_button = QPushButton("Capture Frame")
        self.stop_capture_button.setEnabled(False)
        self.stop_capture_button.clicked.connect(self._capture_stop_frame)
        layout.addWidget(self.stop_capture_button)
        self.stop_delete_button = QPushButton("Delete Last Frame")
        self.stop_delete_button.setObjectName("dangerButton")
        self.stop_delete_button.setEnabled(False)
        self.stop_delete_button.clicked.connect(self._delete_stop_frame)
        layout.addWidget(self.stop_delete_button)
        self.stop_count_label = QLabel("0 frames")
        self.stop_count_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.stop_count_label)

        soundtrack_group = QGroupBox("Render Video")
        soundtrack_layout = QVBoxLayout(soundtrack_group)
        soundtrack_row = QHBoxLayout()
        self.soundtrack_path = QLineEdit()
        self.soundtrack_path.setPlaceholderText("Optional soundtrack")
        soundtrack_row.addWidget(self.soundtrack_path, 1)
        self.soundtrack_button = QPushButton("Select…")
        self.soundtrack_button.clicked.connect(self._select_soundtrack)
        soundtrack_row.addWidget(self.soundtrack_button)
        soundtrack_layout.addLayout(soundtrack_row)
        self.stop_render_button = QPushButton("Render MP4")
        self.stop_render_button.setEnabled(False)
        self.stop_render_button.clicked.connect(self._render_stop_motion)
        soundtrack_layout.addWidget(self.stop_render_button)
        self.render_status_label = QLabel()
        self.render_status_label.setWordWrap(True)
        soundtrack_layout.addWidget(self.render_status_label)
        layout.addWidget(soundtrack_group)
        layout.addStretch(1)
        return tab

    def _build_detection_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.detection_status_label = QLabel("Checking recognition components…")
        self.detection_status_label.setWordWrap(True)
        layout.addWidget(self.detection_status_label)
        self.detection_checkbox = QCheckBox("Enable live detection")
        self.detection_checkbox.toggled.connect(self._detection_toggled)
        layout.addWidget(self.detection_checkbox)

        form = QFormLayout()
        self.confidence_slider = QSlider(QtCore.Qt.Horizontal)
        self.confidence_slider.setRange(15, 90)
        self.confidence_slider.setValue(35)
        self.confidence_slider.valueChanged.connect(self._confidence_changed)
        self.confidence_label = QLabel("35%")
        confidence_row = QWidget()
        confidence_layout = QHBoxLayout(confidence_row)
        confidence_layout.setContentsMargins(0, 0, 0, 0)
        confidence_layout.addWidget(self.confidence_slider, 1)
        confidence_layout.addWidget(self.confidence_label)
        form.addRow("Confidence", confidence_row)
        self.detection_interval = QSpinBox()
        self.detection_interval.setRange(200, 3000)
        self.detection_interval.setSingleStep(100)
        self.detection_interval.setValue(500)
        self.detection_interval.setSuffix(" ms")
        self.detection_interval.valueChanged.connect(self._detection_interval_changed)
        form.addRow("Scan interval", self.detection_interval)
        layout.addLayout(form)

        self.detection_objects = QListWidget()
        self.detection_objects.setMinimumHeight(130)
        layout.addWidget(self.detection_objects)
        self.detection_speed_label = QLabel("Detector idle")
        layout.addWidget(self.detection_speed_label)
        self.detection_snapshot_button = QPushButton("Save Annotated Snapshot")
        self.detection_snapshot_button.setEnabled(False)
        self.detection_snapshot_button.clicked.connect(self._save_detection_snapshot)
        layout.addWidget(self.detection_snapshot_button)
        note = QLabel(
            "This model detects 80 common COCO categories. It does not identify named people or uniquely identify individual objects."
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)
        layout.addStretch(1)
        return tab

    def _build_detection_thread(self, model_path: Path) -> None:
        self.detection_thread = QThread(self)
        self.detection_worker = DetectionWorker(model_path)
        self.detection_worker.moveToThread(self.detection_thread)
        self.detection_requested.connect(self.detection_worker.process)
        self.detection_worker.result_ready.connect(self._detection_finished)
        self.detection_worker.failed.connect(self._detection_failed)
        self.detection_thread.start()

    def _build_timers(self) -> None:
        self.recording_timer = QTimer(self)
        self.recording_timer.setInterval(500)
        self.recording_timer.timeout.connect(self._update_recording_timer)
        self.detection_timer = QTimer(self)
        self.detection_timer.setInterval(self.detection_interval.value())
        self.detection_timer.timeout.connect(self._queue_detection)

    def _enable_continuous_autofocus(self) -> None:
        try:
            self.picam2.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        except Exception as error:
            self.statusBar().showMessage(f"Camera ready; autofocus control unavailable: {error}", 5000)

    def _refocus(self) -> None:
        if self.recording or self.pending_job is not None:
            return
        try:
            self.picam2.set_controls(
                {"AfMode": controls.AfModeEnum.Auto, "AfTrigger": controls.AfTriggerEnum.Start}
            )
            QTimer.singleShot(1800, self._enable_continuous_autofocus)
            self.statusBar().showMessage("Autofocus cycle started", 2500)
        except Exception as error:
            self._show_error("Autofocus failed", error)

    def _set_exposure_compensation(self, value: int) -> None:
        try:
            self.picam2.set_controls({"ExposureValue": value / 10.0})
        except Exception as error:
            self.statusBar().showMessage(f"Exposure control failed: {error}", 4000)

    def _set_capture_controls_enabled(self, enabled: bool) -> None:
        camera_enabled = enabled and not self.recording and self.render_process is None
        self.still_capture_button.setEnabled(camera_enabled)
        self.refocus_button.setEnabled(camera_enabled)
        self.video_record_button.setEnabled(self.recording or camera_enabled)
        if enabled:
            self._refresh_stop_session_ui()
        else:
            for control in (
                self.stop_new_button,
                self.stop_open_button,
                self.stop_capture_button,
                self.stop_delete_button,
                self.stop_render_button,
                self.stop_fps,
                self.onion_checkbox,
                self.onion_alpha,
                self.soundtrack_path,
                self.soundtrack_button,
            ):
                control.setEnabled(False)

    def _stop_motion_is_busy(self) -> bool:
        return bool(
            self.pending_job is not None
            or self.pending_detection_job is not None
            or self.recording
            or self.render_process is not None
        )

    @staticmethod
    def _ensure_output_directory(text: str) -> Path:
        path = Path(text).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise RuntimeError(f"Not a directory: {path}")
        return path

    @staticmethod
    def _failed_output_path(final_path: Path) -> Path:
        candidate = final_path.with_name(f"{final_path.stem}.failed{final_path.suffix}")
        if not candidate.exists():
            return candidate
        return final_path.with_name(
            f"{final_path.stem}.failed_{datetime.now():%Y%m%d_%H%M%S_%f}{final_path.suffix}"
        )

    @classmethod
    def _preserve_failed_output(cls, temp_path: Path | None, final_path: Path | None) -> Path | None:
        if not temp_path:
            return None
        try:
            if not temp_path.is_file():
                return None
            if temp_path.stat().st_size <= 0:
                try:
                    temp_path.unlink(missing_ok=True)
                    return None
                except OSError:
                    return temp_path
            destination = cls._failed_output_path(final_path or temp_path)
            try:
                temp_path.replace(destination)
                return destination
            except OSError:
                return temp_path
        except OSError:
            return temp_path

    def _browse_directory(self, line_edit: QLineEdit, title: str) -> None:
        selected = QFileDialog.getExistingDirectory(self, title, line_edit.text())
        if selected:
            line_edit.setText(selected)

    def _browse_still_directory(self) -> None:
        self._browse_directory(self.still_directory, "Choose Photograph Folder")

    def _browse_video_directory(self) -> None:
        self._browse_directory(self.video_directory, "Choose Video Folder")

    def _start_capture_job(self, path: Path, action: str) -> None:
        if (
            self.pending_job is not None
            or self.pending_detection_job is not None
            or self.recording
            or self.render_process is not None
        ):
            self.statusBar().showMessage("The camera is busy", 2500)
            return
        temp_path = path.with_name(f".{path.stem}.capturing{path.suffix}")
        temp_path.unlink(missing_ok=True)
        self._set_capture_controls_enabled(False)
        self.pending_action = action
        self.pending_path = temp_path
        self.pending_final_path = path
        self.pending_stop_session = self.stop_session if action == "stop_frame" else None
        try:
            self.pending_job = self.picam2.switch_mode_and_capture_file(
                self.still_config,
                str(temp_path),
                name="main",
                wait=False,
                signal_function=self.preview.signal_done,
                delay=1,
            )
        except Exception as error:
            self.pending_action = None
            self.pending_path = None
            self.pending_final_path = None
            self.pending_stop_session = None
            self.pending_job = None
            self._set_capture_controls_enabled(True)
            self._show_error("Capture could not start", error)
            return
        self.statusBar().showMessage("Capturing full-resolution frame…")

    def _capture_still(self) -> None:
        try:
            directory = self._ensure_output_directory(self.still_directory.text())
            suffix = self.still_format.currentData()
            path = directory / timestamped_filename("still", suffix)
        except Exception as error:
            self._show_error("Invalid photograph folder", error)
            return
        self._start_capture_job(path, "still")

    @pyqtSlot(object)
    def _camera_job_finished(self, job) -> None:
        if job is self.pending_detection_job:
            self.pending_detection_job = None
            try:
                frame = self.picam2.wait(job)
            except Exception as error:
                self.detection_busy = False
                self._detection_failed(str(error))
                self._finish_deferred_close()
                return
            if (
                self._closing
                or not self.detection_checkbox.isChecked()
                or self.tabs.currentIndex() != 3
            ):
                self.detection_busy = False
                self._finish_deferred_close()
                return
            self.detection_frame_inflight = frame
            self.detection_requested.emit(frame, self.confidence_slider.value() / 100.0)
            return
        if job is not self.pending_job:
            return
        action = self.pending_action
        temp_path = self.pending_path
        final_path = self.pending_final_path
        stop_session = self.pending_stop_session
        error = None
        try:
            self.picam2.wait(job)
        except Exception as caught:
            error = caught
        self.pending_job = None
        self.pending_action = None
        self.pending_path = None
        self.pending_final_path = None
        self.pending_stop_session = None
        if not self._closing:
            self._set_capture_controls_enabled(True)

        try:
            if error:
                failed_path = self._preserve_failed_output(temp_path, final_path)
                if failed_path:
                    error = RuntimeError(f"{error}\nUnvalidated capture: {failed_path}")
                self._report_camera_job_error("Camera operation failed", error)
                return
            if not temp_path or not final_path:
                self._report_camera_job_error(
                    "Capture failed", RuntimeError("The capture state was incomplete")
                )
                return
            try:
                validate_image_file(temp_path, tuple(self.picam2.sensor_resolution))
                temp_path.replace(final_path)
            except Exception as validation_error:
                failed_path = self._preserve_failed_output(temp_path, final_path)
                detail = str(validation_error)
                if failed_path:
                    detail += f"\nUnvalidated capture: {failed_path}"
                self._report_camera_job_error(
                    "Capture validation failed", RuntimeError(detail)
                )
                return

            if action == "still":
                self.still_last_label.setText(
                    f"Saved and validated {final_path.name}\n{final_path}"
                )
                self.statusBar().showMessage(
                    f"Photograph saved and validated: {final_path.name}", 5000
                )
            elif action == "stop_frame":
                if stop_session:
                    stop_session.write_manifest()
                if stop_session is self.stop_session and not self._closing:
                    self._refresh_stop_session_ui()
                    self._refresh_onion_overlay()
                self.statusBar().showMessage(
                    f"Stop-motion frame saved and validated: {final_path.name}", 5000
                )
        finally:
            self._finish_deferred_close()

    def _report_camera_job_error(self, title: str, error: Exception) -> None:
        if self._closing:
            self.statusBar().showMessage(f"{title}: {error}", 6000)
        else:
            self._show_error(title, error)

    def _finish_deferred_close(self) -> None:
        if self._deferred_close.consume_if_idle(
            self.pending_job,
            self.pending_detection_job,
            self.detection_frame_inflight,
        ):
            QTimer.singleShot(0, self.close)

    def _refresh_audio_sources(self) -> None:
        self.audio_sources = detect_audio_sources()
        self.audio_combo.clear()
        for source in self.audio_sources:
            self.audio_combo.addItem(source.label)
        available = bool(self.audio_sources)
        self.audio_checkbox.setEnabled(available and not self.recording)
        self.audio_combo.setEnabled(available and not self.recording)
        if available:
            self.audio_checkbox.setChecked(True)
            self.audio_status_label.setText("Audio input detected. The first recording should be checked for level and A/V sync.")
        else:
            self.audio_checkbox.setChecked(False)
            self.audio_status_label.setText(
                "No microphone input is currently exposed by ALSA or the desktop audio "
                "service. Video can still be recorded without sound."
            )

    def _toggle_video_recording(self) -> None:
        if self.recording:
            self._stop_video_recording()
        else:
            self._start_video_recording()

    def _start_video_recording(self) -> None:
        if (
            self.pending_job is not None
            or self.pending_detection_job is not None
            or self.render_process is not None
        ):
            self.statusBar().showMessage("Wait for the current camera operation to finish", 3000)
            return
        path = None
        temp_path = None
        encoder = None
        output = None
        try:
            directory = self._ensure_output_directory(self.video_directory.text())
            path = directory / timestamped_filename("video", ".mp4")
            temp_path = path.with_name(f".{path.stem}.recording{path.suffix}")
            encoder = H264Encoder(
                bitrate=int(self.video_bitrate.currentData()), repeat=True, framerate=30
            )
            if self.audio_checkbox.isChecked():
                if not self.audio_sources:
                    raise RuntimeError("No audio input is available")
                source = self.audio_sources[self.audio_combo.currentIndex()]
                encoder.audio = True
                encoder.audio_input = source.pyav_open_kwargs()
                encoder.audio_output = {"codec_name": "aac"}
                encoder.audio_sync = -100_000
            output = PyavOutput(str(temp_path), format="mp4")
            output.error_callback = lambda error: self.mux_error_received.emit(str(error))
            self.picam2.start_encoder(encoder, output, name="main")
        except Exception as error:
            rollback_errors = rollback_partial_encoder(encoder, output)
            failed_path = self._preserve_failed_output(temp_path, path)
            detail = str(error)
            if rollback_errors:
                detail += "\n" + "\n".join(rollback_errors)
            if failed_path:
                detail += f"\nIncomplete recording: {failed_path}"
            self._show_error("Recording could not start", RuntimeError(detail))
            return

        self.video_encoder = encoder
        self.video_output = output
        self.video_path = path
        self.video_temp_path = temp_path
        self.video_requires_audio = bool(encoder.audio)
        self.recording = True
        self.recording_started = monotonic()
        self.recording_timer.start()
        self.video_record_button.setText("Stop Recording")
        self.video_record_button.setObjectName("dangerButton")
        self.video_record_button.style().unpolish(self.video_record_button)
        self.video_record_button.style().polish(self.video_record_button)
        self._set_capture_controls_enabled(False)
        self.audio_checkbox.setEnabled(False)
        self.audio_combo.setEnabled(False)
        sound_text = "with sound" if encoder.audio else "without sound"
        self.statusBar().showMessage(f"Recording {sound_text}: {path.name}")

    def _stop_video_recording(self, forced_error: Exception | None = None) -> None:
        if not self.recording:
            return
        error = forced_error
        encoder = self.video_encoder
        output = self.video_output
        try:
            self.picam2.stop_encoder(encoder)
        except Exception as caught:
            if error is None:
                error = caught
            try:
                self.picam2.encoders.discard(encoder)
            except Exception:
                pass
        try:
            if output is not None:
                output.stop()
        except Exception as caught:
            if error is None:
                error = caught
        path = self.video_path
        temp_path = self.video_temp_path
        require_audio = self.video_requires_audio
        self.recording = False
        self.video_encoder = None
        self.video_output = None
        self.video_path = None
        self.video_temp_path = None
        self.video_requires_audio = False
        self.recording_timer.stop()
        self.video_record_button.setText("Start MP4 Recording")
        self.video_record_button.setObjectName("")
        self.video_record_button.style().unpolish(self.video_record_button)
        self.video_record_button.style().polish(self.video_record_button)
        self._set_capture_controls_enabled(True)
        self.audio_checkbox.setEnabled(bool(self.audio_sources))
        self.audio_combo.setEnabled(bool(self.audio_sources))
        if error:
            failed_path = self._preserve_failed_output(temp_path, path)
            detail = str(error)
            if failed_path:
                detail += f"\nIncomplete recording: {failed_path}"
            self._show_error("Recording stopped with an error", RuntimeError(detail))
        elif temp_path and temp_path.is_file() and temp_path.stat().st_size > 0 and path:
            try:
                probe_media_file(temp_path, require_audio=require_audio)
                temp_path.replace(path)
            except Exception as validation_error:
                failed_path = self._preserve_failed_output(temp_path, path)
                self._show_error(
                    "Recording validation failed",
                    RuntimeError(
                        f"{validation_error}\n"
                        f"The unvalidated file remains at {failed_path}"
                    ),
                )
                return
            self.video_last_label.setText(f"Saved and validated {path.name}\n{path}")
            self.statusBar().showMessage(f"Video saved and validated: {path.name}", 5000)
        else:
            self._show_error("Recording failed", RuntimeError("No usable MP4 file was created"))

    @pyqtSlot(str)
    def _handle_mux_error(self, message: str) -> None:
        if self.recording:
            self._stop_video_recording(RuntimeError(f"Video muxing failed: {message}"))
        else:
            self._show_error("Video muxing failed", RuntimeError(message))

    def _update_recording_timer(self) -> None:
        elapsed = max(0, int(monotonic() - self.recording_started))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.video_timer_label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def _new_stop_session(self) -> None:
        if self._stop_motion_is_busy():
            self.statusBar().showMessage("Wait for the current operation to finish", 3000)
            return
        default = f"sequence_{datetime.now():%Y%m%d_%H%M%S}"
        name, accepted = QInputDialog.getText(self, "New Stop-Motion Sequence", "Sequence name", text=default)
        if not accepted:
            return
        try:
            self.paths.stop_motion.mkdir(parents=True, exist_ok=True)
            self.stop_session = StopMotionSession.create(self.paths.stop_motion, name, self.stop_fps.value())
        except FileExistsError:
            self._show_error("Sequence already exists", RuntimeError("Choose a different sequence name"))
            return
        except Exception as error:
            self._show_error("Sequence could not be created", error)
            return
        self._refresh_stop_session_ui()
        self._refresh_onion_overlay()

    def _open_stop_session(self) -> None:
        if self._stop_motion_is_busy():
            self.statusBar().showMessage("Wait for the current operation to finish", 3000)
            return
        selected = QFileDialog.getExistingDirectory(self, "Open Stop-Motion Sequence", str(self.paths.stop_motion))
        if not selected:
            return
        try:
            self.stop_session = StopMotionSession.open(Path(selected))
            self.stop_fps.setValue(self.stop_session.fps)
        except Exception as error:
            self._show_error("Sequence could not be opened", error)
            return
        self._refresh_stop_session_ui()
        self._refresh_onion_overlay()

    def _stop_fps_changed(self, fps: int) -> None:
        if self.stop_session and not self._stop_motion_is_busy():
            self.stop_session.fps = fps
            self.stop_session.write_manifest()

    def _refresh_stop_session_ui(self) -> None:
        busy = self._stop_motion_is_busy()
        self.stop_new_button.setEnabled(not busy)
        self.stop_open_button.setEnabled(not busy)
        self.stop_fps.setEnabled(not busy)
        self.onion_checkbox.setEnabled(not busy)
        self.onion_alpha.setEnabled(not busy)
        self.soundtrack_path.setEnabled(not busy)
        self.soundtrack_button.setEnabled(not busy)
        if not self.stop_session:
            self.stop_session_label.setText("No sequence is open.")
            self.stop_count_label.setText("0 frames")
            self.stop_capture_button.setEnabled(False)
            self.stop_delete_button.setEnabled(False)
            self.stop_render_button.setEnabled(False)
            return
        count = self.stop_session.frame_count
        self.stop_session_label.setText(f"{self.stop_session.directory.name}\n{self.stop_session.directory}")
        self.stop_count_label.setText(f"{count} frame{'s' if count != 1 else ''}")
        idle = not busy
        self.stop_capture_button.setEnabled(idle)
        self.stop_delete_button.setEnabled(idle and count > 0)
        self.stop_render_button.setEnabled(idle and count >= 2)

    def _capture_stop_frame(self) -> None:
        if not self.stop_session:
            return
        self._start_capture_job(self.stop_session.next_frame_path(), "stop_frame")

    def _delete_stop_frame(self) -> None:
        if (
            self._stop_motion_is_busy()
            or not self.stop_session
            or self.stop_session.frame_count == 0
        ):
            return
        answer = QMessageBox.question(
            self,
            "Delete Last Frame",
            f"Delete {self.stop_session.last_frame_path().name}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            deleted = self.stop_session.delete_last_frame()
        except Exception as error:
            self._show_error("Frame could not be deleted", error)
            return
        self._refresh_stop_session_ui()
        self._refresh_onion_overlay()
        if deleted:
            self.statusBar().showMessage(f"Deleted {deleted.name}", 3000)

    def _refresh_onion_overlay(self) -> None:
        if self.tabs.currentIndex() != 2:
            return
        if not self.onion_checkbox.isChecked() or not self.stop_session:
            self._set_overlay(None)
            return
        path = self.stop_session.last_frame_path()
        if not path:
            self._set_overlay(None)
            return
        try:
            with Image.open(path) as image:
                image = image.convert("RGBA").resize(PREVIEW_SIZE, Image.Resampling.LANCZOS)
                overlay = np.asarray(image).copy()
            overlay[:, :, 3] = int(255 * self.onion_alpha.value() / 100)
            self._set_overlay(np.ascontiguousarray(overlay))
        except Exception as error:
            self.statusBar().showMessage(f"Onion-skin preview failed: {error}", 4000)

    def _select_soundtrack(self) -> None:
        if self._stop_motion_is_busy():
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Optional Soundtrack",
            str(Path.home()),
            "Audio files (*.wav *.flac *.mp3 *.m4a *.aac *.ogg);;All files (*)",
        )
        if selected:
            self.soundtrack_path.setText(selected)

    def _render_stop_motion(self) -> None:
        if (
            self._stop_motion_is_busy()
            or not self.stop_session
            or self.stop_session.frame_count < 2
        ):
            return
        try:
            self.stop_session.validate_contiguous_frames(minimum=2)
        except Exception as error:
            self._show_error("Sequence cannot be rendered", error)
            return
        suggested = self.stop_session.directory / f"{self.stop_session.directory.name}.mp4"
        selected, _ = QFileDialog.getSaveFileName(self, "Save Stop-Motion Video", str(suggested), "MP4 video (*.mp4)")
        if not selected:
            return
        output = Path(selected)
        if output.suffix.lower() != ".mp4":
            output = output.with_suffix(".mp4")
        soundtrack = Path(self.soundtrack_path.text()).expanduser() if self.soundtrack_path.text().strip() else None
        if soundtrack and not soundtrack.is_file():
            self._show_error("Invalid soundtrack", RuntimeError(f"File not found: {soundtrack}"))
            return
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self._show_error("Rendering unavailable", RuntimeError("ffmpeg is not installed"))
            return
        try:
            arguments = build_stop_motion_ffmpeg_args(
                self.stop_session.directory,
                output,
                self.stop_fps.value(),
                soundtrack,
                self.stop_session.frame_count,
            )
        except Exception as error:
            self._show_error("Render settings are invalid", error)
            return

        temp_output = output.with_name(f".{output.stem}.rendering{output.suffix}")
        temp_output.unlink(missing_ok=True)
        arguments[-1] = str(temp_output)
        process = QProcess(self)
        process.setProgram(ffmpeg)
        process.setArguments(arguments)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(self._render_output_ready)
        process.finished.connect(self._render_finished)
        process.errorOccurred.connect(self._render_process_error)
        self.render_process = process
        self.render_output = output
        self.render_temp_output = temp_output
        self.render_requires_audio = soundtrack is not None
        self._set_capture_controls_enabled(False)
        self.render_status_label.setText("Rendering…")
        process.start()

    def _render_output_ready(self) -> None:
        if not self.render_process:
            return
        text = bytes(self.render_process.readAllStandardOutput()).decode(errors="replace").strip()
        if text:
            self.render_status_label.setText(text[-500:])

    @pyqtSlot(QProcess.ProcessError)
    def _render_process_error(self, process_error: QProcess.ProcessError) -> None:
        process = self.sender()
        if process is not self.render_process:
            return
        if process_error != QProcess.FailedToStart:
            self.render_status_label.setText(f"Render process error: {process.errorString()}")
            return
        output = self.render_output
        temp_output = self.render_temp_output
        detail = process.errorString()
        self.render_process = None
        self.render_output = None
        self.render_temp_output = None
        self.render_requires_audio = False
        failed_path = self._preserve_failed_output(temp_output, output)
        if failed_path:
            detail += f". Incomplete render: {failed_path}"
        self.render_status_label.setText(f"Render could not start: {detail}")
        process.deleteLater()
        self._set_capture_controls_enabled(True)

    def _render_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        process = self.sender()
        if process is not self.render_process:
            return
        output = self.render_output
        temp_output = self.render_temp_output
        require_audio = self.render_requires_audio
        self.render_process = None
        self.render_output = None
        self.render_temp_output = None
        self.render_requires_audio = False
        if (
            exit_code == 0
            and exit_status == QProcess.NormalExit
            and output
            and temp_output
            and temp_output.is_file()
            and temp_output.stat().st_size
        ):
            try:
                probe_media_file(temp_output, require_audio=require_audio)
                temp_output.replace(output)
            except Exception as error:
                failed_path = self._preserve_failed_output(temp_output, output)
                self.render_status_label.setText(
                    f"Render validation failed: {error}. Unvalidated file: {failed_path}"
                )
            else:
                self.render_status_label.setText(f"Saved and validated {output}")
                self.statusBar().showMessage(
                    f"Stop-motion video saved and validated: {output.name}", 5000
                )
        else:
            failed_path = self._preserve_failed_output(temp_output, output)
            detail = f"Render failed (exit code {exit_code})"
            if failed_path:
                detail += f". Incomplete render: {failed_path}"
            self.render_status_label.setText(detail)
        process.deleteLater()
        self._set_capture_controls_enabled(True)

    def _refresh_detection_availability(self) -> None:
        inspection = self.model_inspection
        if cv2 is None:
            self.detection_status_label.setText("Unavailable: install python3-opencv.")
            self.detection_checkbox.setEnabled(False)
        elif inspection.manifest is None:
            self.detection_status_label.setText(
                f"Unavailable: {inspection.manifest_error or 'model manifest is invalid.'}"
            )
            self.detection_checkbox.setEnabled(False)
        elif inspection.model_size is None:
            self.detection_status_label.setText(
                f"Unavailable: model file is missing at {inspection.manifest.model_path}"
            )
            self.detection_checkbox.setEnabled(False)
        elif not inspection.model_integrity_ok:
            self.detection_status_label.setText("Unavailable: the NanoDet model failed its integrity check.")
            self.detection_checkbox.setEnabled(False)
        elif not inspection.license_integrity_ok:
            self.detection_status_label.setText(
                "Unavailable: the bundled NanoDet license failed its integrity check."
            )
            self.detection_checkbox.setEnabled(False)
        else:
            self.detection_status_label.setText(
                f"Ready: OpenCV {cv2.__version__}, {inspection.manifest.name} "
                f"({inspection.model_size / 1_000_000:.1f} MB)."
            )
            self.detection_checkbox.setEnabled(True)

    def _detection_toggled(self, enabled: bool) -> None:
        if enabled and self.tabs.currentIndex() == 3:
            self.detection_timer.start()
            self._queue_detection()
        else:
            self.detection_timer.stop()
            if self.tabs.currentIndex() == 3:
                self._set_overlay(None)
            self.detection_objects.clear()
            self.detection_speed_label.setText("Detector idle")

    def _confidence_changed(self, value: int) -> None:
        self.confidence_label.setText(f"{value}%")

    def _detection_interval_changed(self, value: int) -> None:
        self.detection_timer.setInterval(value)

    def _queue_detection(self) -> None:
        if (
            not self.detection_checkbox.isChecked()
            or self.tabs.currentIndex() != 3
            or self.detection_busy
            or self.pending_detection_job is not None
            or self.pending_job is not None
            or self._closing
        ):
            return
        try:
            self.pending_detection_job = self.picam2.capture_array(
                "lores", wait=False, signal_function=self.preview.signal_done
            )
        except Exception as error:
            self._detection_failed(str(error))
            return
        self.detection_busy = True

    @pyqtSlot(object)
    def _detection_finished(self, result: DetectionResult) -> None:
        self.detection_busy = False
        self.last_detection_result = result
        self.last_detection_frame = self.detection_frame_inflight
        self.detection_frame_inflight = None
        if self._closing:
            self._finish_deferred_close()
            return
        if not self.detection_checkbox.isChecked() or self.tabs.currentIndex() != 3:
            return
        try:
            self._set_overlay(render_overlay(result.frame_size, result.detections))
        except Exception as error:
            self._detection_failed(str(error))
            return
        self.detection_objects.clear()
        for detection in sorted(result.detections, key=lambda item: item.confidence, reverse=True):
            self.detection_objects.addItem(f"{detection.label} — {detection.confidence:.0%}")
        if not result.detections:
            self.detection_objects.addItem("No objects above the confidence threshold")
        self.detection_speed_label.setText(
            f"Inference: {result.inference_ms:.0f} ms • {len(result.detections)} object(s)"
        )
        self.detection_snapshot_button.setEnabled(True)

    @pyqtSlot(str)
    def _detection_failed(self, message: str) -> None:
        self.detection_busy = False
        self.detection_frame_inflight = None
        self.detection_timer.stop()
        if self._closing:
            self._finish_deferred_close()
            return
        self.detection_checkbox.blockSignals(True)
        self.detection_checkbox.setChecked(False)
        self.detection_checkbox.blockSignals(False)
        self._set_overlay(None)
        self.detection_status_label.setText(f"Detection error: {message}")
        self.statusBar().showMessage("Object detection stopped after an error", 5000)

    def _save_detection_snapshot(self) -> None:
        if cv2 is None or self.last_detection_result is None or self.last_detection_frame is None:
            return
        try:
            annotated = annotate_frame(self.last_detection_frame, self.last_detection_result.detections)
            self.paths.detections.mkdir(parents=True, exist_ok=True)
            path = self.paths.detections / timestamped_filename("detection", ".jpg")
            temporary_path = path.with_name(f".{path.stem}.capturing{path.suffix}")
            temporary_path.unlink(missing_ok=True)
            if not cv2.imwrite(str(temporary_path), annotated):
                raise RuntimeError("OpenCV did not write the annotated image")
            validate_image_file(
                temporary_path, (int(annotated.shape[1]), int(annotated.shape[0]))
            )
            temporary_path.replace(path)
        except Exception as error:
            if "temporary_path" in locals():
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._show_error("Detection snapshot failed", error)
            return
        self.statusBar().showMessage(f"Detection snapshot saved: {path}", 5000)

    def _set_overlay(self, overlay: np.ndarray | None) -> None:
        try:
            self.preview.set_overlay(overlay)
        except Exception as error:
            if not self._closing:
                self.statusBar().showMessage(f"Preview overlay failed: {error}", 4000)

    def _tab_changed(self, index: int) -> None:
        self._set_overlay(None)
        if index == 2:
            self._refresh_onion_overlay()
        if index == 3 and self.detection_checkbox.isChecked():
            self.detection_timer.start()
            self._queue_detection()
        else:
            self.detection_timer.stop()

    def _show_diagnostics(self) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Pi Camera Studio Diagnostics")
        dialog.setText("Local camera-stack diagnostics")
        dialog.setDetailedText(diagnostics_json())
        dialog.setIcon(QMessageBox.Information)
        dialog.exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_TITLE}",
            f"{APP_TITLE} {__version__}\n\n"
            "Integrated still, H.264/AAC video, stop-motion, and OpenCV NanoDet object detection for Picamera2.",
        )

    def _show_error(self, title: str, error: Exception) -> None:
        self.statusBar().showMessage(f"{title}: {error}", 6000)
        if self._closing:
            print(f"{title}: {error}", file=sys.stderr)
            return
        QMessageBox.critical(self, title, str(error))

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._closing = True
        self.detection_timer.stop()
        if self._deferred_close.defer_if_busy(
            self.pending_job,
            self.pending_detection_job,
            self.detection_frame_inflight,
        ):
            self._set_capture_controls_enabled(False)
            self.detection_checkbox.setEnabled(False)
            self.statusBar().showMessage(
                "Finishing the current camera operation before closing…"
            )
            event.ignore()
            return
        if self.recording:
            self._stop_video_recording()
        if self.render_process and self.render_process.state() != QProcess.NotRunning:
            process = self.render_process
            process.terminate()
            if not process.waitForFinished(1500):
                process.kill()
                process.waitForFinished(1000)
            if self.render_process is process:
                self._preserve_failed_output(self.render_temp_output, self.render_output)
                self.render_process = None
                self.render_output = None
                self.render_temp_output = None
                self.render_requires_audio = False
        if self.detection_thread is not None:
            self.detection_thread.quit()
            if not self.detection_thread.wait(10000):
                self._closing = False
                self._deferred_close.cancel()
                self.detection_checkbox.setChecked(False)
                self.statusBar().showMessage(
                    "Close postponed because the detector is still finishing; try again shortly",
                    10000,
                )
                event.ignore()
                return
        try:
            self._set_overlay(None)
            self.picam2.stop()
        except Exception:
            pass
        try:
            self.preview.close()
        except Exception:
            pass
        try:
            self.picam2.close()
        except Exception:
            pass
        event.accept()


def launch_gui(
    camera_number: int = 0,
    windowed: bool = False,
    smoke_seconds: float = 0,
    smoke_tab: int = 0,
) -> int:
    """Start the Qt interface after the lightweight CLI has selected GUI mode."""

    QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    application = QApplication(sys.argv[:1])
    application.setApplicationName(APP_TITLE)
    application.setOrganizationName("Pi Camera Studio")
    try:
        window = MainWindow(camera_number)
    except Exception as error:
        print(f"Camera initialization failed: {error}", file=sys.stderr)
        if smoke_seconds <= 0:
            QMessageBox.critical(None, "Camera initialization failed", str(error))
        return 1
    window.tabs.setCurrentIndex(smoke_tab)
    if windowed:
        window.show()
    else:
        window.showMaximized()
    if smoke_seconds > 0:
        QTimer.singleShot(max(1, int(smoke_seconds * 1000)), window.close)
    return application.exec()
