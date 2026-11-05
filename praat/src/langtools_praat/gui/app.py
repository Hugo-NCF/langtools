"""Minimal runnable workspace: real I/O and edits, no placeholder analysis results."""

import sys
from pathlib import Path
from threading import Event

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..annotation.session import AnnotationSession
from ..application.workspace import Workspace
from ..errors import Cancelled, InvalidInput
from ..io.audio import AudioFile
from ..media.transport import Transport
from ..media.waveform import summarize
from ..model.textgrid import IntervalTier, TextGrid
from .table import AnnotationTable
from .tiers import TierCanvas


class WaveSignals(QObject):
    ready = Signal(int, object)
    failed = Signal(int, str)


class WaveJob(QRunnable):
    def __init__(self, token, source, start, end, channel, pixels, cancellation):
        super().__init__()
        self.signals = WaveSignals()
        self.args = (source, start, end)
        self.options = {"channel": channel, "pixels": pixels, "cancellation": cancellation}
        self.token = token

    def run(self):
        try:
            result = summarize(*self.args, **self.options)
            self.signals.ready.emit(self.token, result)
        except Cancelled:
            pass
        except Exception as error:
            self.signals.failed.emit(self.token, str(error))


class MainWindow(QMainWindow):
    def __init__(self, transport=None):
        super().__init__()
        self.workspace = Workspace()
        self.transport = transport if transport is not None else Transport()
        self._playback_id = None
        self.source: AudioFile | None = None
        self.session: AnnotationSession | None = None
        self._token = 0
        self._cancel = Event()
        self.setWindowTitle("LangTools Praat — audio and annotation prototype")
        self.resize(1120, 740)
        self.objects = QListWidget()
        self.objects.setAccessibleName("Workspace objects")
        self.objects.setMinimumWidth(190)
        self.objects.currentItemChanged.connect(lambda *_: self.guard(self.show_selected))
        self.details = QLabel("Open a PCM WAV or text TextGrid to begin.")
        self.details.setWordWrap(True)
        self.channel = QComboBox()
        self.channel.setAccessibleName("Waveform channel")
        self.channel.setToolTip("Waveform display channel; playback preserves all source channels.")
        self.channel.currentIndexChanged.connect(lambda *_: self.queue_waveform())
        self.plot = pg.PlotWidget(background="w")
        self.plot.setAccessibleName("Waveform and time selection")
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.setLabel("left", "Amplitude")
        self.plot.getAxis("left").setWidth(110)
        self.plot.disableAutoRange()
        self.plot.setYRange(-1, 1)
        self.curve = self.plot.plot(pen=pg.mkPen("#245b7a", width=1))
        self.region = pg.LinearRegionItem((0, 1), brush=(50, 120, 180, 35))
        self.region.setZValue(10)
        self.plot.addItem(self.region)
        self.cursor = pg.InfiniteLine(pos=0, movable=False, pen=pg.mkPen("#b65610", width=2))
        self.cursor.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.plot.addItem(self.cursor)
        self.region.sigRegionChanged.connect(self.selection_changed)
        self.table_model = AnnotationTable(self.workspace, self)
        self.table_model.content_changed.connect(lambda: self.annotation_changed(reset_table=False))
        self.table = QTableView()
        self.table.setAccessibleName("Annotation intervals and points; labels are editable")
        self.table.setModel(self.table_model)
        self.table.selectionModel().currentRowChanged.connect(
            lambda current, previous: self.select_annotation(current.row())
        )
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.clicked.connect(lambda index: self.select_annotation(index.row()))
        self.tiers = TierCanvas(self.workspace)
        self.tiers.getAxis("left").setWidth(110)
        self.tiers.setXLink(self.plot)
        self.tiers.selected_entity.connect(self.select_graphical_entity)
        self.tiers.edited.connect(self.annotation_changed)
        self.tiers.failed.connect(lambda message: self.statusBar().showMessage(message))
        self.tier_cursor = pg.InfiniteLine(pos=0, movable=False, pen=pg.mkPen("#b65610", width=2))
        self.tier_cursor.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.tiers.addItem(self.tier_cursor)
        self.selection_label = QLabel("Selection: 0–1 s")
        right = QWidget()
        layout = QVBoxLayout(right)
        layout.addWidget(self.details)
        layout.addWidget(self.channel)
        panes = QSplitter(Qt.Orientation.Vertical)
        panes.addWidget(self.plot)
        panes.addWidget(self.tiers)
        panes.addWidget(self.table)
        panes.setSizes([260, 180, 180])
        layout.addWidget(panes)
        layout.addWidget(self.selection_label)
        splitter = QSplitter()
        splitter.addWidget(self.objects)
        splitter.addWidget(right)
        splitter.setSizes([230, 890])
        self.setCentralWidget(splitter)
        self._wave_timer = QTimer(self)
        self._wave_timer.setSingleShot(True)
        self._wave_timer.setInterval(80)
        self._wave_timer.timeout.connect(self.request_waveform)
        self.plot.getViewBox().sigXRangeChanged.connect(lambda *_: self.queue_waveform())
        self._make_actions()
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(40)
        self._play_timer.timeout.connect(self.poll_playback)
        self._play_shortcuts = []
        for target in (self.plot, self.tiers):
            shortcut = QShortcut(QKeySequence("Tab"), target)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda: self.guard(self.toggle_playback))
            self._play_shortcuts.append(shortcut)
        self.statusBar().showMessage("Prototype: waveform and annotation workflow")
        self.refresh_objects()

    def _make_actions(self):
        toolbar = QToolBar("Workspace", self)
        self.addToolBar(toolbar)
        file_menu = self.menuBar().addMenu("&File")
        edit_menu = self.menuBar().addMenu("&Edit")
        annotation_menu = self.menuBar().addMenu("&Annotation")
        audio_menu = self.menuBar().addMenu("&Audio")

        def action(menu, text, callback, shortcut=None, tool=False):
            item = QAction(text, self)
            item.triggered.connect(lambda checked=False: self.guard(callback))
            if shortcut:
                item.setShortcut(shortcut)
            menu.addAction(item)
            if tool:
                toolbar.addAction(item)
            return item

        action(file_menu, "Open…", self.choose_open, QKeySequence.StandardKey.Open, True)
        action(file_menu, "New TextGrid…", self.new_grid, tool=True)
        action(file_menu, "Save TextGrid…", self.save_selected, QKeySequence.StandardKey.Save, True)
        action(file_menu, "Fit recording / annotation", self.fit_view, "Ctrl+0")
        action(file_menu, "Close", self.close, QKeySequence.StandardKey.Close)
        self.play_action = action(audio_menu, "Play selection", self.play_selection, tool=True)
        self.stop_action = action(audio_menu, "Stop playback", self.stop_playback, tool=True)
        action(edit_menu, "Rename object…", self.rename_object)
        action(edit_menu, "Copy object", self.copy_object)
        action(edit_menu, "Remove object", self.remove_object)
        self.undo_action = action(edit_menu, "Undo annotation edit", self.undo, tool=True)
        self.redo_action = action(edit_menu, "Redo annotation edit", self.redo, tool=True)
        # Native text editors retain their own standard text undo shortcuts.
        action(annotation_menu, "Insert boundary / point…", self.insert_mark, tool=True)
        action(annotation_menu, "Move selected interval's left boundary…", self.move_boundary)

    def guard(self, operation):
        try:
            return operation()
        except (InvalidInput, OSError) as error:
            QMessageBox.warning(self, "Operation could not finish", str(error))
            return None

    def selected_id(self):
        item = self.objects.currentItem()
        if item is None:
            raise InvalidInput("Select a workspace object first.")
        return item.data(Qt.ItemDataRole.UserRole)

    def refresh_objects(self, select_id=None):
        current = self.objects.currentItem()
        selected = select_id or (current.data(Qt.ItemDataRole.UserRole) if current else None)
        self.objects.blockSignals(True)
        self.objects.clear()
        for object_id, value in self.workspace.objects.items():
            if isinstance(value, AnnotationSession):
                name = value.document.name + (" *" if value.dirty else "")
                kind = "TextGrid"
            else:
                name, kind = value.name, "Sound"
            item = QListWidgetItem(f"{kind}  {name}")
            item.setData(Qt.ItemDataRole.UserRole, object_id)
            self.objects.addItem(item)
            if object_id == selected:
                self.objects.setCurrentItem(item)
        self.objects.blockSignals(False)
        self.undo_action.setEnabled(bool(self.session and self.session.can_undo))
        self.redo_action.setEnabled(bool(self.session and self.session.can_redo))
        self.play_action.setEnabled(self.source is not None)
        self.stop_action.setEnabled(self._playback_id is not None)

    def choose_open(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open audio or annotations",
            "",
            "Audio / annotations (*.wav *.WAV *.TextGrid *.textgrid)",
        )
        for path in paths:
            self.guard(lambda p=path: self.open_path(Path(p)))

    def open_path(self, path: Path):
        object_id = self.workspace.open(path)
        self.refresh_objects(object_id)
        self.show_selected()
        return object_id

    def show_selected(self):
        if self.objects.currentItem() is None:
            return
        value = self.workspace.get(self.selected_id())
        if isinstance(value, AudioFile):
            if self.source is None or self.source.id != value.id:
                self.stop_playback()
            self.source = value
            self.channel.blockSignals(True)
            self.channel.clear()
            self.channel.addItems([f"Channel {i + 1}" for i in range(value.channels)])
            self.channel.blockSignals(False)
        else:
            self.session = value
            self.table_model.set_session(value)
            self.tiers.set_session(value)
        self.update_details()
        self.fit_view()
        self.refresh_objects()

    def update_details(self):
        parts = []
        if self.source:
            axis = self.source.axis
            parts.append(
                f"{self.source.name}: {axis.nx:,} frames · {axis.sample_rate:g} Hz · "
                f"{self.source.channels} channel(s) · {self.source.subtype}"
            )
        if self.session:
            doc = self.session.document
            parts.append(f"{doc.name}: {len(doc.tiers)} tiers · {doc.xmin:g}–{doc.xmax:g} s")
            parts.extend(doc.domain_warnings())
        self.details.setText("\n".join(parts) or "Open a recording or annotation.")

    def fit_view(self):
        bounds = []
        if self.source:
            bounds.append((self.source.axis.xmin, self.source.axis.xmax))
        if self.session:
            bounds.append((self.session.document.xmin, self.session.document.xmax))
        if bounds:
            start, end = min(b[0] for b in bounds), max(b[1] for b in bounds)
            self.plot.setXRange(start, end, padding=0.01)
            self.region.setBounds((start, end))
            self.region.setRegion((start, end))
        self.queue_waveform()

    def queue_waveform(self):
        # Invalidate immediately, not only when the debounced job eventually starts.
        self._cancel.set()
        self._token += 1
        self._wave_timer.start()

    def request_waveform(self):
        self._cancel.set()
        self._cancel = Event()
        self._token += 1
        if self.source is None:
            self.curve.setData([], [])
            return
        start, end = self.plot.getViewBox().viewRange()[0]
        job = WaveJob(
            self._token,
            self.source,
            start,
            end,
            max(0, self.channel.currentIndex()),
            max(1, min(16_384, self.plot.width())),
            self._cancel,
        )
        job.signals.ready.connect(self.wave_ready)
        job.signals.failed.connect(self.wave_failed)
        QThreadPool.globalInstance().start(job)

    def wave_ready(self, token, wave):
        if token != self._token:
            return
        self.curve.setData(
            np.repeat(wave.times, 2),
            np.column_stack((wave.minimum, wave.maximum)).ravel(),
            connect="pairs",
        )

    def wave_failed(self, token, message):
        if token == self._token:
            self.statusBar().showMessage(f"Waveform unavailable: {message}")
            self.curve.setData([], [])

    def selection_changed(self):
        start, end = self.region.getRegion()
        self.selection_label.setText(f"Selection: {start:.9g}–{end:.9g} s ({end - start:.9g} s)")

    def select_annotation(self, row):
        if not 0 <= row < len(self.table_model.rows):
            return
        tier, item = self.table_model.rows[row]
        self.tiers.select_entity(tier.id, item.id if item else None)
        if item is None:
            return
        if isinstance(tier, IntervalTier):
            self.region.setRegion((item.xmin, item.xmax))
        else:
            self.region.setRegion((item.time, item.time))

    def select_graphical_entity(self, tier_id, entity_id):
        for row, (tier, entity) in enumerate(self.table_model.rows):
            if tier.id == tier_id and (entity.id if entity else None) == entity_id:
                index = self.table_model.index(row, 4)
                self.table.setCurrentIndex(index)
                self.table.scrollTo(index)
                self.select_annotation(row)
                return

    def annotation_changed(self, *, reset_table=True):
        if reset_table:
            self.table_model.set_session(self.session)
        self.tiers.set_session(self.session)
        self.refresh_objects()

    def play_selection(self):
        if self.source is None:
            raise InvalidInput("Open and select an audio recording before playback.")
        self.stop_playback()
        start, end = self.region.getRegion()
        self._playback_id = self.transport.play(self.source, start, end)
        self._play_timer.start()
        self.statusBar().showMessage("Starting audio output…")
        self.refresh_objects()

    def toggle_playback(self):
        if self._playback_id:
            self.stop_playback()
        else:
            self.play_selection()

    def show_playback_position(self, position):
        if (
            position.session_id != self._playback_id
            or self.source is None
            or position.source_id != self.source.id
            or position.source_revision != self.source.revision
        ):
            return
        self.cursor.setValue(position.source_time)
        self.tier_cursor.setValue(position.source_time)
        self.statusBar().showMessage(
            position.error
            or f"Playback {position.state}: {position.source_time:.6f} s "
            f"({position.timing_quality})"
        )

    def poll_playback(self):
        if self._playback_id is None:
            return
        try:
            position = self.transport.position(self._playback_id)
            self.show_playback_position(position)
            if position.state in ("finished", "stopped", "error"):
                self._play_timer.stop()
                self._playback_id = None
                self.refresh_objects()
        except InvalidInput as error:
            self._play_timer.stop()
            self._playback_id = None
            self.statusBar().showMessage(str(error))
            self.refresh_objects()

    def stop_playback(self):
        if self._playback_id is not None:
            position = self.transport.stop(self._playback_id)
            self.show_playback_position(position)
            self._playback_id = None
        if hasattr(self, "_play_timer"):
            self._play_timer.stop()
        if hasattr(self, "stop_action"):
            self.stop_action.setEnabled(False)

    def new_grid(self):
        if self.source:
            xmin, xmax = self.source.axis.xmin, self.source.axis.xmax
            default = self.source.name
        else:
            xmin, xmax, default = 0.0, 1.0, "annotations"
        name, ok = QInputDialog.getText(self, "New TextGrid", "Object name:", text=default)
        if not ok:
            return
        names, ok = QInputDialog.getText(
            self, "Interval tiers", "Comma-separated interval tier names:", text="words, phones"
        )
        if not ok:
            return
        points, ok = QInputDialog.getText(self, "Point tiers", "Comma-separated point tier names:")
        if not ok:
            return
        tiers = [(t.strip(), "interval") for t in names.split(",") if t.strip()]
        tiers += [(t.strip(), "point") for t in points.split(",") if t.strip()]
        if not tiers:
            raise InvalidInput("Enter at least one tier name.")
        grid = TextGrid.create(name, xmin, xmax, tuple(tiers))
        self.workspace.add_annotation(grid)
        # New unsaved documents must prompt on close even before their first edit.
        session = self.workspace.get(grid.id)
        session.mark_unsaved()
        self.refresh_objects(grid.id)
        self.show_selected()

    def save_session(self, session):
        object_id = session.document.id
        proposed = self.workspace.paths.get(object_id, Path(f"{session.document.name}.TextGrid"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save TextGrid", str(proposed), "TextGrid (*.TextGrid)"
        )
        if not path:
            return False
        # Native dialog asks about existing paths. Workspace write remains no-clobber
        # for new paths and allows replacement only after that explicit dialog choice.
        destination = Path(path)
        self.workspace.save(object_id, destination, overwrite=destination.exists())
        self.refresh_objects()
        return True

    def save_selected(self):
        value = self.workspace.get(self.selected_id())
        if not isinstance(value, AnnotationSession):
            raise InvalidInput("Select a TextGrid object to save.")
        return self.save_session(value)

    def selected_entity(self):
        if self.session is None:
            raise InvalidInput("Open or create a TextGrid first.")
        row = self.table.currentIndex().row()
        if not 0 <= row < len(self.table_model.rows):
            raise InvalidInput("Choose a row on the target tier first.")
        return self.table_model.rows[row]

    def edit(self, command_id, **parameters):
        if self.session is None:
            raise InvalidInput("Choose a TextGrid first.")
        self.workspace.execute(
            self.session.document.id,
            command_id,
            expected_revision=self.session.document.revision,
            **parameters,
        )
        self.table_model.set_session(self.session)
        self.tiers.set_session(self.session)
        self.refresh_objects()

    def insert_mark(self):
        tier, entity = self.selected_entity()
        start, end = self.region.getRegion()
        default = (start + end) / 2
        time, ok = QInputDialog.getDouble(
            self,
            "Insert boundary / point",
            "Time (seconds):",
            default,
            tier.xmin,
            tier.xmax,
            9,
        )
        if not ok:
            return
        if isinstance(tier, IntervalTier):
            interval = next((e for e in tier.intervals if e.xmin < time < e.xmax), None)
            offset = 0
            if interval and interval.label:
                offset, ok = QInputDialog.getInt(
                    self,
                    "Split label",
                    "Characters to keep in the left interval:",
                    0,
                    0,
                    len(interval.label),
                )
                if not ok:
                    return
            self.edit("annotation.split_interval", tier_id=tier.id, time=time, text_offset=offset)
        else:
            label, ok = QInputDialog.getText(self, "Insert point", "Label:")
            if ok:
                self.edit("annotation.insert_point", tier_id=tier.id, time=time, label=label)

    def move_boundary(self):
        tier, entity = self.selected_entity()
        if not isinstance(tier, IntervalTier) or entity is None:
            raise InvalidInput("Select the interval to the right of an internal boundary.")
        time, ok = QInputDialog.getDouble(
            self,
            "Move boundary",
            "New time (seconds):",
            entity.xmin,
            tier.intervals[0].xmin,
            tier.intervals[-1].xmax,
            9,
        )
        if ok:
            self.edit(
                "annotation.move_boundary",
                tier_id=tier.id,
                right_interval_id=entity.id,
                time=time,
            )

    def undo(self):
        if self.session:
            self.session.undo()
            self.table_model.set_session(self.session)
            self.tiers.set_session(self.session)
            self.refresh_objects()

    def redo(self):
        if self.session:
            self.session.redo()
            self.table_model.set_session(self.session)
            self.tiers.set_session(self.session)
            self.refresh_objects()

    def rename_object(self):
        object_id = self.selected_id()
        value = self.workspace.get(object_id)
        name = value.document.name if isinstance(value, AnnotationSession) else value.name
        updated, ok = QInputDialog.getText(self, "Rename object", "Name:", text=name)
        if ok:
            self.workspace.rename(object_id, updated)
            if self.source and self.source.id == object_id:
                self.source = self.workspace.get(object_id)
            self.refresh_objects(object_id)
            self.update_details()

    def copy_object(self):
        object_id = self.workspace.copy(self.selected_id())
        self.refresh_objects(object_id)
        self.show_selected()

    def confirm_saved(self, value):
        if not isinstance(value, AnnotationSession) or not value.dirty:
            return True
        choice = QMessageBox.question(
            self,
            "Unsaved annotation",
            f"Save changes to {value.document.name}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if choice == QMessageBox.StandardButton.Save:
            return self.save_session(value)
        return choice == QMessageBox.StandardButton.Discard

    def remove_object(self):
        object_id = self.selected_id()
        value = self.workspace.get(object_id)
        if not self.confirm_saved(value):
            return
        self.workspace.remove(object_id)
        if self.source and self.source.id == object_id:
            self.stop_playback()
            self.source = None
            self.channel.clear()
        if self.session and self.session.document.id == object_id:
            self.session = None
            self.table_model.set_session(None)
            self.tiers.set_session(None)
        self.refresh_objects()
        self.update_details()
        self.queue_waveform()

    def closeEvent(self, event):
        try:
            for value in list(self.workspace.objects.values()):
                if not self.confirm_saved(value):
                    event.ignore()
                    return
        except (InvalidInput, OSError) as error:
            QMessageBox.warning(self, "Could not save", str(error))
            event.ignore()
            return
        self._wave_timer.stop()
        self.stop_playback()
        self.transport.close()
        self._cancel.set()
        self._token += 1
        event.accept()


def launch(files: list[Path] | None = None) -> int:
    app = QApplication.instance() or QApplication([sys.argv[0]])
    window = MainWindow()
    window.show()
    for path in files or []:
        window.guard(lambda p=path: window.open_path(p))
    return app.exec()
