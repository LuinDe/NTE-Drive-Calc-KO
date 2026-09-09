# 대응 소스 코드 고지 / Corresponding Source

AGPL-3.0 §5, §13 이행을 위한 고지입니다.

## 상류 저작물 / Upstream

- 저장소: https://github.com/hxwd94666/NTE-Drive-Calc
- 기준 커밋: `e09cf84d750dc17adfcfcc528c04002b678fea94` (버전 2.2.1)
- 저작권: NTE Drive Calc contributors
- 라이선스: GNU Affero General Public License v3.0 ([LICENSE](LICENSE))

기준 커밋은 임의로 고른 것이 아닙니다. 배포된 2.2.1 실행 파일의 PYZ 아카이브에서 모든 `src.*`
모듈의 바이트코드를 추출해 구조 비교한 결과 **691개 모듈 전부가 이 커밋과 일치**했습니다. 같은
날짜의 `main` HEAD(`7b4293c`)는 5개가 어긋나므로, 재현하실 때는 반드시 이 커밋을 사용하세요.

*The baseline commit was not picked by hand: every one of the 691 `src.*` modules in the released
2.2.1 binary's PYZ matches this commit bytecode-for-bytecode. The `main` HEAD of the same day
(`7b4293c`) differs in 5 modules, so reproduce against `e09cf84`, not HEAD.*

## 이 파생물의 변경 내용 / What this derivative changes

1. `src/` 아래 파이썬 소스의 사용자 표시 문자열을 한국어로 번역
2. `src/i18n_display.py` 추가 — 실행 중 표시 계층. 소스 치환으로 닿을 수 없는 문자열(데이터베이스
   `*_zh` 값, C++ 측에서 생성되는 라벨, 번들 네이티브 컴포넌트 `nte-analysis-core.exe`가 반환하는
   전투 리포트 문장)을 화면에 그려질 때 치환합니다. 한글·초성 검색 보조 함수도 여기에 있습니다.
3. `src/i18n_desc.py` 추가 — 설명문 완전 일치 사전
4. `src/ui/app.py`에 표시 계층 설치 훅 1개 삽입
5. `src/app/theme.py` 글꼴 우선순위에 `Malgun Gothic` 추가
6. 일부 파일에 한글 검색 지원을 위한 소스 패치 (`_ko_contains` 등)

번역해서는 안 되는 문자열(데이터베이스 `*_zh` 값, OCR 별칭 사전, 센티널 비교 대상)은 파일·줄
단위 보호 규칙으로 원문을 유지했습니다.

## 배포되는 실행 파일 / The published binary

원본 PyInstaller 패키지의 PYZ 아카이브에서 **`src.*` 모듈만** 번역본으로 교체하고, PE 체크섬을
다시 계산해 만듭니다. `src.*` 이외의 모든 아카이브 항목은 원본과 **바이트 단위로 동일**합니다.
튜토리얼 이미지 7장은 한국어판으로 교체되며, 원본은 설치 시 `guide_zh` 폴더에 백업됩니다.

*Only the `src.*` modules inside the original PyInstaller PYZ are replaced; every other archive
entry is byte-identical to the original. The PE checksum is recomputed so the header matches the
file.*

## 이 저장소의 `src/`

[`src/`](src) 디렉터리가 배포되는 실행 파일에 대응하는 완전한 소스입니다. 각 릴리스에 해당하는
소스는 같은 태그의 트리를 참조하세요.

## 재현 방법 / Reproducing

1. 상류 저장소를 위 커밋으로 체크아웃
2. 이 저장소의 `src/`로 교체
3. Python 3.11로 컴파일한 뒤, 원본 실행 파일의 PYZ 아카이브에서 `src.*` 모듈을 교체
4. PE 헤더의 CheckSum을 재계산 (`CheckSumMappedFile` 알고리즘)

번역 도구 일체(문자열 추출, 대조표 적용, 보호 규칙, PYZ 재작성, 구조 비교 검증)는 요청하시면
제공합니다.
