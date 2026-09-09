# 通过鼠标热键执行半自动装备截图。
"""Incremental scanner for screenshot-driven inventory capture."""

import time
import os
import math
import ctypes
import ctypes.wintypes
from pathlib import Path
import mss
import mss.tools
import cv2
import numpy as np

from src.utils.logger import logger
from src.utils.image_io import imread_unicode
from src.scanner.window_capture import capture_foreground_window, fit_content_rect, get_foreground_client_rect

# SendInput 结构定义
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("mi", MOUSEINPUT),
    ]


def _send_input(flags, dx=0, dy=0):
    mi = MOUSEINPUT(dx, dy, 0, flags, 0, None)
    inp = INPUT(INPUT_MOUSE, mi)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def load_new_tag_template(template_path):
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"템플릿 파일 {path}을(를) 찾을 수 없습니다. 먼저 NEW 태그 이미지를 캡처하세요.")
    template = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
    if template is None:
        size = path.stat().st_size if path.exists() else 0
        raise ValueError(f"NEW 태그 템플릿이 손상되었거나 읽을 수 없습니다: {path} (size={size})")
    return template


class DroneScanner:
    """
    增量视觉扫描器：捕捉 'NEW' 标签，使用键鼠操作逐一点击并截图。
    """

    BASE_WIDTH = 2560
    BASE_HEIGHT = 1440

    def __init__(self, output_dir="scanned_images", template_path="config/templates/new_tag.png"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self._stopped = False
        self._capture_flag = False
        self._finish_flag = False

        sm = ctypes.windll.user32.GetSystemMetrics
        self._screen_w = sm(0)
        self._screen_h = sm(1)
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._content_left = 0
        self._content_top = 0
        self._window_rect = get_foreground_client_rect()

        self._raw_template = load_new_tag_template(template_path)
        self.template = self._raw_template
        self.template_w, self.template_h = self.template.shape[::-1]

        self.confidence_threshold = 0.85

        logger.info("스캐너 초기화 완료, 템플릿을 불러왔습니다.")

    def _apply_window_rect(self, rect):
        self._window_rect = rect
        target_aspect = rect.width / max(1, rect.height)
        base_aspect = self.BASE_WIDTH / self.BASE_HEIGHT
        if target_aspect < base_aspect - 0.02:
            content_w = rect.width
            content_h = round(content_w / base_aspect)
            self._content_left, self._content_top = 0, 0
            logger.debug(f"16:10/좁은 화면 클라이언트 영역을 상단 16:9 모드로 매핑: {rect.width}x{rect.height}")
        else:
            self._content_left, self._content_top, content_w, content_h = fit_content_rect(rect.width, rect.height, (self.BASE_WIDTH, self.BASE_HEIGHT))
        self._scale_x = content_w / self.BASE_WIDTH
        self._scale_y = content_h / self.BASE_HEIGHT
        new_w = max(1, int(self._raw_template.shape[1] * self._scale_x))
        new_h = max(1, int(self._raw_template.shape[0] * self._scale_y))
        if self.template.shape[1] != new_w or self.template.shape[0] != new_h:
            self.template = cv2.resize(self._raw_template, (new_w, new_h), interpolation=cv2.INTER_AREA)
            self.template_w, self.template_h = self.template.shape[::-1]

    def _capture_window_png(self, sct, filename):
        screenshot, rect = capture_foreground_window(sct)
        self._apply_window_rect(rect)
        mss.tools.to_png(screenshot.rgb, screenshot.size, output=filename)
        return screenshot

    def _base_to_window(self, x, y):
        return (
            self._content_left + int(x * self._scale_x),
            self._content_top + int(y * self._scale_y),
        )

    def _is_bottom_reached(self, img_gray: np.ndarray) -> bool:
        """检测滚动条区域是否被滑块覆盖，判断是否已到底部"""
        probe_x, probe_y = self._base_to_window(1707, 1232)
        probe_region = img_gray[probe_y:probe_y + 2, probe_x:probe_x + 2]
        mean_brightness = np.mean(probe_region)
        is_bottom = mean_brightness > 100
        return is_bottom

    def emergency_stop(self):
        logger.error("\n" + "!" * 50)
        logger.error("전역 중지 키 명령을 받아 긴급 중지했습니다")
        logger.error("!" * 50)
        self._stopped = True

    def _find_new_tags(self, screen_gray: np.ndarray) -> list:
        """寻找屏幕上所有 NEW 标签坐标"""
        res = cv2.matchTemplate(screen_gray, self.template, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= self.confidence_threshold)

        raw_points = list(zip(*loc[::-1]))

        nms_dist = 20 * self._scale_x
        filtered_points = []
        for p in raw_points:
            if not any(math.dist(p, fp) < nms_dist for fp in filtered_points):
                filtered_points.append(p)

        return filtered_points

    def _abs_coord(self, x, y):
        """将像素坐标转换为 SendInput 归一化坐标 (0-65535)"""
        return int(x * 65535 / self._screen_w), int(y * 65535 / self._screen_h)

    def _move_to(self, x, y):
        """移动鼠标到绝对像素坐标"""
        ax, ay = self._abs_coord(x, y)
        _send_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay)

    def _click_at(self, x, y):
        """在窗口内指定像素坐标点击"""
        rect = self._window_rect
        self._move_to(rect.left + x, rect.top + y)
        time.sleep(0.05)
        _send_input(MOUSEEVENTF_LEFTDOWN)
        time.sleep(0.02)
        _send_input(MOUSEEVENTF_LEFTUP)

    def _swipe_up(self):
        """通过 SendInput 模拟长按拖拽翻页（兼容全屏游戏）"""
        logger.info("현재 화면이 비어 스와이프로 페이지를 넘깁니다...")

        rect = self._window_rect
        start_x = rect.left + rect.width // 2
        _, window_y = self._base_to_window(0, 1300)
        start_y = rect.top + min(rect.height - 5, max(5, window_y))

        self._move_to(start_x, start_y)
        time.sleep(0.15)

        _send_input(MOUSEEVENTF_LEFTDOWN)
        time.sleep(0.3)

        total_dy = int(-1000 * self._scale_y)
        steps = 50
        step_dy = int(total_dy / steps)

        for _ in range(steps):
            _send_input(MOUSEEVENTF_MOVE, 0, step_dy)
            time.sleep(0.012)

        time.sleep(0.3)
        _send_input(MOUSEEVENTF_LEFTUP)
        time.sleep(0.3)

    def start_scan(self):
        """启动自动巡航扫描"""
        logger.warning("\n" + "=" * 50)
        logger.warning("3초 후 스캔을 시작합니다. 게임으로 돌아가 드라이브 가방을 여세요")
        logger.warning("=" * 50)
        time.sleep(3)

        probe_captured = 0
        total_captured = 0
        page = 1

        with mss.MSS() as sct:
            first_filename = os.path.join(self.output_dir, "raw_drive_probe_0000.png")
            self._capture_window_png(sct, first_filename)
            probe_captured = 1
            logger.success("첫 번째 NEW 장비를 놓치지 않도록 현재 첫 드라이브를 먼저 캡처했습니다.")

            while not self._stopped:
                logger.info(f"{page}페이지 스캔 중...")

                while not self._stopped:
                    screenshot, rect = capture_foreground_window(sct)
                    self._apply_window_rect(rect)
                    img_bgra = np.array(screenshot)
                    img_gray = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2GRAY)

                    targets = self._find_new_tags(img_gray)

                    if not targets:
                        break

                    logger.info(f"대상 {len(targets)}개 발견, 하나씩 클릭을 시작합니다")

                    for (x, y) in targets:
                        if self._stopped:
                            break
                        target_x = x + self.template_w // 2
                        target_y = y + self.template_h // 2
                        self._click_at(target_x, target_y)

                        time.sleep(0.3)

                        total_captured += 1
                        filename = os.path.join(self.output_dir, f"raw_drive_new_{total_captured:04d}.png")
                        self._capture_window_png(sct, filename)
                        logger.success(f"캡처 성공: raw_drive_new_{total_captured:04d}.png")

                        time.sleep(0.05)

                if self._stopped:
                    break

                if self._is_bottom_reached(img_gray):
                    logger.success("맨 아래에 도달해 스캔을 종료합니다")
                    break

                self._swipe_up()
                page += 1

        logger.info(f"스캔 작업 완료, 새 자산 {total_captured}개를 캡처했습니다.")
        return probe_captured + total_captured

    def start_semi_auto_scan(self):
        """半自动模式：手动点击装备后按 F9 截图，F10 结束"""
        logger.warning("\n" + "=" * 50)
        logger.warning("반자동 모드가 시작되었습니다")
        logger.warning("게임으로 돌아가 등록할 NEW 장비를 직접 클릭하세요.")
        logger.warning("상세 패널이 뜨면 F9로 캡처하고, 등록이 끝나면 F10으로 종료하세요.")
        logger.warning("=" * 50)

        captured_count = 0
        with mss.MSS() as sct:
            while not self._stopped:
                if self._capture_flag:
                    self._capture_flag = False
                    captured_count += 1
                    filename = os.path.join(self.output_dir, f"raw_drive_semi_{captured_count:04d}.png")
                    self._capture_window_png(sct, filename)

                    logger.success(f"캡처 성공: raw_drive_semi_{captured_count:04d}.png")

                    time.sleep(0.3)

                if self._finish_flag:
                    logger.info("\n종료 명령을 받아 반자동 작업을 종료합니다")
                    break

                time.sleep(0.05)

        logger.info(f"이번에 새 자산 {captured_count}개를 캡처했습니다.")
        return captured_count
