# 按逐击与扣时停时钟重放噩梦、蚀心、鸩火和浊燃的目标层数。
"""Forward-only target stack reconstruction for direct-formula DOT hits."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from src.services.battle_state_payload import HIT_FIELDS, state_rows
from src.domain.native_analysis import BattleComputeBackend
from src.services.battle_hit_state_compute import compute_hit_state, restore_dot_states

from src.domain.battle_report import BattleAnalysisHit, BattleAnalysisSnapshot
from src.services.battle_character_passive_service import BattleCharacterPassiveService
from src.services.battle_damage_composition_service import (
    explicit_reaction_channel_for_hit,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
)


_NIGHTMARE_IDS = frozenset({
    "ge_player_lacrimosa_blood_damage",
    "ge_player_lacrimosa_blood_damage_lv6",
})
_EROSION_ID = "ge_player_zankou_dotdamage"
_VENOM_ID = "ge_player_zankou_dotultradamage"
_ORDINARY_SCORCH_ID = "buff_reaction_5_new"
_ZANKOU_SCORCH_ID = "buff_reaction_5_new_1036"
_SCORCH_IDS = frozenset({_ORDINARY_SCORCH_ID, _ZANKOU_SCORCH_ID})
_CANG_FIELD_DOT_ID = "ge_player_cang_ultraskill_damage"
_ADLER_SKILL_DOT_ID = "ge_player_adler_skill_damage"
_RECENT_DOT_OBSERVATION_US = 1_500_000
_CANG_CAST_DAMAGE_IDS = frozenset({"ge_player_cang_ultraskill2_damage"})
_ADLER_CAST_DAMAGE_IDS = frozenset({
    "ge_player_adler_skill2_damage",
    "ge_player_adler_skill3_damage",
})
_DOT_KIND_BY_EFFECT = {
    **{effect_id: "nightmare" for effect_id in _NIGHTMARE_IDS},
    _EROSION_ID: "erosion",
    _VENOM_ID: "venom",
    _ORDINARY_SCORCH_ID: "scorch",
    _ZANKOU_SCORCH_ID: "scorch",
    _CANG_FIELD_DOT_ID: "cang_field",
    _ADLER_SKILL_DOT_ID: "adler_skill",
}
_DOT_KIND_NAMES = {
    "nightmare": "噩梦",
    "erosion": "蚀心",
    "venom": "鸩火",
    "scorch": "浊燃",
    "cang_field": "判予秋",
    "adler_skill": "诛恶护持",
}
@dataclass(frozen=True, slots=True)
class BattleDotStackState:
    event_id: str
    coefficient: int
    label: str
    confidence: str
    evidence_basis: str
    active_dot_kind_count: int = 0
    dot_final_multiplier: float = 1.0
    dot_final_multiplier_basis: str = ""


@dataclass(slots=True)
class _Stack:
    count: int = 0
    expires_at_active_us: int | None = None
    remove_one_on_expiry: bool = False
    duration_us: int = 0
    independent_expiry: bool = False
    expiry_times_active_us: list[int] = field(default_factory=list)

    def advance(self, active_us: int) -> None:
        if self.independent_expiry:
            self.expiry_times_active_us = [
                expiry
                for expiry in self.expiry_times_active_us
                if expiry > active_us
            ]
            self.count = len(self.expiry_times_active_us)
            return
        while (
            self.count > 0
            and self.expires_at_active_us is not None
            and active_us >= self.expires_at_active_us
        ):
            if not self.remove_one_on_expiry:
                self.count = 0
                self.expires_at_active_us = None
                return
            self.count -= 1
            self.expires_at_active_us += self.duration_us

    def add(self, amount: int, active_us: int) -> None:
        self.advance(active_us)
        if self.independent_expiry:
            available = max(0, 10 - len(self.expiry_times_active_us))
            accepted = min(available, max(0, amount))
            self.expiry_times_active_us.extend(
                active_us + self.duration_us for _ in range(accepted)
            )
            self.count = len(self.expiry_times_active_us)
            return
        self.count = min(10, self.count + max(0, amount))
        if self.count:
            self.expires_at_active_us = active_us + self.duration_us

    def refresh(self, active_us: int) -> None:
        self.advance(active_us)
        if self.count > 0:
            self.expires_at_active_us = active_us + self.duration_us

    def clear(self) -> None:
        self.count = 0
        self.expires_at_active_us = None
        self.expiry_times_active_us.clear()

    def set_single(self, active_us: int) -> None:
        self.count = 1
        self.expires_at_active_us = active_us + self.duration_us


@dataclass(slots=True)
class _VisibleDotBatch:
    """One target's application batch, bounded by cast identity and duration."""

    duration_us: int
    observed_cast_serial: int = -1
    expires_at_active_us: int | None = None

    def observe(self, active_us: int, cast_serial: int) -> int:
        new_cast = cast_serial != self.observed_cast_serial
        expired = (
            self.expires_at_active_us is None
            or active_us >= self.expires_at_active_us
        )
        if not new_cast and not expired:
            return 0
        self.observed_cast_serial = cast_serial
        self.expires_at_active_us = active_us + self.duration_us
        return 1


