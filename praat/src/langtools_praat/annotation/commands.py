import math
from dataclasses import dataclass, replace
from numbers import Real
from typing import Protocol

from ..errors import InvalidInput
from ..model.textgrid import Interval, IntervalTier, Point, PointTier, TextGrid, Tier


class Edit(Protocol):
    def apply(self, grid: TextGrid) -> TextGrid: ...


def replace_tier(grid: TextGrid, tier_id: str, updated: Tier) -> TextGrid:
    return replace(grid, tiers=tuple(updated if t.id == tier_id else t for t in grid.tiers))


def find_tier(grid: TextGrid, tier_id: str) -> Tier:
    for tier in grid.tiers:
        if tier.id == tier_id:
            return tier
    raise InvalidInput("Tier no longer exists.")


def _move_time(tier_id: str, entity_id: str, time: float) -> float:
    if not all(isinstance(value, str) and value for value in (tier_id, entity_id)):
        raise InvalidInput("Movement needs nonempty string tier and entity IDs.")
    if isinstance(time, bool) or not isinstance(time, Real):
        raise InvalidInput("Movement time must be a finite real number.")
    try:
        value = float(time)
    except OverflowError as error:
        raise InvalidInput("Movement time must be finite.") from error
    if not math.isfinite(value):
        raise InvalidInput("Movement time must be finite.")
    return value


@dataclass(frozen=True)
class SetLabel:
    tier_id: str
    entity_id: str
    label: str

    def apply(self, grid: TextGrid) -> TextGrid:
        tier = find_tier(grid, self.tier_id)
        entities = tier.intervals if isinstance(tier, IntervalTier) else tier.points
        if not any(e.id == self.entity_id for e in entities):
            raise InvalidInput("Annotation entity no longer exists.")
        updated = tuple(
            replace(e, label=self.label) if e.id == self.entity_id else e for e in entities
        )
        new_tier = (
            replace(tier, intervals=updated)
            if isinstance(tier, IntervalTier)
            else replace(tier, points=updated)
        )
        return replace_tier(grid, tier.id, new_tier)


@dataclass(frozen=True)
class SplitInterval:
    tier_id: str
    time: float
    text_offset: int = 0

    def apply(self, grid: TextGrid) -> TextGrid:
        """Collapsed-caret subset only. Offset counts Python Unicode code points.

        Range/selected-text splitting awaits reference fixtures (§10.4).
        """
        tier = find_tier(grid, self.tier_id)
        if not isinstance(tier, IntervalTier):
            raise InvalidInput("Choose an interval tier to insert a boundary.")
        for i, interval in enumerate(tier.intervals):
            if interval.xmin < self.time < interval.xmax:
                if type(self.text_offset) is not int or not 0 <= self.text_offset <= len(
                    interval.label
                ):
                    raise InvalidInput("Text offset is outside the label.")
                left = replace(interval, xmax=self.time, label=interval.label[: self.text_offset])
                right = Interval(self.time, interval.xmax, interval.label[self.text_offset :])
                intervals = tier.intervals[:i] + (left, right) + tier.intervals[i + 1 :]
                return replace_tier(grid, tier.id, replace(tier, intervals=intervals))
        raise InvalidInput("Insert strictly inside an interval, away from existing boundaries.")


@dataclass(frozen=True)
class InsertPoint:
    tier_id: str
    time: float
    label: str = ""

    def apply(self, grid: TextGrid) -> TextGrid:
        tier = find_tier(grid, self.tier_id)
        if not isinstance(tier, PointTier):
            raise InvalidInput("Choose a point tier to insert a point.")
        if not tier.xmin <= self.time <= tier.xmax:
            raise InvalidInput("Point must be inside the tier's domain.")
        points = tuple(sorted((*tier.points, Point(self.time, self.label)), key=lambda p: p.time))
        return replace_tier(grid, tier.id, replace(tier, points=points))


@dataclass(frozen=True)
class MoveBoundary:
    tier_id: str
    right_interval_id: str
    time: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "time", _move_time(self.tier_id, self.right_interval_id, self.time)
        )

    def apply(self, grid: TextGrid) -> TextGrid:
        tier = find_tier(grid, self.tier_id)
        if not isinstance(tier, IntervalTier):
            raise InvalidInput("Boundary movement needs an interval tier.")
        for i, right in enumerate(tier.intervals):
            if i > 0 and right.id == self.right_interval_id:
                left = tier.intervals[i - 1]
                if not left.xmin < self.time < right.xmax:
                    raise InvalidInput("A boundary cannot cross neighboring boundaries.")
                intervals = (
                    tier.intervals[: i - 1]
                    + (replace(left, xmax=self.time), replace(right, xmin=self.time))
                    + tier.intervals[i + 1 :]
                )
                return replace_tier(grid, tier.id, replace(tier, intervals=intervals))
        raise InvalidInput("Choose an internal boundary.")


@dataclass(frozen=True)
class MovePoint:
    tier_id: str
    point_id: str
    time: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "time", _move_time(self.tier_id, self.point_id, self.time))

    def apply(self, grid: TextGrid) -> TextGrid:
        tier = find_tier(grid, self.tier_id)
        if not isinstance(tier, PointTier):
            raise InvalidInput("Point movement needs a point tier.")
        if not tier.xmin <= self.time <= tier.xmax:
            raise InvalidInput("Point must remain inside its tier domain.")
        if not any(point.id == self.point_id for point in tier.points):
            raise InvalidInput("Point no longer exists.")
        points = tuple(
            sorted(
                (
                    replace(point, time=self.time) if point.id == self.point_id else point
                    for point in tier.points
                ),
                key=lambda point: point.time,
            )
        )
        return replace_tier(grid, tier.id, replace(tier, points=points))
