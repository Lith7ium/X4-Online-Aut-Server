[CmdletBinding()]
param(
    [Parameter()] [string] $Game,
    [Parameter(Mandatory = $true)] [string] $ConnectionFile,
    [string] $WorkRoot,
    [string] $Python = 'python',
    [string] $NetworkDll,
    [ValidatePattern('^[0-9a-fA-F]{64}$')] [string] $NetworkDllSha256,
    [ValidateRange(1, 3600)] [int] $TimeoutSeconds = 1800,
    [ValidateRange(1, 3600)] [int] $ObserveSeconds = 30,
    [switch] $ChooseCompanyShip,
    [switch] $CompanyMotion,
    [ValidateRange(1, 200000)] [int] $CompanyHullPlaybackIndex
)

$ErrorActionPreference = 'Stop'
if ($PSBoundParameters.ContainsKey('CompanyHullPlaybackIndex') -and -not $CompanyMotion) {
    throw 'CompanyHullPlaybackIndex requires CompanyMotion.'
}
if (($ChooseCompanyShip -or $CompanyMotion) -and -not $PSBoundParameters.ContainsKey('ObserveSeconds')) {
    $ObserveSeconds = 900
}
if ($ObserveSeconds -ge $TimeoutSeconds) {
    throw 'Allow time for the initial join before the observation window.'
}
$packageRoot = Split-Path -Parent $PSScriptRoot
$client = Join-Path $PSScriptRoot 'dev/authority_windows_fixture_client.py'
if (-not $PSBoundParameters.ContainsKey('Game')) {
    $discovery = @(& $Python $client '--find-game')
    if ($LASTEXITCODE -ne 0 -or $discovery.Count -ne 1) {
        throw 'Could not find X4 Foundations in Steam libraries. Pass -Game with its installed folder.'
    }
    $Game = ($discovery[0] | ConvertFrom-Json).game
    if ($Game -isnot [string] -or -not [IO.Path]::IsPathRooted($Game)) {
        throw 'Steam discovery did not return an absolute game folder. Pass -Game.'
    }
}
$gameDirectory = Get-Item -LiteralPath $Game
$connection = Get-Item -LiteralPath $ConnectionFile
if (-not $gameDirectory.PSIsContainer -or $connection.PSIsContainer) {
    throw 'Use an installed X4 game directory and a private connection.json file.'
}
$parent = $gameDirectory.Parent
if ($WorkRoot) {
    $parent = Get-Item -LiteralPath $WorkRoot
}
if (-not $parent.PSIsContainer -or
    [IO.Path]::GetPathRoot($parent.FullName) -ne [IO.Path]::GetPathRoot($gameDirectory.FullName)) {
    throw 'WorkRoot must be an existing directory on the game drive.'
}

if ($NetworkDll -or $NetworkDllSha256) {
    if (-not $NetworkDll -or -not $NetworkDllSha256) {
        throw 'Pass both NetworkDll and its independently verified NetworkDllSha256.'
    }
} else {
    $build = Get-Content -LiteralPath (Join-Path $packageRoot 'BUILD.json') -Raw | ConvertFrom-Json
    if ($null -eq $build.network_dll) {
        throw 'Pass NetworkDll and its independently verified NetworkDllSha256.'
    }
    $NetworkDll = Join-Path $packageRoot 'network/x4lan_net_64.dll'
    $NetworkDllSha256 = $build.network_dll.sha256
}
$dll = Get-Item -LiteralPath $NetworkDll
if ($dll.PSIsContainer -or (Get-FileHash -LiteralPath $dll.FullName -Algorithm SHA256).Hash -ne $NetworkDllSha256) {
    throw 'Windows network DLL differs from its expected SHA-256.'
}

$runId = 'x4-authority-' + [guid]::NewGuid().ToString('N')
$clientArgs = @(
    '--isolated-fixture-test',
    '--timeout', [string]$TimeoutSeconds,
    '--observe-seconds', [string]$ObserveSeconds,
    '--game', $gameDirectory.FullName,
    '--connection-file', $connection.FullName,
    '--view-container', (Join-Path $parent.FullName "$runId-view"),
    '--out', (Join-Path $parent.FullName "$runId-run"),
    '--network-dll', $dll.FullName,
    '--network-dll-sha256', $NetworkDllSha256.ToLowerInvariant()
)
if ($CompanyMotion) {
    $clientArgs += '--company-motion'
    if ($PSBoundParameters.ContainsKey('CompanyHullPlaybackIndex')) {
        $clientArgs += @('--company-hull-playback-index', [string]$CompanyHullPlaybackIndex)
    }
} elseif ($ChooseCompanyShip) {
    $clientArgs += '--choose-company-ship'
}
& $Python $client @clientArgs
exit $LASTEXITCODE
