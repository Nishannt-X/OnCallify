# resize_pool: set pool.max (committed to the config repository).
param(
    [string]$App,
    [string]$Size,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$Rest
)
$script:RbAction = 'resize_pool'
$script:RbDryRun = [bool]$DryRun
. (Join-Path $PSScriptRoot '_lib.ps1')
Assert-Args -App $App -Rest $Rest
Assert-Env @('LEDGERLINE_CONF_DIR')
if ($Size -notmatch '^[0-9]+$') { Stop-Usage '-Size must be an integer' }
$n = [int]$Size
if ($n -lt 5 -or $n -gt 50) { Stop-Usage '-Size must be between 5 and 50' }
if (-not (Test-Path -LiteralPath (Get-ConfigFile))) { Stop-Precondition "config file $(Get-ConfigFile) not found" }

$current = Get-Prop 'pool.max'
$shown = if ($current) { $current } else { 'unset' }
$before = [ordered]@{ 'pool.max' = $shown }
if ($current -eq "$n") { Write-Result -Code 0 -Ok $true -Changed $false -Detail "pool.max is already $n" -Before $before -After $before }
if ($DryRun) { Write-Result -Code 0 -Ok $true -Changed $false -Detail "would set pool.max $shown -> $n" -Before $before }
try { $sha = Set-PropCommit 'pool.max' "$n" "resize pool.max $shown -> $n (oncallify)" }
catch { Stop-Execution "could not commit config change: $($_.Exception.Message)" }
Write-Result -Code 0 -Ok $true -Changed $true -Detail "pool.max $shown -> $n in $sha" -Before $before `
    -After ([ordered]@{ 'pool.max' = "$n"; commit = $sha })
