# PROJECT INVISIBLE sync - Telemetry Footer.
# Mirrors the LIVE extension into both PROJECT INVISIBLE destinations:
#   1. ARCHIVE_DIR  full working copy + git history
#   2. GITHUB_DIR   publishable copy (no machine-specific config.json)
#   powershell -ExecutionPolicy Bypass -File "sync.ps1"
$ErrorActionPreference = "Stop"

$live    = "G:\FORGE UI NEO\sd-webui-forge-classic\extensions\project-invisible-telemetry-footer"
$archive = "D:\CODING PROJECTS\EXTENSIONS CODING\PROJECT INVISIBLE\TelemetryFooter"
$github  = "D:\CODING PROJECTS\EXTENSIONS CODING\PROJECT INVISIBLE\github\project-invisible-telemetry-footer"

function Run-Git {
    param([Parameter(Mandatory = $true)][string[]]$GitArgs)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & git @GitArgs 2>&1 | Out-Null
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

if (-not (Test-Path -LiteralPath $live)) { Write-Error "LIVE_EXT not found: $live" }

# .git MUST stay in the /XD list. The live folder sits inside Forge's own repo
# and therefore carries no .git of its own, so without this exclusion /MIR
# deletes the destination's .git on every run and the git init below silently
# recreates an empty repo - the archive then never holds more than one commit.
$exclude = @("/MIR", "/XD", "__pycache__", ".git", "/XF", "*.pyc", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")

New-Item -ItemType Directory -Path $archive -Force | Out-Null
robocopy $live $archive @exclude | Out-Null
if ($LASTEXITCODE -gt 7) { Write-Error "robocopy to ARCHIVE_DIR failed with code $LASTEXITCODE" }

# config.json is a per-machine hardware fingerprint the extension rewrites on
# first launch, so it is deliberately not published.
New-Item -ItemType Directory -Path $github -Force | Out-Null
robocopy $live $github @exclude "config.json" | Out-Null
if ($LASTEXITCODE -gt 7) { Write-Error "robocopy to GITHUB_DIR failed with code $LASTEXITCODE" }

if (-not (Test-Path -LiteralPath "$archive\.git")) { Run-Git @("-C", $archive, "init") | Out-Null }
Run-Git @("-C", $archive, "add", "-A") | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$code = Run-Git @("-C", $archive, "-c", "user.name=Project Invisible", "-c", "user.email=invisible@local", "commit", "-m", "sync $stamp")
if ($code -eq 0) { Write-Output "Archive committed: $stamp" } else { Write-Output "Nothing new to commit (exit $code)." }

Write-Output "Sync OK"
Write-Output "  LIVE_EXT   -> $archive"
Write-Output "  LIVE_EXT   -> $github"
