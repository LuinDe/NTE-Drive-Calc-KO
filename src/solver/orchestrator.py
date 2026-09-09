# 统筹库存、评分和求解器生成最终配装。
"""End-to-end pipeline for blueprints, scoring, dispatch, and output."""

import copy
import json
import time
from pathlib import Path
from typing import List, Dict

from src.domain.allocation_rating import loadout_total_grade
from src.integrations.bundled_resources import bundled_config_dir
from src.optimizer.contracts import PLAN_CUSTOM_WEAPON
from src.domain.equipment_normalizer import normalize_equipment_item
from src.models.equipment import DriveShape, Drive, Tape
from src.solver.combinatorics import PuzzleCombinatorics
from src.solver.dfs_puzzle import DFSPuzzleSolver
from src.solver.blueprint_utils import dedupe_blueprints_by_piece_signature
from src.solver.set_effects import normalize_set_effect_mode, set_piece_options_for_mode
from src.optimizer.scoring import ScoringEngine
from src.optimizer.allocation_kernel import AllocationKernel, AllocationKernelRequest, estimate_candidate_pool_limits
from src.utils.visualizer import BoardVisualizer
from src.utils.logger import logger
from src.utils.name_resolver import resolve_name
from src.utils.set_name import normalize_set_display_name
from src.services.legacy_allocation_static_catalog import build_legacy_allocation_static_catalog


