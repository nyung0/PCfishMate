# Requires PowerShell 7, Python 3.12 x64, and the .NET 6 SDK.
param([ValidatePattern('^v\d+\.\d+\.\d+$')][string] $PreviousRelease)

$ErrorActionPreference = 'Stop'

function Restore-PublishedModule {
    param([string] $Tag)

    # An app-only update must carry the exact DLL that the existing updater compares by SHA-256.
    git diff --quiet "refs/tags/$Tag" HEAD -- ':(glob)internal/**/*.cs' ':(glob)internal/**/*.csproj' `
        ':(glob)**/Directory.Build.*' ':(glob)**/Directory.Packages.props' ':(glob)**/global.json'
    if ($LASTEXITCODE -eq 1) { return }
    if ($LASTEXITCODE) { throw "Could not compare module sources with $Tag" }

    $api = "https://api.github.com/repos/nyung0/PCfishMate/releases/tags/$Tag"
    $release = Invoke-RestMethod $api -Headers @{ Accept = 'application/vnd.github+json' }
    $url = "https://github.com/nyung0/PCfishMate/releases/download/$Tag/PCfishMate.exe"
    $asset = $release.assets | Where-Object { $_.browser_download_url -ceq $url } | Select-Object -First 1
    if (!$asset -or $asset.digest -cnotmatch '^sha256:[0-9a-f]{64}$') {
        throw "Could not verify the published executable for $Tag"
    }
    $previousExe = 'build/previous-PCfishMate.exe'
    Invoke-WebRequest $url -OutFile $previousExe
    $hash = (Get-FileHash $previousExe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ("sha256:$hash" -cne $asset.digest) { throw "Published executable checksum mismatch for $Tag" }

    @'
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader

previous = CArchiveReader("build/previous-PCfishMate.exe")
if previous.extract("loader.zip") == Path("build/loader.zip").read_bytes():
    Path("build/plugin/PCFishInternal.dll").write_bytes(previous.extract("PCFishInternal.dll"))
    print("Embedded the published game module unchanged")
else:
    print("Loader changed; embedding the freshly built game module")
'@ | python -
    if ($LASTEXITCODE) { throw "Could not read the published game module for $Tag" }
}

$previousPythonPath = $env:PYTHONPATH
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
    python -m pip install --upgrade --target build/python pyinstaller==6.22.3 UnityPy==1.25.4
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    $env:PYTHONPATH = (Resolve-Path build/python).Path
    if ($PreviousRelease) { Restore-PublishedModule -Tag $PreviousRelease }
    # Sprite.image imports UnityPy.export, which eagerly imports the audio converter.
    # PCfishMate only reads images, so leave that converter unloaded in the bundled copy.
    $exports = 'build/python/UnityPy/export/__init__.py'
    $source = Get-Content $exports -Raw
    if ($source -notmatch '(?m)^    AudioClipConverter,\r?$') { throw 'Unexpected UnityPy export layout' }
    $source -replace '(?m)^    (AudioClipConverter|"AudioClipConverter"),\r?\n', '' |
        Set-Content $exports -Encoding utf8NoBOM
    python -m PyInstaller --paths . --icon internal/pcfish.ico --add-data 'internal/pcfish.ico:.' --add-data 'internal/pcfish-badge.png:.' --add-data 'internal/color-tools:color-tools' --collect-data UnityPy --collect-data archspec --exclude-module fmod_toolkit --exclude-module pyfmodex --noconfirm --onefile --windowed --name PCfishMate --add-data 'internal/release-version.txt:.' --add-data 'build/6000.0.76.zip:.' --add-data 'build/loader.zip:.' --add-data 'build/plugin/PCFishInternal.dll:.' internal/controller.py
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    $archive = python -m PyInstaller.utils.cliutils.archive_viewer --list dist/PCfishMate.exe
    if ($LASTEXITCODE -or !($archive -match 'pcfish\.ico') -or !($archive -match 'pcfish-badge\.png') -or !($archive -match 'release-version\.txt')) {
      throw 'Bundled app resources missing'
    }
    $exe = (Resolve-Path dist/PCfishMate.exe).Path
    $check = Start-Process -FilePath $exe -ArgumentList '--check-image-runtime' -WindowStyle Hidden -Wait -PassThru
    if ($check.ExitCode -ne 0) { throw "Packaged image runtime check failed ($($check.ExitCode))" }
    $report = (Join-Path (Resolve-Path build).Path 'module-check.sha256')
    $check = Start-Process -FilePath $exe -ArgumentList '--check-update-module', ('"{0}"' -f $report) -WindowStyle Hidden -Wait -PassThru
    $expectedModule = (Get-FileHash build/plugin/PCFishInternal.dll -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($check.ExitCode -ne 0 -or !(Test-Path -LiteralPath $report) -or (Get-Content -LiteralPath $report -Raw) -cne $expectedModule) {
        throw "Packaged module digest check failed ($($check.ExitCode))"
    }
    $hash = (Get-FileHash dist/PCfishMate.exe -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  PCfishMate.exe" | Set-Content dist/SHA256SUMS.txt -Encoding ascii
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
