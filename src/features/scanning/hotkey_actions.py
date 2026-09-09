# 将全局热键动作投影为当前扫描 worker 的停止、截图和完成标记。
"""Scanning-specific actions invoked by the shared global hotkey manager."""

from __future__ import annotations

from src.utils.logger import logger


def on_hotkey_stop(owner) -> None:
    worker = (
        getattr(owner, "_scan_worker", None)
        or getattr(owner, "_gamepad_worker", None)
        or getattr(owner, "_gamepad_pipeline_worker", None)
    )
    if worker and worker.scanner:
        logger.warning(
            "중지 단축키 {}를 받았습니다. 현재 스캔/상태 동기화 작업을 중지할 준비를 합니다.",
            owner._hotkey_manager.configuration.stop,
        )
        if hasattr(worker.scanner, "emergency_stop"):
            worker.scanner.emergency_stop()
        else:
            worker.scanner._stopped = True
        worker.scanner._finish_flag = True
    else:
        logger.warning(
            "중지 단축키 {}를 받았지만 현재 중지할 스캐너가 없습니다.",
            owner._hotkey_manager.configuration.stop,
        )


def on_hotkey_capture(owner) -> None:
    worker = getattr(owner, "_scan_worker", None)
    if worker and worker.scanner:
        worker.scanner._capture_flag = True


def on_hotkey_finish(owner) -> None:
    worker = getattr(owner, "_scan_worker", None)
    if worker and worker.scanner:
        worker.scanner._finish_flag = True
