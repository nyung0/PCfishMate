# PC FISH 내부 교배 플러그인

PC FISH Windows x64 IL2CPP용 BepInEx 플러그인입니다. 게임의 메인 스레드에서 현재 하트와 물고기 목록을 읽고, 게임의 `UIBreed.SetParent`와 `UIBreed.Breed`를 호출합니다. 게임이 결과를 반영하면 다음 주기를 기다립니다.

## 설치 및 사용

**PCFishAutomation.exe**에 로더와 플러그인이 포함되어 있습니다.

1. PC FISH를 종료하고 EXE를 실행합니다.
2. 초기 설정 창에서 자동 감지된 Steam 게임 폴더를 확인하거나 **폴더 선택**으로 변경합니다. 설치 내용을 읽고 동의한 뒤 **설치 및 실행**을 누릅니다.
3. 관리자 권한 설치가 완료되면 컨트롤러가 열립니다. 이후 같은 버전에서는 저장된 폴더와 모듈을 확인한 뒤 컨트롤러로 바로 진입합니다. 새 버전의 모듈이 포함된 EXE를 실행하면 다시 설치 설정이 열립니다.
4. **게임 실행**을 누르고 연결이 표시되면 필요한 자동화와 허용 희귀도를 선택한 뒤 시작합니다.

교배에는 체크된 희귀도의 개체만 부모로 사용합니다. 실행 중 체크를 바꾸면 다음 교배부터 적용되며, 체크가 하나도 없으면 교배만 대기합니다. 수조 자동화는 별도로 동작합니다. 자동화 기록은 현지 시간으로 교배 요청·결과, 확인된 수조 레벨 변경, 시작·중지·오류를 보여줍니다. 상세 게임 로그는 **로그 보기**에서 열 수 있습니다.

### 수동 설치

1. [BepInEx IL2CPP 설치 안내](https://docs.bepinex.dev/master/articles/user_guide/installation/unity_il2cpp.html)에 따라 PC FISH 설치 폴더에 BepInEx 6 Windows x64 IL2CPP를 설치합니다. 현재 게임에서는 메타데이터 버전 31을 지원하는 6.0.0-be.793으로 검증했습니다.
2. `PCFishInternal.dll`을 게임 설치 폴더 아래 `BepInEx/plugins`에 복사한 뒤 게임을 시작합니다.
3. 컨트롤러 `PCFishAutomation.exe`를 실행합니다. 허용 희귀도와 간격을 정한 후 **자동화 시작**을 누릅니다. 기본 간격은 10초입니다.
4. 컨트롤러의 **중지**나 **F8**로 정지합니다. 컨트롤러를 닫거나 연결이 끊기면 컨트롤러에서 시작한 자동화는 중지됩니다.

남은 교배 횟수가 있는 개체와 횟수 제한이 없는 기본 개체를 대상으로, 잠금·쿨다운 상태를 제외한 뒤 희귀도와 별 등급이 높은 순으로 고릅니다. 두 부모의 개체 ID는 서로 달라야 합니다.

실제 게임에서 컨트롤러와 동일한 연결을 사용해 교배 탭 자동 열기, 연속 교배 2회와 중지를 검증했습니다.

## 소스 빌드

.NET 6 SDK에서 `dotnet build PCFishInternal.csproj -c Release -p:BepInExCore=<BepInEx/core>`를 실행합니다. `controller.py`와 함께 패키징할 때 빌드한 `PCFishInternal.dll`과 공식 BepInEx Windows x64 IL2CPP 아카이브를 `loader.zip` 이름으로 PyInstaller의 `--add-data`에 추가합니다.
