# 将原生逐击效果快照渲染为来源明确的只读详情，不计算 Buff 收益。
from __future__ import annotations

import json
import math

from src.domain.battle_native_evidence import BattleHitFieldEvidence, BattleNativeHitEvidence


def field_critical_label(evidence: BattleHitFieldEvidence) -> str:
    """Render the core's decision without interpreting the captured payload."""
    state = {"critical": "暴击", "non_critical": "비치명",
             "not_applicable": "해당 없음", "unknown": "未知"}[evidence.critical_state]
    source = evidence.critical_source
    kind = {"unknown": "未知", "static_rule": "정적 규칙", "static_ge": "정적 규칙",
            "native_prediction": "클라이언트 예측 관측", "native_execution": "실행 관측",
            "server_settlement": "서버 정산 관측",
            "native_metadata": "네이티브 관측"}.get(source, "출처 미식별")
    return f"{state}（{kind}，{evidence.critical_confidence}）"


def render_field_evidence(evidence: BattleHitFieldEvidence | None) -> str:
    if evidence is None:
        return "필드 판정: 아직 독립적인 증거 결과가 없습니다; 공식 판정은 추론으로 이해해야 합니다."
    return "\n".join((
        "필드 판정(분석 핵심)",
        f"치명타: {field_critical_label(evidence)}; 출처: {evidence.critical_source}",
        f"속성 출처: {evidence.damage_attribute_source};"
        f"신뢰도: {evidence.damage_attribute_confidence}",
        *evidence.notes,
    ))


def _value(value: object) -> str:
    if value is None or value == "":
        return "未知"
    if isinstance(value, bool):
        return "예" if value else "아니요"
    if isinstance(value, (str, int)):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return str(value)
    return "未知"


def _execution(value: object) -> list[str]:
    lines = ["【실행 입력과 출력 (원본 관측)】"]
    if value is None:
        return lines + ["이번 히트에는 연결된 실행 입력 기록이 없습니다; 치명타나 원소 필드가 있다고 해서 완전한 입력이 수집되었다는 뜻은 아닙니다."]
    if not isinstance(value, dict):
        return lines + ["실행 증거 형식이 유효하지 않으며, 보정 항목이 없다는 뜻으로 해석할 수 없습니다."]
    if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        return lines + ["실행 증거 버전은 아직 해석을 지원하지 않으며, 원문은 히트별로 그대로 보존됩니다."]
    lines.extend([
        f"실행 ID: {_value(value.get('executionId'))}; 수집 세대: {_value(value.get('generation'))}",
        f"평가 커버리지: {_value(value.get('evaluationCoverage'))} (관측되지 않음이 참여 항목이 없다는 뜻은 아님)",
        f"이름 해석: {_value(value.get('nameResolution'))}; FName 인덱스는 이번 실행에서의 식별자로만 쓰이며, 태그 이름을 직접 복원할 수 없습니다.",
        "아래는 실행 단계에서 고정된 Spec 입력과 출력입니다; 캡처 정의, 태그 또는 Spec 수정 항목이 존재한다고 해서 그 항목이 실제로 공식에 채택되었다는 뜻은 아닙니다.",
        "DLL은 피해를 다시 계산하지 않습니다; 아직 관측되지 않은 속성 평가, 조건 필터링, 출처 체인은 확인된 사실로 취급하지 않습니다.",
    ])
    for key, label in (("before", "실행 전"), ("after", "실행 후")):
        phase = value.get(key)
        lines.append(f"{label}：")
        if not isinstance(phase, dict):
            lines.append("해당 단계 기록을 획득하지 못해 다른 시점과 조합하여 완전한 입력을 구성할 수 없습니다.")
        else:
            lines.append(f"시간(Unix 마이크로초): {_value(phase.get('observedUnixUs'))};"
                         f"상태: {_value(phase.get('status'))}")
            for name, item in phase.items():
                if name in ("observedUnixUs", "status"):
                    continue
                # Display captured values only, without resolving definitions into applied bonuses.
                try:
                    raw = json.dumps(item, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":"))
                except (ValueError, TypeError):
                    raw = "형식이 유효하지 않음; 원본 기록은 보존되며 0 또는 빈 목록으로 해석할 수 없음"
                lines.append(f"{name}：{raw}")
    return lines


def _snapshot(title: str, value: object, names: dict[str, str]) -> list[str]:
    lines = [f"【{title}】"]
    if value is None:
        return lines + ["스냅샷을 획득하지 못했습니다; 해당 측에 Buff가 있는지 판단할 수 없습니다."]
    if not isinstance(value, dict) or not isinstance(value.get("effects"), list):
        return lines + ["스냅샷 형식이 유효하지 않습니다; 빈 Buff 목록으로 해석할 수 없습니다."]
    effects = value["effects"]
    complete = value.get("complete") is True
    live = value.get("ended") is False
    lines.extend([
        f"대상: {_value(value.get('actorName'))} / {_value(value.get('actor'))}",
        f"스냅샷 ID: {_value(value.get('id'))}; 객체 인덱스/세대: "
        f"{_value(value.get('actorIndex'))}/{_value(value.get('actorSerial'))}",
        f"스냅샷 내용 버전 시간 (Unix 마이크로초): {_value(value.get('observedUs'))};"
        f"월드 시간 (초): {_value(value.get('worldSeconds'))}",
        f"스냅샷 목록 완전함: {'是' if complete else '未确认'};"
        f"상태: {_value(value.get('status'))}; 대상 종료됨: {_value(value.get('ended'))}",
    ])
    if not effects:
        return lines + (["유효한 빈 목록을 수집했습니다: 이 객체의 이번 스냅샷에는 효과 항목이 없습니다."]
                        if complete and live else
                        ["이번에 0건이 반환되었습니다; 스냅샷이 불완전하거나 대상이 이미 종료되어 Buff가 없다고 단정할 수 없습니다."])
    lines.append(f"관찰된 효과 {len(effects)}건 (기록 존재가 곧 이번 히트 적용을 뜻하지는 않습니다):")
    for index, effect in enumerate(effects, 1):
        if not isinstance(effect, dict):
            lines.append(f"{index}. 항목 형식이 유효하지 않음")
            continue
        name = names.get(str(effect.get('key', '')))
        lines.extend([
            f"{index}. {name + '（静态目录名称）' if name else _value(effect.get('name') or effect.get('key'))}",
            f"   정의: {_value(effect.get('key'))}; 인스턴스: {_value(effect.get('instanceKey'))}",
            f"   출처: {_value(effect.get('source'))}; 종류: {_value(effect.get('kind'))}",
            f"   중첩 수: {_value(effect.get('stacks'))}; 레벨: {_value(effect.get('level'))};"
            f"억제됨: {_value(effect.get('inhibited'))}",
            f"   지속 시간(초): {_value(effect.get('duration'))};"
            f"시작 월드 시간: {_value(effect.get('startWorldTime'))}",
        ])
        if effect.get("description"):
            lines.append(f"   설명: {_value(effect.get('description'))}")
    return lines


