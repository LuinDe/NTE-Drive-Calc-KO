# 定义后台线程工作器，避免耗时任务阻塞界面。
"""Worker thread classes shared by feature controllers."""

from __future__ import annotations

import traceback as tb
import threading
import time
from concurrent.futures import CancelledError

from PySide6.QtCore import QThread, Signal

from src.scanner.drone_scanner import DroneScanner
from src.scanner.batch_processor import BatchProcessor
from src.utils.logger import logger
from src.utils.perf import log_perf


def _close_scanner(scanner):
    if scanner is None or not hasattr(scanner, "close"):
        return
    try:
        scanner.close()
    except Exception as exc:
        logger.warning(f"가상 게임패드 해제 실패: {exc}")


class WorkerThread(QThread):
    result_ready = Signal(object)
    error = Signal(str)
    progress = Signal(object)

    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.target = target

    def run(self):
        try:
            self.result_ready.emit(self.target())
        except CancelledError:
            self.error.emit("작업이 취소되었습니다")
        except SystemExit as exc:
            logger.error(f"WorkerThread가 SystemExit을 포착했습니다: {exc}")
            self.error.emit(f"시스템 비정상 종료: {exc}")
        except Exception as exc:
            err_detail = f"{exc}\n\n{tb.format_exc()}"
            logger.error(f"WorkerThread 예외: {err_detail}")
            self.error.emit(str(exc))


class ScanWorkerThread(QThread):
    scan_done = Signal(int)
    error = Signal(str)
    scanner_ready = Signal()

    def __init__(self, *, output_dir, template_path, mode="semi", parent=None):
        super().__init__(parent)
        self.output_dir = str(output_dir)
        self.template_path = str(template_path)
        self.mode = mode
        self.scanner = None

    def run(self):
        try:
            self.scanner = DroneScanner(
                output_dir=self.output_dir,
                template_path=self.template_path,
            )
            self.scanner_ready.emit()
            if self.mode == "auto":
                count = self.scanner.start_scan()
            else:
                count = self.scanner.start_semi_auto_scan()
            self.scan_done.emit(count)
        except Exception as exc:
            logger.error(f"ScanWorker 예외: {exc}")
            self.error.emit(str(exc))


