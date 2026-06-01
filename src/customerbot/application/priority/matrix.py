"""Prio matrix loader (decision #4).

Reads a YAML file mapping (customer_weight, severity) → priority. Reloaded
weekly in-process via the `reload_if_stale` helper. P0 is never permitted in
the matrix — only SE/CTO can set P0 manually (flow §7c).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from customerbot.domain.tickets.value_objects import (
    CustomerWeight,
    Priority,
    Severity,
)

logger = logging.getLogger(__name__)

WEEKLY_RELOAD_SECONDS = 7 * 24 * 60 * 60


def _default_cells() -> dict[CustomerWeight, dict[Severity, Priority]]:
    """Hardcoded fallback used when `CUSTOMERBOT_PRIO_MATRIX_PATH` is unset."""
    return {
        CustomerWeight.LOW: {
            Severity.BLOCKING: Priority.P2,
            Severity.DEGRADED: Priority.P3,
            Severity.COSMETIC: Priority.P4,
            Severity.UNSURE: Priority.P3,
        },
        CustomerWeight.MEDIUM: {
            Severity.BLOCKING: Priority.P1,
            Severity.DEGRADED: Priority.P2,
            Severity.COSMETIC: Priority.P3,
            Severity.UNSURE: Priority.P3,
        },
        CustomerWeight.HIGH: {
            Severity.BLOCKING: Priority.P1,
            Severity.DEGRADED: Priority.P2,
            Severity.COSMETIC: Priority.P3,
            Severity.UNSURE: Priority.P2,
        },
        CustomerWeight.CRITICAL: {
            Severity.BLOCKING: Priority.P1,
            Severity.DEGRADED: Priority.P1,
            Severity.COSMETIC: Priority.P3,
            Severity.UNSURE: Priority.P2,
        },
    }


@dataclass
class PriorityMatrix:
    cells: dict[CustomerWeight, dict[Severity, Priority]] = field(default_factory=_default_cells)
    source_path: Path | None = None
    loaded_at: float = field(default_factory=time.monotonic)

    def lookup(self, weight: CustomerWeight, severity: Severity) -> Priority:
        return self.cells.get(weight, {}).get(severity) or Priority.P3

    def is_stale(self, *, ttl_seconds: int = WEEKLY_RELOAD_SECONDS) -> bool:
        return time.monotonic() - self.loaded_at >= ttl_seconds

    def reload_if_stale(self, *, ttl_seconds: int = WEEKLY_RELOAD_SECONDS) -> bool:
        """Re-read the source YAML if older than `ttl_seconds`. Returns True if reloaded."""
        if self.source_path is None or not self.is_stale(ttl_seconds=ttl_seconds):
            return False
        try:
            new = load_from_yaml(self.source_path)
        except FileNotFoundError:
            logger.warning(
                "Prio matrix path %s no longer exists; keeping in-memory cells",
                self.source_path,
            )
            return False
        self.cells = new.cells
        self.loaded_at = time.monotonic()
        return True


def load_from_yaml(path: str | Path) -> PriorityMatrix:
    """Parse a matrix YAML file. Cells with P0 are clamped to P1 with a warning."""
    p = Path(path)
    with p.open() as fh:
        raw = yaml.safe_load(fh) or {}
    cells: dict[CustomerWeight, dict[Severity, Priority]] = {}
    for weight_str, sev_map in raw.items():
        try:
            weight = CustomerWeight(weight_str)
        except ValueError:
            logger.warning("Prio matrix: unknown customer_weight %r — skipping", weight_str)
            continue
        if not isinstance(sev_map, dict):
            continue
        row: dict[Severity, Priority] = {}
        for sev_str, prio_str in sev_map.items():
            try:
                sev = Severity(sev_str)
            except ValueError:
                logger.warning(
                    "Prio matrix [%s]: unknown severity %r — skipping", weight_str, sev_str
                )
                continue
            try:
                prio = Priority(prio_str)
            except ValueError:
                logger.warning(
                    "Prio matrix [%s][%s]: unknown priority %r — defaulting to P3",
                    weight_str,
                    sev_str,
                    prio_str,
                )
                prio = Priority.P3
            if prio == Priority.P0:
                logger.warning(
                    "Prio matrix [%s][%s] uses P0; clamping to P1. "
                    "P0 is only assignable by SE/CTO via the candidate flag.",
                    weight_str,
                    sev_str,
                )
                prio = Priority.P1
            row[sev] = prio
        cells[weight] = row
    return PriorityMatrix(cells=cells, source_path=p, loaded_at=time.monotonic())


def load_or_default(path: str | None) -> PriorityMatrix:
    """Load from `path` if set+readable; otherwise return the hardcoded default matrix."""
    if path is None:
        logger.info("Prio matrix path unset — using built-in defaults")
        return PriorityMatrix()
    try:
        return load_from_yaml(path)
    except FileNotFoundError:
        logger.warning("Prio matrix file %s not found — using built-in defaults", path)
        return PriorityMatrix()
    except yaml.YAMLError as exc:
        logger.error("Prio matrix YAML at %s is invalid: %s — using defaults", path, exc)
        return PriorityMatrix()
