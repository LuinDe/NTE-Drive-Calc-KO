# 评估全量扫描后的弃置与锁定目标。
"""Evaluate post-scan discard/lock targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.domain.post_actions import (
    PostActionScoreContext,
    build_state_changes,
    merge_post_action_config,
    post_actions_enabled,
    summarize_post_action_filtering,
)
from src.integrations.bundled_resources import bundled_config_dir
from src.optimizer.scoring import ScoringEngine
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.utils.logger import logger


@dataclass
class PostActionEvaluation:
    config: dict[str, Any] | None = None
    enabled: bool = False
    state_changes: list[dict[str, Any]] = field(default_factory=list)
    filter_summary: dict[str, int] = field(default_factory=dict)


class PostActionEvaluator:
    def __init__(
        self,
        *,
        post_actions_config: dict | None = None,
        selected_roles: list[str] | None = None,
        config_dir=None,
        user_database_path: str | Path | None = None,
    ):
        self.raw_config = post_actions_config
        self.selected_roles = selected_roles
        self.config_dir = Path(config_dir) if config_dir is not None else bundled_config_dir()
        # 仓库管理和鉴定都必须按当前账号的自定义权重评分；未自定义
        # 的角色仍由 ScoringEngine 自动回退到公共 SQLite 推荐权重。
        self.user_database_path = (
            Path(user_database_path) if user_database_path is not None else None
        )

    def evaluate(self, parsed_items: list[tuple[int, object, str]], inventory) -> PostActionEvaluation:
        effective_config = merge_post_action_config(self.raw_config) if self.raw_config else None
        if not effective_config or not post_actions_enabled(effective_config):
            return PostActionEvaluation(config=effective_config, enabled=False)

        scoring = ScoringEngine(
            str(self.config_dir),
            user_database_path=self.user_database_path,
        )
        has_preserve_rules = bool(effective_config.get("preserve_rules"))
        if not scoring.roles_db and not has_preserve_rules:
            return PostActionEvaluation(config=effective_config, enabled=True)

        scoring.evaluate_global_inventory(inventory)
        selected_roles = self.selected_roles
        if not selected_roles:
            selected_roles = _selected_role_names(
                scoring.roles_db,
                effective_config.get("selected_character_ids", []),
            )
        score_context = PostActionScoreContext.from_config_dir(
            str(self.config_dir),
            user_database_path=self.user_database_path,
        )
        if score_context.strict:
            logger.info(
                "[상태 관리] 실제 사용 가능한 캐릭터 점수를 켰습니다:"
                f"드라이브 형태 매핑 {len(score_context.drive_roles_by_shape)}개,"
                f"카트리지 세트 매핑 {len(score_context.tape_roles_by_set)}개"
            )
        else:
            logger.warning("[상태 관리] 청사진 사용 가능 캐릭터 매핑을 만들지 못해 전체 캐릭터 점수로 되돌렸습니다")
        filter_summary = summarize_post_action_filtering(parsed_items, effective_config)
        state_changes = build_state_changes(
            parsed_items,
            effective_config,
            scoring,
            selected_roles,
            score_context,
        )
        logger.info(
            f"[상태 관리] 점수 계산 완료: 분석 성공 {len(parsed_items)}개,"
            f"계산 참여 {filter_summary.get('post_action_candidate_count', 0)}개,"
            f"대상 변경 {len(state_changes)}개"
        )
        logger.info(
            "[상태 관리] 필터 통계:"
            f"품질 범위 {filter_summary.get('post_action_quality_filtered_count', 0)}개,"
            f"처리 종류 {filter_summary.get('post_action_type_filtered_count', 0)}개,"
            f"유형 범위 {filter_summary.get('post_action_type_range_filtered_count', 0)}개,"
            f"보존 규칙 적중 {filter_summary.get('preserve_rule_matched_count', 0)}개"
        )
        for change in state_changes:
            decision = change.get("decision", {}) or {}
            lock_detail = decision.get("lock", {}) or {}
            discard_detail = decision.get("discard", {}) or {}
            preserve_detail = decision.get("preserve", {}) or {}
            chosen = lock_detail if change.get("target_state") == "locked" else discard_detail
            if preserve_detail.get("action"):
                chosen = preserve_detail
            if change.get("target_state") == "normal" and not preserve_detail.get("action"):
                chosen = lock_detail if change.get("current_state") == "locked" else discard_detail
            logger.info(
                f"[상태 관리] 대상 raw_drive_{int(change.get('index', 0)):04d}"
                f"{change.get('current_state')} -> {change.get('target_state')} "
                f"type={change.get('item_type')} quality={change.get('quality')} "
                f"shape={change.get('shape_id')} set={change.get('set_name')} "
                f"best_role={chosen.get('role', '')} score={float(chosen.get('score', 0.0) or 0.0):.2f} "
                f"grade={chosen.get('grade', '')} threshold={chosen.get('threshold', '')} "
                f"eligible_roles={chosen.get('eligible_roles', 0)} mode={chosen.get('match_mode', '')} "
                f"reason={chosen.get('reason', '')} sub_stats={change.get('sub_stats')}"
            )
        return PostActionEvaluation(
            config=effective_config,
            enabled=True,
            state_changes=state_changes,
            filter_summary=filter_summary,
        )


def _selected_role_names(
    roles_db: dict[str, Any],
    selected_character_ids: list[int] | tuple[int, ...],
) -> list[str]:
    """Resolve the management dialog's role IDs to scoring role names.

    Avatar variants share one logical role.  Resolving through the static
    logical key keeps a saved female/male avatar selection valid when the
    account snapshot later exposes the other official variant ID.
    """

    selected_ids: set[int] = set()
    for value in selected_character_ids:
        try:
            character_id = int(value)
        except (TypeError, ValueError):
            continue
        if character_id > 0:
            selected_ids.add(character_id)
    if not selected_ids:
        return []
    with StaticGameDataDao() as static_dao:
        selected_keys = {
            static_dao.get_logical_character_key(character_id)
            or f"custom:{character_id}"
            for character_id in selected_ids
        }
        names: list[str] = []
        matched_keys: set[str] = set()
        for role_name, role in roles_db.items():
            try:
                character_id = int(role.get("character_id"))
            except (AttributeError, TypeError, ValueError):
                continue
            logical_key = (
                static_dao.get_logical_character_key(character_id)
                or f"custom:{character_id}"
            )
            if logical_key in selected_keys:
                names.append(str(role_name))
                matched_keys.add(logical_key)
        missing_count = len(selected_keys - matched_keys)
        if missing_count:
            raise ValueError(
                f"폐기/잠금 관리에서 지정 캐릭터 {missing_count}명에게 사용 가능한 점수 설정이 없습니다. 캐릭터를 다시 선택하세요"
            )
    return names
