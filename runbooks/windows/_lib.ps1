# Shared helpers for the OnCallify PowerShell runbooks. Dot-source it; never run it.
#
# Same contract as the bash runbooks in ../linux:
#   args    -App NAME [-Commit SHA] [-Size N] [-DryRun]
#   stdout  one JSON object on the last line: ok, action, dry_run, changed, detail, before, after
#   exit    0 ok | 2 usage | 3 precondition failed | 4 execution failed
#
# Environment: LEDGERLINE_CONF_DIR, LEDGERLINE_SECRET_FILE, ONCALLIFY_VAULT, TOMCAT_URL,
# TOMCAT_MANAGER_USER, TOMCAT_MANAGER_PASSWORD_FILE, RUNBOOK_ACTOR.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RbKnownApps = @('ledgerline')
$script:RbConfigFileName = 'ledgerline.properties'
$script:RbActor = if ($env:RUNBOOK_ACTOR) { $env:RUNBOOK_ACTOR } else { 'oncallify-runbook' }
if (-not (Get-Variable -Name RbAction -Scope Script -ErrorAction SilentlyContinue)) { $script:RbAction = 'unknown' }
if (-not (Get-Variable -Name RbDryRun -Scope Script -ErrorAction SilentlyContinue)) { $script:RbDryRun = $false }

function Write-Result {
    param(
        [int]$Code,
        [bool]$Ok,
        [bool]$Changed,
        [string]$Detail,
        [System.Collections.IDictionary]$Before = @{},
        [System.Collections.IDictionary]$After = @{}
    )
    $obj = [ordered]@{
        ok      = $Ok
        action  = $script:RbAction
        dry_run = [bool]$script:RbDryRun
        changed = $Changed
        detail  = $Detail
        before  = $Before
        after   = $After
    }
    [Console]::Out.WriteLine(($obj | ConvertTo-Json -Compress -Depth 4))
    exit $Code
}

function Stop-Usage([string]$Message) { Write-Result -Code 2 -Ok $false -Changed $false -Detail "usage: $Message" }
function Stop-Precondition([string]$Message) { Write-Result -Code 3 -Ok $false -Changed $false -Detail "precondition failed: $Message" }
function Stop-Execution([string]$Message) { Write-Result -Code 4 -Ok $false -Changed $false -Detail "execution failed: $Message" }

function Assert-Args {
    param([string]$App, [object[]]$Rest)
    if ($Rest -and $Rest.Count -gt 0) { Stop-Usage "unknown argument $($Rest[0])" }
    if (-not $App) { Stop-Usage '-App is required' }
    if ($script:RbKnownApps -notcontains $App) { Stop-Usage "unknown app $App" }
}

function Assert-Env([string[]]$Names) {
    foreach ($n in $Names) {
        if (-not [Environment]::GetEnvironmentVariable($n)) { Stop-Precondition "environment variable $n is not set" }
    }
}

function Get-ConfigFile { Join-Path $env:LEDGERLINE_CONF_DIR $script:RbConfigFileName }

function Get-Prop([string]$Key) {
    foreach ($line in Get-Content -LiteralPath (Get-ConfigFile)) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#') -or $t.StartsWith('!')) { continue }
        $i = $t.IndexOf('=')
        if ($i -gt 0 -and $t.Substring(0, $i).Trim() -eq $Key) { return $t.Substring($i + 1).Trim() }
    }
    return ''
}

function Invoke-Git {
    $out = & git -C $env:LEDGERLINE_CONF_DIR -c "user.name=$script:RbActor" -c "user.email=$script:RbActor@oncallify.local" @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' ') failed: $out" }
    return $out
}

function Move-Atomic([string]$Source, [string]$Destination) {
    # pwsh 7 (.NET Core) has an overwrite overload; Windows PowerShell 5.1 does not
    try { [System.IO.File]::Move($Source, $Destination, $true) }
    catch [System.Management.Automation.MethodException] { [System.IO.File]::Replace($Source, $Destination, $null) }
}

function Set-PropCommit([string]$Key, [string]$Value, [string]$Message) {
    $file = Get-ConfigFile
    $lines = [System.Collections.Generic.List[string]]::new()
    $done = $false
    foreach ($line in Get-Content -LiteralPath $file) {
        $t = $line.Trim()
        $i = $t.IndexOf('=')
        if (-not $t.StartsWith('#') -and $i -gt 0 -and $t.Substring(0, $i).Trim() -eq $Key) {
            $lines.Add("$Key=$Value"); $done = $true
        } else { $lines.Add($line) }
    }
    if (-not $done) { $lines.Add("$Key=$Value") }
    $tmp = Join-Path $env:LEDGERLINE_CONF_DIR (".ledgerline." + [guid]::NewGuid().ToString('N'))
    [System.IO.File]::WriteAllText($tmp, (($lines -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))
    Move-Atomic $tmp $file
    Invoke-Git add $script:RbConfigFileName | Out-Null
    Invoke-Git commit -q -m $Message | Out-Null
    return ((Invoke-Git rev-parse --short=12 HEAD) | Out-String).Trim()
}

function Get-Fingerprint([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant().Substring(0, 12)
}

function Invoke-Manager([string]$PathAndQuery) {
    $pwFile = if ($env:TOMCAT_MANAGER_PASSWORD_FILE) { $env:TOMCAT_MANAGER_PASSWORD_FILE } else { Join-Path $env:ONCALLIFY_VAULT 'tomcat-manager' }
    if (-not (Test-Path -LiteralPath $pwFile)) { Stop-Precondition "manager password file $pwFile is not readable" }
    $user = if ($env:TOMCAT_MANAGER_USER) { $env:TOMCAT_MANAGER_USER } else { 'oncallify-runbook' }
    $pair = "${user}:$((Get-Content -LiteralPath $pwFile -Raw).Trim())"
    $auth = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "$env:TOMCAT_URL/manager/text/$PathAndQuery" `
        -Headers @{ Authorization = $auth } -TimeoutSec 60
    return [string]$resp.Content
}
