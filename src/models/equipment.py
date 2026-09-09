# 定义驱动盘和音擎的数据模型。
"""Typed equipment models shared by scanning, scoring, and allocation."""

from pydantic import BaseModel, Field, model_validator
from typing import Dict, Literal, List


class DriveShape(BaseModel):
    shape_id: str
    label: str
    matrix: List[List[int]]
    area: int
    description: str = ""

    @model_validator(mode='after')
    def validate_matrix_and_area(self):
        if self.shape_id == "TAPE_15":
            return self

        calculated_area = sum(cell for row in self.matrix for cell in row)
        if calculated_area != self.area:
            raise ValueError(f"형태 {self.shape_id} 데이터 이상: 지정 면적은 {self.area}이지만 실제 행렬 면적은 {calculated_area}입니다")
        return self


class BaseEquipment(BaseModel):
    uid: str
    item_type: Literal["drive", "tape"]
    # 官方静态物品 ID。旧来源可以没有它；SQLite 快照投影会保留它，
    # 以便配装结果复用 game_ui 中对应的卡带/驱动图像。
    item_id: str = ""
    quality: Literal["Gold", "Purple", "Blue"]
    area: int
    sub_stats: Dict[str, float] = Field(default_factory=dict)
    # 官方背包的弃置状态只用于展示；弃置装备仍可参与方案计算。
    discarded: bool = False
    # 从固定背包快照导入的派生标记：游戏筛选器无法区分的重复驱动。
    is_duplicate_drive: bool = False
    duplicate_group_id: str | None = None
    duplicate_index: int | None = None
    duplicate_count: int | None = None

    role_scores: Dict[str, float] = Field(default_factory=dict)
    max_score: float = Field(default=0.0)
    is_mvp: bool = Field(default=False)
    pick_order: int = Field(default=0)


class Drive(BaseEquipment):
    item_type: Literal["drive"] = "drive"
    shape_id: str
    set_name: str = "未知套装"
    main_stats: Dict[str, float] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_drive_rules(self):
        if len(self.main_stats) != 2:
            raise ValueError(f"드라이브 {self.uid}는 메인 스탯을 정확히 2개 포함해야 합니다")
        if len(self.sub_stats) > 4:
            raise ValueError(f"드라이브 {self.uid}의 서브 스탯은 4개를 넘을 수 없습니다")
        if self.area not in [1, 2, 3, 4]:
            raise ValueError(f"드라이브 {self.uid} 면적 이상: 물리 드라이브 면적은 1~4만 가능합니다")
        return self


class Tape(BaseEquipment):
    item_type: Literal["tape"] = "tape"
    shape_id: str = "TAPE_15"
    # Official suit identity used by allocation.  Legacy OCR/test objects may
    # omit it and use the normalized display-name compatibility path.
    suit_id: str | None = None
    set_name: str
    main_stats: str
    # Exact snapshot main-stat value, in the same display unit as ``sub_stats``.
    # Vision snapshots synthesize it from the shared max-level stat catalogue.
    main_value: float | None = None

    # Tape main_stats remains a plain text label; ``main_value`` carries its value.
    @model_validator(mode='after')
    def validate_cartridge_rules(self):
        if self.area != 15:
            raise ValueError(f"카트리지 {self.uid}의 곱연산 구간은 15칸 등가로 고정되어야 합니다")
        return self
