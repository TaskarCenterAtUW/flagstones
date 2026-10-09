"""Parameters for a tiling run, with the collision check on the seed grid."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from typing import Any

__all__ = ["TileParams", "validate_params"]


def _require_finite(name: str, value: float) -> None:
    """Reject NaN or an infinity in a field, reported by name.

    No comparison to NaN succeeds, so a plain range check would never
    fire on it, and an infinity clears every maximum. Checking with
    `math.isfinite` catches both. An integer too large for a float
    makes that check raise `OverflowError`, and this turns the event
    into a named refusal rather than a dump of digits.
    """
    try:
        finite = math.isfinite(value)
    except OverflowError:
        raise ValueError(f"{name} exceeds the float range") from None
    if not finite:
        raise ValueError(f"{name} must be finite, got {value}")


def validate_params(*, consolidation_tolerance_m: float, seed_grid_m: float) -> None:
    """Refuse a seed grid that could seat two clusters in the same cell.

    `ValueError` covers non-finite or non-positive inputs, and any
    `seed_grid_m` reaching `consolidation_tolerance_m * sqrt(2)`, the
    size at which the cell diagonal reaches the merge distance, so two
    points inside the same cell need not merge. osmnx reads the tolerance
    as a node's buffer radius, and merging spans twice that: pass 5
    to merge junctions up to 10 m apart.
    """
    _require_finite("consolidation_tolerance_m", consolidation_tolerance_m)
    _require_finite("seed_grid_m", seed_grid_m)
    if consolidation_tolerance_m <= 0 or seed_grid_m <= 0:
        raise ValueError("consolidation_tolerance_m and seed_grid_m must be positive")
    bound = consolidation_tolerance_m * math.sqrt(2)
    if seed_grid_m >= bound:
        raise ValueError(
            f"seed_grid_m {seed_grid_m} has reached the collision bound "
            f"{bound:.4f} for tolerance {consolidation_tolerance_m} "
            f"(merge distance {2 * consolidation_tolerance_m}): a cell "
            f"could seat two clusters and mint one ID for two tiles. Lower "
            f"seed_grid_m or raise consolidation_tolerance_m"
        )


@dataclass(frozen=True)
class TileParams:
    """Parameters for a tiling run, preset to the published design.

    The tolerance means osmnx's buffer radius for a single node, and
    merging spans twice it. Construction runs `validate_params` on the
    sizing fields and range-checks the rest, with the collision
    bound documented there. A switch must hold `True` or `False`, and
    `degree_filter_min` a whole number. Other values raise instead of
    coercing.
    """

    consolidation_tolerance_m: float = 17.0
    seed_grid_m: float = 24.0
    degree_filter_min: int = 3
    contract_transitions: bool = True
    transition_require_same_road: bool = True
    transition_spread_max_deg: float = 60.0
    transition_opposite_min_deg: float = 120.0
    clip_buffer_m: float = 250.0
    inheritance_radius_m: float = 34.0
    snap_centroid: bool = True

    def __post_init__(self) -> None:
        validate_params(
            consolidation_tolerance_m=self.consolidation_tolerance_m,
            seed_grid_m=self.seed_grid_m,
        )
        # The flip catches NaN, for which `x >= 1` answers False.
        if not (self.degree_filter_min >= 1):
            raise ValueError("degree_filter_min must be 1 or more")
        _require_finite("degree_filter_min", self.degree_filter_min)
        # Two legs span 0 to 180 degrees, so a limit outside that half
        # turn makes its rule fire always or never.
        for name in ("transition_spread_max_deg", "transition_opposite_min_deg"):
            value = getattr(self, name)
            _require_finite(name, value)
            if not 0 <= value <= 180:
                raise ValueError(f"{name} must be 0 to 180, got {value!r}")
        _require_finite("clip_buffer_m", self.clip_buffer_m)
        if self.clip_buffer_m <= 0:
            raise ValueError("clip_buffer_m must exceed 0")
        _require_finite("inheritance_radius_m", self.inheritance_radius_m)
        if self.inheritance_radius_m < 0:
            raise ValueError("inheritance_radius_m must be 0 or greater")
        # TOML keeps "false" as a non-empty string, which reads as true
        # in Python, so an unchecked switch would stay on, reporting nothing.
        for name in (
            "contract_transitions",
            "transition_require_same_road",
            "snap_centroid",
        ):
            value = getattr(self, name)
            if value not in (True, False):
                raise ValueError(f"{name} must be true or false, got {value!r}")
        degree = self.degree_filter_min
        if isinstance(degree, bool) or degree != int(degree):
            raise ValueError(
                f"degree_filter_min must be a whole number, got {degree!r}"
            )

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, Any], *, ignore: Iterable[str] = ()
    ) -> TileParams:
        """Build a record from a `[tiles]`-style configuration table.

        A key that matches neither a field nor `ignore` raises
        `ValueError`. A typo fails there, instead of returning a record
        whose default never changed.
        """
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(values) - known - set(ignore))
        if unknown:
            raise ValueError(f"unrecognized tile parameter(s): {', '.join(unknown)}")
        return cls(**{k: v for k, v in values.items() if k in known})