@dataclass(slots=True)
class _ScorchState:
    """One target's scorch layers plus Zankou's observed DOT activation gate."""

    stack_limit: int
    dot_activation_required: bool
    count: int = 0
    present: bool = False
    expires_at_active_us: int | None = None
    duration_us: int = 15_000_000

    def advance(self, active_us: int) -> None:
        if (
            self.expires_at_active_us is not None
            and active_us >= self.expires_at_active_us
        ):
            self.count = 0
            self.present = False
            self.expires_at_active_us = None

    def prepare_application(self, active_us: int) -> None:
        """Store one layer; Zankou keeps it dormant until an observed DOT hit."""

        self.advance(active_us)
        self.count = min(self.stack_limit, self.count + 1)
        if not self.dot_activation_required:
            self.present = True
        self.expires_at_active_us = active_us + self.duration_us

    def observe_settlement(self, active_us: int) -> None:
        """A recorded damage tick proves at least one layer was activated."""

        self.advance(active_us)
        if self.count == 0:
            self.count = 1
        self.present = True
        self.expires_at_active_us = active_us + self.duration_us

    def apply_dot_hit(self, active_us: int) -> None:
        """One recorded non-scorch DOT settlement activates and adds one layer."""

        self.advance(active_us)
        if not self.dot_activation_required or self.count <= 0:
            return
        self.count = min(self.stack_limit, self.count + 1)
        self.present = True
        self.expires_at_active_us = active_us + self.duration_us


def _builds(build: Mapping[str, object] | None) -> dict[int, Mapping[str, object]]:
    return {
        int(row["character_id"]): row
        for row in (build or {}).get("characters") or ()
        if isinstance(row, Mapping) and row.get("character_id") is not None
    }


def _nightmare_duration_seconds(build: Mapping[str, object] | None) -> float:
    character = _builds(build).get(1004, {})
    profile = character.get("profile") if isinstance(character, Mapping) else {}
    awakenings = (
        profile.get("selected_awaken_effect_ids")
        if isinstance(profile, Mapping)
        else ()
    ) or ()
    return 6.0 if "Effect4" in awakenings else 3.0


def _nightmare_early_settlement_enabled(
    build: Mapping[str, object] | None,
) -> bool:
    character = _builds(build).get(1004, {})
    profile = character.get("profile") if isinstance(character, Mapping) else {}
    awakenings = (
        profile.get("selected_awaken_effect_ids")
        if isinstance(profile, Mapping)
        else ()
    ) or ()
    return "Effect3" in awakenings


