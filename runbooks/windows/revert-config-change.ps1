# revert_config_change: git-revert ONE commit in the config repository.
param(
    [string]$App,
    [string]$Commit,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$Rest
)
$script:RbAction = 'revert_config_change'
$script:RbDryRun = [bool]$DryRun
. (Join-Path $PSScriptRoot '_lib.ps1')
Assert-Args -App $App -Rest $Rest
Assert-Env @('LEDGERLINE_CONF_DIR')
if ($Commit -notmatch '^[0-9a-f]{7,40}$') { Stop-Usage '-Commit must be 7-40 hex characters' }

$repo = $env:LEDGERLINE_CONF_DIR
& git -C $repo rev-parse --git-dir *> $null
if ($LASTEXITCODE -ne 0) { Stop-Precondition "$repo is not a git repository" }
$full = (& git -C $repo rev-parse --verify --quiet "$Commit^{commit}" 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $full) { Stop-Precondition "commit $Commit does not exist" }
& git -C $repo merge-base --is-ancestor $full HEAD
if ($LASTEXITCODE -ne 0) { Stop-Precondition "commit $Commit is not on the current branch" }
& git -C $repo rev-parse --verify --quiet "$full^" *> $null
if ($LASTEXITCODE -ne 0) { Stop-Precondition 'cannot revert the initial commit' }
$touched = (& git -C $repo diff --name-only "$full^" $full | Out-String).Trim()
if ($touched -ne $script:RbConfigFileName) { Stop-Precondition "commit $Commit touches files other than $($script:RbConfigFileName)" }
$bodies = (& git -C $repo log --format=%B HEAD | Out-String)
if ($bodies -match "This reverts commit $full") { Stop-Precondition "commit $Commit was already reverted" }
$dirty = (& git -C $repo status --porcelain -- $script:RbConfigFileName | Out-String).Trim()
if ($dirty) { Stop-Precondition 'config has uncommitted local edits; refusing to revert on top of them' }

$keys = (& git -C $repo diff -U0 "$full^" $full -- $script:RbConfigFileName |
    Where-Object { $_ -match '^[+-][^+-]' } |
    ForEach-Object { ($_.Substring(1) -split '=', 2)[0] } | Sort-Object -Unique) -join ','
$headBefore = (& git -C $repo rev-parse --short=12 HEAD | Out-String).Trim()
$before = [ordered]@{ head = $headBefore; commit = $full.Substring(0, 12); keys = $keys }

# rehearse the exact revert in a throwaway clone so the dry run matches the real run
$scratch = Join-Path ([IO.Path]::GetTempPath()) ("oncallify-" + [guid]::NewGuid().ToString('N'))
try {
    & git clone -q $repo (Join-Path $scratch 'repo') *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Precondition 'could not clone config repo for rehearsal' }
    & git -C (Join-Path $scratch 'repo') -c user.name=rehearsal -c user.email=r@x revert --no-edit $full *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Precondition "commit $Commit no longer reverts cleanly (later commits touch the same lines)" }
} finally {
    Remove-Item -Recurse -Force -LiteralPath $scratch -ErrorAction SilentlyContinue
}

if ($DryRun) {
    Write-Result -Code 0 -Ok $true -Changed $false -Detail "would revert $($full.Substring(0, 12)) (keys: $keys)" -Before $before
}

try { Invoke-Git revert --no-edit $full | Out-Null }
catch {
    & git -C $repo revert --abort *> $null
    Stop-Execution 'git revert failed'
}
$headAfter = (& git -C $repo rev-parse --short=12 HEAD | Out-String).Trim()
Write-Result -Code 0 -Ok $true -Changed $true -Detail "reverted $($full.Substring(0, 12)) (keys: $keys)" `
    -Before $before -After ([ordered]@{ head = $headAfter; keys = $keys })
