$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$script = Join-Path $root "src\a_share_reporter.py"
$workdir = $root

$morningAction = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --mode morning --push" -WorkingDirectory $workdir
$closeAction = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --mode close --push" -WorkingDirectory $workdir

$morningTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:00
$closeTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 15:30

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "AShareMorningReport" -Action $morningAction -Trigger $morningTrigger -Settings $settings -Description "Generate and push A-share pre-market report." -Force
Register-ScheduledTask -TaskName "AShareCloseReport" -Action $closeAction -Trigger $closeTrigger -Settings $settings -Description "Generate and push A-share close report." -Force

Write-Host "Registered AShareMorningReport at 09:00 and AShareCloseReport at 15:30, Monday-Friday."