def _cang_field_duration_seconds(build: Mapping[str, object] | None) -> float:
    character = _builds(build).get(1023, {})
    profile = character.get("profile") if isinstance(character, Mapping) else {}
    awakenings = (
        profile.get("selected_awaken_effect_ids")
        if isinstance(profile, Mapping)
        else ()
    ) or ()
    return 16.0 if "Effect5" in awakenings else 12.0


def zankou_scorch_variant_for_build(
    build: Mapping[str, object] | None,
) -> str | None:
    """Resolve the replacement only from an explicitly frozen Zankou stage."""

    character = _builds(build).get(1036, {})
    if not character:
        return None
    profile = character.get("profile") if isinstance(character, Mapping) else {}
    has_explicit_stage = "breakthrough_stage" in character or (
        isinstance(profile, Mapping) and "breakthrough_stage" in profile
    )
    if not has_explicit_stage:
        return None
    stage = max(
        int(character.get("breakthrough_stage") or 0),
        int(profile.get("breakthrough_stage") or 0)
        if isinstance(profile, Mapping) else 0,
    )
    return "zankou" if stage >= 2 else "ordinary"


def _active_us(analysis: BattleAnalysisSnapshot, raw_us: int) -> int:
    return project_timeline_time_us(
        raw_us,
        battle_start_us=0,
        intervals=analysis.time_stop_intervals,
        mode=ACTIVE_TIME_MODE,
    )


def _is_nightmare_application(
    hit: BattleAnalysisHit,
) -> int:
    effect = hit.gameplay_effect_id.casefold()
    ability = hit.ability_id.casefold()
    if (
        hit.character_id != 1004
        or effect in _NIGHTMARE_IDS
        or hit.classification != "direct"
        or "qte" in effect
        or "qte" in ability
        or "steal" in effect
        or ability == "ga_lacrimosa_steal"
    ):
        return 0
    return 1


def _is_zankou_scorch_application_trigger(hit: BattleAnalysisHit) -> bool:
    if (
        hit.gameplay_effect_id.casefold() in _SCORCH_IDS
        or explicit_reaction_channel_for_hit(hit) == (
            "reaction_scorch",
            "浊燃",
        )
    ):
        return False
    return "浊燃" in " ".join((hit.attack_type, hit.skill_name, hit.damage_name))


def _is_erosion_application(hit: BattleAnalysisHit) -> int:
    effect = hit.gameplay_effect_id.casefold()
    if hit.character_id != 1036:
        return 0
    if _is_zankou_magic_melee(hit) or "zankou_magicbranch" in effect:
        return 1
    if effect in {
        "ge_player_zankou_skill1_1_damage",
        "ge_player_zankou_skill2_1_damage",
    }:
        return 5
    return 0


def _is_zankou_magic_melee(hit: BattleAnalysisHit) -> bool:
    return (
        hit.character_id == 1036
        and hit.classification == "direct"
        and "zankou_magicmelee" in hit.gameplay_effect_id.casefold()
    )


def _burst_final_markers(
    hits: Sequence[BattleAnalysisHit],
    *,
    character_id: int,
    effect_marker: str,
) -> set[str]:
    """Use the final hit of each multi-hit application burst as its state point."""

    markers: set[str] = set()
    by_target: dict[tuple[str, str], list[BattleAnalysisHit]] = {}
    for hit in hits:
        if (
            hit.character_id == character_id
            and effect_marker in hit.gameplay_effect_id.casefold()
        ):
            by_target.setdefault(_target_key(hit), []).append(hit)
    for target_hits in by_target.values():
        burst: list[BattleAnalysisHit] = []
        for hit in sorted(
            target_hits,
            key=lambda row: (row.relative_time_us, row.sequence),
        ):
            if (
                burst
                and hit.relative_time_us - burst[-1].relative_time_us
                > 2_000_000
            ):
                markers.add(burst[-1].event_id)
                burst = []
            burst.append(hit)
        if burst:
            markers.add(burst[-1].event_id)
    return markers


