param(
  [Parameter(Mandatory = $true)]
  [string]$Action,

  [string]$Session = "popo-sheet-task",

  [string]$ArgsJson = "{}",

  [string]$ArgsPath = "",

  [string]$OutPath = "",

  [string]$CopyScreenshotTo = "",

  [string]$ScreenshotName = ""
)

$ErrorActionPreference = "Stop"
$endpoint = "http://127.0.0.1:10086/command"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if ($ArgsPath) {
  $ArgsJson = Get-Content -Raw -Encoding UTF8 -LiteralPath $ArgsPath
}

if (-not $OutPath) {
  $outDir = Join-Path (Get-Location) "webbridge-artifacts"
  New-Item -ItemType Directory -Force -Path $outDir | Out-Null
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
  $safeAction = $Action -replace "[^A-Za-z0-9_.-]", "_"
  $OutPath = Join-Path $outDir "$stamp-$safeAction.response.json"
}

$outParent = Split-Path -Parent $OutPath
if ($outParent) {
  New-Item -ItemType Directory -Force -Path $outParent | Out-Null
}

$argsObj = $ArgsJson | ConvertFrom-Json
$bodyObj = [ordered]@{
  action = $Action
  args = $argsObj
  session = $Session
}

$requestJson = $bodyObj | ConvertTo-Json -Depth 100 -Compress
$requestPath = Join-Path $env:TEMP ("webbridge-req-{0}.json" -f ([guid]::NewGuid().ToString("N")))
[System.IO.File]::WriteAllText($requestPath, $requestJson, $utf8NoBom)

try {
  curl.exe -s -X POST $endpoint `
    -H "Content-Type: application/json" `
    --data-binary "@$requestPath" `
    -o $OutPath
} finally {
  Remove-Item -LiteralPath $requestPath -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $OutPath)) {
  throw "WebBridge response was not written: $OutPath"
}

$response = Get-Content -Raw -Encoding UTF8 -LiteralPath $OutPath | ConvertFrom-Json
$result = [ordered]@{
  responsePath = (Resolve-Path -LiteralPath $OutPath).Path
  ok = $response.ok
  action = $Action
}

if ($Action -eq "screenshot") {
  $returnedPath = $response.data.path
  $result.returnedScreenshotPath = $returnedPath
  if (-not $returnedPath -or -not (Test-Path -LiteralPath $returnedPath)) {
    throw "Screenshot response did not include a usable file path. Response: $OutPath"
  }

  if ($CopyScreenshotTo) {
    New-Item -ItemType Directory -Force -Path $CopyScreenshotTo | Out-Null
    if (-not $ScreenshotName) {
      $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
      $ScreenshotName = "$stamp-screenshot.png"
    }
    $copyPath = Join-Path $CopyScreenshotTo $ScreenshotName
    Copy-Item -LiteralPath $returnedPath -Destination $copyPath -Force
    $result.copiedScreenshotPath = (Resolve-Path -LiteralPath $copyPath).Path
  }
}

$result | ConvertTo-Json -Depth 10 -Compress
