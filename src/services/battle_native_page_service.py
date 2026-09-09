# 冻结页面选择与用户输入并调用数据库直读核心，只还原展示结果。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import asdict, is_dataclass
from pathlib import Path

from src.integrations.native_battle_page_wire import decode_page, decode_derived_snapshot
from src.integrations.nte_analysis_core import NativeAnalysisError
from src.services.battle_analysis_progress import report_battle_analysis_progress
from src.services.battle_page_overall_progress import BattlePageOverallProgress
from src.services.battle_native_page_cache import (
    BattleNativePageCache, file_identity, page_cache_key,
)


_PROGRESS_MESSAGES = {
    'load': '전투 리포트를 읽는 중…',
    'target': '대상을 식별하고 환경을 피팅하는 중…',
    'analyze': '히트와 전투 상태를 재구성하는 중…',
    'buff_remove': 'Buff 제거 이득을 항목별로 계산하는 중',
    'core_baseline': '콘솔 대조 기준선을 만드는 중…',
    'core_candidates': '콘솔 메인 속성 후보를 비교하는 중',
    'fork': '아크 이득을 계산하는 중…',
    'panel': '캐릭터 패널을 계산하는 중…',
    'details': '히트별 상세를 정리하는 중…',
    'serialize': '계산 결과를 전달하는 중…',
}


def _user_input(value):
    if is_dataclass(value):
        return _user_input(asdict(value))
    if isinstance(value, dict):
        return {str(key): _user_input(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_user_input(item) for item in value]
    return value


class BattleNativePageService:
    def __init__(self, *, client, dependencies, semantics_path: Path, context_is_current):
        self._client = client
        self._dependencies = dependencies
        self._semantics_path = Path(semantics_path).resolve()
        self._context_is_current = context_is_current
        self._page_cache = BattleNativePageCache()

    def load_editor_facts(self, battle_record_id):
        from src.integrations.native_battle_editor_facts import load_editor_facts
        return load_editor_facts(self._client, self._dependencies, self._context_is_current, battle_record_id)

    def load(self, request, *, progress_callback=None):
        if self._client is None or not self._client.supports_battle_page:
            raise NativeAnalysisError('전투 리포트 페이지에는 데이터베이스 직접 읽기를 지원하는 분석 구성 요소가 필요합니다. 호환 버전을 배포한 뒤 다시 시도하세요')
        dependencies = self._dependencies
        if dependencies.static_database_path is None:
            raise ValueError('네이티브 전투 리포트 분석에 정적 데이터 경로가 없습니다')
        if (request.static_database_path is not None
                and Path(request.static_database_path).resolve()
                != Path(dependencies.static_database_path).resolve()):
            raise ValueError('전투 리포트 요청의 정적 데이터 컨텍스트가 만료되었습니다')
        cache_enabled = getattr(self._client, 'supports_battle_page_identity', False) is True
        frozen_files = (Path(dependencies.static_database_path).resolve(), self._semantics_path)
        if cache_enabled:
            frozen_files += (Path(self._client.executable).resolve(),)
        def identities():
            return tuple(file_identity(path) for path in frozen_files)
        try:
            frozen_identity = identities()
        except FileNotFoundError:
            raise NativeAnalysisError('전투 리포트 분석 배포 리소스가 없습니다. 완전한 호환 버전을 다시 설치한 뒤 다시 시도하세요') from None
        overall = BattlePageOverallProgress(request)

        def publish(event):
            if progress_callback is not None:
                progress_callback(overall.project(event))

        def checkpoint():
            if not self._context_is_current(dependencies):
                raise CancelledError
            if identities() != frozen_identity:
                raise CancelledError

        checkpoint()
        report_battle_analysis_progress(
            publish, phase='native_page', message='전투 리포트를 읽고 페이지를 계산하는 중…',
        )

        def native_progress(event):
            checkpoint()
            report_battle_analysis_progress(
                publish, phase=event['phase'],
                message=_PROGRESS_MESSAGES[event['phase']],
                completed=event['completed'], total=event['total'],
            )

        # Deliberately list input fields. Cached analyses are rendering state and
        # must never enter the request, even when the request dataclass grows.
        payload = {
            'account_id': dependencies.account_id, 'generation': dependencies.generation,
            'user_database_path': str(Path(dependencies.user_database_path).resolve()),
            'static_database_path': str(Path(dependencies.static_database_path).resolve()),
            'semantics_path': str(self._semantics_path),
            **{key: _user_input(getattr(request, key)) for key in (
                'battle_record_id', 'start_us', 'end_us', 'detail_scope', 'detail_level',
                'marginal_candidate', 'marginal_benefit_candidate',
                'selected_character_id', 'marginal_drive_units')},
        }
        if request.detail_level != 'marginal':
            # Native consumes role selection only when building marginal benefits/panels.
            payload['selected_character_id'] = None
        raw = None
        cache_key = None
        self._page_cache.select_record(request.battle_record_id)
        if cache_enabled:
            input_digest = self._client.load_battle_page_identity(payload, checkpoint=checkpoint)
            checkpoint()
            cache_key = page_cache_key(
                payload, input_digest=input_digest, files=frozen_identity,
                engine_version=self._client.engine_version, dataset_version=self._client.dataset_version,
            )
            raw = self._page_cache.get(cache_key)
            checkpoint()
        cache_hit = raw is not None
        if cache_hit:
            report_battle_analysis_progress(publish, phase='cache', message='완료된 결과를 읽는 중…')
        else:
            raw = self._client.load_battle_page(
                payload, checkpoint=checkpoint,
                progress_callback=native_progress if progress_callback is not None else None,
            )
            checkpoint()
            if cache_enabled:
                after_digest = self._client.load_battle_page_identity(payload, checkpoint=checkpoint)
                checkpoint()
                if after_digest != input_digest:
                    raise CancelledError
        checkpoint()
        report_battle_analysis_progress(
            publish, phase='decode', message='페이지 결과를 정리하는 중…',
        )
        result = decode_page(raw)
        checkpoint()
        snapshot = decode_derived_snapshot(raw.get('derived_snapshot'),
            battle_record_id=request.battle_record_id, dataset_version=self._client.dataset_version)
        if snapshot is not None and not cache_hit:
            from src.services.battle_native_snapshot_persistence import persist_native_snapshot
            persist_native_snapshot(snapshot, dependencies=dependencies, checkpoint=checkpoint)
        checkpoint()
        if cache_key is not None and not cache_hit:
            self._page_cache.put(cache_key, raw)
        checkpoint()
        report_battle_analysis_progress(publish, phase='complete', message='전투 리포트 계산 완료')
        checkpoint()
        return result