def render_native_evidence(evidence: BattleNativeHitEvidence | None) -> str:
    """Display immutable observations without joining inferred identity or modifier state."""
    heading = "네이티브 DLL 히트별 증거"
    if evidence is None:
        return heading + "\n네이티브 히트별 근거를 수집하지 않았습니다 (이전 전투 리포트이거나 이 히트에 네이티브 근거가 없음)."
    try:
        payload = json.loads(evidence.payload_json)
    except (ValueError, TypeError):
        return heading + "\n네이티브 근거 형식이 유효하지 않아 빈 목록으로 해석할 수 없습니다."
    if not isinstance(payload, dict) or not isinstance(payload.get("rawHit"), dict):
        return heading + "\n네이티브 근거 형식이 유효하지 않아 빈 목록으로 해석할 수 없습니다."
    hit = payload["rawHit"]
    critical_known = hit.get("criticalKnown") is True or hit.get("metadataKnown") is True
    element_known = hit.get("damageTypeKnown") is True or hit.get("metadataKnown") is True
    critical = _value(hit.get("critical")) if critical_known else "未知"
    lines = [heading,
             f"제공자: {_value(payload.get('providerId'))}; 수집 회차: {_value(payload.get('captureId'))};"
             f"네이티브 히트별 ID: {_value(payload.get('hitId'))}",
             f"피해 출처: {_value(hit.get('source'))}; 연관 근거: {_value(hit.get('association'))}",
             f"피해 샘플링 단계: {_value(hit.get('captureStage'))};"
             f"효과 샘플링 단계: {_value(hit.get('effectsStage'))}",
             f"이번 히트 소스 측 / 대상 측 효과 읽기 시간(Unix 마이크로초):"
             f"{_value(hit.get('attackerEffectsObservedUnixUs'))} / "
             f"{_value(hit.get('victimEffectsObservedUnixUs'))}",
             f"정식 정산 피해: {_value(hit.get('damage'))}; GE: {_value(hit.get('gameplayEffectName'))}",
             f"네이티브 치명타: {critical}; 근거 출처: {_value(hit.get('criticalSource'))}",
             f"네이티브 필드 플래그: metadataKnown={_value(hit.get('metadataKnown'))};"
             f"criticalKnown={_value(hit.get('criticalKnown'))}；"
             f"damageTypeKnown={_value(hit.get('damageTypeKnown'))}；"
             f"정식 표시 유형: {_value(hit.get('displayType'))}",
             f"피해 유형 원본 열거값: {_value(hit.get('damageType')) if element_known else '未知'};"
             f"증거 출처: {_value(hit.get('damageTypeSource'))}",
             f"정산 식별자: {_value(hit.get('settlementKey'))}; 성분: {_value(hit.get('componentOrdinal'))};"
             f"마지막 성분: {_value(hit.get('lastTargetComponent'))}",
             f"서버 정산 생명력: {_value(hit.get('victimHp'))}; 샘플링 단계: {_value(hit.get('victimHpStage'))}",
             f"관측 최대 HP: {_value(hit.get('victimMaxHp'))}; 샘플링 단계: {_value(hit.get('victimMaxHpStage'))}",
             f"생명 / 상한 샘플링 시간(Unix 마이크로초): {_value(hit.get('victimHpObservedUnixUs'))} / "
             f"{_value(hit.get('victimMaxHpObservedUnixUs'))}",
             "생명과 상한은 각자의 샘플링 단계에 따라 표시되며, 같은 시점의 전후 HP로 이어 붙이지 않습니다.",
             "완전성: 당시 읽어 들인 대상 효과만 설명합니다; 전투 전체의 소스 커버리지는 알 수 없으며, 시간 정지의 완전성은 이번 전투의 시계 증거로 별도 설명합니다.",
             "효과 존재 여부, 중첩 수, 샘플링 시점은 관측값입니다; 이를 근거로 피해 증가 비율, 이번 히트에 대한 적용 여부, 전체 파티 Buff를 단정하지 않습니다."]
    # Keep source and target identities as sampled, even if a replay uses another panel owner.
    names = dict(evidence.static_names)
    lines.extend(_execution(hit.get("executionEvidence")))
    lines.extend(_snapshot("보조: 피해 출처 측 효과", hit.get("attackerEffects"), names))
    lines.extend(_snapshot("보조: 피격 대상 측 효과", hit.get("victimEffects"), names))
    return "\n".join(lines)
