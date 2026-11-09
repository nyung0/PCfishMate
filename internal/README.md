# PC FISH 내부 교배 플러그인

PC FISH Windows x64 IL2CPP용 BepInEx 플러그인입니다. 게임의 메인 스레드에서 현재 하트와 물고기 목록을 읽고, 게임의 `UIBreed.SetParent`와 `UIBreed.Breed`를 호출합니다. 게임이 결과를 반영하면 다음 주기를 기다립니다.

## 설치 및 사용

**PCFishAutomation.exe**에 로더와 플러그인이 포함되어 있습니다. 처음에는 게임을 종료하고 프로그램에서 **모듈 설치**를 누릅니다. 설치 후 **게임 실행**을 누르면 연결 상태와 교배 로그가 같은 창에 표시됩니다. 모듈 설치에는 Windows 관리자 권한이 필요합니다. BepInEx 콘솔은 숨기고 파일 로그를 사용합니다.

### 수동 설치

1. [BepInEx IL2CPP 설치 안내](https://docs.bepinex.dev/master/articles/user_guide/installation/unity_il2cpp.html)에 따라 PC FISH 설치 폴더에 BepInEx 6 Windows x64 IL2CPP를 설치합니다. 현재 게임에서는 메타데이터 버전 31을 지원하는 6.0.0-be.793으로 검증했습니다.
2. `PCFishInternal.dll`을 게임 설치 폴더 아래 `BepInEx/plugins`에 복사한 뒤 게임을 시작합니다.
3. 별도 프로그램 `PCFishInternalController.exe`를 실행합니다. 허용 희귀도와 간격을 정한 후 **자동화 시작**을 누릅니다. 기본 간격은 10초입니다.
4. 컨트롤러의 **중지**나 **F8**로 정지합니다. 컨트롤러를 닫거나 연결이 끊기면 컨트롤러에서 시작한 자동화는 중지됩니다.

남은 교배 횟수가 있는 개체와 횟수 제한이 없는 기본 개체를 대상으로, 잠금·쿨다운 상태를 제외한 뒤 희귀도와 별 등급이 높은 순으로 고릅니다. 두 부모의 개체 ID는 서로 달라야 합니다.

실제 게임에서 컨트롤러와 동일한 연결을 사용해 교배 탭 자동 열기, 연속 교배 2회와 중지를 검증했습니다.

## 소스 빌드

.NET 6 SDK에서 `dotnet build PCFishInternal.csproj -c Release -p:BepInExCore=<BepInEx/core>`를 실행합니다. `controller.py`와 함께 패키징할 때 빌드한 `PCFishInternal.dll`과 공식 BepInEx Windows x64 IL2CPP 아카이브를 `loader.zip` 이름으로 PyInstaller의 `--add-data`에 추가합니다.
