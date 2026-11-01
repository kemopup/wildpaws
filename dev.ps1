[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'doctor', 'up', 'watch', 'down', 'logs', 'status', 'test', 'check')]
    [string]$Action = 'up'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = $PSScriptRoot
$localEnvPath = Join-Path $repoRoot 'dev\.env'
$localConfigPath = Join-Path $repoRoot 'dev\config.local.yaml'
$originalProcessPath = $env:PATH

function New-DevSecret {
    $randomBytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($randomBytes) } finally { $generator.Dispose() }
    return ([BitConverter]::ToString($randomBytes)).Replace('-', '').ToLowerInvariant()
}

function Initialize-DevConfig {
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    if (-not (Test-Path -LiteralPath $localEnvPath)) {
        $contents = "DEV_HTTP_PORT=8088`nDEV_DB_PASSWORD=$(New-DevSecret)`n"
        [IO.File]::WriteAllText($localEnvPath, $contents, $utf8)
        Write-Host 'Created dev/.env with a local-only database password.'
    }
    if (-not (Test-Path -LiteralPath $localConfigPath)) {
        $template = [IO.File]::ReadAllText((Join-Path $repoRoot 'dev\config.example.yaml'))
        [IO.File]::WriteAllText($localConfigPath, $template.Replace('__DEV_SECRET__', (New-DevSecret)), $utf8)
        Write-Host 'Created dev/config.local.yaml with a local-only application secret.'
    }
}

function Find-Docker {
    $command = Get-Command docker -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw 'Docker Desktop is not installed. See doc/DEVELOPMENT.md for Windows setup.'
}

Push-Location $repoRoot
try {
    Initialize-DevConfig
    if ($Action -eq 'setup') {
        Write-Host 'Local configuration is ready. Run .\dev.cmd doctor, then .\dev.cmd watch.'
        return
    }

    $dockerExe = Find-Docker
    # Desktop's credential helper must also be discoverable in shells opened
    # before installation, even when docker.exe is invoked by absolute path.
    $env:PATH = (Split-Path -Parent $dockerExe) + [IO.Path]::PathSeparator + $env:PATH
    $contextName = (& $dockerExe context show | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not determine the Docker context.' }
    $endpoint = (& $dockerExe context inspect $contextName --format '{{.Endpoints.docker.Host}}' | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the Docker context.' }
    if (-not $endpoint.StartsWith('npipe://')) {
        throw "This script requires a local Windows Docker named pipe. Context '$contextName' uses '$endpoint'. Select Docker Desktop's local context first."
    }
    # Pin every call to the inspected local context, overriding DOCKER_HOST.
    $dockerArgs = @('--context', $contextName)
    $composeVersion = & $dockerExe @dockerArgs compose version --short
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose is unavailable. Install a current Docker Desktop release.' }
    $composeVersionText = ($composeVersion | Out-String).Trim().TrimStart('v') -replace '-.*$', ''
    if ([version]$composeVersionText -lt [version]'2.32.0') {
        throw 'Docker Compose 2.32 or newer is required for this development workflow.'
    }
    $composeArgs = @('compose', '--project-name', 'wildpaws-dev', '--env-file', $localEnvPath, '-f', (Join-Path $repoRoot 'compose.dev.yaml'))

    if ($Action -eq 'check') {
        & $dockerExe @dockerArgs @composeArgs config --quiet
        if ($LASTEXITCODE -ne 0) { throw 'Development Compose configuration is invalid.' }
        Write-Host 'Development Compose configuration is valid.'
        return
    }

    $engineOS = & $dockerExe @dockerArgs info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0) { throw 'Docker engine is unavailable. Start Docker Desktop with Linux containers, and finish WSL setup if prompted.' }
    if (($engineOS | Out-String).Trim() -ne 'linux') { throw 'Select Linux containers in Docker Desktop before starting this application.' }

    switch ($Action) {
        'doctor' {
            & $dockerExe @dockerArgs @composeArgs config --quiet
            if ($LASTEXITCODE -ne 0) { throw 'Development Compose configuration is invalid.' }
            Write-Host "Docker context: $contextName ($endpoint)"
            Write-Host "Compose version: $composeVersion"
            Write-Host 'Ready. Run .\dev.cmd watch.'
        }
        'up' { & $dockerExe @dockerArgs @composeArgs up --build --detach --wait }
        'watch' { & $dockerExe @dockerArgs @composeArgs up --build --watch }
        'down' { & $dockerExe @dockerArgs @composeArgs down }
        'logs' { & $dockerExe @dockerArgs @composeArgs logs --follow --tail 100 }
        'status' { & $dockerExe @dockerArgs @composeArgs ps }
        'test' { & $dockerExe @dockerArgs @composeArgs --profile test run --build --rm --no-deps tests }
    }
    if ($LASTEXITCODE -ne 0) { throw "Development command '$Action' failed (exit $LASTEXITCODE)." }
    if ($Action -eq 'up') { Write-Host 'Open http://localhost:8088 (or DEV_HTTP_PORT from dev/.env).' }
}
finally {
    $env:PATH = $originalProcessPath
    Pop-Location
}
