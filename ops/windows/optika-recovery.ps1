[CmdletBinding()]
param(
    [string]$Docker = (Join-Path $env:ProgramFiles 'Docker/Docker/resources/bin/docker.exe')
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$appName = 'optika_timka_prod_app'
$dbName = 'optika_timka_prod_db'

function Write-RecoveryEvent([string]$Message) {
    Write-EventLog -LogName Application -Source 'Optika Timka Recovery' -EventId 1000 `
        -EntryType Information -Message $Message
}

$format = '{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.StartedAt}}'
if (-not (Test-Path -LiteralPath $Docker -PathType Leaf)) { throw 'Docker CLI not found.' }
try {
    # Windows PowerShell can promote native stderr to a terminating error.
    $ErrorActionPreference = 'Continue'
    $states = & $Docker --context desktop-linux inspect --format $format $appName $dbName 2>$null
    $inspectExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = 'Stop'
}
if ($inspectExit -ne 0) {
    # A running Desktop may still be initializing its Linux engine.
    if (Get-Process -Name 'Docker Desktop','com.docker.backend' -ErrorAction SilentlyContinue) { return }

    $launcher = Get-ScheduledTask -TaskName 'Start Docker Desktop' -TaskPath '\'
    $lastRun = ($launcher | Get-ScheduledTaskInfo).LastRunTime
    if ($lastRun -gt (Get-Date).AddMinutes(-3)) { return }
    Start-ScheduledTask -InputObject $launcher
    Write-RecoveryEvent 'Requested Docker Desktop startup because its engine was unavailable.'
    return
}

if (@($states).Count -ne 2) { throw 'Unexpected production container state.' }
$app = $states[0] -split '\|'
$db = $states[1] -split '\|'
if ($app[0] -ne "/$appName" -or $db[0] -ne "/$dbName") {
    throw 'Unexpected production container names.'
}

if ($db[1] -eq 'exited') {
    & $Docker --context desktop-linux start $dbName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not start PostgreSQL.' }
    Write-RecoveryEvent 'Started the existing PostgreSQL container.'
    return
}
if ($db[2] -ne 'healthy') { return }

if ($app[1] -eq 'exited') {
    & $Docker --context desktop-linux start $appName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the application.' }
    Write-RecoveryEvent 'Started the existing application container.'
} elseif ($app[2] -eq 'unhealthy' -and
    [DateTimeOffset]::Parse($app[3]) -lt [DateTimeOffset]::UtcNow.AddMinutes(-5)) {
    & $Docker --context desktop-linux restart --time 10 $appName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restart the unhealthy application.' }
    Write-RecoveryEvent 'Restarted the application after its Docker health check failed.'
}
