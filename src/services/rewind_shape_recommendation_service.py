# 编排倒带形状推荐的数据读取与策略计算。
"""Read a pinned inventory snapshot and build rewind-shape advice."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from src.domain.rewind_shape_recommendation import (
    RewindPlan,
    RewindPricingRule,
    RewindShape,
    RewindShapeRecommendation,
    recommend_score_shortfall_shapes,
    target_grade_score,
    target_percentage_score,
)
from src.domain.role_name_order import role_name_sort_key
from src.domain.rewind_loadout import RewindSlotReference, RewindSlotSummary, positive_id
from src.services.rewind_loadout_reader import RewindLoadoutReader, snapshot_complete
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True, slots=True)
class RewindShapeAnalysis:
    """A read-only recommendation tied to one account inventory snapshot."""

    snapshot_id: int | None
    snapshot_source: str
    shape_count: int
    selection_limit: int
    recommendations: tuple[RewindShapeRecommendation, ...]
    plans: tuple[RewindPlan, ...] = ()
    pricing_rule: RewindPricingRule = RewindPricingRule()
    notice: str = ""
    required_count: int = 0
    strategy: str = "balanced"
    owned_shape_counts: tuple[tuple[str, int], ...] = ()
    selected_slots: tuple[RewindSlotReference, ...] = ()
    static_identity: tuple[str, int, int] | None = None
    selected_source_snapshots: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class RewindTargetRole:
    character_id: int
    name: str
    default_suit_id: str | None
    is_custom: bool = False
    slots: tuple[RewindSlotSummary, ...] = ()


class RewindShapeRecommendationService:
    """Application boundary for the generic custom-rewind recommendation."""

    def __init__(
        self,
        *,
        user_database_path: str | Path,
        static_database_path: str | Path,
        asset_root: str | Path | None = None,
        user_dao_factory: Callable[..., Any] = UserDataDao,
        static_dao_factory: Callable[..., Any] = StaticGameDataDao,
    ) -> None:
        self._user_database_path = Path(user_database_path)
        self._static_database_path = Path(static_database_path)
        self.asset_root = Path(asset_root) if asset_root is not None else None
        self._user_dao_factory = user_dao_factory
        self._static_dao_factory = static_dao_factory

    def load_preferences(self) -> dict[str, object]:
        if not self._user_database_path.is_file():
            return {}
        with self._user_dao_factory(self._user_database_path) as user_dao:
            copies = getattr(user_dao, "list_application_setting_copies", lambda: {})()
        value = copies.get("rewind_recommendation") if isinstance(copies, dict) else None
        return dict(value) if isinstance(value, dict) else {}

    def save_preferences(self, value: dict[str, object], *,
                         expected_slots: tuple[RewindSlotReference, ...] = (), static_identity=None) -> None:
        if expected_slots and static_identity != self.static_identity():
            raise ValueError("계산에 사용한 정적 데이터가 변경되었습니다. 되감기 추천을 닫았다가 다시 연 뒤 생성하세요.")
        with self._user_dao_factory(self._user_database_path) as user_dao:
            if "slot_selection_version" in value:
                version = value["slot_selection_version"]
                if type(version) is not int or version not in {1, 2}:
                    raise ValueError("슬롯 선호 설정의 버전 또는 형식이 유효하지 않습니다")
                if version == 1:
                    maps = {"legacy": value.get("selected_slots")}
                else:
                    maps = value.get("selected_slots_by_strategy")
                    if not isinstance(maps, dict) or set(maps) != {"balanced", "focused"}:
                        raise ValueError("슬롯 선호 설정의 버전 또는 형식이 유효하지 않습니다")
                for selected in maps.values():
                    if not isinstance(selected, dict):
                        raise ValueError("슬롯 선호 설정의 버전 또는 형식이 유효하지 않습니다")
                    for key, slot_id in selected.items():
                        identifier = positive_id(key)
                        if identifier is None or (slot_id is not None and (type(slot_id) is not int or slot_id <= 0)):
                            raise ValueError("슬롯 선호 설정의 식별 정보가 유효하지 않습니다")
                        slot = user_dao.get_loadout_slot(slot_id) if slot_id is not None else None
                        if slot is not None and slot["character_id"] != identifier:
                            raise ValueError("슬롯이 선택한 캐릭터의 것이 아닙니다")
            if expected_slots:
                user_dao.replace_application_setting_copy("rewind_recommendation", value,
                    expected_loadout_plans=tuple({"character_id": ref.character_id, "slot_id": ref.slot_id,
                                                 "plan_id": ref.plan_id} for ref in expected_slots))
            else:
                user_dao.replace_application_setting_copy("rewind_recommendation", value)

    def list_target_roles(self, *, checkpoint=lambda: None) -> tuple[RewindTargetRole, ...]:
        checkpoint()
        identity = self.static_identity()
        with self._static_dao_factory(self._static_database_path) as static_dao:
            # The static catalog contains combat transformations and two avatar
            # variants under the same display name.  Neither is a separate
            # cultivation target in this picker.
            roles_by_name: dict[str, dict[str, Any]] = {}
            for character in static_dao.list_characters():
                if str(character.get("classification") or "") == "combat_transformation":
                    continue
                name = str(character.get("name_zh") or character["character_id"])
                existing = roles_by_name.get(name)
                if existing is None or _role_picker_order(character) < _role_picker_order(existing):
                    roles_by_name[name] = character
            roles = [
                RewindTargetRole(
                    character_id=int(character["character_id"]),
                    name=str(character.get("name_zh") or character["character_id"]),
                    default_suit_id=(
                        str(template.get("core_suit_id"))
                        if (template := static_dao.get_character_graduation_template(
                            int(character["character_id"])
                        )) and template.get("core_suit_id")
                        else None
                    ),
                )
                for character in sorted(
                    roles_by_name.values(),
                    key=lambda row: role_name_sort_key(str(row.get("name_zh") or row["character_id"])),
                )
            ]
        slots_by_character = {}
        if self._user_database_path.is_file():
            with self._user_dao_factory(self._user_database_path) as user_dao, self._static_dao_factory(self._static_database_path) as static_dao:
                with user_dao.read_consistent_state():
                    custom_roles = user_dao.list_custom_characters()
                    reader = RewindLoadoutReader(user_dao, static_dao.list_shapes())
                    visible_slots = user_dao.list_visible_loadout_slots_with_plans()
                    for slot in visible_slots:
                        checkpoint()
                        identifier = int(slot["character_id"])
                        slots_by_character.setdefault(identifier, []).append(reader.inspect(slot))
            known_ids = {role.character_id for role in roles}
            roles.extend(
                RewindTargetRole(
                    character_id=character_id,
                    name=str(role.get("name_zh") or character_id),
                    default_suit_id=(
                        str(role["target_suit_id"])
                        if role.get("target_suit_id")
                        else None
                    ),
                    is_custom=True,
                )
                for role in custom_roles
                if (character_id := int(role["character_id"])) not in known_ids
            )
        roles = [
            replace(
                role,
                slots=tuple(slots_by_character.get(role.character_id, ())),
            )
            for role in roles
        ]
        checkpoint()
        if identity != self.static_identity():
            raise ValueError("정적 자료가 변경되었습니다. 되감기 추천을 닫고 다시 여세요.")
        return tuple(sorted(
            roles, key=lambda role: (role_name_sort_key(role.name), role.character_id),
        ))

    def load_owned_shape_counts(self) -> tuple[tuple[str, int], ...]:
        """Count every official drive shape in the current pinned inventory."""

        with self._static_dao_factory(self._static_database_path) as static_dao:
            shape_ids = tuple(str(row["shape_id"]) for row in static_dao.list_shapes())
        known_shape_ids = set(shape_ids)
        counts: Counter[str] = Counter()
        if self._user_database_path.is_file():
            with self._user_dao_factory(self._user_database_path) as user_dao:
                with user_dao.read_consistent_state():
                    snapshot_id = user_dao.current_inventory_snapshot_id()
                    summary = user_dao.inventory_snapshot_summary(snapshot_id) if snapshot_id is not None else None
                    if snapshot_complete(summary):
                        counts.update(
                            shape_id for row in user_dao.list_inventory_items(snapshot_id, kind="module")
                            if (shape_id := _official_shape_id(str(row.get("geometry") or ""), known_shape_ids))
                        )
        return tuple((shape_id, int(counts[shape_id])) for shape_id in shape_ids)

    def analyze_for_targets(
        self, *, selected_slots: tuple[RewindSlotReference, ...],
        target_character_ids: tuple[int, ...] = (), strategy: str = "balanced",
        primary_character_ids: tuple[int, ...] = (), primary_character_id: int | None = None,
        selection_limit: int = 8, target_grade: str = "S", target_custom_percent: float | None = None,
        checkpoint: Callable[[], None] = lambda: None,
    ) -> RewindShapeAnalysis:
        if strategy not in {"balanced", "focused"}:
            raise ValueError(f"unknown rewind strategy: {strategy}")
        selected_ids = set(primary_character_ids if strategy == "focused" else target_character_ids)
        if strategy == "focused" and primary_character_id is not None:
            selected_ids.add(primary_character_id)
        if not selected_ids:
            raise ValueError("먼저 이번 전략에 참여할 캐릭터를 선택한 뒤 추천을 생성하세요.")
        if any(type(value) is not int or value <= 0 for value in selected_ids):
            raise ValueError("캐릭터 선택 데이터가 유효하지 않습니다. 다시 선택하세요.")
        target_label = _target_label(target_grade, target_custom_percent)
        if not self._user_database_path.is_file():
            raise ValueError("저장된 방안이 아직 없습니다. 먼저 계산하고 저장하세요.")
        checkpoint()
        identity = self.static_identity()
        with self._static_dao_factory(self._static_database_path) as static_dao, self._user_dao_factory(self._user_database_path) as dao:
            shape_rows = static_dao.list_shapes()
            shapes = tuple(RewindShape(str(row["shape_id"]), int(row["cell_count"])) for row in shape_rows)
            known_shape_ids = {shape.shape_id for shape in shapes}
            role_names = {int(row["character_id"]): str(row.get("name_zh") or row["character_id"])
                          for row in static_dao.list_characters()}
            with dao.read_consistent_state():
                role_names.update({int(row["character_id"]): str(row.get("name_zh") or row["character_id"])
                                   for row in dao.list_custom_characters()})
                snapshot_id = dao.current_inventory_snapshot_id()
                summary = dao.inventory_snapshot_summary(snapshot_id) if snapshot_id is not None else None
                if not snapshot_complete(summary):
                    raise ValueError("현재 인벤토리 데이터가 완전하지 않습니다. 먼저 가방을 동기화한 뒤 추천을 생성하세요.")
                snapshot_source = str(summary.get("source") or "")
                owned_shape_counts = Counter()
                for item in dao.list_inventory_items(snapshot_id, kind="module"):
                    shape_id = _official_shape_id(str(item.get("geometry") or ""), known_shape_ids)
                    if shape_id:
                        owned_shape_counts[shape_id] += 1
                references = {}
                for ref in selected_slots:
                    if not isinstance(ref, RewindSlotReference) or positive_id(ref.character_id) is None:
                        raise ValueError("슬롯 선택 데이터가 유효하지 않습니다. 다시 선택하세요.")
                    if ref.character_id in references:
                        raise ValueError("한 캐릭터당 장비 세팅 슬롯은 하나만 선택할 수 있습니다.")
                    references[ref.character_id] = ref
                reader, validated, problems = RewindLoadoutReader(dao, shape_rows), [], []
                for identifier in sorted(selected_ids):
                    checkpoint()
                    name, ref = role_names.get(identifier, str(identifier)), references.get(identifier)
                    if ref is None:
                        problems.append(f"{name}의 장비 세팅 슬롯이 아직 선택되지 않았습니다. 먼저 선택하세요.")
                        continue
                    slot = dao.get_loadout_slot(ref.slot_id) if positive_id(ref.slot_id) else None
                    plan = (slot or {}).get("current_plan") or {}
                    if (slot is None or slot.get("is_archived") or int(slot["character_id"]) != identifier
                            or plan.get("plan_id") != ref.plan_id):
                        problems.append(f"{name}의 선택한 장비 세팅이 변경되었습니다. 되감기 추천을 닫았다가 다시 연 뒤 선택하세요.")
                        continue
                    row = reader.inspect(slot)
                    if row.state != "ready":
                        problems.append(f"{name}의 “{row.slot_name}” 슬롯: {row.reason}")
                    else:
                        validated.append(row)
                if problems:
                    raise ValueError("\n".join(problems))
        shortfalls, score_gaps = Counter(), Counter()
        for slot in validated:
            checkpoint()
            for drive in slot.drives:
                threshold = (target_percentage_score(target_custom_percent, drive.area)
                             if target_custom_percent is not None else target_grade_score(target_grade, drive.area))
                gap = max(0.0, threshold - drive.score)
                if gap > 0:
                    shortfalls[drive.shape_id] += 1
                    score_gaps[drive.shape_id] += gap
        frozen_references = tuple(row.reference for row in validated)
        checkpoint()
        self.validate_selection(frozen_references, identity)
        pricing_rule = RewindPricingRule()
        notice = ""
        recommendations = recommend_score_shortfall_shapes(
            shapes=shapes,
            owned_shape_counts=owned_shape_counts,
            shortfalls=shortfalls,
            score_gaps=score_gaps,
            selection_limit=selection_limit,
            proportional=strategy == "focused",
        )
        positive_shortfall_shape_count = sum(
            1 for score_gap in score_gaps.values() if score_gap > 0
        )
        if positive_shortfall_shape_count > selection_limit:
            notice = (
                f"필요한 드라이브가 {selection_limit}개를 넘습니다."
                "점수 등급을 낮추거나 무작위 되감기 뽑기를 사용하는 것이 좋습니다."
            )
        elif not recommendations:
            notice = (
                "선택한 캐릭터의 저장 방안을 읽었습니다."
                f"{target_label} 미만의 장착 드라이브가 없습니다."
            )
        benefit = sum(row.priority_score * row.quantity for row in recommendations)
        cost = sum(pricing_rule.cost_for_quantity(row.quantity) for row in recommendations)
        labels = {"balanced": "전면 균형", "focused": "소수 집중"}
        plans = (RewindPlan(strategy, labels[strategy], recommendations, benefit, cost),) if recommendations else ()
        return RewindShapeAnalysis(
            snapshot_id=snapshot_id,
            selected_slots=frozen_references, static_identity=identity,
            selected_source_snapshots=tuple((row.reference.slot_id, row.source_snapshot_id) for row in validated),
            snapshot_source=snapshot_source,
            shape_count=len(shapes),
            selection_limit=selection_limit,
            recommendations=recommendations,
            plans=plans,
            pricing_rule=pricing_rule,
            notice=notice,
            required_count=sum(shortfalls.values()),
            strategy=strategy,
            owned_shape_counts=tuple(
                (shape.shape_id, int(owned_shape_counts[shape.shape_id]))
                for shape in shapes
            ),
        )

    def static_identity(self):
        stat = self._static_database_path.stat()
        return str(self._static_database_path.resolve()), stat.st_size, stat.st_mtime_ns

    def validate_selection(self, references, static_identity):
        if static_identity != self.static_identity():
            raise ValueError("계산에 사용한 정적 데이터가 변경되었습니다. 되감기 추천을 닫았다가 다시 연 뒤 생성하세요.")
        with self._user_dao_factory(self._user_database_path) as dao, dao.read_consistent_state():
            for ref in references:
                slot = dao.get_loadout_slot(ref.slot_id)
                if (slot is None or slot.get("is_archived") or slot["character_id"] != ref.character_id
                        or ((slot.get("current_plan") or {}).get("plan_id")) != ref.plan_id):
                    raise ValueError("선택한 장비 세팅이 변경되었습니다. 되감기 추천을 닫았다가 다시 연 뒤 생성하세요.")


def _target_label(target_grade: str, target_custom_percent: float | None) -> str:
    """Format and validate the threshold name shown in rewind analysis notices."""

    if target_custom_percent is None:
        return f" {target_grade.upper()} 점수 등급"
    target_percentage_score(target_custom_percent, 1)
    return f"직접 지정 {float(target_custom_percent):g}% 목표"


def _official_shape_id(value: str, known_shape_ids: set[str]) -> str:
    """Normalize snapshot geometry (``Hen2``) to its static official ID."""

    if value in known_shape_ids:
        return value
    candidate = f"EquipmentGeometry_{value.removeprefix('EquipmentGeometry_')}"
    return candidate if candidate in known_shape_ids else ""



def _role_picker_order(character: dict[str, Any]) -> tuple[int, int, int]:
    """Choose one canonical picker record for a duplicated display name."""

    classification = str(character.get("classification") or "")
    actor_path = str(character.get("actor_path") or "").casefold()
    avatar_variant = classification == "available_avatar_variant"
    return (
        1 if avatar_variant else 0,
        0 if avatar_variant and "female" in actor_path else 1,
        int(character["character_id"]),
    )