class NTEPipelineOrchestrator:
    _blueprint_cache: dict[str, List[Dict]] = {}
    _blueprint_cache_limit = 256

    def __init__(self, config_dir: str | Path | None = None, *, user_database_path=None):
        self.config_dir = str(Path(config_dir) if config_dir is not None else bundled_config_dir())
        self.user_database_path = user_database_path
        self.roles_db = {}
        self.sets_db = {}
        self.shapes_db = {}
        self._board_matrices = {}
        self._load_configs()

    @classmethod
    def from_frozen_inputs(
        cls,
        *,
        roles_db: dict,
        sets_db: dict,
        shapes_db: dict[str, DriveShape],
        config_dir: str | Path | None = None,
    ) -> "NTEPipelineOrchestrator":
        """Create a puzzle-only orchestrator without rereading mutable config.

        Context callers already copied every role, suit and shape input.  This
        named constructor keeps that boundary explicit instead of having an
        adapter bypass ``__init__`` and patch private attributes afterwards.
        """

        instance = cls.__new__(cls)
        instance.config_dir = str(
            Path(config_dir) if config_dir is not None else bundled_config_dir()
        )
        instance.user_database_path = None
        instance.roles_db = roles_db
        instance.sets_db = sets_db
        instance.shapes_db = shapes_db
        instance._board_matrices = {
            role_name: role_data["board_matrix"]
            for role_name, role_data in roles_db.items()
        }
        instance._blueprint_cache = {}
        return instance

    def _load_configs(self):
        catalog = build_legacy_allocation_static_catalog(
            config_dir=self.config_dir,
            user_database_path=self.user_database_path,
        )
        self.roles_db = catalog.roles_db
        self.sets_db = catalog.sets_db
        self.shapes_db = catalog.shapes_db
        self._board_matrices = catalog.board_matrices

    def _resolve_set_name(self, set_name: str) -> str:
        normalized_name = normalize_set_display_name(set_name)
        resolved = resolve_name(normalized_name, self.sets_db.keys(), cutoff=0.78)
        if not resolved:
            available = "、".join(self.sets_db.keys())
            raise ValueError(f"오류: 지정한 세트 {set_name}이(가) 공식 SQLite 데이터에 없습니다! 사용 가능한 세트: {available}")
        return resolved

    def _canonicalize_role_sets(self):
        for role_name, role_data in self.roles_db.items():
            if "default_set" not in role_data:
                continue
            raw_set = role_data["default_set"]
            resolved = self._resolve_set_name(raw_set)
            if resolved != raw_set:
                logger.warning(f"캐릭터 [{role_name}]의 기본 세트 이름을 자동 수정했습니다: {raw_set} -> {resolved}")
                role_data["default_set"] = resolved

    def _canonicalize_custom_sets(self, custom_sets: Dict[str, str] | None) -> Dict[str, str]:
        resolved_sets = {}
        for role_name, set_name in (custom_sets or {}).items():
            if set_name:
                resolved_sets[role_name] = self._resolve_set_name(set_name)
        return resolved_sets

    def solve_blueprints(self, target_roles: List[str], custom_sets: Dict[str, str] = None,
                         set_effect_modes: Dict[str, str] = None,
                         include_layout_variants: bool = False) -> Dict[str, List[Dict]]:
        custom_sets = self._canonicalize_custom_sets(custom_sets)
        set_effect_modes = set_effect_modes or {}
        logger.info(f"\n[2단계] {target_roles}의 유효 보드 청사진을 푸는 중...")
        combinatorics = PuzzleCombinatorics(self.shapes_db)
        dfs_solver = DFSPuzzleSolver(self.shapes_db)
        real_blueprints_db = {}

        for role_name in target_roles:
            role_data = self.roles_db[role_name]
            set_name = self._resolve_set_name(custom_sets.get(role_name, role_data["default_set"]))

            set_shapes = self.sets_db[set_name]["shapes"]
            extra_label = role_data["extra_shape_label"]
            board_matrix = self._board_matrices[role_name]
            set_effect_mode = normalize_set_effect_mode(set_effect_modes.get(role_name))
            set_piece_options = set_piece_options_for_mode(set_shapes, set_effect_mode)
            cache_key = self._blueprint_cache_key(
                role_name,
                set_name,
                set_shapes,
                extra_label,
                board_matrix,
                set_effect_mode,
                include_layout_variants,
            )

            logger.info(f"  -> [{role_name}] 세트: {set_name} | 세트 효과: {set_effect_mode} | 풀이 중...")
            _t0 = time.perf_counter()
            if cache_key in self._blueprint_cache:
                real_blueprints_db[role_name] = copy.deepcopy(self._blueprint_cache[cache_key])
                logger.info(f"  [{role_name}] 청사진 캐시 적중, 유효 방안 총 {len(real_blueprints_db[role_name])}개.")
                continue
            role_blueprints = []

            for set_pieces in set_piece_options:
                combos = combinatorics.generate_piece_combinations(set_pieces, extra_label)
                logger.info(f"     세트 형태: {len(set_pieces)} | 조합 수: {len(combos)} | 소요 시간: {time.perf_counter()-_t0:.2f}s")

                for combo in combos:
                    pieces_to_place = set_pieces + combo
                    board_copy = [row[:] for row in board_matrix]
                    results = []
                    dfs_solver.solve(
                        board_copy,
                        pieces_to_place,
                        results,
                        max_solutions=0 if include_layout_variants else 1,
                    )

                    for result_board in results:
                        role_blueprints.append({
                            "set_pieces": list(set_pieces),
                            "extra_pieces": combo,
                            "set_effect_mode": set_effect_mode,
                            "board": result_board,
                        })

            if not include_layout_variants:
                role_blueprints = dedupe_blueprints_by_piece_signature(role_blueprints)
            real_blueprints_db[role_name] = role_blueprints
            self._remember_blueprint_cache(cache_key, role_blueprints)
            logger.success(f"  [{role_name}] 청사진 풀이 완료, 유효 방안 총 {len(role_blueprints)}개. (총 소요 시간 {time.perf_counter()-_t0:.2f}s)")

        return real_blueprints_db

    def _blueprint_cache_key(
        self,
        role_name: str,
        set_name: str,
        set_shapes: List[str],
        extra_label: str,
        board_matrix: List[List[int]],
        set_effect_mode: str,
        include_layout_variants: bool,
    ) -> str:
        shape_payload = {
            shape_id: {"area": shape.area, "label": shape.label}
            for shape_id, shape in sorted(self.shapes_db.items())
        }
        payload = {
            "role": role_name,
            "set": set_name,
            "set_shapes": list(set_shapes or []),
            "extra_label": extra_label,
            "board_matrix": board_matrix,
            "set_effect_mode": set_effect_mode,
            "include_layout_variants": include_layout_variants,
            "shapes": shape_payload,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _remember_blueprint_cache(self, cache_key: str, blueprints: List[Dict]) -> None:
        if len(self._blueprint_cache) >= self._blueprint_cache_limit:
            self._blueprint_cache.pop(next(iter(self._blueprint_cache)))
        self._blueprint_cache[cache_key] = copy.deepcopy(blueprints)

    def _max_priority_group_size(self, priority_list: List[str], priority_groups: List[List[str]] | None) -> int:
        selected = set(priority_list or [])
        max_size = 1
        for group in priority_groups or []:
            size = len([role for role in group or [] if role in selected])
            max_size = max(max_size, size)
        return max_size

    def run_full_allocation(self, inventory: List[Dict], priority_list: List[str],
                            custom_sets: Dict[str, str] = None, mode: str = "role_priority",
                            locked_uids: set = None, tape_main_filters: Dict[str, List[str]] = None,
                            crit_priority_modes: Dict[str, str] = None, set_effect_modes: Dict[str, str] = None,
                            priority_groups: List[List[str]] = None, crit_rate_caps: Dict[str, float] = None,
                            crit_rate_baselines: Dict[str, float] = None,
                            custom_weapons: Dict[str, str] = None,
                            blueprint_combo_limit: int = 500,
                            cancel_check=None):
        locked_uids = locked_uids or set()
        tape_main_filters = tape_main_filters or {}
        crit_priority_modes = crit_priority_modes or {}
        crit_rate_caps = crit_rate_caps or {}
        crit_rate_baselines = crit_rate_baselines or {}
        set_effect_modes = set_effect_modes or {}
        custom_weapons = custom_weapons or {}
        priority_groups = priority_groups or None
        if mode != "role_priority":
            tape_main_filters = {}
            crit_priority_modes = {}
            crit_rate_caps = {}
        for role_name, value in crit_rate_baselines.items():
            if role_name not in self.roles_db:
                continue
            try:
                fork_crit_rate = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
            self.roles_db[role_name] = {
                **self.roles_db[role_name],
                "fork_crit_rate": fork_crit_rate,
            }
        custom_sets = self._canonicalize_custom_sets(custom_sets)
        total_t0 = time.perf_counter()
        logger.info(f"\n[1단계] 전체 분배 흐름 시작 | 인벤토리: {len(inventory)} | 캐릭터: {priority_list} | 모드: {mode}")
        stage_t0 = time.perf_counter()
        blueprints_db = self.solve_blueprints(priority_list, custom_sets, set_effect_modes)
        logger.info(f"[시간] 청사진 풀이 단계: {time.perf_counter() - stage_t0:.2f}s")

        logger.info(f"\n[3단계] 자산 {len(inventory)}개를 받아 필터와 유형 변환 중...")
        stage_t0 = time.perf_counter()
        parsed_inventory = []
        filtered_count = 0

        for item in inventory:
            item = normalize_equipment_item(item)
            obj = Drive(**item) if item.get("item_type") == "drive" else Tape(**item)

            # Skip equipment already worn by other characters
            if obj.uid in locked_uids:
                filtered_count += 1
                continue

            parsed_inventory.append(obj)

        if locked_uids:
            logger.info(
                f"[모드 4] 잠긴 장비 {filtered_count}개를 제외하고 남은 {len(parsed_inventory)}개로 분배합니다.")
        logger.info(f"[시간] 인벤토리 변환 단계: {time.perf_counter() - stage_t0:.2f}s")

        stage_t0 = time.perf_counter()
        scoring_engine = ScoringEngine(
            config_dir=self.config_dir,
            user_database_path=self.user_database_path,
            roles_db=self.roles_db,
        )
        drive_screen_limit, tape_screen_limit = estimate_candidate_pool_limits(
            blueprints_db, priority_list, priority_groups or (),
        )
        if drive_screen_limit > 15:
            logger.info(f"  후보 드라이브 선별 상한을 현재 캐릭터 수요에 따라 Top {drive_screen_limit}/형태/캐릭터로 올렸습니다.")
        kernel_request = AllocationKernelRequest(
            inventory=tuple(parsed_inventory), roles_db=self.roles_db, sets_db=self.sets_db,
            shapes_db=self.shapes_db, blueprints_db=blueprints_db, role_order=tuple(priority_list),
            strategy=mode, module_set_targets=custom_sets, set_effect_modes=set_effect_modes,
            core_main_filters={key: tuple(value) for key, value in tape_main_filters.items()},
            core_set_targets={}, stat_priority_configs=crit_priority_modes, property_limits={},
            priority_groups=tuple(tuple(group) for group in (priority_groups or ())),
            crit_rate_caps=crit_rate_caps, drive_screen_limit=drive_screen_limit,
            tape_screen_limit=tape_screen_limit,
            # 缺少卡带只表示本次推荐没有核心，不能否定已经完整填满图纸的驱动方案。
            # 自动/极速装配仍会拒绝缺少核心的保存方案。
            allow_missing_core=True,
            blueprint_combo_limit=int(blueprint_combo_limit),
            cancel_check=cancel_check,
        )
        if tape_main_filters:
            logger.info("  캐릭터 우선순위 설정에 따라 카트리지 메인 스탯을 미리 필터했습니다.")

        logger.success("  선별 입력 준비 완료.")
        logger.info(f"     - 카트리지 분류: 캐릭터별 세트마다 Top {tape_screen_limit} 고정")
        logger.info(f"[시간] 점수 선별 단계: {time.perf_counter() - stage_t0:.2f}s")

        logger.info(f"\n[4단계] 스케줄 모드 시작: [{mode}]...")
        stage_t0 = time.perf_counter()
        final_plan = AllocationKernel(scoring_engine).execute(kernel_request)
        logger.info(f"[시간] 스케줄 단계: {time.perf_counter() - stage_t0:.2f}s")

        stage_t0 = time.perf_counter()
        self._render_results(final_plan, scoring_engine, custom_sets)
        logger.info(f"[시간] 로그 렌더링 단계: {time.perf_counter() - stage_t0:.2f}s")
        for role_name, plan in final_plan.items():
            if isinstance(plan, dict) and custom_weapons.get(role_name):
                plan[PLAN_CUSTOM_WEAPON] = custom_weapons[role_name]
        logger.info(f"[시간] 전체 분배 흐름 총 소요 시간: {time.perf_counter() - total_t0:.2f}s")

        return final_plan

    def _render_results(self, final_plan: Dict, scoring_engine: ScoringEngine, custom_sets: Dict[str, str]):
        custom_sets = custom_sets or {}
        for role, plan in final_plan.items():
            if not plan or not plan.get("valid", True):
                logger.error(f"캐릭터 [{role}] 분배 실패: 유효한 청사진을 맞출 수 없습니다.\n")
                continue

            grade = loadout_total_grade(plan['score'])
            used_set = custom_sets.get(role, self.roles_db[role]["default_set"])

            BoardVisualizer.display_final_plan(role_name=role, plan=plan, default_set=used_set, grade=grade)

            logger.opt(raw=True).info("  [카트리지 분배]\n")
            assigned_tape: Tape = plan.get("assigned_tape")
            if assigned_tape:
                t_score = assigned_tape.role_scores.get(role, 0.0)
                t_grade = scoring_engine.get_grade_tag(t_score, area=15)
                logger.opt(raw=True).info(f"     - {assigned_tape.set_name.ljust(8)} | "
                                          f"등급:[{t_grade.ljust(3)}] |"
                                          f"총점:{str(t_score).ljust(6)} |"
                                          f"품질:{assigned_tape.quality.ljust(5)} |\n"
                                          f"       메인 스탯: {assigned_tape.main_stats}\n"
                                          f"       서브 스탯: {assigned_tape.sub_stats}\n")
            else:
                logger.warning("     - 이 캐릭터에게 유효한 카트리지를 분배하지 못했습니다.")

            for category, key in [("\n  [세트 효과 드라이브]\n", 'assigned_set_drives'),
                                  ("  [추가 단품]\n", 'assigned_extra_drives')]:
                logger.opt(raw=True).info(f"{category}")
                for d in plan.get(key, []):
                    score = d.role_scores.get(role, 0.0)
                    d_grade = scoring_engine.get_grade_tag(score, d.area)

                    mvp_tag = f" [선선택: {d.pick_order}순위]" if d.is_mvp else ""

                    logger.opt(raw=True).info(f"     - {d.shape_id.ljust(10)} | "
                                              f"등급:[{d_grade.ljust(3)}] |"
                                              f"점수:{str(score).ljust(5)} |"
                                              f"품질:{d.quality.ljust(5)} |"
                                              f"수치:{d.sub_stats}{mvp_tag}\n")
            logger.opt(raw=True).info("\n" + "=" * 60 + "\n")
