"""One batched graphics item for ordered tiers, with transactional drag previews."""

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsItem

from ..errors import InvalidInput
from ..model.textgrid import IntervalTier


@dataclass(frozen=True)
class Hit:
    tier_id: str
    entity_id: str | None
    kind: str
    time: float


class TierItem(pg.GraphicsObject):
    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def boundingRect(self):
        return self.canvas.bounds

    def paint(self, painter, option, widget):
        canvas = self.canvas
        if canvas.session is None:
            return
        left, right = canvas.viewRange()[0]
        top, bottom = canvas.viewRange()[1]
        transform = painter.worldTransform()
        for row in range(
            max(0, int(top)), min(len(canvas.session.document.tiers), math.ceil(bottom))
        ):
            tier = canvas.session.document.tiers[row]
            interval = isinstance(tier, IntervalTier)
            entities, starts = canvas.index[tier.id]
            painter.setPen(pg.mkPen("#b8c4cd", width=1))
            painter.setBrush(QColor("#f5f8fa" if row % 2 == 0 else "#ffffff"))
            painter.drawRect(QRectF(left, row, right - left, 1))
            begin = max(0, bisect_right(starts, left) - 1)
            finish = bisect_right(starts, right)
            budget = max(1, canvas.width() * 2)
            stride = max(1, math.ceil((finish - begin) / budget))
            for entity in entities[begin:finish:stride]:
                selected = canvas.selected == (tier.id, entity.id)
                painter.setPen(pg.mkPen("#194d70" if selected else "#71899b", width=1))
                if interval:
                    x0, x1 = max(left, entity.xmin), min(right, entity.xmax)
                    if x1 < x0:
                        continue
                    painter.setBrush(QColor("#dcecf7" if selected else "#ffffff"))
                    rectangle = QRectF(x0, row + 0.04, x1 - x0, 0.92)
                    painter.drawRect(rectangle)
                else:
                    if not left <= entity.time <= right:
                        continue
                    painter.drawLine(
                        pg.Point(entity.time, row + 0.05), pg.Point(entity.time, row + 0.95)
                    )
                    # Point labels get a bounded screen-space width, not a time-width guess.
                    pixel = transform.map(pg.Point(entity.time, row + 0.5))
                    rectangle = None
                if entity.label and stride == 1:
                    device_rect = (
                        transform.mapRect(rectangle)
                        if interval
                        else QRectF(pixel.x() + 5, pixel.y() - 12, 150, 24)
                    )
                    if device_rect.width() > 25:
                        painter.save()
                        painter.resetTransform()
                        painter.setClipRect(device_rect)
                        painter.setPen(QColor("#142f40"))
                        painter.drawText(
                            device_rect.adjusted(4, 0, -4, 0),
                            Qt.AlignmentFlag.AlignCenter,
                            entity.label.replace("\n", " ↵ ").replace("\r", ""),
                        )
                        painter.restore()
            if stride > 1:
                rect = transform.mapRect(QRectF(left, row, right - left, 1))
                painter.save()
                painter.resetTransform()
                painter.setPen(QColor("#536b79"))
                painter.drawText(
                    rect, Qt.AlignmentFlag.AlignCenter, "Dense tier — zoom in for labels"
                )
                painter.restore()
        if canvas.preview is not None:
            painter.setPen(QPen(QColor("#bd354b"), 0))
            painter.drawLine(
                pg.Point(canvas.preview, 0),
                pg.Point(canvas.preview, len(canvas.session.document.tiers)),
            )

    def mouseClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.canvas.choose(event.pos().x(), event.pos().y())
            self.canvas.setFocus()
            event.accept()

    def mouseDragEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        canvas = self.canvas
        if event.isStart():
            if not canvas.begin_drag(event.buttonDownPos().x(), event.buttonDownPos().y()):
                event.ignore()
                return
            canvas.setFocus()
        if canvas.drag is None:
            event.accept()
            return
        canvas.preview_drag(event.pos().x())
        if event.isFinish():
            canvas.commit_drag()
        event.accept()