def _cast_first_markers(
    hits: Sequence[BattleAnalysisHit],
    *,
    character_id: int,
    damage_ids: frozenset[str],
) -> set[str]:
    """Collapse one cast's multi-target/multi-hit direct damage to its first hit."""

    candidates = sorted(
        (
            hit for hit in hits
            if hit.direction == "outgoing"
            and hit.character_id == character_id
            and hit.gameplay_effect_id.casefold() in damage_ids
        ),
        key=lambda row: (row.relative_time_us, row.sequence, row.event_id),
    )
    markers: set[str] = set()
    burst_first: BattleAnalysisHit | None = None
    previous: BattleAnalysisHit | None = None
    for hit in candidates:
        if (
            previous is not None
            and hit.relative_time_us - previous.relative_time_us > 2_000_000
        ):
            assert burst_first is not None
            markers.add(burst_first.event_id)
            burst_first = None
        if burst_first is None:
            burst_first = hit
        previous = hit
    if burst_first is not None:
        markers.add(burst_first.event_id)
    return markers


def _target_key(hit: BattleAnalysisHit) -> tuple[str, str]:
    return (
        str(hit.scope_half or "").casefold(),
        str(hit.target_id or "__unknown_target__"),
    )


def _stack_for(
    states: dict[tuple[str, str], _Stack],
    hit: BattleAnalysisHit,
    *,
    duration_us: int,
    independent_expiry: bool = False,
) -> _Stack:
    return states.setdefault(
        _target_key(hit),
        _Stack(
            duration_us=duration_us,
            independent_expiry=independent_expiry,
        ),
    )


