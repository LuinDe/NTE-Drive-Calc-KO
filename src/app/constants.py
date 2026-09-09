# 集中定义应用版本、链接和说明文案常量。
"""Application constants shared by UI and feature modules."""

from src.app.version import __version__


APP_VERSION = __version__
ALLOCATION_TOTAL_SCORE_AREA = 35
GITHUB_HOME_URL = "https://github.com/hxwd94666/NTE-Drive-Calculator"
GITHUB_RELEASES_URL = GITHUB_HOME_URL + "/releases"
GITHUB_LATEST_RELEASE_URL = GITHUB_RELEASES_URL + "/latest"
MIRROR_UPDATE_API = "https://mirrorchyan.com/api/resources/NTE-Drive-Calc/latest"
MIRROR_PROJECT_URL = "https://mirrorchyan.com/zh/projects?rid=NTE-Drive-Calc&channel=stable"
BILIBILI_HOME_URL = "https://b23.tv/nXJGdh3"
SUPPORT_US_URL = "https://afdian.com/a/hxwd94666"
GROUP_CHAT_NOTICE = "QQ 단체 채팅: 1029030672\n개발 단체 채팅은 참여 후 방장에게 개인 메시지로 문의하세요."
WORKSHOP_WEIGHT_CONFIGS_API = "https://yh.zzzmap.com/api/open/game-character/weight-configs"
QUARK_NETDISK_URL = "https://pan.quark.cn/s/82f16b845aec"
BAIDU_NETDISK_URL = "https://pan.baidu.com/s/1sPVqCpzmkQwKYCGstcZuIQ?pwd=ygke"
XUNLEI_NETDISK_URL = "https://pan.xunlei.com/s/VP0W_ptzSZwkVamy2UvF_CliA1?pwd=2hb6#"
NETDISK_DOWNLOAD_LINKS = (
    ("Quark 클라우드", QUARK_NETDISK_URL),
    ("Baidu 클라우드", BAIDU_NETDISK_URL),
    ("Xunlei 클라우드", XUNLEI_NETDISK_URL),
)

CORE_CONFIG_FILES = ("stats.json",)
ACCOUNT_USER_FILES = (
    "hotkeys.json", "update_config.json", "quick_start_seen.json", "guide_seen.json",
    "ui_preferences.json",
)

SCAN_HELP = {
    "4": "· 기존 인벤토리 사용\n· 스캔·분석 없음\n· 바로 재계산할 때 적합",
    "3": "· 기존 스크린샷 분석\n· 인벤토리 기록 생성\n· 스캔 후 아직 분석하지 않았을 때 적합",
    "2": "· 새 장비만 등록\n· 기존 인벤토리에 추가\n· 일상적인 갱신에 적합",
    "1": "· 가방 전체를 다시 스캔\n· 인벤토리 기록 재생성\n· 첫 사용 또는 완전 재스캔에 적합",
}

DRONE_HELP = {
    "2": "· 장비를 직접 클릭 선택\n· F9 스크린샷, F10 완료\n· 더 빠르고 안정적\n· 평소 권장",
    "1": "· 자동으로 페이지를 넘기며 NEW 장비 탐색\n· 스크린샷 자동 완료\n· 시작 전 가방 첫 페이지에 머물러 주세요",
}

OFFLINE_HELP = {
    "full": "· 전체 스캔 스크린샷 읽기\n· 인벤토리 재생성\n· 전체 스캔 분석이 중단됐을 때 적합",
    "incremental": "· 증분 스캔 스크린샷 읽기\n· 기존 인벤토리에 추가\n· 증분 분석이 중단됐을 때 적합",
    "all": "· 폴더의 모든 스크린샷 읽기\n· 오래된 스크린샷이 중복 등록될 수 있음\n· 인벤토리가 이상하면 전체 스캔을 다시 실행하세요",
}
