# PCfishMate

**[최신 Windows 릴리스 다운로드](https://github.com/nyung0/PCfishMate/releases/latest)** · [최신 EXE 바로 받기](https://github.com/nyung0/PCfishMate/releases/latest/download/PCfishMate.exe)

PCfishMate는 Windows용 PC FISH 보조 앱입니다. 교배, 일반 합성, 수조 레벨업을 자동화하고 시즌 합성 재료와 자동화 기록을 보여줍니다.

## 설치 및 사용

1. PC FISH를 종료하고 최신 릴리스의 `PCfishMate.exe`를 실행합니다.
2. 초기 설정 창에서 Steam 게임 폴더를 확인하고 **설치 및 실행**을 누릅니다.
3. 앱에서 실행할 기능을 체크하고 **자동화 시작**을 누릅니다. **중지** 또는 **F8**로 멈출 수 있습니다.

앱의 **업데이트 확인**에서 새 Windows 릴리스를 확인하고 적용할 수 있습니다. 교배와 합성 설정은 [사용 안내](internal/README.md)를 참고하세요.

## main을 클론한 경우

PowerShell 7, Python 3.12 x64, .NET 6 SDK를 준비하고 저장소 루트에서 실행합니다.

```powershell
pwsh -File .\build-windows.ps1
.\dist\PCfishMate.exe
```

GitHub Actions도 같은 스크립트로 빌드합니다. 소스에서 직접 실행하려면 위 빌드로 의존성과 게임 모듈을 준비한 뒤 저장소 루트에서 `python pc_fish_helper.py`를 실행하세요. 이 명령은 현재 컨트롤러를 엽니다.