class TierCanvas(pg.PlotWidget):
    selected_entity = Signal(str, object)
    edited = Signal()
    failed = Signal(str)

    def __init__(self, workspace, parent=None):
        super().__init__(parent, background="w")
        self.workspace = workspace
        self.session = None
        self.index = {}
        self.selected = None
        self.drag = None
        self.preview = None
        self.bounds = QRectF(0, 0, 1, 1)
        self._tier_signature = None
        self.setAccessibleName(
            "Graphical annotation tiers; use annotation table for keyboard editing"
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(140)
        self.setLabel("bottom", "Time", units="s")
        self.getViewBox().invertY(True)
        self.getViewBox().setMouseEnabled(x=True, y=True)
        self.item = TierItem(self)
        self.addItem(self.item)
        self.getViewBox().sigRangeChanged.connect(lambda *_: self.item.update())

    def set_session(self, session):
        self.cancel_drag()
        self.item.prepareGeometryChange()
        self.session = session
        self.index = {}
        if session:
            tiers = session.document.tiers
            signature = (session.document.id, tuple(t.id for t in tiers))
            lower, upper = session.document.xmin, session.document.xmax
            for tier in tiers:
                entities = tier.intervals if isinstance(tier, IntervalTier) else tier.points
                starts = tuple(
                    e.xmin if isinstance(tier, IntervalTier) else e.time for e in entities
                )
                self.index[tier.id] = (entities, starts)
                lower, upper = min(lower, tier.xmin), max(upper, tier.xmax)
                if entities:
                    lower = min(lower, starts[0])
                    upper = max(
                        upper, entities[-1].xmax if isinstance(tier, IntervalTier) else starts[-1]
                    )
            self.bounds = QRectF(lower, 0, upper - lower, max(1, len(tiers)))
            self.getAxis("left").setTicks([[(i + 0.5, t.name) for i, t in enumerate(tiers)]])
            if signature != self._tier_signature:
                self.setYRange(0, min(6, max(1, len(tiers))), padding=0)
            self._tier_signature = signature
            self.setLimits(yMin=0, yMax=max(1, len(tiers)), minYRange=1)
            if self.selected and not any(
                t.id == self.selected[0]
                and any(e.id == self.selected[1] for e in self.index[t.id][0])
                for t in tiers
            ):
                self.selected = None
        else:
            self._tier_signature = None
            self.bounds = QRectF(0, 0, 1, 1)
            self.selected = None
            self.getAxis("left").setTicks([])
        self.item.update()

    def hit(self, x, y, *, tolerance=None):
        if self.session is None or not math.isfinite(x) or not math.isfinite(y):
            return None
        row = math.floor(y)
        if not 0 <= row < len(self.session.document.tiers):
            return None
        tier = self.session.document.tiers[row]
        entities, starts = self.index[tier.id]
        tolerance = tolerance if tolerance is not None else 6 * self.getViewBox().viewPixelSize()[0]
        insertion = bisect_left(starts, x)
        nearest = min(
            range(max(0, insertion - 1), min(len(starts), insertion + 1)),
            key=lambda i: abs(starts[i] - x),
            default=None,
        )
        interval = isinstance(tier, IntervalTier)
        if (
            nearest is not None
            and abs(starts[nearest] - x) <= tolerance
            and (not interval or nearest > 0)
        ):
            return Hit(
                tier.id, entities[nearest].id, "boundary" if interval else "point", starts[nearest]
            )
        if interval:
            i = bisect_right(starts, x) - 1
            if 0 <= i < len(entities) and entities[i].xmin <= x <= entities[i].xmax:
                return Hit(tier.id, entities[i].id, "interval", x)
        return Hit(tier.id, None, "tier", x)

    def select_entity(self, tier_id, entity_id):
        self.selected = (tier_id, entity_id)
        self.item.update()

    def choose(self, x, y):
        hit = self.hit(x, y)
        if hit:
            self.select_entity(hit.tier_id, hit.entity_id)
            self.selected_entity.emit(hit.tier_id, hit.entity_id)

    def begin_drag(self, x, y):
        hit = self.hit(x, y)
        if hit is None or hit.kind not in ("boundary", "point"):
            return False
        self.drag = (hit, self.session.document.id, self.session.document.revision)
        self.preview = hit.time
        self.choose(x, y)
        return True

    def preview_drag(self, time):
        if self.drag and math.isfinite(time):
            self.preview = time
            self.item.update()

    def commit_drag(self):
        if self.drag is None:
            return
        hit, document_id, revision = self.drag
        time = self.preview
        self.cancel_drag()
        if time == hit.time:
            return
        kind = "annotation.move_boundary" if hit.kind == "boundary" else "annotation.move_point"
        parameter = "right_interval_id" if hit.kind == "boundary" else "point_id"
        try:
            self.workspace.execute(
                document_id,
                kind,
                expected_revision=revision,
                tier_id=hit.tier_id,
                time=time,
                **{parameter: hit.entity_id},
            )
        except InvalidInput as error:
            self.failed.emit(str(error))
            return
        self.set_session(self.session)
        self.edited.emit()
        self.selected_entity.emit(hit.tier_id, hit.entity_id)

    def cancel_drag(self):
        self.drag = None
        self.preview = None
        if hasattr(self, "item"):
            self.item.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_drag()
            event.accept()
        else:
            super().keyPressEvent(event)
