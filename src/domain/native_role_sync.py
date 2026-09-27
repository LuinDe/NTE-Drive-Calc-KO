# 定义角色稀疏同步的实际保存数量与未采用字段说明。
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NativeRoleSyncResult:
    saved_count: int
    warnings: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        summary = (f'캐릭터 {self.saved_count}명의 확인된 필드를 동기화했습니다.' if self.saved_count
                   else '이번에는 새 캐릭터 상태를 기록하지 않았습니다.')
        if self.warnings:
            return summary + '\n다음 필드는 동기화되지 않아 기존 설정을 유지합니다:\n' + '\n'.join(self.warnings)
        return summary + '나머지 육성 설정은 기존 값을 유지합니다.'
