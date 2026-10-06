# 대응 소스 코드 고지 / Corresponding Source

AGPL-3.0 §5, §13 이행을 위한 고지입니다.

## 상류 저작물 / Upstream

- 저장소: https://github.com/hxwd94666/NTE-Drive-Calculator (옛 이름 `NTE-Drive-Calc`)
- 기준 커밋: `0e7a6b4e8e61d7a288e520bbc192a411d68c2fb1` (버전 2.3.1)
- 저작권: NTE Drive Calc contributors
- 라이선스: GNU Affero General Public License v3.0 ([LICENSE](LICENSE))

기준 커밋은 임의로 고른 것이 아닙니다. 공식 2.3.1 실행 파일의 PYZ 아카이브에서 모든 `src.*` 모듈의
바이트코드를 추출해 후보 커밋들과 구조 비교한 결과, **893개 모듈 전부가 `0e7a6b4`와 일치**했습니다.
상류의 태그 `2.3.1`은 이 커밋이 아니라 버전 2.3.0인 커밋 `765d78a`를 가리킵니다. 공식 빌드가 태그와 다른
커밋에서 나온 전례가 이번에도 반복되었으므로, 재현하실 때는 반드시 위 커밋을 사용하세요.

*The baseline was not picked by hand: all 893 `src.*` modules in the official 2.3.1 binary's PYZ match
`0e7a6b4` structurally. The upstream tag `2.3.1` points to `765d78a`, which is version 2.3.0 - do not use the
tag; reproduce against this exact commit.*

## 이 파생물의 변경 내용 / What this derivative changes

1. `src/` 아래 파이썬 소스의 사용자 표시 문자열을 한국어로 번역
2. `src/i18n_display.py` 추가 — 실행 중 표시 계층. 소스 치환으로 닿을 수 없는 문자열(데이터베이스
   `*_zh` 값, C++ 측에서 생성되는 라벨, 번들 네이티브 컴포넌트 `nte-analysis-core.exe`가 반환하는
   전투 리포트 문장)을 화면에 그려질 때 치환합니다. 한글·초성 검색 보조 함수도 여기에 있습니다.
3. `src/i18n_desc.py` 추가 — 설명문 완전 일치 사전
4. `src/ui/app.py`에 표시 계층 설치 훅 1개 삽입
5. `src/app/theme.py` 글꼴 우선순위에 `Malgun Gothic` 추가
6. 일부 파일에 한글 검색 지원을 위한 소스 패치 (`_ko_contains` 등)
7. 번역 때문에 생긴 키 불일치 수정 — 딕셔너리 키·표식 문자열의 한쪽만 번역되어 생긴 버그(창고 상태 저장,
   감정 수동 입력, 몬스터·메커니즘·자산 도감 등)에서, 소비하는 쪽이 실제로 만들어지는 값을 받도록 고쳤습니다.
8. 네이티브 분석 컴포넌트가 보내는 중국어 라벨(`倍率区`, `未识别技能` 등)을 한국어 코드도 인식하도록 수정
9. 게임 폴더 보호 — `services/native_plugin_deployment.py`, `services/managed_plugin_cleanup.py`,
   `integrations/legacy_game_proxy.py`, `services/component_upgrade_guide.py`, `services/work_mode_runtime.py`,
   `ui/controllers/native_plugin_deployment_ui.py`. 게임 폴더에 컴포넌트를 배포·정리할 때 이 프로그램이 배포한
   파일(공식 빌드 해시 또는 배포 기록 해시)만 덮어쓰거나 지우고, 다른 프로그램의 파일(예: ReShade의
   `d3d12.dll`, 다른 모드의 `dwmapi.dll`)은 남겨 둔 채 알립니다.

번역해서는 안 되는 문자열(데이터베이스 `*_zh` 값, OCR 별칭 사전, 센티널 비교 대상)은 파일·줄
단위 보호 규칙으로 원문을 유지했습니다.

## 배포되는 실행 파일 / The published binary

공식 2.3.1 PyInstaller 패키지의 PYZ 아카이브에서 **`src.*` 모듈만** 번역본으로 교체하고(추가 모듈
`src.i18n_display`, `src.i18n_desc` 포함), PE 체크섬을 다시 계산해 만듭니다. `src.*` 이외의 모든 아카이브 항목은
원본과 **바이트 단위로 동일**합니다. 튜토리얼 이미지 7장은 한국어판으로 교체되며, 원본은 설치 시 `guide_zh`
폴더에 백업됩니다.

*Only the `src.*` modules inside the original PyInstaller PYZ are replaced (plus the added `src.i18n_display` and
`src.i18n_desc`); every other archive entry is byte-identical to the original. The PE checksum is recomputed so
the header matches the file.*

## 이 저장소의 `src/`

[`src/`](src) 디렉터리가 배포되는 실행 파일에 대응하는 완전한 소스입니다. 각 릴리스에 해당하는
소스는 같은 태그의 트리를 참조하세요. 릴리스마다 같은 내용의 `NTE_Drive_Calc_<버전>_KO_source.zip`도 함께 올립니다.

## 재현 방법 / Reproducing

1. 상류 저장소를 위 커밋으로 체크아웃
2. 이 저장소의 `src/`로 교체
3. Python 3.11로 컴파일한 뒤, 원본 실행 파일의 PYZ 아카이브에서 `src.*` 모듈을 교체
4. PE 헤더의 CheckSum을 재계산 (`CheckSumMappedFile` 알고리즘)

번역 도구 일체(문자열 추출, 대조표 적용, 보호 규칙, PYZ 재작성, 구조 비교 검증)는 요청하시면
제공합니다.
