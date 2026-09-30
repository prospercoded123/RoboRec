"""GPU Status panel — wired to robo_rec.gpu via GpuProbeWorker (PRD 4.5).

Shows NVIDIA driver/CUDA toolkit presence, OpenCL device availability, and PyCUDA
importability. "Run Full Diagnostics & Export" goes well beyond a GPU snapshot: it re-runs
the GPU probe, runs a live BTC/ETH/SOL recovery self-test, and bundles in recent
recovery-run history, so the exported file is actually useful for debugging a real failed
search, not just "was a GPU found" (PRD 4.5/6.3; see robo_rec.diagnostics.report). The
"Include sensitive data" checkbox controls whether real addresses/words/recovered phrases
go into that file unredacted — off by default, since this file is meant to be handed to
someone else for debugging.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from robo_rec.gpu.report import GpuStatusReport
from robo_rec.gui.diagnostics_worker import DiagnosticsWorker
from robo_rec.gui.gpu_state import is_gpu_available
from robo_rec.gui.gpu_worker import GpuProbeWorker
from robo_rec.gui.icons import load_pixmap
from robo_rec.gui.panels.base_panel import BasePanel
from robo_rec.gui.theme import ACCENT, TEXT_SECONDARY
from robo_rec.gui.widgets.run_clock import RunClock


class GpuStatusPanel(BasePanel):
    def __init__(self, parent=None) -> None:
        super().__init__(
            "GPU Status",
            "Robo-Rec detects NVIDIA GPU acceleration automatically — this view shows "
            "what it found, and lets you export a full diagnostics report.",
            parent,
        )

        self._worker: GpuProbeWorker | None = None
        self._diagnostics_worker: DiagnosticsWorker | None = None
        self._export_target_path: Path | None = None
        self._latest_report: GpuStatusReport | None = None
        self._on_report_ready = None  # optional callback set by MainWindow

        summary_row = QHBoxLayout()
        summary_row.setSpacing(10)
        self._summary_icon = QLabel()
        summary_row.addWidget(self._summary_icon)
        self._summary_label = QLabel("Checking for a GPU…")
        self._summary_label.setObjectName("DashboardTitle")
        summary_row.addWidget(self._summary_label)
        summary_row.addStretch(1)
        self.root_layout.addLayout(summary_row)

        details_group = QGroupBox("Details")
        details_layout = QVBoxLayout(details_group)
        self._nvidia_label = QLabel()
        self._nvidia_label.setWordWrap(True)
        details_layout.addWidget(self._nvidia_label)
        self._opencl_label = QLabel()
        self._opencl_label.setWordWrap(True)
        details_layout.addWidget(self._opencl_label)
        self._pycuda_label = QLabel()
        self._pycuda_label.setWordWrap(True)
        details_layout.addWidget(self._pycuda_label)
        self.root_layout.addWidget(details_group)

        self._cpu_group = QGroupBox("Running on CPU")
        cpu_layout = QVBoxLayout(self._cpu_group)
        self._cpu_model_label = QLabel()
        self._cpu_model_label.setWordWrap(True)
        cpu_layout.addWidget(self._cpu_model_label)
        self._cpu_cores_label = QLabel()
        cpu_layout.addWidget(self._cpu_cores_label)
        self._cpu_os_label = QLabel()
        cpu_layout.addWidget(self._cpu_os_label)
        self._cpu_group.setVisible(False)
        self.root_layout.addWidget(self._cpu_group)

        self._errors_label = QLabel()
        self._errors_label.setObjectName("InfoNotice")
        self._errors_label.setWordWrap(True)
        self._errors_label.hide()
        self.root_layout.addWidget(self._errors_label)

        diagnostics_group = QGroupBox("Diagnostics Report")
        diagnostics_layout = QVBoxLayout(diagnostics_group)

        diagnostics_hint = QLabel(
            "Re-runs the GPU check, runs a live BTC/ETH/SOL recovery test, and bundles in "
            "your recent recovery runs — everything needed to debug a search that didn't "
            "behave as expected. Takes a few minutes."
        )
        diagnostics_hint.setWordWrap(True)
        diagnostics_hint.setObjectName("PanelDescription")
        diagnostics_layout.addWidget(diagnostics_hint)

        self._diagnostics_clock = RunClock()
        self._diagnostics_clock.hide()
        diagnostics_layout.addWidget(self._diagnostics_clock)

        self._include_sensitive_checkbox = QCheckBox(
            "Include sensitive data (addresses, seed words, recovered phrases) unredacted"
        )
        self._include_sensitive_checkbox.setChecked(False)
        diagnostics_layout.addWidget(self._include_sensitive_checkbox)

        self._diagnostics_status_label = QLabel()
        self._diagnostics_status_label.setWordWrap(True)
        self._diagnostics_status_label.setObjectName("InfoNotice")
        self._diagnostics_status_label.hide()
        diagnostics_layout.addWidget(self._diagnostics_status_label)

        self.root_layout.addWidget(diagnostics_group)

        buttons_row = QHBoxLayout()
        self._refresh_button = QPushButton("Re-check GPU")
        self._refresh_button.clicked.connect(self._start_probe)
        buttons_row.addWidget(self._refresh_button)

        self._export_button = QPushButton("Run Full Diagnostics && Export")
        self._export_button.setObjectName("PrimaryButton")
        self._export_button.clicked.connect(self._on_export_clicked)
        self._export_button.setEnabled(False)
        buttons_row.addWidget(self._export_button)
        buttons_row.addStretch(1)
        self.root_layout.addLayout(buttons_row)

        self.root_layout.addStretch(1)

        # No automatic probe here. MainWindow runs exactly one probe at startup and
        # feeds the result into this panel via apply_report() once it's ready — this
        # panel used to fire its own probe in __init__ *in addition to* MainWindow's
        # startup probe for the top-bar badge, so every launch raced two concurrent
        # GPU probes (each shelling out to a subprocess). Whichever one finished last
        # silently won, which meant the badge and this panel could disagree, and a
        # probe that lost the race under load could report "CPU only" even when the
        # other one found the GPU fine. Only the explicit "Re-check GPU" button (and
        # apply_report(), for MainWindow's shared result) update this panel now.

    def _start_probe(self) -> None:
        self._refresh_button.setEnabled(False)
        self._summary_label.setText("Checking for a GPU…")
        self._summary_icon.setPixmap(load_pixmap("loader-circle", TEXT_SECONDARY, 20))
        # force_recheck=True: this button is the only way to discard a cached OpenCL
        # correctness verdict and actually redo the (slow) test — e.g. after a driver
        # update that might have fixed a previously-broken GPU. MainWindow's own
        # startup probe never does this, so a verdict is normally tested once per
        # device/driver and then reused on every later launch.
        self._worker = GpuProbeWorker(force_recheck=True)
        self._worker.finished.connect(self._on_report_finished)
        self._worker.start()

    def shutdown(self) -> None:
        """Called from MainWindow.closeEvent: wait for any in-flight probe so its
        background QThread doesn't get destroyed while still running. GPU probes have no
        cancel() (they're not long-running searches) — just join whatever's in flight.

        The diagnostics self-test has no cancel() either (unlike a normal recovery search),
        so closing the window while one is running blocks briefly until it finishes — an
        accepted tradeoff given how rarely a close would land mid-self-test."""
        if self._worker is not None:
            self._worker.wait_and_cleanup()
            self._worker = None
        if self._diagnostics_worker is not None:
            self._diagnostics_worker.wait_and_cleanup()
            self._diagnostics_worker = None

    def _on_report_finished(self, report: GpuStatusReport) -> None:
        self._refresh_button.setEnabled(True)
        if self._worker is not None:
            self._worker.wait_and_cleanup()
            self._worker = None
        self.apply_report(report)

    def apply_report(self, report: GpuStatusReport) -> None:
        """Update the panel's display from a GpuStatusReport obtained elsewhere — either
        this panel's own Re-check probe, or MainWindow's single shared startup probe.
        Kept separate from _on_report_finished so MainWindow can push its result here
        without this panel spawning a second, redundant probe of its own."""
        self._latest_report = report
        self._export_button.setEnabled(True)

        if report.gpu_acceleration_available:
            self._summary_icon.setPixmap(load_pixmap("cpu", ACCENT, 20))
            self._summary_label.setText("GPU acceleration available")
            self._cpu_group.setVisible(False)
        elif report.gpu_present_but_unusable:
            # A device is present but failed (or has never passed) the correctness
            # self-test — a real state, not a corner case: confirmed directly on an
            # Intel iGPU driver that silently returned wrong PBKDF2 results with no
            # error at all. Recovery uses CPU here despite hardware existing, so the
            # badge must say that plainly rather than collapsing into "CPU Only" (which
            # would hide that a GPU exists) or "GPU acceleration available" (which
            # would be false).
            self._summary_icon.setPixmap(load_pixmap("cpu", TEXT_SECONDARY, 20))
            self._summary_label.setText("GPU detected but not usable — running on CPU")
            self._populate_cpu_details(report.cpu_info)
            self._cpu_group.setVisible(True)
        else:
            self._summary_icon.setPixmap(load_pixmap("cpu", TEXT_SECONDARY, 20))
            self._summary_label.setText("No GPU acceleration — running on CPU")
            self._populate_cpu_details(report.cpu_info)
            self._cpu_group.setVisible(True)

        if report.nvidia_gpu_name:
            self._nvidia_label.setText(
                f"NVIDIA: {report.nvidia_gpu_name}  ·  driver {report.nvidia_driver_version}"
                + (f"  ·  CUDA {report.cuda_toolkit_version}" if report.cuda_toolkit_version else "")
            )
        else:
            self._nvidia_label.setText("NVIDIA: no driver detected")

        if report.opencl_devices:
            device_names = ", ".join(d.name for d in report.opencl_devices)
            text = f"OpenCL devices: {device_names}"
            if report.opencl_usable is not None:
                source = "cached result" if report.opencl_usability_cached else "just tested"
                if report.opencl_usable:
                    text += f"  ·  correctness check passed ({source})"
                else:
                    reason = report.opencl_usability_error or "correctness check failed"
                    text += f"  ·  {reason} ({source}) — using CPU instead"
            self._opencl_label.setText(text)
        else:
            self._opencl_label.setText("OpenCL: no devices available")

        self._pycuda_label.setText(
            "PyCUDA: available (optional)"
            if report.pycuda_importable
            else "PyCUDA: not installed (optional; OpenCL is used for recovery)"
        )

        if report.probe_errors:
            self._errors_label.setText(
                "Diagnostics:\n" + "\n".join(f"• {err}" for err in report.probe_errors)
            )
            self._errors_label.show()
        else:
            self._errors_label.hide()

        if self._on_report_ready is not None:
            self._on_report_ready(
                report.gpu_acceleration_available, report.gpu_present_but_unusable
            )

    def _populate_cpu_details(self, cpu_info) -> None:
        self._cpu_model_label.setText(cpu_info.model_name or "Model name unavailable")
        cores_text = (
            f"{cpu_info.logical_cores} logical cores  ·  {cpu_info.architecture}"
            if cpu_info.logical_cores
            else cpu_info.architecture
        )
        self._cpu_cores_label.setText(cores_text)
        self._cpu_os_label.setText(cpu_info.os_name)

    def set_report_callback(self, callback) -> None:
        """Optional hook so MainWindow can update its top-bar GPU badge from real data."""
        self._on_report_ready = callback

    def _on_export_clicked(self) -> None:
        # Ask where to save *before* running the (multi-minute) self-test, so a user who
        # changes their mind at the file dialog never pays that cost for nothing.
        default_name = "robo-rec-diagnostics.json"
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Export Diagnostics Report", default_name, "JSON files (*.json)"
        )
        if not path_str:
            return
        self._export_target_path = Path(path_str)

        self._export_button.setEnabled(False)
        self._refresh_button.setEnabled(False)
        self._include_sensitive_checkbox.setEnabled(False)
        self._diagnostics_status_label.setText(
            "Running diagnostics — GPU probe, then a live BTC/ETH/SOL recovery self-test. "
            "This takes a few minutes; the app will stay responsive."
        )
        self._diagnostics_status_label.show()
        self._diagnostics_clock.show()
        self._diagnostics_clock.start()

        self._diagnostics_worker = DiagnosticsWorker(
            use_gpu_for_self_test=is_gpu_available(),
            include_sensitive=self._include_sensitive_checkbox.isChecked(),
        )
        self._diagnostics_worker.finished.connect(self._on_diagnostics_ready)
        self._diagnostics_worker.failed.connect(self._on_diagnostics_failed)
        self._diagnostics_worker.start()

    def _reset_diagnostics_controls(self) -> None:
        self._export_button.setEnabled(True)
        self._refresh_button.setEnabled(True)
        self._include_sensitive_checkbox.setEnabled(True)
        self._diagnostics_status_label.hide()
        self._diagnostics_clock.stop()  # stays visible, frozen, showing the last run
        if self._diagnostics_worker is not None:
            self._diagnostics_worker.wait_and_cleanup()
            self._diagnostics_worker = None

    def _on_diagnostics_ready(self, report: dict) -> None:
        path = self._export_target_path
        self._reset_diagnostics_controls()
        try:
            path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Exported", f"Diagnostics report saved to:\n{path}")

    def _on_diagnostics_failed(self, error: str) -> None:
        self._reset_diagnostics_controls()
        QMessageBox.critical(self, "Diagnostics failed", error)
