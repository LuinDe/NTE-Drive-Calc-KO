# 只读校验历史配置并从当前完整资料准备原样恢复的角色编辑种子。
from __future__ import annotations

import json
from dataclasses import dataclass, replace

from src.domain.cultivation_history import HistoryRecord, freeze_configuration
from src.services.cultivation_planner_models import CultivationForkSeed, CultivationSeed
from src.services.cultivation_planner_service import CultivationPlannerService


@dataclass(frozen=True, slots=True)
class PreparedHistoryRestore:
    mode: str
    configuration_json: str
    seeds: tuple[CultivationSeed, ...]


def prepare_history_restore(
    service: CultivationPlannerService, record: HistoryRecord,
) -> PreparedHistoryRestore:
    configuration = json.loads(record.payload.configuration_json)
    service.validate_history_configuration(configuration)
    seeds = []
    for target in configuration["targets"]:
        catalog_seed = service.load_seed(target["character_id"])
        levels = {skill["skill_id"]: skill["current_level"] for skill in target["skills"]}
        fork = target["fork"]
        fork_seed = None
        if fork is not None:
            catalog_fork = service.load_fork_seed(fork["fork_id"])
            fork_seed = CultivationForkSeed(
                fork["fork_id"], catalog_fork.fork_name, fork["current_level"], fork["current_stage"],
            )
        seeds.append(replace(
            catalog_seed, current_level=target["current_level"], current_breakthrough_stage=target["current_stage"],
            skills=tuple(replace(skill, current_level=levels[skill.skill_id]) for skill in catalog_seed.skills),
            fork=fork_seed,
        ))
    return PreparedHistoryRestore(record.payload.mode, freeze_configuration(configuration), tuple(seeds))
