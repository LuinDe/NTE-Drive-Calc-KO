# 初始化 OCR 后端并识别截图文本。
"""OCR backend selection and text extraction for equipment screenshots."""

import sys
import os
import subprocess
import cv2
import numpy as np
from src.utils.logger import logger
from src.utils.exceptions import OCRParseError

import logging

logging.getLogger("root").setLevel(logging.ERROR)

def _register_runtime_dll_paths() -> None:
    """Register bundled native runtime paths before OCR packages are imported."""
    if sys.platform != "win32":
        return

    base_paths = []
    if getattr(sys, "frozen", False):
        base_paths.append(getattr(sys, "_MEIPASS", ""))
        base_paths.append(os.path.dirname(sys.executable))

    for base in [p for p in base_paths if p]:
        for rel in ("openvino\\libs", "onnxruntime\\capi"):
            dll_dir = os.path.join(base, rel)
            if not os.path.isdir(dll_dir):
                continue
            try:
                os.add_dll_directory(dll_dir)
            except Exception as exc:
                logger.debug(f"DLL 검색 경로 등록 실패 {dll_dir}: {exc}")
            os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")
            if rel == "openvino\\libs":
                os.environ["OPENVINO_LIB_PATHS"] = dll_dir


_register_runtime_dll_paths()


def _get_video_adapter_names() -> list[str]:
    if sys.platform != "win32":
        return []

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    commands = [
        ["wmic", "path", "win32_VideoController", "get", "Name"],
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
        ],
    ]
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=3,
                creationflags=creationflags,
            )
            if result.returncode != 0:
                continue
            names = [
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip() and line.strip().lower() != "name"
            ]
            if names:
                return names
        except Exception as exc:
            logger.debug(f"그래픽 카드 정보 읽기 실패: {exc}")
    return []


def _has_discrete_gpu(adapter_names: list[str]) -> bool:
    discrete_keywords = (
        "nvidia",
        "geforce",
        "rtx",
        "gtx",
        "quadro",
        "radeon",
        "amd",
        "rx ",
        "arc",
    )
    integrated_keywords = (
        "intel(r) uhd",
        "intel uhd",
        "iris",
        "xe graphics",
        "vega",
    )
    for name in adapter_names:
        lower = name.lower()
        if "intel" in lower and "arc" not in lower:
            continue
        if any(k in lower for k in integrated_keywords):
            continue
        if any(k in lower for k in discrete_keywords):
            return True
    return False


def _available_ort_providers() -> list[str]:
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except Exception as exc:
        logger.debug(f"ONNX Runtime provider 조회 실패: {exc}")
        return []


def _ocr_backend_preference(value: str | None = None) -> str:
    """Return user-selected OCR backend preference.

    Default is OpenVINO because it is reliable on Intel iGPU/hybrid laptops.
    DirectML is opt-in: broken dGPUs and non-direct-display laptops can expose
    NVIDIA/AMD adapters but still run DirectML slowly or unstably.
    """
    source = "매개변수" if value is not None else "NTE_OCR_BACKEND"
    value = (value if value is not None else os.environ.get("NTE_OCR_BACKEND", "openvino")).strip().lower()
    aliases = {
        "ov": "openvino",
        "intel": "openvino",
        "gpu": "directml",
        "dml": "directml",
        "auto-safe": "auto",
        "amd": "amd_compat",
        "amd-compat": "amd_compat",
        "amd_compatibility": "amd_compat",
        "low-load": "low_load",
        "low_load": "low_load",
    }
    value = aliases.get(value, value)
    if value not in {"openvino", "directml", "auto", "cpu", "amd_compat", "low_load"}:
        logger.warning(f"알 수 없는 OCR 백엔드 설정 {source}={value!r}, openvino를 사용했습니다.")
        return "openvino"
    return value


def _apply_low_load_runtime_limits() -> None:
    """Reduce native OCR/OpenCV thread pressure for unstable AMD systems."""

    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "OV_CPU_THREADS_NUM",
        "OPENVINO_CPU_THREADS_NUM",
    ):
        os.environ[key] = "1"
    try:
        cv2.setNumThreads(1)
    except Exception as exc:
        logger.debug(f"OpenCV 스레드 제한 실패: {exc}")


def _warmup(ocr):
    _test_img = np.zeros((32, 100, 3), dtype=np.uint8)
    ocr(_test_img)


def _create_openvino_ocr():
    try:
        from rapidocr_openvino import RapidOCR
        ocr = RapidOCR(use_cls=False)
        _warmup(ocr)
        return ocr, "OpenVINO (Intel CPU/내장 그래픽 가속)"
    except SystemExit as e:
        logger.warning(f"OpenVINO가 sys.exit을 발생시켰습니다 (DLL 경로 문제): {e}")
    except Exception as e:
        logger.warning(f"OpenVINO 엔진 초기화 실패, 대체 방안으로 폴백합니다: {e}")
    return None, ""


def _create_directml_ocr(providers: list[str]):
    if "DmlExecutionProvider" not in providers:
        logger.warning(f"현재 ONNX Runtime에 DirectML Provider가 없습니다. 사용 가능한 Provider: {providers}")
        return None, ""
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR(use_cls=False, det_use_dml=True, cls_use_dml=True, rec_use_dml=True)
        _warmup(ocr)
        return ocr, "DirectML GPU"
    except Exception as e:
        logger.warning(f"DirectML OCR 초기화 실패, OpenVINO/CPU로 폴백합니다: {e}")
        return None, ""


