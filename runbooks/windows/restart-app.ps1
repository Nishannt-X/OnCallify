# restart_app: reload the application context through the Tomcat manager text API.
param(
    [string]$App,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$Rest
)
$script:RbAction = 'restart_app'
$script:RbDryRun = [bool]$DryRun
. (Join-Path $PSScriptRoot '_lib.ps1')
Assert-Args -App $App -Rest $Rest
Assert-Env @('TOMCAT_URL', 'ONCALLIFY_VAULT')

try { $list = Invoke-Manager 'list' } catch { Stop-Precondition "Tomcat manager is not reachable at $env:TOMCAT_URL" }
$line = ($list -split "`n" | Where-Object { $_ -like "/${App}:*" } | Select-Object -First 1)
if (-not $line) { Stop-Precondition "context /$App is not deployed" }
$state = ($line -split ':')[1]
$before = [ordered]@{ context = "/$App"; state = $state }

if ($DryRun) {
    Write-Result -Code 0 -Ok $true -Changed $false -Detail "would reload /$App (currently $state) via the Tomcat manager" -Before $before
}

try { $out = Invoke-Manager "reload?path=/$App" } catch { Stop-Execution "manager call failed: $($_.Exception.Message)" }
if (-not $out.StartsWith('OK')) { Stop-Execution $out.Trim() }
$afterLine = ((Invoke-Manager 'list') -split "`n" | Where-Object { $_ -like "/${App}:*" } | Select-Object -First 1)
$after = [ordered]@{ context = "/$App"; state = ($afterLine -split ':')[1] }
Write-Result -Code 0 -Ok $true -Changed $true -Detail $out.Trim() -Before $before -After $after
