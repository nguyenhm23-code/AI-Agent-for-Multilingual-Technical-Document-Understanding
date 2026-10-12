param(
    [string]$Index = "",
    [string]$Questions = "evaluation/slide_questions.json",
    [string]$Config = "configs/rag.local.json",
    [string]$Output = "",
    [int]$Limit = 0
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Project .venv/Scripts/python.exe is missing.'
}
if ($Limit -lt 0) { throw 'Limit must be positive, or 0 for all questions.' }
$testArgs = @('-m', 'denso_a3', 'rag-evaluate-slides', '--questions', $Questions, '--config', $Config)
if ($Index) { $testArgs += @('--index', $Index) }
if ($Output) { $testArgs += @('--output', $Output) }
if ($Limit -gt 0) { $testArgs += @('--limit', "$Limit") }
Push-Location -LiteralPath $projectRoot
try {
    & $pythonExe @testArgs
    $testExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($testExitCode -ne 0) {
    Write-Warning "Exit code $testExitCode. If inference started, the HTML preserves completed attempts and errors."
}
exit $testExitCode
