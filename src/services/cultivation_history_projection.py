# 从同次计算的冻结输入和输出生成不含路径或重复明细的养成历史快照。
from __future__ import annotations

import json
from collections.abc import Mapping

from src.domain.cultivation_history import HistoryPayload, PAYLOAD_VERSION
from src.domain.progression_material_conversion import allocate_owned
from src.domain.progression_stamina import ProgressionStaminaResult
from src.services.cultivation_batch_planner_service import CultivationBatchPlan
from src.services.cultivation_planner_models import CultivationPlan, CultivationStaminaPlan

# Bump when the planning/aggregation semantics change, independently of the JSON format.
CULTIVATION_ALGORITHM_VERSION = "cultivation-v1"


def _stamina(result: ProgressionStaminaResult | None) -> dict[str, object]:
    if result is None:
        return {"status": "unavailable", "known_stamina": 0, "total_stamina": None, "runs": []}
    return {
        "status": str(result.status), "known_stamina": result.known_stamina,
        "total_stamina": result.total_stamina,
        "runs": [{"stage_id": run.stage_id, "label": run.label, "runs": run.runs,
                  "stamina_per_run": run.stamina_cost_per_run,
                  "total_stamina": run.total_stamina, "source": run.source} for run in result.runs],
    }


def _base(dataset: Mapping[str, object]) -> dict[str, object]:
    return {"version": PAYLOAD_VERSION, "dataset": dict(dataset),
            "algorithm_version": CULTIVATION_ALGORITHM_VERSION}


def single_history_payload(
    configuration_json: str, plan: CultivationPlan, stamina: CultivationStaminaPlan | None,
    dataset: Mapping[str, object],
    *, stamina_item_ids: frozenset[str] | None = None,
) -> HistoryPayload:
    configuration = json.loads(configuration_json)
    target = configuration["targets"][0]
    owned = {item["item_id"]: item["quantity"] for item in configuration["owned_materials"]}
    allocated = allocate_owned({item.item_id: item.quantity for item in plan.totals}, owned)
    eligible = stamina.stamina_item_ids if stamina is not None else (stamina_item_ids or frozenset())
    total = stamina.total if stamina is not None else None
    gaps = [{"line_id": target["line_id"], "reason_code": gap.reason_code, "item_id": gap.item_id}
            for gap in plan.gaps]
    if total is not None:
        gaps.extend({"line_id": None, "reason_code": reason, "item_id": None} for reason in total.gaps)
    else:
        gaps.append({"line_id": None, "reason_code": "stamina_unavailable", "item_id": None})
    snapshot = _base(dataset)
    snapshot.update({
        "materials": [{"item_id": item.item_id, "name": item.name, "required": item.quantity,
                       "allocated_equivalent": allocated.get(item.item_id, 0),
                       "remaining": item.quantity - allocated.get(item.item_id, 0),
                       "stamina_eligible": item.item_id in eligible} for item in plan.totals],
        "target_summaries": [{"line_id": target["line_id"], "character_id": target["character_id"],
                              "status": str(plan.status),
                              "known_stamina": total.known_stamina if total is not None else 0,
                              "total_stamina": total.total_stamina if total is not None else None}],
        "stamina": _stamina(total), "gaps": gaps,
    })
    return HistoryPayload.create(configuration, snapshot)


def batch_history_payload(
    configuration_json: str, plan: CultivationBatchPlan, dataset: Mapping[str, object],
) -> HistoryPayload:
    configuration = json.loads(configuration_json)
    remaining = {item.item_id: item.quantity for item in plan.remaining_totals}
    snapshot = _base(dataset)
    snapshot.update({
        "materials": [{"item_id": item.item_id, "name": item.name, "required": item.quantity,
                       "allocated_equivalent": item.quantity - remaining.get(item.item_id, 0),
                       "remaining": remaining.get(item.item_id, 0),
                       "stamina_eligible": item.item_id in plan.stamina_item_ids}
                      for item in plan.merged_totals],
        "target_summaries": [{"line_id": target.line_id, "character_id": target.character_id,
                              "status": str(target.plan.status),
                              "known_stamina": target.stamina.total.known_stamina,
                              "total_stamina": target.stamina.total.total_stamina}
                             for target in plan.target_plans],
        "stamina": _stamina(plan.combined_stamina),
        "gaps": [{"line_id": gap.line_id, "reason_code": gap.reason_code, "item_id": gap.item_id}
                 for gap in plan.gaps],
    })
    return HistoryPayload.create(configuration, snapshot)