def reconstruct_dot_stack_states(
    analysis: BattleAnalysisSnapshot,
    build: Mapping[str, object] | None,
    *, compute_backend: BattleComputeBackend | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, BattleDotStackState]:
    """Return state evidence for each recorded DOT settlement hit."""

    nightmare_duration = round(_nightmare_duration_seconds(build) * 1_000_000)
    cang_field_duration = round(_cang_field_duration_seconds(build) * 1_000_000)
    early_settlement_enabled = _nightmare_early_settlement_enabled(build)
    if isinstance(compute_backend, BattleComputeBackend) and compute_backend.supports_battle_compute:
        return restore_dot_states(compute_hit_state("dot", {
            "hits": state_rows(analysis.hits, HIT_FIELDS),
            "time_stop_intervals": list(analysis.time_stop_intervals),
            "nightmare_duration_us": nightmare_duration, "cang_field_duration_us": cang_field_duration,
            "early_settlement_enabled": early_settlement_enabled,
            "scorch_variant": zankou_scorch_variant_for_build(build),
            "sagiri_dot_final_enabled": BattleCharacterPassiveService.is_unlocked(build, 1003, 2),
        }, backend=compute_backend, checkpoint=checkpoint))
    nightmare_by_target: dict[tuple[str, str], _Stack] = {}
    erosion_by_target: dict[tuple[str, str], _Stack] = {}
    venom_by_target: dict[tuple[str, str], _Stack] = {}
    cang_field_by_target: dict[tuple[str, str], _Stack] = {}
    adler_skill_by_target: dict[tuple[str, str], _Stack] = {}
    cang_batch_by_target: dict[tuple[str, str], _VisibleDotBatch] = {}
    adler_batch_by_target: dict[tuple[str, str], _VisibleDotBatch] = {}
    scorch_variant = zankou_scorch_variant_for_build(build)
    scorch_stack_enabled = scorch_variant == "zankou"
    sagiri_dot_final_enabled = BattleCharacterPassiveService.is_unlocked(
        build,
        1003,
        2,
    )
    scorch_by_target: dict[tuple[str, str], _ScorchState] = {}
    venom_markers = _burst_final_markers(
        analysis.hits,
        character_id=1036,
        effect_marker="zankou_magicultraskill",
    )
    cang_cast_markers = _cast_first_markers(
        analysis.hits,
        character_id=1023,
        damage_ids=_CANG_CAST_DAMAGE_IDS,
    )
    adler_cast_markers = _cast_first_markers(
        analysis.hits,
        character_id=1033,
        damage_ids=_ADLER_CAST_DAMAGE_IDS,
    )
    cang_cast_serial = 0
    adler_cast_serial = 0
    results: dict[str, BattleDotStackState] = {}
    recent_dot_observations: dict[
        tuple[str, str],
        dict[str, int],
    ] = {}
    settlement_pending_by_target: dict[tuple[str, str], int] = {}
    ordered = sorted(
        (hit for hit in analysis.hits if hit.direction == "outgoing"),
        key=lambda row: (row.relative_time_us, row.sequence, row.event_id),
    )
    for hit in ordered:
        now = _active_us(analysis, hit.relative_time_us)
        state_wall_now = hit.relative_time_us
        target_key = _target_key(hit)
        if hit.event_id in cang_cast_markers:
            cang_cast_serial += 1
        if hit.event_id in adler_cast_markers:
            adler_cast_serial += 1
        nightmare = _stack_for(
            nightmare_by_target,
            hit,
            duration_us=nightmare_duration,
            independent_expiry=True,
        )
        erosion = _stack_for(
            erosion_by_target,
            hit,
            duration_us=30_000_000,
        )
        venom = _stack_for(
            venom_by_target,
            hit,
            duration_us=30_000_000,
        )
        cang_field = _stack_for(
            cang_field_by_target,
            hit,
            duration_us=cang_field_duration,
        )
        adler_skill = _stack_for(
            adler_skill_by_target,
            hit,
            duration_us=10_000_000,
        )
        cang_batch = cang_batch_by_target.setdefault(
            target_key,
            _VisibleDotBatch(duration_us=cang_field_duration),
        )
        adler_batch = adler_batch_by_target.setdefault(
            target_key,
            _VisibleDotBatch(duration_us=10_000_000),
        )
        scorch = scorch_by_target.setdefault(
            target_key,
            _ScorchState(
                stack_limit=3 if scorch_stack_enabled else 1,
                dot_activation_required=scorch_stack_enabled,
            ),
        )
        nightmare.advance(now)
        erosion.advance(now)
        venom.advance(now)
        cang_field.advance(now)
        adler_skill.advance(now)
        scorch.advance(now)
        observed_effect = hit.gameplay_effect_id.casefold()
        effect = observed_effect
        explicit_scorch = explicit_reaction_channel_for_hit(hit) == (
            "reaction_scorch",
            "浊燃",
        )
        ordinary_scorch_application = (
            not scorch_stack_enabled
            and (
                observed_effect == _ORDINARY_SCORCH_ID
                or explicit_scorch
            )
        )
        if scorch_stack_enabled and _is_zankou_scorch_application_trigger(hit):
            scorch.prepare_application(now)
        if effect in _SCORCH_IDS and scorch_variant is not None:
            effect = (
                _ZANKOU_SCORCH_ID
                if scorch_stack_enabled else _ORDINARY_SCORCH_ID
            )
        elif explicit_scorch and effect not in _SCORCH_IDS:
            effect = (
                _ZANKOU_SCORCH_ID
                if scorch_stack_enabled else _ORDINARY_SCORCH_ID
            )
        if ordinary_scorch_application:
            scorch.prepare_application(now)
        scorch_was_active = scorch.present
        is_early_settlement = False
        if effect in _NIGHTMARE_IDS:
            state = nightmare
            is_early_settlement = bool(
                now <= settlement_pending_by_target.get(target_key, -1)
            )
            label = "3각: 「噩梦」 조기 정산" if is_early_settlement else "「噩梦」 현재 중첩"
        elif effect == _EROSION_ID:
            state = erosion
            label = "「蚀心」 현재 중첩"
        elif effect == _VENOM_ID:
            state = venom
            label = "「鸩火」 현재 중첩"
        elif effect in _SCORCH_IDS:
            state = scorch
            label = "스코치 정산 전 중첩"
        elif effect == _CANG_FIELD_DOT_ID:
            state = cang_field
            label = "「判予秋」 현재 상태"
        elif effect == _ADLER_SKILL_DOT_ID:
            state = adler_skill
            label = "「诛恶护持」 현재 상태"
        else:
            state = None
            label = ""
        if state is not None:
            is_scorch = effect in _SCORCH_IDS
            current_kind = _DOT_KIND_BY_EFFECT[effect]
            coefficient_is_lower_bound = bool(
                not is_early_settlement
                and not is_scorch
                and state.count <= 0
            )
            if coefficient_is_lower_bound:
                label = f"{_DOT_KIND_NAMES[current_kind]} 관측 하한"
            modeled_kinds = {
                kind
                for kind, count in (
                    ("nightmare", nightmare.count),
                    ("erosion", erosion.count),
                    ("venom", venom.count),
                    (
                        "scorch",
                        scorch.count,
                    ),
                    ("cang_field", cang_field.count),
                    ("adler_skill", adler_skill.count),
                )
                if count > 0
            }
            recent_kinds = {
                kind
                for kind, observed_at_us in recent_dot_observations.get(
                    target_key,
                    {},
                ).items()
                if 0 <= state_wall_now - observed_at_us <= _RECENT_DOT_OBSERVATION_US
            }
            active_kinds = modeled_kinds | recent_kinds
            active_kinds.add(current_kind)
            scorch_confirmed_for_hit = scorch_was_active
            scorch_started_for_hit = (
                scorch.count > 0 or is_scorch
            )
            if scorch_started_for_hit:
                active_kinds.add("scorch")
            dot_kind_count = min(4, len(active_kinds))
            dot_final_multiplier = 1.0
            dot_final_basis = (
                "사키리 돌파 2 패시브 「먹어도 돼?」 미활성;"
                "DOT 전용 최종 곱연산 구간은 1로 고정"
            )
            if sagiri_dot_final_enabled:
                if scorch_confirmed_for_hit:
                    dot_final_multiplier += min(1.0, dot_kind_count * 0.25)
                    kind_names = "、".join(
                        _DOT_KIND_NAMES[kind] for kind in sorted(active_kinds)
                    )
                    recent_only = sorted(recent_kinds - modeled_kinds)
                    recent_basis = (
                        "; 그중"
                        + "、".join(_DOT_KIND_NAMES[kind] for kind in recent_only)
                        + " 항목은 이번 히트 전 1.5초 이내의 최근 정식 틱 피해로만 확인된 것이며,"
                        "이를 근거로 전체 지속 시간을 갱신하지는 않습니다"
                        if recent_only
                        else ""
                    )
                    dot_final_basis = (
                        "사키리 돌파 2 패시브 「먹어도 돼?」;"
                        + "대상이 정산 전에 이미 스코치 상태;"
                        + f"활성 DOT 종류: {kind_names}, 총 {dot_kind_count}종;"
                        + "1 + min(종류 수 × 25%, 100%)"
                        + recent_basis
                    )
                else:
                    dot_final_basis = (
                        "사키리 돌파 2 패시브 「먹어도 돼?」는 활성화되었으나,"
                        "이번 히트 정산 전 대상의 스코치 상태가 아직 확인되지 않아 DOT 전용 최종 곱연산 구간은 1로 고정"
                    )
            visible_dot_basis = ""
            if effect == _CANG_FIELD_DOT_ID:
                visible_dot_basis = (
                    "「判予秋」 전개 직접 피해를 우선 기준으로 Q 배치를 구분하며,"
                    "같은 대상의 첫 틱은 해당 배치 상태에 1중첩이 적용되었다는 가시적 근거로 삼습니다; 전개 직접 피해가 없으면 정식 12/16초 영역을 기준으로,"
                    "보수적 배치를 유지합니다; 실제로 보이는 각 DOT 틱 피해는 추가로 잔홍의 1중첩 보충을 발동하며,"
                    "중간에 누락된 틱은 역추정하지 않습니다"
                )
            elif effect == _ADLER_SKILL_DOT_ID:
                visible_dot_basis = (
                    "「诛恶护持」 초기 직접 피해를 우선 기준으로 E 배치를 구분하며,"
                    "같은 대상의 첫 틱은 해당 배치 상태에 1중첩이 적용되었다는 가시적 근거로 삼습니다; 초기 직접 피해가 없으면 정식 10초 지속 시간을 기준으로,"
                    "보수적 배치를 유지합니다; 실제로 보이는 각 DOT 틱 피해는 추가로 잔홍의 1중첩 보충을 발동하며,"
                    "중간에 누락된 틱은 역추정하지 않습니다"
                )
            state_basis = visible_dot_basis
            if effect in _NIGHTMARE_IDS:
                state_basis = (
                    "같은 대상의 히트를 순방향으로 리플레이해 히트 후 중첩을 추가합니다;"
                    "라크리모사는 QTE와 학습 E를 제외한 모든 유효 직접 피해 hit마다 「噩梦」 1중첩을 부여합니다;"
                    "「噩梦」 중첩이 실제로 틱 피해를 줄 때마다 잔홍 스코치 1중첩을 추가로 보충합니다; 최대 10중첩;"
                    "각 중첩은 시간 정지를 제외한 시계로 독립적으로 만료 시간을 계산합니다;"
                    + (
                        "이번 히트 전에 부여 이벤트를 찾지 못했습니다; 이번 틱은 「噩梦」 상태가 최소 1개 존재함만 증명하며,"
                        "정확한 중첩 수는 역추정하지 않습니다"
                        if coefficient_is_lower_bound
                        else ""
                    )
                )
            elif effect == _EROSION_ID:
                state_basis = (
                    "같은 대상의 히트를 순방향으로 리플레이해 「蚀心」 부여를 추적합니다;"
                    "환상 형태의 일반/분기 히트는 1중첩, 강화 스킬 히트는 5중첩을 추가합니다; 최대 10중첩;"
                    "30초 지속 시간은 시간 정지를 제외한 유효 전투 시계로 계산하며 시간 정지 중에는 흐르지 않습니다"
                    + (
                        "; 이번 히트 전에 가시적인 부여 이벤트가 없어 이번 틱은 「蚀心」 상태가 최소 1개 존재함만 증명하며,"
                        "관측 하한을 정확한 1중첩으로 해석하지 않습니다"
                        if coefficient_is_lower_bound
                        else ""
                    )
                )
            elif effect == _VENOM_ID:
                state_basis = (
                    "같은 대상의 히트를 순방향으로 리플레이해 「鸩火」 부여를 추적합니다;"
                    "「血宴入梦时」의 최종 부여 시점에 5중첩을 추가합니다; 환상 형태 일반 공격의 확산은 기존 「鸩火」의 지속 시간만 갱신하고,"
                    "중첩은 늘리지 않습니다; 최대 10중첩;"
                    "30초 지속 시간은 시간 정지를 제외한 유효 전투 시계로 계산하며 시간 정지 중에는 흐르지 않습니다"
                    + (
                        "; 이번 히트 전에 가시적인 부여 이벤트가 없어 이번 틱은 「鸩火」 상태가 최소 1개 존재함만 증명하며,"
                        "관측 하한을 정확한 1중첩으로 해석하지 않습니다"
                        if coefficient_is_lower_bound
                        else ""
                    )
                )
            results[hit.event_id] = BattleDotStackState(
                event_id=hit.event_id,
                coefficient=(0 if is_early_settlement else max(1, state.count)),
                label=label,
                confidence=(
                    "未解析"
                    if is_early_settlement
                    else (
                        "中"
                        if is_scorch and scorch_stack_enabled and state.count > 1
                        else "低"
                        if is_scorch or coefficient_is_lower_bound
                        else "中"
                    )
                ),
                evidence_basis=(
                    (
                        "일반 공격 마지막 단이 3각 잔여 피해 정산을 발동합니다;"
                        "현재는 잔여 정산 횟수를 중첩별로 아직 리플레이하지 않았으므로 이번 히트는 일반 「噩梦」 틱 피해로 추정하지 않습니다"
                    )
                    if is_early_settlement
                    else (
                        "하프와 대상별로 격리해 잔홍 스코치 공유 상태를 리플레이합니다; 이번 틱은 먼저 정산 전 중첩을 읽습니다;"
                        "잔홍 돌파 패시브는 상한을 3으로 바꿉니다; 스코치 반응이 일어날 때마다 먼저 비활성 스코치 1중첩을 저장하고,"
                        "이후 전투 리포트에 비스코치 DOT 틱 피해 hit가 실제로 1개 나타날 때마다 스코치도 함께 1중첩 증가하며 전체 피해가 활성화됩니다;"
                        "중간에 누락된 틱은 역추정하지 않으며, 스코치 자체의 틱 피해는 재귀적으로 중첩을 쌓지 않습니다."
                        "이 투영은 로컬 기록 전투 리포트의 잔차 회귀로 제약됩니다"
                        if is_scorch and effect == _ZANKOU_SCORCH_ID
                        else (
                            "일반 스코치는 최대 1중첩입니다; 정식 발동은 전체 15초 지속 시간만 갱신하고,"
                            "실제 주기 틱 피해는 다음 틱을 초기화하지 않으며 중첩을 잔홍의 3중첩으로 올리지도 않습니다"
                            if is_scorch and effect == _ORDINARY_SCORCH_ID
                            else "히트는 이 대상에 현재 스코치가 최소 1중첩 존재함만 증명합니다"
                        )
                    )
                    if is_scorch
                    else state_basis
                ),
                active_dot_kind_count=dot_kind_count,
                dot_final_multiplier=dot_final_multiplier,
                dot_final_multiplier_basis=dot_final_basis,
            )
            if is_early_settlement:
                nightmare.clear()
                settlement_pending_by_target.pop(target_key, None)
            if is_scorch:
                scorch.observe_settlement(now)
            if effect not in _SCORCH_IDS:
                recent_dot_observations.setdefault(target_key, {})[
                    current_kind
                ] = state_wall_now
        nightmare.add(_is_nightmare_application(hit), now)
        if early_settlement_enabled and effect in {
            "ge_player_lacrimosa_melee5_damage",
            "ge_player_lacrimosa_b_melee8_damage",
        }:
            settlement_pending_by_target[target_key] = now + 500_000
        nightmare_application = _is_nightmare_application(hit)
        erosion_application = _is_erosion_application(hit)
        venom_application = 5 if hit.event_id in venom_markers else 0
        cang_field_application = (
            cang_batch.observe(now, cang_cast_serial)
            if effect == _CANG_FIELD_DOT_ID else 0
        )
        adler_skill_application = (
            adler_batch.observe(now, adler_cast_serial)
            if effect == _ADLER_SKILL_DOT_ID else 0
        )
        erosion.add(erosion_application, now)
        if _is_zankou_magic_melee(hit):
            venom.refresh(now)
        if venom_application:
            venom.add(venom_application, now)
        if cang_field_application:
            cang_field.set_single(now)
        if adler_skill_application:
            adler_skill.set_single(now)
        if (
            scorch_stack_enabled
            and effect in _DOT_KIND_BY_EFFECT
            and effect not in _SCORCH_IDS
        ):
            scorch.apply_dot_hit(now)
    return results
