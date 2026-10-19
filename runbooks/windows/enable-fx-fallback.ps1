# enable_fx_fallback: turn on the cached-rate fallback flag (committed to config).
param(
    [string]$App,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$Rest
)
$script:RbAction = 'enable_fx_fallback'
$script:RbDryRun = [bool]$DryRun
. (Join-Path $PSScriptRoot '_lib.ps1')
Assert-Args -App $App -Rest $Rest
Assert-Env @('LEDGERLINE_CONF_DIR')
if (-not (Test-Path -LiteralPath (Get-ConfigFile))) { Stop-Precondition "config file $(Get-ConfigFile) not found" }

$current = Get-Prop 'fx.fallback.enabled'
$shown = if ($current) { $current } else { 'false' }
$before = [ordered]@{ 'fx.fallback.enabled' = $shown }
if ($current -eq 'true') { Write-Result -Code 0 -Ok $true -Changed $false -Detail 'fx fallback is already enabled' -Before $before -After $before }
if ($DryRun) { Write-Result -Code 0 -Ok $true -Changed $false -Detail "would set fx.fallback.enabled $shown -> true" -Before $before }
try { $sha = Set-PropCommit 'fx.fallback.enabled' 'true' 'enable fx fallback (oncallify)' }
catch { Stop-Execution "could not commit config change: $($_.Exception.Message)" }
Write-Result -Code 0 -Ok $true -Changed $true -Detail "fx.fallback.enabled -> true in $sha" -Before $before `
    -After ([ordered]@{ 'fx.fallback.enabled' = 'true'; commit = $sha })
