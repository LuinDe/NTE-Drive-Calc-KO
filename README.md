# NTE Drive Calc 한국어 패치

[NTE Drive Calc](https://github.com/hxwd94666/NTE-Drive-Calc) (异环 드라이브 계산기)를 한국어로 번역한 패치입니다.
원작자의 허락을 받고 배포합니다.

## 설치

1. 공식 프로그램 **2.2.1을 먼저 설치**하세요. 이 패치에는 원본 프로그램이 들어 있지 않습니다.
   → https://github.com/hxwd94666/NTE-Drive-Calc
2. [Releases](../../releases)에서 `NTE_Drive_Calc_..._KO_Patch_Setup.exe`를 받아 **더블클릭**합니다.
3. UAC 창에서 "예"를 누르면 끝입니다. 설치 폴더는 자동으로 찾고, 프로그램이 켜져 있으면 알아서 닫습니다.

원본 실행 파일은 `NTE_Drive_Calc.original.exe`로, 원본 튜토리얼 이미지는 `guide_zh` 폴더로 자동 백업됩니다.

**되돌리려면** 설치 파일을 다시 실행하세요. "다시 설치 / 원본으로 복원 / 닫기"를 물어봅니다.

## 번역 범위

- 화면 문자열 약 8,000개
- 캐릭터·세트·아크·속성·스킬·몬스터 이름과 각성·스킬·세트 효과 설명문
- **전투 리포트 전체** — 히트별 로그, 툴팁, 타임라인, 드라이브 서브 스탯
- "사용 방법" 튜토리얼 이미지 7장 한국어판
- 검색창에서 **한글·초성 검색** 지원 (중국어·병음도 그대로 됩니다)

스킬·서브 스킬·각성·공명 이름은 **한국 서버 공식 명칭**을 사용했습니다. 한국 서버에 아직 출시되지
않은 콘텐츠는 임시 번역이며, 출시되는 대로 공식 명칭으로 교체합니다.

## 주의

- 게임 클라이언트 언어는 **简体中文**로 두셔야 스캔/감정(OCR)이 동작합니다. 프로그램이 중국어
  텍스트를 인식하는 방식이라 그렇습니다.
- 프로그램이 **자동 업데이트되면 패치가 사라집니다.** 새 버전용 패치가 따로 필요합니다.
- 이 패치는 특정 버전 전용입니다. 다른 버전에서는 설치가 중단됩니다.

## 백신 경고에 대하여 (오탐입니다)

코드 서명 인증서가 없어 일부 백신이 경고할 수 있습니다. 직접 확인하실 수 있도록 VirusTotal 실측을
적어 둡니다.

| 파일 | 탐지 |
| --- | --- |
| **공식 원본** 실행 파일 (패치 안 한 것) | 4 / 71 |
| **한국어 패치** 실행 파일 | 4 / 71 — 같은 백신 4곳, 개수도 동일 |
| 설치 파일 | 2 / 69 |

**한국어 패치가 새로 만든 탐지는 없습니다.** 원본을 쓰시던 분들도 이미 같은 판정을 받는 파일을
쓰고 계셨습니다. 판정명은 전부 머신러닝 추정(`!ml`)이거나 제네릭이고, 특정 악성코드로 지목한
백신은 한 곳도 없습니다. 설치 파일에 남은 2곳은 기업용 딥러닝 엔진이며 Defender·카스퍼스키·
ESET·알약·V3는 모두 깨끗합니다.

> GitHub의 150~180MB짜리 공식 "설치 파일"을 검사하면 0건으로 나오는데, 그건 압축된 통 파일이라
> 백신이 겉만 보기 때문입니다. 비교하시려면 설치 폴더의 19MB짜리 `NTE_Drive_Calc.exe`로 하셔야 맞습니다.

각 버전의 SHA256은 [Releases](../../releases) 노트에 있습니다. 아래로 직접 확인하실 수 있습니다.

```
certutil -hashfile "C:\Program Files\NTE Drive Calc\NTE_Drive_Calc.exe" SHA256
```

## 라이선스 / 대응 소스

NTE Drive Calc는 **AGPL-3.0**으로 배포되는 프로그램이며, 이 패치는 그 파생물입니다.

이 저장소의 [`src/`](src) 가 배포되는 실행 파일에 대응하는 소스 코드입니다. 자세한 고지는
[SOURCE_OFFER.md](SOURCE_OFFER.md)를 참고하세요.

- 원저작권: NTE Drive Calc contributors ([@hxwd94666](https://github.com/hxwd94666))
- 한국어 번역: [@LuinDe](https://github.com/LuinDe)

---

## 中文 / English

**这是什么** — [NTE Drive Calc](https://github.com/hxwd94666/NTE-Drive-Calc) 的韩文本地化补丁，经原作者同意后发布。
不是 i18n 框架，而是针对特定发行版本的两层本地化：构建期替换 `src/` 里的中文字面量并只替换
PyInstaller PYZ 中的 `src.*` 模块（其余归档条目与原版逐字节相同），运行期再由一个显示层处理
源码替换够不到的部分（数据库 `*_zh` 值、C++ 侧构造的标签、以及原生分析组件返回的战报文本）。

**A Korean localization patch** for NTE Drive Calc, published with the author's permission. Not an
i18n framework - a two-layer localization of one specific release: build-time replacement of the
Chinese literals in `src/`, repacking only the `src.*` modules inside the PyInstaller PYZ (every
other archive entry stays byte-identical), plus a runtime display layer for the strings that source
rewriting cannot reach.

`src/` in this repository is the corresponding source for the published binary, as required by
AGPL-3.0. See [SOURCE_OFFER.md](SOURCE_OFFER.md).
