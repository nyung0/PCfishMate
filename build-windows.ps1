# Requires PowerShell 7, Python 3.12 x64, and the .NET 6 SDK.
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    New-Item -ItemType Directory -Path build -Force | Out-Null
    Invoke-WebRequest 'https://builds.bepinex.dev/projects/bepinex_be/793/BepInEx-Unity.IL2CPP-win-x64-6.0.0-be.793%2Bdbc82f0.zip' -OutFile build/loader.zip
    $expected = '5B63D7D0E92A2448CCD13B738FF95FF1CC1026C6D718555D4DF8E6C824953AC4'
    if ((Get-FileHash build/loader.zip -Algorithm SHA256).Hash -ne $expected) { throw 'Unexpected BepInEx archive hash' }
    Expand-Archive build/loader.zip build/loader -Force
    
    Invoke-WebRequest 'https://unity.bepinex.dev/libraries/6000.0.76.zip' -OutFile build/6000.0.76.zip
    $expected = '2BA752C2CA15CDBF780B123A3F335B745F1DE14A15C3967211E1E58BED0FCFA5'
    if ((Get-FileHash build/6000.0.76.zip -Algorithm SHA256).Hash -ne $expected) { throw 'Unexpected Unity library archive hash' }
    
    $core = (Resolve-Path build/loader/BepInEx/core).Path
    dotnet build internal/PCFishInternal.csproj -c Release "-p:BepInExCore=$core" -o build/plugin
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    python -m pip install pyinstaller==6.22.3 UnityPy==1.25.4
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    $fmod = python -c "from importlib.util import find_spec; from pathlib import Path; print(Path(find_spec('fmod_toolkit').origin).parent / 'libfmod/Windows/x64/fmod.dll')"
    if (!(Test-Path -LiteralPath $fmod)) { throw 'FMOD DLL missing' }
    python -m PyInstaller --paths . --icon internal/pcfish.ico --add-data 'internal/pcfish.ico:.' --add-data 'internal/pcfish-badge.png:.' --collect-data UnityPy --collect-data archspec --add-binary "$($fmod):fmod_toolkit/libfmod/Windows/x64" --noconfirm --onefile --windowed --name PCfishMate --add-data 'internal/release-version.txt:.' --add-data 'build/6000.0.76.zip:.' --add-data 'build/loader.zip:.' --add-data 'build/plugin/PCFishInternal.dll:.' internal/controller.py
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    $archive = python -m PyInstaller.utils.cliutils.archive_viewer --list dist/PCfishMate.exe
    if ($LASTEXITCODE -or !($archive -match 'pcfish\.ico') -or !($archive -match 'pcfish-badge\.png') -or !($archive -match 'release-version\.txt')) {
      throw 'Bundled app resources missing'
    }
    $hash = (Get-FileHash dist/PCfishMate.exe -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  PCfishMate.exe" | Set-Content dist/SHA256SUMS.txt -Encoding ascii
}
finally {
    Pop-Location
}
