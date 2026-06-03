param(
    [string]$Version = "v0.1.0",
    [string]$Proxy = "",
    [switch]$SkipRuntimeDownload
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CacheDir = Join-Path $Root ".release-cache"
$StageDir = Join-Path $Root "release-assets"
$StandardDir = Join-Path $StageDir "SmartGIF-Windows-Standard-$Version"
$EasyDir = Join-Path $StageDir "SmartGIF-Windows-Easy-OneClick-$Version"

$PythonVersion = "3.13.13"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$WebpVersion = "1.6.0"
$WebpUrl = "https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-$WebpVersion-windows-x64.zip"
$LauncherExe = Join-Path $CacheDir "launcher-dist\SmartGIF.exe"

function Build-Launcher {
    $source = Join-Path $Root "launcher\smartgif_launcher.py"
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing launcher source: $source"
    }

    Remove-Item -LiteralPath (Join-Path $CacheDir "launcher-dist") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $CacheDir "launcher-build") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $CacheDir "launcher-spec") -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null

    & python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name SmartGIF `
        --distpath (Join-Path $CacheDir "launcher-dist") `
        --workpath (Join-Path $CacheDir "launcher-build") `
        --specpath (Join-Path $CacheDir "launcher-spec") `
        $source
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed to build SmartGIF.exe"
    }
    if (-not (Test-Path -LiteralPath $LauncherExe)) {
        throw "Launcher build did not produce $LauncherExe"
    }
}

function Invoke-Download {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile
    )

    if (Test-Path -LiteralPath $OutFile) {
        Write-Host "Using cached: $OutFile"
        return
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
    $args = @("-L", "--retry", "3", "--retry-delay", "2", "-o", $OutFile)
    if ($Proxy) {
        $args += @("-x", $Proxy)
    }
    $args += $Uri
    Write-Host "Downloading: $Uri"
    & curl.exe @args
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed: $Uri"
    }
}

function Copy-AppFiles {
    param(
        [Parameter(Mandatory = $true)][string]$Destination,
        [switch]$IncludeLauncher
    )

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $patterns = @(
        "animation_*.py",
        "README.md",
        "*说明.md",
        "*.cmd"
    )
    foreach ($pattern in $patterns) {
        Copy-Item -Path (Join-Path $Root $pattern) -Destination $Destination -Force
    }
    if ($IncludeLauncher) {
        Copy-Item -LiteralPath $LauncherExe -Destination (Join-Path $Destination "SmartGIF.exe") -Force
    }
    $webuiDir = Join-Path $Destination "webui"
    New-Item -ItemType Directory -Force -Path $webuiDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $Root "webui\dist") -Destination $webuiDir -Recurse -Force
}

function Expand-CleanArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Archive,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    Remove-Item -LiteralPath $Destination -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $Destination -Force
}

function Copy-DirectoryContents {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item -Path (Join-Path $Source "*") -Destination $Destination -Recurse -Force
}

