# 独立原生分析程序的显式批量公式后端契约。
from __future__ import annotations
from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable


class BuffProjectionBatchTooLarge(ValueError):
    """The exact projection batch needs partitioning to fit the wire bound."""


class DirectFormulaBackend(Protocol):
    """Calculate ordered frozen inputs; errors propagate without fallback."""

    def calculate_batch(
        self,
        inputs: Sequence[dict[str, Any]],
        *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]: ...


@runtime_checkable
class BuffProjectionBackend(Protocol):
    """Project a batch of frozen hit/interval tables without Python rule evaluation."""

    def project_batch(
        self, payload: dict[str, Any], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]: ...


@runtime_checkable
class BuffProjectionPlanBackend(Protocol):
    """Select time/recipient scopes and project complete frozen candidate sets."""

    @property
    def supports_projection_plan(self) -> bool: ...

    def project_plan_batch(
        self, payload: dict[str, Any], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]: ...


@runtime_checkable
class BattleComputeBackend(Protocol):
    """Run ordered batches of frozen battle computations in the isolated core."""

    @property
    def supports_battle_compute(self) -> bool: ...

    def compute_batch(
        self, operation: str, inputs: Sequence[dict[str, Any]], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]: ...


def available_battle_compute(backend: object) -> BattleComputeBackend | None:
    """Resolve only a capability explicitly supplied by the composition root."""
    if isinstance(backend, BattleComputeBackend) and backend.supports_battle_compute:
        return backend
    return None