def _create_onnx_cpu_ocr():
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR(use_cls=False)
        _warmup(ocr)
        return ocr, "ONNX Runtime CPU (범용 모드)"
    except Exception as e:
        logger.warning(f"ONNX Runtime OCR 초기화 실패: {e}")
    return None, ""


def _create_ocr_engine(backend_preference: str | None = None):
    """Create OCR engine with safe defaults.

    OpenVINO is the default. DirectML is only used when explicitly requested by
    a caller or NTE_OCR_BACKEND=directml/auto and a discrete adapter is detected.
    """
    adapter_names = _get_video_adapter_names()
    has_discrete_gpu = _has_discrete_gpu(adapter_names)
    backend_pref = _ocr_backend_preference(backend_preference)
    if adapter_names:
        logger.info(f"감지된 그래픽 카드: {'; '.join(adapter_names)}")
    else:
        logger.info("그래픽 카드 정보를 읽지 못해 외장 그래픽 없음 정책에 따라 OpenVINO를 우선 사용합니다.")

    providers = _available_ort_providers()
    if backend_pref in {"amd_compat", "low_load"}:
        mode_label = (
            "이상 호환 모드" if backend_pref == "amd_compat" else "정식 저부하 모드"
        )
        logger.warning(
            f"{mode_label}을(를) 켰습니다: DirectML을 비활성화하고 OCR/이미지 처리 스레드를 제한하며 저부하 OCR 초기화를 사용합니다."
        )
        _apply_low_load_runtime_limits()
        ocr, engine_type = _create_openvino_ocr()
        if ocr is not None:
            return ocr, f"{engine_type} / {mode_label}"
        ocr, engine_type = _create_onnx_cpu_ocr()
        if ocr is not None:
            return ocr, f"{engine_type} / {mode_label}"
        raise ImportError(f"{mode_label}에서 사용 가능한 RapidOCR 추론 엔진을 찾지 못했습니다")

    if backend_pref in {"directml", "auto"} and has_discrete_gpu:
        logger.info(f"OCR 백엔드가 {backend_pref}(으)로 설정되어 있고 외장 그래픽이 감지되어 DirectML GPU 가속을 시도합니다.")
        ocr, engine_type = _create_directml_ocr(providers)
        if ocr is not None:
            return ocr, engine_type
    elif backend_pref in {"directml", "auto"}:
        logger.info("OCR 백엔드가 GPU를 허용하지만 외장 그래픽이 감지되지 않아 OpenVINO를 사용합니다.")
    elif has_discrete_gpu:
        logger.info("외장 그래픽이 감지되었지만 기본 안전 정책에 따라 OpenVINO를 우선 사용합니다.")
    else:
        logger.info("외장 그래픽이 감지되지 않아 OpenVINO 가속을 우선 사용합니다.")

    if backend_pref != "cpu":
        ocr, engine_type = _create_openvino_ocr()
        if ocr is not None:
            return ocr, engine_type

    ocr, engine_type = _create_onnx_cpu_ocr()
    if ocr is not None:
        return ocr, engine_type

    raise ImportError("사용 가능한 RapidOCR 추론 엔진을 찾지 못했습니다")


class OCREngine:
    """支持硬件自适应的 OCR 引擎，带运行时兜底"""

    def __init__(self, backend_preference: str | None = None):
        logger.info("OCR 엔진을 초기화하는 중...")
        self.ocr, engine_type = _create_ocr_engine(backend_preference)
        logger.success(f"OCR 엔진 준비 완료 [백엔드: {engine_type}]")

    def extract_text(self, image_input: np.ndarray) -> list:
        extracted_texts = []
        try:
            if len(image_input.shape) == 2:
                final_input = cv2.cvtColor(image_input, cv2.COLOR_GRAY2BGR)
            else:
                final_input = image_input

            results, _ = self.ocr(final_input)
            if results:
                for line in results:
                    extracted_texts.append(str(line[1]).strip())
        except Exception as e:
            logger.error(f"OCR 조각 분석 실패: {e}")
            raise OCRParseError(f"OCR 엔진 예외: {e}")

        return extracted_texts

    def extract_lines(self, image_input: np.ndarray) -> list[dict]:
        lines = []
        try:
            if len(image_input.shape) == 2:
                final_input = cv2.cvtColor(image_input, cv2.COLOR_GRAY2BGR)
            else:
                final_input = image_input

            results, _ = self.ocr(final_input)
            if not results:
                return lines
            for line in results:
                if len(line) < 2:
                    continue
                box = line[0]
                text = str(line[1]).strip()
                if not text:
                    continue
                try:
                    xs = [float(p[0]) for p in box]
                    ys = [float(p[1]) for p in box]
                    rect = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
                except Exception:
                    rect = (0, 0, final_input.shape[1], final_input.shape[0])
                lines.append({"text": text, "box": rect})
        except Exception as e:
            logger.error(f"OCR 행 상자 분석 실패: {e}")
            raise OCRParseError(f"OCR 엔진 예외: {e}")
        return lines

    def identify_item_type(self, identity_image_input: np.ndarray) -> str:
        texts = self.extract_text(identity_image_input)
        full_text = "".join(texts)
        if any(char in full_text for char in ["驱", "动", "型", "I"]):
            return "drive"
        return "tape"
