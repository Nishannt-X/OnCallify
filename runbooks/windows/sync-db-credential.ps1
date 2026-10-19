# sync_db_credential: copy the vault's CURRENT database password into the app's secret
# file (temp file + atomic replace). Secrets are never printed, only fingerprints.
param(
    [string]$App,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$Rest
)
$script:RbAction = 'sync_db_credential'
$script:RbDryRun = [bool]$DryRun
. (Join-Path $PSScriptRoot '_lib.ps1')
Assert-Args -App $App -Rest $Rest
Assert-Env @('ONCALLIFY_VAULT', 'LEDGERLINE_SECRET_FILE')

$vaultFile = Join-Path $env:ONCALLIFY_VAULT "$App-db"
if (-not (Test-Path -LiteralPath $vaultFile)) { Stop-Precondition "vault secret $vaultFile is not readable" }
if ((Get-Item -LiteralPath $vaultFile).Length -eq 0) { Stop-Precondition "vault secret $vaultFile is empty" }
$target = $env:LEDGERLINE_SECRET_FILE
if (-not (Test-Path -LiteralPath $target)) { Stop-Precondition "app secret file $target does not exist" }

$vaultFp = Get-Fingerprint $vaultFile
$appFp = Get-Fingerprint $target
$before = [ordered]@{ vault = $vaultFp; app = $appFp }
if ($vaultFp -eq $appFp) { Write-Result -Code 0 -Ok $true -Changed $false -Detail 'app credential already matches the vault' -Before $before -After $before }
if ($DryRun) { Write-Result -Code 0 -Ok $true -Changed $false -Detail "would replace app credential $appFp with vault credential $vaultFp" -Before $before }

$tmp = Join-Path (Split-Path -Parent $target) (".secret." + [guid]::NewGuid().ToString('N'))
try {
    [System.IO.File]::WriteAllBytes($tmp, [System.IO.File]::ReadAllBytes($vaultFile))
    Move-Atomic $tmp $target
} catch { Stop-Execution "could not replace ${target}: $($_.Exception.Message)" }
$newFp = Get-Fingerprint $target
if ($newFp -ne $vaultFp) { Stop-Execution 'credential did not match after write' }
Write-Result -Code 0 -Ok $true -Changed $true -Detail "app credential now matches vault ($vaultFp)" -Before $before `
    -After ([ordered]@{ vault = $vaultFp; app = $newFp })