class FullVisualScanParseWorkerThread(QThread):
    processing_done = Signal(dict)
    error = Signal(str)
    scanner_ready = Signal()
    scan_done = Signal(int, int)
    parse_done = Signal()
    post_actions_ready = Signal()
    progress = Signal(int, int, str)

    def __init__(
        self,
        total_drives,
        *,
        screenshot_dir,
        config_dir,
        user_database_path,
        parent=None,
        post_actions_config=None,
        selected_roles=None,
        parse_during_scan=True,
        amd_compatibility=False,
        capture_driver="mouse",
        result_is_current=None,
    ):
        super().__init__(parent)
        self.total_drives = total_drives
        self.screenshot_dir = str(screenshot_dir)
        self.config_dir = str(config_dir)
        self.user_database_path = user_database_path
        self.post_actions_config = post_actions_config
        self.selected_roles = list(selected_roles or [])
        self.amd_compatibility = bool(amd_compatibility)
        self.capture_driver = str(capture_driver or "mouse").strip().casefold()
        if self.capture_driver not in {"mouse", "gamepad"}:
            raise ValueError(f"지원하지 않는 전체 스캔 드라이버: {capture_driver}")
        self.mouse_compatibility_mode = (
            self.capture_driver == "mouse" and self.amd_compatibility
        )
        self.mouse_input_profile = (
            "compatibility-low-load-v1"
            if self.mouse_compatibility_mode
            else "standard-low-load-v1"
            if self.capture_driver == "mouse"
            else "one-point-five-trial-v1"
        )
        # Normal mouse scans honor the dual-thread option just like the
        # gamepad route.  Compatibility mode deliberately remains serial and
        # low-load for machines that need the fallback.
        self.capture_queue_maxsize = 7 if self.mouse_compatibility_mode else 21
        self.parse_during_scan = (
            bool(parse_during_scan) and not self.mouse_compatibility_mode
        )
        self.low_load_mode = (
            self.capture_driver == "mouse" and not self.parse_during_scan
        )
        self.low_load_parse_delay_seconds = (
            0.20 if self.mouse_compatibility_mode else 0.12
        )
        self.result_is_current = result_is_current
        self.scanner = None
        self._post_actions_ready_event = threading.Event()

    def acknowledge_post_actions_ready(self):
        self._post_actions_ready_event.set()

    def _notify_post_actions_ready(self):
        self._post_actions_ready_event.clear()
        self.post_actions_ready.emit()
        if not self._post_actions_ready_event.wait(timeout=5.0):
            logger.warning("스캔 후 관리 화면 전환 확인 대기가 시간 초과되어 상태 동기화를 계속 진행합니다.")
        time.sleep(2.0)

    def run(self):
        worker_start = time.perf_counter()
        try:
            from src.services.streaming_scan_service import run_streaming_scan_parse
            if self.capture_driver == "mouse":
                from src.integrations.vision.mouse_inventory_scan import MouseInventoryScanner
                from src.integrations.vision.mouse_scan_runtime import require_mouse_scan_runtime

                require_mouse_scan_runtime()
                self.scanner = MouseInventoryScanner(
                    output_dir=self.screenshot_dir,
                    input_speed_profile=self.mouse_input_profile,
                )
            else:
                from src.scanner.gamepad_controller import GamepadScanner

                self.scanner = GamepadScanner(output_dir=self.screenshot_dir)
            self.scanner_ready.emit()
            init_start = time.perf_counter()
            processor = BatchProcessor(
                input_dir=self.screenshot_dir,
                config_dir=self.config_dir,
                replace_output=True,
                ocr_backend_preference=(
                    "amd_compat"
                    if self.mouse_compatibility_mode
                    else "low_load"
                    if self.low_load_mode
                    else "openvino"
                ),
            )
            init_ms = (time.perf_counter() - init_start) * 1000.0
            log_perf(
                logger,
                "vision.processor_init",
                elapsed_ms=init_ms,
                scope="full",
                replace_output=1,
                streaming=int(self.parse_during_scan),
                amd_compat=int(self.amd_compatibility),
            )
            stats = run_streaming_scan_parse(
                self.scanner,
                processor,
                self.total_drives,
                progress_callback=lambda current, total, filename: self.progress.emit(current, total, filename),
                cancel_check=lambda: bool(getattr(self.scanner, "_stopped", False)),
                scan_done_callback=lambda captured, total: self.scan_done.emit(captured, total),
                parse_done_callback=lambda: self.parse_done.emit(),
                post_action_ready_callback=self._notify_post_actions_ready,
                post_actions_config=self.post_actions_config,
                selected_roles=self.selected_roles,
                config_dir=self.config_dir,
                user_database_path=self.user_database_path,
                parse_during_scan=self.parse_during_scan,
                low_load_mode=self.low_load_mode,
                low_load_parse_delay_seconds=self.low_load_parse_delay_seconds,
                capture_queue_maxsize=self.capture_queue_maxsize,
                allow_post_actions=True,
                result_is_current=self.result_is_current,
            )
            if stats.get("discarded_stale"):
                self.processing_done.emit(stats)
                return
            if int(stats.get("total_count", 0) or 0) != int(self.total_drives):
                raise RuntimeError("전체 스캔이 완전히 끝나지 않아 파이프라인 분석 결과를 인벤토리에 기록하지 않았습니다.")
            stats["vision_items"] = [item.model_dump() for item in processor.inventory]
            stats["capture_driver"] = self.capture_driver
            if self.capture_driver == "mouse":
                stats["mouse_input_profile"] = self.mouse_input_profile
            del processor
            log_perf(
                logger,
                "vision.total",
                elapsed_ms=(time.perf_counter() - worker_start) * 1000.0,
                scope="full",
                total=stats.get("total_count", 0),
                success=stats.get("success_count", 0),
                duplicate=stats.get("duplicate_count", 0),
                failed=stats.get("failed_count", 0),
                streaming=int(self.parse_during_scan),
                amd_compat=int(self.amd_compatibility),
            )
            self.processing_done.emit(stats)
        except (FileNotFoundError, OSError) as exc:
            logger.error(f"FullVisualScanParseWorker 시스템 의존성 오류: {exc}")
            if self.capture_driver == "gamepad":
                self.error.emit(
                    "ViGEmClient.dll 로드 실패. 다음을 확인하세요:\n"
                    "1. ViGEmBus 드라이버가 설치되어 있는지 (https://github.com/nefarius/ViGEmBus/releases)\n"
                    f"2. PC를 재시작한 뒤 다시 시도\n\n원본 오류: {exc}"
                )
            else:
                self.error.emit(str(exc))
        except Exception as exc:
            err_detail = f"{exc}\n\n{tb.format_exc()}"
            logger.error(f"FullVisualScanParseWorker 예외: {err_detail}")
            self.error.emit(str(exc))
        finally:
            _close_scanner(self.scanner)


class GamepadScanParseWorkerThread(FullVisualScanParseWorkerThread):
    """Compatibility wrapper preserving the legacy gamepad default."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("capture_driver", "gamepad")
        super().__init__(*args, **kwargs)
