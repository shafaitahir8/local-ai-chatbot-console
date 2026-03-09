param(
    [ValidateSet("cloudflare", "ngrok")]
    [string]$Provider = "cloudflare",
    [string]$CloudflareTunnelToken = "",
    [string]$CloudflarePublicBaseUrl = "",
    [string]$NgrokAuthtoken = "",
    [string]$NgrokStaticDomain = "",
    [string]$TunnelSharedSecret = "",
    [string]$BotId = "",
    [string]$EnvPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $EnvPath) {
    $EnvPath = Join-Path $PSScriptRoot "..\.env"
}
$EnvPath = [System.IO.Path]::GetFullPath($EnvPath)

if (Test-Path $EnvPath) {
    $lines = [System.Collections.Generic.List[string]]::new()
    Get-Content -Path $EnvPath | ForEach-Object { [void]$lines.Add($_) }
} else {
    $lines = [System.Collections.Generic.List[string]]::new()
}

function Set-EnvValue {
    param(
        [System.Collections.Generic.List[string]]$FileLines,
        [string]$Key,
        [string]$Value
    )

    $pattern = "^\s*" + [Regex]::Escape($Key) + "\s*="
    $updated = $false
    for ($i = 0; $i -lt $FileLines.Count; $i++) {
        if ($FileLines[$i] -match $pattern) {
            $FileLines[$i] = "$Key=$Value"
            $updated = $true
            break
        }
    }
    if (-not $updated) {
        [void]$FileLines.Add("$Key=$Value")
    }
}

function Ensure-SharedSecret {
    param([string]$Candidate)
    if ($Candidate) {
        return $Candidate
    }
    $bytes = New-Object byte[] 16
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

$normalizedProvider = $Provider.Trim().ToLowerInvariant()
$finalSecret = Ensure-SharedSecret -Candidate $TunnelSharedSecret

if ($normalizedProvider -eq "cloudflare") {
    if (-not $CloudflareTunnelToken.Trim()) {
        throw "Cloudflare mode requires -CloudflareTunnelToken."
    }
    if (-not $CloudflarePublicBaseUrl.Trim()) {
        throw "Cloudflare mode requires -CloudflarePublicBaseUrl."
    }

    $publicBase = $CloudflarePublicBaseUrl.Trim().TrimEnd("/")
    Set-EnvValue -FileLines $lines -Key "TUNNEL_PROVIDER" -Value "cloudflare"
    Set-EnvValue -FileLines $lines -Key "TUNNEL_API_ONLY" -Value "true"
    Set-EnvValue -FileLines $lines -Key "CLOUDFLARE_TUNNEL_TOKEN" -Value $CloudflareTunnelToken.Trim()
    Set-EnvValue -FileLines $lines -Key "CLOUDFLARE_PUBLIC_BASE_URL" -Value $publicBase
    Set-EnvValue -FileLines $lines -Key "TUNNEL_SHARED_SECRET" -Value $finalSecret

    if ($BotId.Trim()) {
        $chatEndpoint = "$publicBase/api/bot/$($BotId.Trim())/chat?token=$finalSecret"
        Write-Host "HubSpot API endpoint:" -ForegroundColor Green
        Write-Host $chatEndpoint -ForegroundColor Cyan
    }
}
elseif ($normalizedProvider -eq "ngrok") {
    if (-not $NgrokAuthtoken.Trim()) {
        throw "ngrok mode requires -NgrokAuthtoken."
    }
    if (-not $NgrokStaticDomain.Trim()) {
        throw "ngrok mode requires -NgrokStaticDomain."
    }

    $domain = $NgrokStaticDomain.Trim()
    if ($domain.StartsWith("https://", [System.StringComparison]::OrdinalIgnoreCase)) {
        $domain = $domain.Substring(8)
    } elseif ($domain.StartsWith("http://", [System.StringComparison]::OrdinalIgnoreCase)) {
        $domain = $domain.Substring(7)
    }
    $domain = $domain.Trim().Trim("/")
    $publicBase = "https://$domain"

    Set-EnvValue -FileLines $lines -Key "TUNNEL_PROVIDER" -Value "ngrok"
    Set-EnvValue -FileLines $lines -Key "TUNNEL_API_ONLY" -Value "true"
    Set-EnvValue -FileLines $lines -Key "NGROK_AUTHTOKEN" -Value $NgrokAuthtoken.Trim()
    Set-EnvValue -FileLines $lines -Key "NGROK_STATIC_DOMAIN" -Value $domain
    Set-EnvValue -FileLines $lines -Key "TUNNEL_SHARED_SECRET" -Value $finalSecret

    if ($BotId.Trim()) {
        $chatEndpoint = "$publicBase/api/bot/$($BotId.Trim())/chat?token=$finalSecret"
        Write-Host "HubSpot API endpoint:" -ForegroundColor Green
        Write-Host $chatEndpoint -ForegroundColor Cyan
    }
}

Set-Content -Path $EnvPath -Value $lines

Write-Host ""
Write-Host "Updated tunnel configuration in: $EnvPath" -ForegroundColor Green
Write-Host "Provider: $normalizedProvider" -ForegroundColor Green
Write-Host "TUNNEL_API_ONLY=true and TUNNEL_SHARED_SECRET are enabled." -ForegroundColor Yellow
Write-Host "Restart start_console.bat to apply changes." -ForegroundColor Yellow