function Prepare-Runtime {
    param([Parameter(Mandatory = $true)][string]$Destination)

    $runtime = Join-Path $Destination "runtime"
    $pythonZip = Join-Path $CacheDir "python-$PythonVersion-embed-amd64.zip"
    $ffmpegZip = Join-Path $CacheDir "ffmpeg-release-essentials.zip"
    $webpZip = Join-Path $CacheDir "libwebp-$WebpVersion-windows-x64.zip"

    if (-not $SkipRuntimeDownload) {
        Invoke-Download -Uri $PythonUrl -OutFile $pythonZip
        Invoke-Download -Uri $FfmpegUrl -OutFile $ffmpegZip
        Invoke-Download -Uri $WebpUrl -OutFile $webpZip
    }

    if (-not (Test-Path -LiteralPath $pythonZip)) { throw "Missing $pythonZip" }
    if (-not (Test-Path -LiteralPath $ffmpegZip)) { throw "Missing $ffmpegZip" }
    if (-not (Test-Path -LiteralPath $webpZip)) { throw "Missing $webpZip" }

    $tmp = Join-Path $CacheDir "expanded"
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null

    $pythonDir = Join-Path $runtime "python"
    Expand-CleanArchive -Archive $pythonZip -Destination $pythonDir
    $pythonPathFile = Get-ChildItem -LiteralPath $pythonDir -Filter "python*._pth" | Select-Object -First 1
    if (-not $pythonPathFile) { throw "Unable to locate Python embedded ._pth file." }
    $pythonPathLines = Get-Content -LiteralPath $pythonPathFile.FullName
    if ($pythonPathLines -notcontains "..\..") {
        $pythonPathLines = @("..\..") + $pythonPathLines
        Set-Content -LiteralPath $pythonPathFile.FullName -Value $pythonPathLines -Encoding ASCII
    }

    $ffmpegTmp = Join-Path $tmp "ffmpeg"
    Expand-CleanArchive -Archive $ffmpegZip -Destination $ffmpegTmp
    $ffmpegRoot = Get-ChildItem -LiteralPath $ffmpegTmp -Directory | Select-Object -First 1
    if (-not $ffmpegRoot) { throw "Unable to locate FFmpeg directory in archive." }
    Copy-DirectoryContents -Source $ffmpegRoot.FullName -Destination (Join-Path $runtime "ffmpeg")

    $webpTmp = Join-Path $tmp "webp"
    Expand-CleanArchive -Archive $webpZip -Destination $webpTmp
    $webpRoot = Get-ChildItem -LiteralPath $webpTmp -Directory | Select-Object -First 1
    if (-not $webpRoot) { throw "Unable to locate WebP tools directory in archive." }
    Copy-DirectoryContents -Source $webpRoot.FullName -Destination (Join-Path $runtime "webp")
}

function Write-Lines {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string[]]$Lines
    )
    Set-Content -LiteralPath $Path -Value ($Lines -join [Environment]::NewLine) -Encoding UTF8
}

function New-Zip {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDir,
        [Parameter(Mandatory = $true)][string]$ZipPath
    )
    Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
    Compress-Archive -LiteralPath $SourceDir -DestinationPath $ZipPath -CompressionLevel Optimal
    $item = Get-Item -LiteralPath $ZipPath
    [pscustomobject]@{
        Name = $item.Name
        SizeMB = [math]::Round($item.Length / 1MB, 2)
        Path = $item.FullName
    }
}

Remove-Item -LiteralPath $StageDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
Build-Launcher

Copy-AppFiles -Destination $StandardDir
Write-Lines -Path (Join-Path $StandardDir "Windows-Standard-README.md") -Lines @(
    "# SmartGIF Windows Standard",
    "",
    "Small package without bundled runtime.",
    "",
    "Requirements:",
    "- Windows",
    "- Python 3.10+",
    "- ffmpeg and ffprobe available in PATH",
    "",
    "Double-click the .cmd launcher, then open http://localhost:8765."
)

Copy-AppFiles -Destination $EasyDir -IncludeLauncher
Prepare-Runtime -Destination $EasyDir
Write-Lines -Path (Join-Path $EasyDir "Windows-Easy-OneClick-README.md") -Lines @(
    "# SmartGIF Windows Easy OneClick",
    "",
    "This package bundles Python, FFmpeg, ffprobe, and img2webp.",
    "It also includes SmartGIF.exe for one-click launch.",
    "",
    "Usage:",
    "1. Extract the whole folder.",
    "2. Double-click SmartGIF.exe.",
    "3. Open http://localhost:8765.",
    "",
    "The launcher only prepends local runtime paths for this process and does not change system PATH."
)

$standardZip = Join-Path $StageDir "SmartGIF-Windows-Standard-$Version.zip"
$easyZip = Join-Path $StageDir "SmartGIF-Windows-Easy-OneClick-$Version.zip"
$outputs = @()
$outputs += New-Zip -SourceDir $StandardDir -ZipPath $standardZip
$outputs += New-Zip -SourceDir $EasyDir -ZipPath $easyZip

$outputs | Format-Table -AutoSize
