# 调用原生窄编辑事实入口，只传冻结身份和库路径，不读取或分析逐击。
from concurrent.futures import CancelledError
from pathlib import Path

from src.domain.battle_report import BattleInferredCharacterFact
from src.integrations.native_battle_page_wire import decode
from src.integrations.nte_analysis_core import NativeAnalysisError


def load_editor_facts(client, dependencies, context_is_current, battle_record_id):
    if client is None or not client.supports_battle_page:
        raise NativeAnalysisError('전투 리포트 편집 사실에는 짝을 이루는 데이터베이스 직접 읽기 분석 구성 요소가 필요합니다')

    def checkpoint():
        if not context_is_current(dependencies):
            raise CancelledError

    checkpoint()
    raw = client.load_battle_page({
        'account_id': dependencies.account_id, 'generation': dependencies.generation,
        'user_database_path': str(Path(dependencies.user_database_path).resolve()),
        'battle_record_id': battle_record_id, 'detail_level': 'editor',
    }, checkpoint=checkpoint)
    checkpoint()
    if not isinstance(raw, dict) or set(raw) != {'editor_facts'}:
        raise NativeAnalysisError('분석 코어 편집 사실 응답 필드가 일치하지 않습니다')
    value = raw['editor_facts']
    if not isinstance(value, dict) or set(value) != {'inferred_character_facts', 'character_analysis_scopes'}:
        raise NativeAnalysisError('분석 코어 편집 사실 형식이 잘못되었습니다')
    facts = decode(value['inferred_character_facts'], tuple[BattleInferredCharacterFact, ...])
    scopes = {}
    if not isinstance(value['character_analysis_scopes'], list):
        raise NativeAnalysisError('분석 코어 캐릭터 하프 소속 형식이 잘못되었습니다')
    for row in value['character_analysis_scopes']:
        if (not isinstance(row, dict) or set(row) != {'character_id', 'scope'}
                or type(row['character_id']) is not int or row['character_id'] in scopes
                or row['scope'] not in (None, 'first', 'second')):
            raise NativeAnalysisError('분석 코어 캐릭터 하프 소속이 잘못되었습니다')
        scopes[row['character_id']] = row['scope']
    return facts, scopes
