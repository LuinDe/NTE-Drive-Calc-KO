# 将原生战报页面响应还原为只读展示对象，不执行公式或补算缺失结果。
from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass
from functools import lru_cache
import math
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from src.domain.battle_report import BattleAnalysisSnapshot, BattleDamageComposition, BattleTargetCondition
from src.domain.battle_marginal_benefit import BattleMarginalBenefits
from src.integrations.nte_analysis_core import NativeAnalysisError


@lru_cache(maxsize=None)
def _dataclass_fields(cls):
    hints = get_type_hints(cls, localns={
        'BattleTargetCondition': BattleTargetCondition,
        'BattleDamageComposition': BattleDamageComposition,
    })
    return tuple((field, hints[field.name]) for field in fields(cls))


def decode(value: object, annotation: Any):
    """Accept only the declared wire shape; unknowns stay None, never zero."""
    origin, args = get_origin(annotation), get_args(annotation)
    if annotation is Any:
        return value
    if annotation is type(None):
        if value is None:
            return None
        raise NativeAnalysisError('분석 코어의 알 수 없는 값 형식이 잘못되었습니다')
    if origin in (UnionType, Union):
        for member in args:
            try:
                return decode(value, member)
            except NativeAnalysisError:
                pass
        raise NativeAnalysisError('분석 코어 필드 유형이 일치하지 않습니다')
    if origin is Literal:
        if value in args:
            return value
        raise NativeAnalysisError('분석 코어 상태 값이 잘못되었습니다')
    if annotation is float:
        if type(value) in (int, float) and math.isfinite(value):
            return float(value)
        raise NativeAnalysisError('분석 코어 수치가 잘못되었습니다')
    if annotation in (str, int, bool):
        if type(value) is annotation:
            return value
        raise NativeAnalysisError('분석 코어 필드 유형이 잘못되었습니다')
    if origin is tuple:
        if not isinstance(value, list):
            raise NativeAnalysisError('분석 코어 목록 형식이 잘못되었습니다')
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(decode(item, args[0]) for item in value)
        if len(value) != len(args):
            raise NativeAnalysisError('분석 코어 목록 길이가 잘못되었습니다')
        return tuple(decode(item, typ) for item, typ in zip(value, args))
    if is_dataclass(annotation):
        if not isinstance(value, dict):
            raise NativeAnalysisError('분석 코어 결과 객체가 잘못되었습니다')
        declared = _dataclass_fields(annotation)
        known = {field.name for field, _ in declared}
        if value.keys() - known:
            raise NativeAnalysisError('분석 코어 응답에 알 수 없는 필드가 있습니다')
        result = {}
        for field, typ in declared:
            if field.name not in value:
                if field.default is MISSING and field.default_factory is MISSING:
                    raise NativeAnalysisError('분석 코어 응답에 필수 필드가 없습니다')
                continue
            result[field.name] = decode(value[field.name], typ)
        return annotation(**result)
    raise NativeAnalysisError('분석 코어 응답 유형을 지원하지 않습니다')


def decode_page(value: dict):
    from src.services.battle_marginal_panel_service import BattleMarginalPanelResult
    from src.services.battle_report_analysis_load_service import BattleReportAnalysisLoadResult

    required = {'analysis', 'target_catalog', 'target_catalog_error',
                'marginal_benefits', 'marginal_panel', 'candidate_display_analysis'}
    if not required <= value.keys() or value.keys() - required - {'derived_snapshot', 'hit_details'}:
        raise NativeAnalysisError('분석 코어 페이지 응답 필드가 일치하지 않습니다')
    catalog = value['target_catalog']
    if catalog is not None and not isinstance(catalog, dict):
        raise NativeAnalysisError('분석 코어 대상 목록이 잘못되었습니다')
    if value['target_catalog_error'] is not None:
        raise NativeAnalysisError('분석 코어 대상 목록 읽기 실패')
    from src.integrations.native_battle_hit_details_wire import decode_hit_details
    analysis = decode(value['analysis'], BattleAnalysisSnapshot | None)
    candidate = decode(value['candidate_display_analysis'], BattleAnalysisSnapshot | None)
    return BattleReportAnalysisLoadResult(
        analysis=analysis,
        target_catalog=catalog,
        marginal_benefits=decode(value['marginal_benefits'], BattleMarginalBenefits | None),
        marginal_panel=decode(value['marginal_panel'], BattleMarginalPanelResult | None),
        candidate_display_analysis=candidate,
        hit_details=decode_hit_details(value.get('hit_details'), analysis, candidate),
    )


def decode_derived_snapshot(value: object, *, battle_record_id: int, dataset_version: str):
    """Validate native persistence metadata without rebuilding its inferred payload."""
    from src.services.battle_inferred_target_condition_service import (
        BattleInferredEncounter, INFERRED_ENCOUNTER_ALGORITHM_VERSION,
    )
    if value is None:
        return None
    string_fields = ('algorithm_version', 'static_dataset_id', 'inference_status',
                     'environment_kind', 'environment_ref', 'environment_name', 'source_kind', 'confidence')
    if (not isinstance(value, dict) or set(value) != {
            'battle_record_id', 'payload_schema_version', 'static_schema_version', 'inferred_payload',
            *string_fields}
            or any(type(value[key]) is not str for key in string_fields)
            or type(value['battle_record_id']) is not int or value['battle_record_id'] != battle_record_id
            or type(value['payload_schema_version']) is not int or value['payload_schema_version'] != 1
            or type(value['static_schema_version']) is not int or value['static_schema_version'] <= 0
            or value['static_dataset_id'] != dataset_version
            or value['algorithm_version'] != INFERRED_ENCOUNTER_ALGORITHM_VERSION
            or value['inference_status'] != 'resolved'):
        raise NativeAnalysisError('분석 코어 파생 스냅샷의 식별 정보 또는 버전이 잘못되었습니다')
    inferred = decode(value['inferred_payload'], BattleInferredEncounter)
    if any(getattr(inferred, key) != value[key] for key in (
            'algorithm_version', 'environment_kind', 'environment_ref', 'environment_name',
            'source_kind', 'confidence')):
        raise NativeAnalysisError('분석 코어 파생 스냅샷 내용이 메타데이터와 일치하지 않습니다')
    return value
