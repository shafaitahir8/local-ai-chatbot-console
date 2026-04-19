param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "start"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DataDir = Join-Path $ProjectRoot "data"
$LogsDir = Join-Path $ProjectRoot "logs"
$StateFile = Join-Path $DataDir "runtime_roles.json"
$EnvFile = Join-Path $ProjectRoot ".env"
$PythonPath = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $PythonPath)) {
    $PythonPath = "python"
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

function Import-DotEnv {
    if (-not (Test-Path $EnvFile)) {
        return
    }

    foreach ($line in Get-Content -Path $EnvFile) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        $trimmed = $line.Trim()
        if ($trimmed.StartsWith("#")) {
            continue
        }

        $parts = $trimmed.Split("=", 2)
        if ($parts.Count -ne 2) {
            continue
        }

        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ([string]::IsNullOrWhiteSpace($name)) {
            continue
        }

        if (-not (Test-Path "Env:$name")) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

Import-DotEnv

function Read-State {
    if (-not (Test-Path $StateFile)) {
        return @{}
    }
    try {
        $raw = Get-Content -Path $StateFile -Raw -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($raw)) {
            return @{}
        }
        $state = ConvertTo-Hashtable ($raw | ConvertFrom-Json)
        if ($state -is [System.Collections.IDictionary]) {
            return $state
        }
        return @{}
    } catch {
        return @{}
    }
}

function ConvertTo-Hashtable($InputObject) {
    if ($null -eq $InputObject) {
        return $null
    }
    if ($InputObject -is [string] -or $InputObject -is [ValueType]) {
        return $InputObject
    }
    if ($InputObject -is [System.Collections.IDictionary]) {
        $hash = @{}
        foreach ($key in $InputObject.Keys) {
            $hash[$key] = ConvertTo-Hashtable $InputObject[$key]
        }
        return $hash
    }
    if ($InputObject -is [System.Collections.IEnumerable] -and -not ($InputObject -is [string])) {
        $items = @()
        foreach ($item in $InputObject) {
            $items += ,(ConvertTo-Hashtable $item)
        }
        return $items
    }
    if ($InputObject.PSObject) {
        $hash = @{}
        foreach ($property in $InputObject.PSObject.Properties) {
            $hash[$property.Name] = ConvertTo-Hashtable $property.Value
        }
        return $hash
    }
    return $InputObject
}

function Write-State([hashtable]$State) {
    $json = $State | ConvertTo-Json -Depth 5
    Set-Content -Path $StateFile -Value $json -Encoding UTF8
}

function Test-ProcessAlive([int]$ProcessId) {
    try {
        $null = Get-Process -Id $ProcessId -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Get-RoleDefinitions {
    $tunnelEnabled = $false
    $tunnelProvider = [string]($env:TUNNEL_PROVIDER)
    if (-not [string]::IsNullOrWhiteSpace($tunnelProvider)) {
        $tunnelEnabled = $true
    }

    $ollamaEnabled = $false
    try {
        $null = Get-Command ollama -ErrorAction Stop
        $ollamaEnabled = $true
    } catch {
        $ollamaEnabled = $false
    }

    return @(
        @{ Name = "scheduler"; Enabled = $true },
        @{ Name = "scraper-worker"; Enabled = $true },
        @{ Name = "embedding-worker"; Enabled = $true },
        @{ Name = "gateway-agent"; Enabled = $true },
        @{ Name = "tunnel-agent"; Enabled = $tunnelEnabled },
        @{ Name = "ollama-sentinel"; Enabled = $ollamaEnabled }
    )
}

function Start-ManagedRoles {
    $state = Read-State
    foreach ($roleDef in Get-RoleDefinitions) {
        if (-not $roleDef.Enabled) {
            continue
        }

        $role = [string]$roleDef.Name
        if ($state.ContainsKey($role)) {
            $existingPid = [int]$state[$role].pid
            if ($existingPid -gt 0 -and (Test-ProcessAlive $existingPid)) {
                continue
            }
        }

        $stdoutLog = Join-Path $LogsDir ("{0}.stdout.log" -f $role)
        $stderrLog = Join-Path $LogsDir ("{0}.stderr.log" -f $role)
        $process = Start-Process `
            -FilePath $PythonPath `
            -ArgumentList @("-m", "app.runtime_supervisor", $role) `
            -WorkingDirectory $ProjectRoot `
            -WindowStyle Hidden `
            -PassThru `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog

        $state[$role] = @{
            pid = $process.Id
            stdout_log = $stdoutLog
            stderr_log = $stderrLog
            started_at = [DateTime]::UtcNow.ToString("o")
        }
        Start-Sleep -Milliseconds 200
    }
    Write-State $state
    return $state
}

function Stop-ManagedRoles {
    $state = Read-State
    if (-not $state -or $state.Count -eq 0) {
        Write-State @{}
        return @{}
    }
    foreach ($role in @($state.Keys)) {
        if ([string]::IsNullOrWhiteSpace([string]$role)) {
            continue
        }
        $entry = $state[$role]
        if (-not $entry) {
            continue
        }
        $processId = if ($entry.pid) { [int]$entry.pid } else { 0 }
        if ($processId -gt 0 -and (Test-ProcessAlive $processId)) {
            try {
                Stop-Process -Id $processId -Force -ErrorAction Stop
            } catch {
            }
        }
    }
    Write-State @{}
    return @{}
}

function Get-ManagedRoleStatus {
    $state = Read-State
    $status = @{}
    foreach ($roleDef in Get-RoleDefinitions) {
        $role = [string]$roleDef.Name
        $entry = $state[$role]
        $processId = if ($entry) { [int]$entry.pid } else { 0 }
        $status[$role] = @{
            enabled = [bool]$roleDef.Enabled
            pid = $processId
            running = ($processId -gt 0 -and (Test-ProcessAlive $processId))
            stdout_log = if ($entry) { $entry.stdout_log } else { $null }
            stderr_log = if ($entry) { $entry.stderr_log } else { $null }
        }
    }
    return $status
}

switch ($Action) {
    "start" {
        Start-ManagedRoles | Out-Null
        (Get-ManagedRoleStatus | ConvertTo-Json -Depth 5 -Compress)
    }
    "stop" {
        Stop-ManagedRoles | Out-Null
        (Get-ManagedRoleStatus | ConvertTo-Json -Depth 5 -Compress)
    }
    "status" {
        (Get-ManagedRoleStatus | ConvertTo-Json -Depth 5 -Compress)
    }
}
