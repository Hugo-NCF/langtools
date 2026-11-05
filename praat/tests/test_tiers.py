import pyqtgraph as pg
import pytest
from PySide6.QtCore import Qt

from langtools_praat.annotation.commands import MovePoint, SetLabel
from langtools_praat.annotation.session import AnnotationSession
from langtools_praat.application.workspace import Workspace
from langtools_praat.errors import InvalidInput
from langtools_praat.gui.app import MainWindow
from langtools_praat.gui.tiers import TierCanvas
from langtools_praat.io.textgrid import write
from langtools_praat.model.textgrid import Interval, IntervalTier, Point, PointTier, TextGrid


def document():
    return TextGrid(
        "labels",
        3,
        5,
        (
            IntervalTier("phones", 3, 5, (Interval(3, 4, "ɲ"), Interval(4, 5, "e\u0301"))),
            PointTier("events", 3, 5, (Point(3.5, "one"), Point(4.5, "two"))),
            PointTier("empty", 3, 5),
        ),
    )


def canvas(qtbot):
    workspace = Workspace()
    object_id = workspace.add_annotation(document())
    session = workspace.get(object_id)
    session.mark_saved()
    view = TierCanvas(workspace)
    qtbot.addWidget(view)
    view.resize(800, 300)
    view.set_session(session)
    view.setXRange(3, 5, padding=0)
    view.show()
    return view, session


def test_hit_selection_empty_tier_and_real_mouse_click(qtbot):
    view, session = canvas(qtbot)
    assert view.hit(4, 0.5).kind == "boundary"
    assert view.hit(3.2, 0.5).kind == "interval"
    assert view.hit(3.5, 1.5).kind == "point"
    assert view.hit(3.2, 2.5).kind == "tier"
    assert view.hit(3, -1) is None
    point = view.mapFromScene(view.getViewBox().mapViewToScene(pg.Point(3.2, 0.5)))
    with qtbot.waitSignal(view.selected_entity):
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert view.selected == (
        session.document.tiers[0].id,
        session.document.tiers[0].intervals[0].id,
    )


def test_boundary_preview_cancel_commit_and_single_undo(qtbot):
    view, session = canvas(qtbot)
    original = session.document
    assert view.begin_drag(4, 0.5)
    view.preview_drag(4.2)
    view.preview_drag(4.3)
    assert session.document is original
    qtbot.keyClick(view, Qt.Key.Key_Escape)
    view.commit_drag()
    assert session.document is original
    assert not session.can_undo
    assert view.begin_drag(4, 0.5)
    view.preview_drag(4.25)
    view.commit_drag()
    left, right = session.document.tiers[0].intervals
    assert left.xmax == right.xmin == 4.25
    assert session.document.revision == 1
    session.undo()
    assert session.document.tiers == original.tiers
    assert not session.can_undo


def test_crossing_and_stale_drags_publish_no_partial_change(qtbot):
    view, session = canvas(qtbot)
    original = session.document
    assert view.begin_drag(4, 0.5)
    view.preview_drag(5.5)
    with qtbot.waitSignal(view.failed):
        view.commit_drag()
    assert session.document is original
    assert view.begin_drag(4, 0.5)
    view.preview_drag(4.25)
    tier = session.document.tiers[0]
    session.apply(SetLabel(tier.id, tier.intervals[0].id, "new"), expected_revision=0)
    before = session.document
    with qtbot.waitSignal(view.failed):
        view.commit_drag()
    assert session.document is before


def test_point_move_reorders_with_identity_and_duplicate_is_rejected(qtbot):
    view, session = canvas(qtbot)
    tier = session.document.tiers[1]
    point_id = tier.points[0].id
    assert view.begin_drag(3.5, 1.5)
    view.preview_drag(4.75)
    view.commit_drag()
    points = session.document.tiers[1].points
    assert [p.time for p in points] == [4.5, 4.75]
    assert points[1].id == point_id
    assert view.selected == (tier.id, point_id)
    before = session.document
    with pytest.raises(InvalidInput):
        session.apply(MovePoint(tier.id, point_id, 4.5), expected_revision=before.revision)
    assert session.document is before
    session.undo()
    assert session.document.tiers[1] == tier


def test_dense_tier_uses_single_item_and_exact_bisect_hit(qtbot):
    workspace = Workspace()
    intervals = tuple(Interval(i, i + 1) for i in range(100000))
    grid = TextGrid("dense", 0, 100000, (IntervalTier("dense", 0, 100000, intervals),))
    session = AnnotationSession(grid)
    view = TierCanvas(workspace)
    qtbot.addWidget(view)
    view.set_session(session)
    view.resize(600, 200)
    view.setXRange(0, 100000, padding=0)
    view.show()
    assert view.hit(90000.5, 0.5, tolerance=0).entity_id == intervals[90000].id
    view.grab()  # Exercise the dense rendering path without an item per interval.
    assert len([item for item in view.items() if type(item).__name__ == "TierItem"]) == 1


def test_canvas_and_native_table_stay_synchronized(qtbot, tmp_path):
    path = tmp_path / "labels.TextGrid"
    write(path, document())
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(path)
    window.tiers.choose(4.7, 0.5)
    assert window.table.currentIndex().row() == 1
    assert window.region.getRegion() == (4, 5)
    window.select_annotation(2)
    assert window.tiers.selected[1] == window.session.document.tiers[1].points[0].id
    window.table_model.setData(window.table_model.index(0, 4), "edited")
    assert window.tiers.index[window.session.document.tiers[0].id][0][0].label == "edited"
    window.session.mark_saved()
    window.close()


def test_actual_mouse_drag_commits_once(qtbot):
    view, session = canvas(qtbot)
    qtbot.waitExposed(view)
    start = view.mapFromScene(view.getViewBox().mapViewToScene(pg.Point(4, 0.5)))
    end = view.mapFromScene(view.getViewBox().mapViewToScene(pg.Point(4.25, 0.5)))
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(view.viewport(), end, delay=20)
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    qtbot.waitUntil(lambda: session.document.revision == 1)
    left, right = session.document.tiers[0].intervals
    assert left.xmax == right.xmin
    assert left.xmax == pytest.approx(4.25, abs=0.02)
    session.undo()
    assert session.document.tiers[0].intervals[0].xmax == 4
