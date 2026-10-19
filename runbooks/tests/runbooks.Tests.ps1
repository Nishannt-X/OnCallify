# Pester 5 tests for the PowerShell runbooks. Mirrors tests/runbooks.bats so both
# implementations are held to the same contract. Run: Invoke-Pester runbooks/tests

BeforeAll {
    $script:Rb = Join-Path $PSScriptRoot '..' 'windows'
    $script:Shell = (Get-Process -Id $PID).Path

    function Invoke-Runbook([string]$Name, [string[]]$RbArgs) {
        $out = & $script:Shell -NoProfile -NonInteractive -File (Join-Path $script:Rb "$Name.ps1") @RbArgs
        $last = ($out | Where-Object { $_ -like '{*' } | Select-Object -Last 1)
        [pscustomobject]@{ Code = $LASTEXITCODE; Json = ($last | ConvertFrom-Json); Raw = ($out -join "`n") }
    }

    function New-Commit([string]$From, [string]$To, [string]$Message) {
        $f = Join-Path $env:LEDGERLINE_CONF_DIR 'ledgerline.properties'
        (Get-Content -LiteralPath $f -Raw).Replace($From, $To) | Set-Content -LiteralPath $f -NoNewline
        & git -C $env:LEDGERLINE_CONF_DIR -c user.name=dev -c user.email=dev@x commit -qam $Message | Out-Null
        (& git -C $env:LEDGERLINE_CONF_DIR rev-parse --short=12 HEAD | Out-String).Trim()
    }
}

BeforeEach {
    $script:T = Join-Path ([IO.Path]::GetTempPath()) ("rb-" + [guid]::NewGuid().ToString('N'))
    $env:LEDGERLINE_CONF_DIR = Join-Path $script:T 'conf'
    $env:ONCALLIFY_VAULT = Join-Path $script:T 'vault'
    $env:LEDGERLINE_SECRET_FILE = Join-Path $script:T 'app-secret'
    New-Item -ItemType Directory -Force -Path $env:LEDGERLINE_CONF_DIR, $env:ONCALLIFY_VAULT | Out-Null
    @(
        'db.url=jdbc:postgresql://127.0.0.1:5432/ledgerline'
        'pool.max=10'
        'pool.max.wait.ms=500'
        'fx.url=http://127.0.0.1:8080/ledgerline/fx/rate'
        'fx.timeout.ms=1000'
        'fx.fallback.enabled=false'
    ) -join "`n" | Set-Content -LiteralPath (Join-Path $env:LEDGERLINE_CONF_DIR 'ledgerline.properties') -NoNewline
    & git -C $env:LEDGERLINE_CONF_DIR init -q
    & git -C $env:LEDGERLINE_CONF_DIR -c user.name=dev -c user.email=dev@x add ledgerline.properties
    & git -C $env:LEDGERLINE_CONF_DIR -c user.name=dev -c user.email=dev@x commit -qm initial | Out-Null
    Set-Content -LiteralPath $env:LEDGERLINE_SECRET_FILE -Value 'old-password' -NoNewline
    Set-Content -LiteralPath (Join-Path $env:ONCALLIFY_VAULT 'ledgerline-db') -Value 'new-password' -NoNewline
}

AfterEach {
    Remove-Item -Recurse -Force -LiteralPath $script:T -ErrorAction SilentlyContinue
}

Describe 'contract' {
    It 'unknown app is a usage error' {
        $r = Invoke-Runbook 'enable-fx-fallback' @('-App', 'payroll')
        $r.Code | Should -Be 2
        $r.Json.ok | Should -BeFalse
    }
    It 'unexpected extra argument is a usage error' {
        $r = Invoke-Runbook 'enable-fx-fallback' @('-App', 'ledgerline', 'surprise')
        $r.Code | Should -Be 2
    }
}

