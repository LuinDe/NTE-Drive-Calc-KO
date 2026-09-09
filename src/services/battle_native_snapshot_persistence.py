# 校验后的原生派生目标快照由既有 DAO 尽力保存，不运行目标推断。
from concurrent.futures import CancelledError

from src.integrations.nte_analysis_core import NativeAnalysisCancelled
from src.observability import OperationContext
from src.observability.operation import log_event, safe_exception
from src.storage.sqlite.user_data_dao import UserDataDao


def persist_native_snapshot(snapshot, *, dependencies, checkpoint):
    checkpoint()
    try:
        with UserDataDao(dependencies.user_database_path, account_id=dependencies.account_id,
                         account_name=dependencies.account_id) as dao:
            checkpoint()
            old = dao.load_battle_inferred_target_snapshot(snapshot['battle_record_id'])
            nullable_labels = {'environment_kind', 'environment_ref', 'environment_name', 'source_kind', 'confidence'}
            if old is not None and all(
                old.get(key) == (value.strip() or None if key in nullable_labels else value)
                for key, value in snapshot.items()
            ):
                return
            # A user confirmation made while calculation ran owns the target.
            if dao.load_battle_target_condition(snapshot['battle_record_id']) is not None:
                return
            checkpoint()
            dao.save_battle_inferred_target_snapshot(**snapshot)
    except (CancelledError, NativeAnalysisCancelled):
        raise
    except Exception as error:
        log_event('WARNING', 'battle_report.inferred_target_snapshot_save_failed',
                  '전투 리포트 자동 대상 추론 스냅샷 저장 실패',
                  OperationContext.create('battle_report', account_id=dependencies.account_id,
                                          context_generation=dependencies.generation),
                  phase='failed', battle_record_id=snapshot['battle_record_id'], error=safe_exception(error))
