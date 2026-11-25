<img src="internal/pcfish-badge.png" width="72" alt="PCfishMate 물고기 아이콘">

# PCfishMate

PC FISH의 교배, 합성, 수조 관리를 도와주는 Windows 앱이에요.

**[🐟 최신 릴리스](https://github.com/nyung0/PCfishMate/releases/latest)**

## 할 수 있어요

- **자동 교배** · 부모의 희귀도와 별 등급을 골라 교배해요.
- **자동 합성과 수조 레벨업** · 설정한 조건을 충족하면 진행해요.
- **시즌합성** · 보유 재료와 부족한 재료를 한눈에 볼 수 있어요.

- **도감과 즐겨찾기** · 발견·보유 상태를 확인하고, 색상별로 보거나 같은 등급끼리 묶어 볼 수 있어요. 즐겨찾기한 물고기가 나오면 알림을 받을 수 있어요.
- **색상 편집** · 물고기의 색 계열을 바꾸거나 픽셀을 직접 칠하고, 게임을 켜 둔 상태에서 적용하거나 원본으로 복원할 수 있어요.

## 시작하기

Windows 10/11 64비트와 Steam에서 설치한 PC FISH가 필요해요.

1. PC FISH를 종료하고 내려받은 `PCfishMate.exe`를 실행해요.
2. 게임 폴더와 설치 안내를 확인하고 동의한 뒤 **설치 및 실행**을 눌러요. 게임 모듈 설치 중 관리자 권한을 요청할 수 있어요.
3. 원하는 기능을 체크하고 **자동화 시작**을 눌러요.

상단 톱니바퀴의 **알림 설정**에서 새 버전을 확인하고 업데이트할 수 있어요. [자세한 사용 안내](internal/README.md)

## 소스에서 빌드

PowerShell 7, Python 3.12 x64, .NET 6 SDK를 준비하고 저장소 루트에서 실행해요.

```powershell
pwsh -File .\build-windows.ps1
.\dist\PCfishMate.exe
```

빌드 후 `python pc_fish_helper.py`로도 현재 앱을 열 수 있어요.
