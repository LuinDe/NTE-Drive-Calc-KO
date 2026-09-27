# 为实时战报页面和悬浮窗标明采集摘要实际使用的 DPS 计时方式。
def summary_clock_label(mode: str) -> str:
    return {
        "subtract_time_stop": "유효 시간",
        "wall_clock": "실제 시간",
    }.get(mode, "시간 측정 불명")