Describe 'resize_pool' {
    It 'dry run changes nothing' {
        $r = Invoke-Runbook 'resize-pool' @('-App', 'ledgerline', '-Size', '20', '-DryRun')
        $r.Code | Should -Be 0
        $r.Json.changed | Should -BeFalse
        (Get-Content (Join-Path $env:LEDGERLINE_CONF_DIR 'ledgerline.properties')) | Should -Contain 'pool.max=10'
    }
    It 'commits the new size as the runbook actor' {
        $r = Invoke-Runbook 'resize-pool' @('-App', 'ledgerline', '-Size', '20')
        $r.Code | Should -Be 0
        (Get-Content (Join-Path $env:LEDGERLINE_CONF_DIR 'ledgerline.properties')) | Should -Contain 'pool.max=20'
        (& git -C $env:LEDGERLINE_CONF_DIR log -1 --format=%an | Out-String).Trim() | Should -Be 'oncallify-runbook'
    }
    It 'rejects out-of-range sizes' {
        (Invoke-Runbook 'resize-pool' @('-App', 'ledgerline', '-Size', '500')).Code | Should -Be 2
    }
}

Describe 'revert_config_change' {
    It 'reverts only the target commit and keeps a later unrelated one' {
        $bad = New-Commit 'pool.max=10' 'pool.max=2' 'shrink'
        New-Commit 'fx.timeout.ms=1000' 'fx.timeout.ms=1200' 'timeout' | Out-Null
        $r = Invoke-Runbook 'revert-config-change' @('-App', 'ledgerline', '-Commit', $bad)
        $r.Code | Should -Be 0
        $lines = Get-Content (Join-Path $env:LEDGERLINE_CONF_DIR 'ledgerline.properties')
        $lines | Should -Contain 'pool.max=10'
        $lines | Should -Contain 'fx.timeout.ms=1200'
    }
    It 'refuses to revert twice' {
        $bad = New-Commit 'pool.max=10' 'pool.max=2' 'shrink'
        (Invoke-Runbook 'revert-config-change' @('-App', 'ledgerline', '-Commit', $bad)).Code | Should -Be 0
        (Invoke-Runbook 'revert-config-change' @('-App', 'ledgerline', '-Commit', $bad)).Code | Should -Be 3
    }
    It 'refuses an unknown commit' {
        (Invoke-Runbook 'revert-config-change' @('-App', 'ledgerline', '-Commit', 'deadbeefdead')).Code | Should -Be 3
    }
}

Describe 'enable_fx_fallback' {
    It 'is idempotent' {
        (Invoke-Runbook 'enable-fx-fallback' @('-App', 'ledgerline')).Json.changed | Should -BeTrue
        (Invoke-Runbook 'enable-fx-fallback' @('-App', 'ledgerline')).Json.changed | Should -BeFalse
    }
}

Describe 'sync_db_credential' {
    It 'copies the vault secret and never prints it' {
        $r = Invoke-Runbook 'sync-db-credential' @('-App', 'ledgerline')
        $r.Code | Should -Be 0
        (Get-Content -LiteralPath $env:LEDGERLINE_SECRET_FILE -Raw) | Should -Be 'new-password'
        $r.Raw | Should -Not -Match 'new-password'
        $r.Raw | Should -Not -Match 'old-password'
    }
    It 'fails the precondition when the vault secret is missing' {
        Remove-Item (Join-Path $env:ONCALLIFY_VAULT 'ledgerline-db')
        (Invoke-Runbook 'sync-db-credential' @('-App', 'ledgerline')).Code | Should -Be 3
    }
}

Describe 'restart_app' {
    It 'fails the precondition when the manager is unreachable' {
        Set-Content -LiteralPath (Join-Path $env:ONCALLIFY_VAULT 'tomcat-manager') -Value 'pw' -NoNewline
        $env:TOMCAT_URL = 'http://127.0.0.1:1'
        (Invoke-Runbook 'restart-app' @('-App', 'ledgerline')).Code | Should -Be 3
    }
}
