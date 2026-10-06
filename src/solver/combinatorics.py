# 生成拼图填充和装备组合候选。
"""Shape combination generation used before board placement solving."""

from concurrent.futures import CancelledError
from typing import Callable, List, Dict
from src.models.equipment import DriveShape

class PuzzleCombinatorics:
    def __init__(self, shapes_db: Dict[str, DriveShape]):
        self.shapes_db = shapes_db
        # Exclude virtual tape shape from physical puzzle
        self.shape_list = [shape for shape in shapes_db.values() if shape.shape_id != "TAPE_15"]

    def generate_piece_combinations(
        self,
        set_shapes: List[str],
        extra_label: str,
        *,
        only_max_extra: bool = True,
        cancel_check: Callable[[], bool] | None = None,
    ) -> List[List[str]]:
        """生成填满底盘的驱动组合。

        四件套、无效果只返回理论最大额外形状件数；二件套可保留
        较低件数层，供后续按真实库存和暴击约束回退。
        """
        set_area = sum(self.shapes_db[shape_id].area for shape_id in set_shapes)
        remain_area = 20 - set_area

        if remain_area < 0:
            raise ValueError(f"세트 총 면적({set_area}칸)이 보드 상한(20칸)을 넘었습니다!")
        if remain_area == 0:
            return [[]]

        all_valid_combos = []

        def find_combinations(target_area: int, current_combo: List[str], start_idx: int):
            if cancel_check is not None and cancel_check():
                raise CancelledError("분배 청사진 조합이 취소되었습니다")
            if target_area == 0:
                all_valid_combos.append(list(current_combo))
                return
            for i in range(start_idx, len(self.shape_list)):
                shape = self.shape_list[i]
                if target_area - shape.area >= 0:
                    current_combo.append(shape.shape_id)
                    find_combinations(target_area - shape.area, current_combo, i)
                    current_combo.pop()

        find_combinations(remain_area, [], 0)

        if not all_valid_combos:
            raise ValueError(f"기존 형태 라이브러리로는 정확히 {remain_area}칸인 단품 조합을 만들 수 없습니다!")

        combo_scores = []
        for combo in all_valid_combos:
            extra_count = sum(1 for shape_id in combo if self.shapes_db[shape_id].label == extra_label)
            combo_scores.append((extra_count, combo))

        max_extra_count = max(score[0] for score in combo_scores)
        best_combos = [
            combo for count, combo in combo_scores
            if not only_max_extra or count == max_extra_count
        ]
        best_combos.sort(key=lambda combo: (
            -sum(self.shapes_db[shape].label == extra_label for shape in combo),
            len(combo), combo,
        ))

        return best_combos
