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
    [switch] $BaseGameOnly,
    [switch] $FreshCompanyProfile,
    [string] $GraphicsConfig,
    [switch] $ChooseCompanyShip,
    [ValidateRange(1, 200000)] [int] $CompanyShipIndex,
    [switch] $CompanyMotion,
    [switch] $CompanyCombat,
    [switch] $CompanyPurchases,
    [switch] $CompanyUndockTest,
    [switch] $CompanyTradeView,
    [switch] $CompanySales,
    [switch] $CompanyFleetOrders,
    [ValidateCount(2, 2)] [string[]] $ObserveRegisteredNpc,
    [switch] $TrialRegisteredNpcMeasurement,
    [switch] $CompanyCockpitPlayback,
    [switch] $CompanyCockpitContinuousPlayback,
    [ValidateRange(1, 200000)] [int] $CompanyCockpitPlaybackIndex,
    [ValidateRange(1, 200000)] [int] $CompanyHullPlaybackIndex,
    [switch] $CombatCapture
)

$ErrorActionPreference = 'Stop'
if ($CompanyCombat -and -not $CompanyMotion) {
    throw '-CompanyCombat requires -CompanyMotion'
}
if ($CompanyFleetOrders -and -not $CompanyPurchases) {
    throw '-CompanyFleetOrders requires -CompanyPurchases'
}
if ($CompanySales -and (-not $CompanyPurchases -or -not $CompanyTradeView)) {
    throw '-CompanySales requires -CompanyPurchases and -CompanyTradeView'
}
if ($CompanyTradeView -and -not $CompanyPurchases) {
    throw '-CompanyTradeView requires -CompanyPurchases'
}
if ($CompanyUndockTest -and -not $CompanyPurchases) {
    throw '-CompanyUndockTest requires -CompanyPurchases'
}
if ($ChooseCompanyShip -and $PSBoundParameters.ContainsKey('CompanyShipIndex')) {
    throw 'Choose either an interactive ship prompt or a fixed company ship index.'
}
if ($PSBoundParameters.ContainsKey('CompanyHullPlaybackIndex') -and -not $CompanyMotion) {
    throw 'CompanyHullPlaybackIndex requires CompanyMotion.'
}
if ($CompanyCockpitContinuousPlayback -and (-not $CompanyMotion -or -not $CompanyCockpitPlayback)) {
    throw 'CompanyCockpitContinuousPlayback requires CompanyMotion and CompanyCockpitPlayback.'
}
if ($CompanyCockpitPlayback) {
    if (-not $CompanyMotion) {
        throw 'CompanyCockpitPlayback requires CompanyMotion.'
    }
    if ($PSBoundParameters.ContainsKey('CompanyHullPlaybackIndex') -or $PSBoundParameters.ContainsKey('CompanyCockpitPlaybackIndex')) {
        throw 'Choose only one cockpit playback mode.'
    }
}
if ($PSBoundParameters.ContainsKey('CompanyCockpitPlaybackIndex')) {
    if (-not $CompanyMotion) {
        throw 'CompanyCockpitPlaybackIndex requires CompanyMotion.'
    }
    if ($PSBoundParameters.ContainsKey('CompanyHullPlaybackIndex')) {
        throw 'Choose either CompanyCockpitPlaybackIndex or CompanyHullPlaybackIndex.'
    }
    if ($PSBoundParameters.ContainsKey('CompanyShipIndex') -and $CompanyCockpitPlaybackIndex -eq $CompanyShipIndex) {
        throw 'CompanyCockpitPlaybackIndex requires the other Company ship index.'
    }
}
if ($ObserveRegisteredNpc -and (-not $CompanyMotion -or $CompanyCockpitPlayback -or
        $PSBoundParameters.ContainsKey('CompanyHullPlaybackIndex') -or $PSBoundParameters.ContainsKey('CompanyCockpitPlaybackIndex'))) {
    throw 'ObserveRegisteredNpc requires CompanyMotion with ship playback disabled.'
}
if ($TrialRegisteredNpcMeasurement -and (-not $BaseGameOnly -or $CompanyShipIndex -ne 1073 -or
        -not $ObserveRegisteredNpc -or $ObserveRegisteredNpc[0] -ne '943' -or $ObserveRegisteredNpc[1] -cne 'MFQ-419')) {
    throw 'TrialRegisteredNpcMeasurement requires the vanilla HVC-420 pilot observing 943 MFQ-419.'
}
$companyPilot = $ChooseCompanyShip -or $CompanyMotion -or $CompanyPurchases -or $PSBoundParameters.ContainsKey('CompanyShipIndex')
if ($FreshCompanyProfile -and -not $companyPilot) {
    throw 'FreshCompanyProfile requires a Company ship selection or Company play option.'
}
if ($CombatCapture -and -not $companyPilot) {
    throw 'CombatCapture requires a Company ship selection or Company play option.'
}
$playUntilExit = $companyPilot -and -not $PSBoundParameters.ContainsKey('ObserveSeconds') -and -not $PSBoundParameters.ContainsKey('TimeoutSeconds')
if ($PSBoundParameters.ContainsKey('GraphicsConfig') -and (-not $playUntilExit -or -not $GraphicsConfig)) {
    throw 'Pass a nonempty GraphicsConfig path for a Company play-until-exit join.'
}
if ($companyPilot -and -not $playUntilExit -and -not $PSBoundParameters.ContainsKey('ObserveSeconds')) {
    $ObserveSeconds = 900
}
if (-not $playUntilExit -and $ObserveSeconds -ge $TimeoutSeconds) {
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
# Resolve through the provider: on Windows PowerShell 5.1 DirectoryInfo.Parent has no PSIsContainer.
$workRootPath = if ($WorkRoot) { $WorkRoot } elseif ($gameDirectory.Parent) { $gameDirectory.Parent.FullName } else { $null }
if (-not $workRootPath -or -not (Test-Path -LiteralPath $workRootPath -PathType Container)) {
    throw 'WorkRoot must be an existing directory on the game drive.'
}
$parent = Get-Item -LiteralPath $workRootPath
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
    '--game', $gameDirectory.FullName,
    '--connection-file', $connection.FullName,
    '--view-container', (Join-Path $parent.FullName "$runId-view"),
    '--out', (Join-Path $parent.FullName "$runId-run"),
    '--network-dll', $dll.FullName,
    '--network-dll-sha256', $NetworkDllSha256.ToLowerInvariant()
)
if ($playUntilExit) {
    $clientArgs += '--play-until-exit'
    if ($GraphicsConfig) {
        $configPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($GraphicsConfig)
        $clientArgs += @('--graphics-config', $configPath)
    }
} else {
    $clientArgs += @('--observe-seconds', [string]$ObserveSeconds)
}
if ($BaseGameOnly) {
    $clientArgs += '--base-game-only'
}
if ($FreshCompanyProfile) {
    $clientArgs += '--fresh-company-profile'
}
if ($PSBoundParameters.ContainsKey('CompanyShipIndex')) {
    $clientArgs += @('--company-ship-index', [string]$CompanyShipIndex)
}
if ($CompanyMotion) {
    $clientArgs += '--company-motion'
    if ($CompanyCombat) { $clientArgs += '--company-combat' }
    if ($PSBoundParameters.ContainsKey('CompanyHullPlaybackIndex')) {
        $clientArgs += @('--company-hull-playback-index', [string]$CompanyHullPlaybackIndex)
    } elseif ($PSBoundParameters.ContainsKey('CompanyCockpitPlaybackIndex')) {
        $clientArgs += @('--company-cockpit-playback-index', [string]$CompanyCockpitPlaybackIndex)
    } elseif ($ObserveRegisteredNpc) {
        $clientArgs += @('--observe-registered-npc', $ObserveRegisteredNpc[0], $ObserveRegisteredNpc[1])
        if ($TrialRegisteredNpcMeasurement) { $clientArgs += '--trial-registered-npc-measurement' }
    } elseif ($CompanyCockpitPlayback) {
        $clientArgs += '--company-cockpit-playback'
        if ($CompanyCockpitContinuousPlayback) { $clientArgs += '--company-cockpit-continuous-playback' }
    }
} elseif ($ChooseCompanyShip) {
    $clientArgs += '--choose-company-ship'
}
if ($CompanyPurchases) {
    $clientArgs += '--company-purchases'
}
if ($CompanyFleetOrders) {
    $clientArgs += '--company-fleet-orders'
}
if ($CompanyTradeView) {
    $clientArgs += '--company-trade-view'
}
if ($CompanySales) {
    $clientArgs += '--company-sales'
}
if ($CompanyUndockTest) {
    $clientArgs += '--company-undock-test'
}
if ($CombatCapture) {
    $clientArgs += '--combat-capture'
}
& $Python $client @clientArgs
exit $LASTEXITCODE
