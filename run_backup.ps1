param(
    [switch]$Watch,
    [int]$IntervalSeconds = 300,
    [int]$Keep = 10
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$taskPython = $null
$taskPrefix = @()
$taskBundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $taskBundledPython -PathType Leaf) {
    $taskPython = $taskBundledPython
} else {
    $taskLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($taskLauncher) {
        $taskPython = $taskLauncher.Source
        $taskPrefix = @('-3')
    } else {
        $taskCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($taskCommand -and $taskCommand.Source -notlike '*\WindowsApps\*') {
            $taskPython = $taskCommand.Source
        }
    }
}
if (-not $taskPython) {
    Write-Host 'Python 3 was not found. Open Codex once to initialize its runtime, or install Python 3.9+.'
    exit 1
}
$taskArguments = @((Join-Path $PSScriptRoot 'backup_codex_chats.py'), '--interval', [string]$IntervalSeconds, '--keep', [string]$Keep)
if ($Watch) { $taskArguments += '--watch' }
& $taskPython @taskPrefix @taskArguments
exit $LASTEXITCODE
