"""Matrix loader: YAML parsing, defaults, P0 clamp, unknown-tier handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from customerbot.application.priority.matrix import (
    PriorityMatrix,
    load_from_yaml,
    load_or_default,
)
from customerbot.domain.tickets.value_objects import (
    CustomerWeight,
    Priority,
    Severity,
)


def test_default_matrix_covers_every_cell() -> None:
    m = PriorityMatrix()
    for weight in CustomerWeight:
        for severity in Severity:
            assert isinstance(m.lookup(weight, severity), Priority)


def test_default_never_returns_p0() -> None:
    m = PriorityMatrix()
    for weight in CustomerWeight:
        for severity in Severity:
            assert m.lookup(weight, severity) != Priority.P0


def test_unknown_weight_returns_p3_safety() -> None:
    """Defensive: lookups with cells stripped fall back to P3."""
    m = PriorityMatrix(cells={})
    assert m.lookup(CustomerWeight.LOW, Severity.BLOCKING) == Priority.P3


def test_load_from_yaml_round_trips(tmp_path: Path) -> None:
    yaml_path = tmp_path / "matrix.yaml"
    yaml_path.write_text(
        """
low:
  blocking: P2
  degraded: P3
  cosmetic: P4
  unsure: P3
critical:
  blocking: P1
  degraded: P1
"""
    )
    m = load_from_yaml(yaml_path)
    assert m.lookup(CustomerWeight.LOW, Severity.BLOCKING) == Priority.P2
    assert m.lookup(CustomerWeight.LOW, Severity.COSMETIC) == Priority.P4
    assert m.lookup(CustomerWeight.CRITICAL, Severity.BLOCKING) == Priority.P1
    # Missing cell → P3 fallback.
    assert m.lookup(CustomerWeight.MEDIUM, Severity.BLOCKING) == Priority.P3


def test_p0_in_yaml_is_clamped_to_p1(tmp_path: Path) -> None:
    """Spec §5a — P0 must never be assigned by the matrix."""
    yaml_path = tmp_path / "matrix.yaml"
    yaml_path.write_text(
        """
critical:
  blocking: P0
"""
    )
    m = load_from_yaml(yaml_path)
    assert m.lookup(CustomerWeight.CRITICAL, Severity.BLOCKING) == Priority.P1


def test_unknown_severity_is_skipped(tmp_path: Path) -> None:
    yaml_path = tmp_path / "matrix.yaml"
    yaml_path.write_text(
        """
low:
  blocking: P2
  shimmering: P1
"""
    )
    m = load_from_yaml(yaml_path)
    assert m.lookup(CustomerWeight.LOW, Severity.BLOCKING) == Priority.P2
    # The unknown 'shimmering' key is silently dropped.


def test_unknown_priority_value_defaults_to_p3(tmp_path: Path) -> None:
    yaml_path = tmp_path / "matrix.yaml"
    yaml_path.write_text(
        """
low:
  blocking: P9
"""
    )
    m = load_from_yaml(yaml_path)
    assert m.lookup(CustomerWeight.LOW, Severity.BLOCKING) == Priority.P3


def test_load_or_default_with_none_path_returns_defaults() -> None:
    m = load_or_default(None)
    # Matches the hardcoded defaults — sanity-check a couple of cells.
    assert m.lookup(CustomerWeight.LOW, Severity.COSMETIC) == Priority.P4
    assert m.lookup(CustomerWeight.CRITICAL, Severity.BLOCKING) == Priority.P1


def test_load_or_default_with_missing_path_returns_defaults(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    m = load_or_default(str(missing))
    assert m.lookup(CustomerWeight.LOW, Severity.BLOCKING) == Priority.P2


def test_load_or_default_with_invalid_yaml_returns_defaults(tmp_path: Path) -> None:
    bad = tmp_path / "broken.yaml"
    bad.write_text("[: : not valid yaml at all : :")
    m = load_or_default(str(bad))
    # Falls back without raising.
    assert m.lookup(CustomerWeight.LOW, Severity.BLOCKING) == Priority.P2


def test_example_yaml_in_repo_loads_cleanly() -> None:
    """The committed `config/prio_matrix.example.yaml` should parse without warnings."""
    example_path = Path(__file__).resolve().parents[3] / "config" / "prio_matrix.example.yaml"
    if not example_path.exists():
        pytest.skip("example file not present")
    m = load_from_yaml(example_path)
    # All 16 cells should be populated.
    for weight in CustomerWeight:
        for severity in Severity:
            assert isinstance(m.lookup(weight, severity), Priority)
            assert m.lookup(weight, severity) != Priority.P0
