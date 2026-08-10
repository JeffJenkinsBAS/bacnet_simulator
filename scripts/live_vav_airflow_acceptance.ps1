param(
    [string]$BaseUrl = "http://127.0.0.1:8001",
    [string]$EvidenceRoot = "C:\bacnet_simulator-main\artifacts"
)

$ErrorActionPreference = "Stop"
$startedAt = Get-Date
$runStamp = $startedAt.ToString("yyyyMMdd-HHmmss")
$evidenceDir = Join-Path $EvidenceRoot "live-vav-airflow-acceptance-$runStamp"
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

$forcedPoints = [System.Collections.Generic.List[object]]::new()
$results = [System.Collections.Generic.List[object]]::new()
$originalSpeed = 1.0

function Invoke-SimGet {
    param([string]$Path)
    Invoke-RestMethod -Method Get -Uri "$BaseUrl$Path"
}

function Invoke-SimPost {
    param([string]$Path, [object]$Body = $null)
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method Post -Uri "$BaseUrl$Path"
    }
    Invoke-RestMethod -Method Post -Uri "$BaseUrl$Path" `
        -ContentType "application/json" `
        -Body ($Body | ConvertTo-Json -Depth 8)
}

function Set-InstructorForce {
    param([string]$GroupId, [string]$Alias, [object]$Value)
    Invoke-SimPost -Path "/api/force" -Body @{
        group_id = $GroupId
        alias = $Alias
        value = $Value
    } | Out-Null
    $forcedPoints.Add([pscustomobject]@{
        group_id = $GroupId
        alias = $Alias
    }) | Out-Null
}

function Get-FocusSnapshot {
    param([string]$Stage)
    $status = Invoke-SimGet -Path "/api/status"
    $commandCenter = Invoke-SimGet -Path "/api/command-center"
    $snapshot = [ordered]@{
        stage = $Stage
        captured_at = (Get-Date).ToString("o")
        simulation = $status.simulation
        bacnet = $status.bacnet
        ahu = $commandCenter.systems.air_handler
        vav = $commandCenter.locations | Where-Object id -eq "vav-3"
    }
    $snapshot | ConvertTo-Json -Depth 12 |
        Set-Content -LiteralPath (Join-Path $evidenceDir "$Stage.json") -Encoding UTF8
    return $snapshot
}

function Add-Check {
    param([string]$Name, [bool]$Passed, [string]$Evidence)
    $results.Add([pscustomobject]@{
        name = $Name
        passed = $Passed
        evidence = $Evidence
    }) | Out-Null
}

try {
    $baselineStatus = Invoke-SimGet -Path "/api/status"
    $originalSpeed = [double]$baselineStatus.simulation.speed_multiplier
    Get-FocusSnapshot -Stage "00-baseline" | Out-Null

    # Establish a proven AHU and meaningful VAV flow quickly, then return to
    # real time so a newly closed damper cannot pass by simply decaying for
    # many accelerated simulated seconds.
    Invoke-SimPost -Path "/api/simulation/speed/60" | Out-Null
    Set-InstructorForce "ACI-SIM-AHU-1" "sa_fan_ss" 1
    Set-InstructorForce "ACI-SIM-VAV-3" "damper_position_command" 100
    Set-InstructorForce "ACI-SIM-VAV-3" "airflow_setpoint" 350
    Start-Sleep -Seconds 4
    Invoke-SimPost -Path "/api/simulation/speed/1" | Out-Null

    $open = Get-FocusSnapshot -Stage "01-open-damper"
    $openFlow = [double]$open.vav.air_delivery.airflow_cfm
    Add-Check "Open damper establishes airflow" ($openFlow -gt 300.0) `
        "AHU proven=$($open.ahu.fan_proven); damper=$($open.vav.air_delivery.damper_pct)%; flow=$openFlow CFM"

    Set-InstructorForce "ACI-SIM-VAV-3" "damper_position_command" 0
    Start-Sleep -Seconds 1
    $closed = Get-FocusSnapshot -Stage "02-closed-damper"
    $closedFlow = [double]$closed.vav.air_delivery.airflow_cfm
    Add-Check "Closed damper permits only modeled leakage" `
        ($closed.ahu.supply_air_available -and $closedFlow -ge 0.0 -and $closedFlow -le 3.0) `
        "AHU available=$($closed.ahu.supply_air_available); damper=$($closed.vav.air_delivery.damper_pct)%; flow=$closedFlow CFM"

    # Re-establish flow, command the fan off, and capture the first instant
    # fan proof is lost. The BACnet airflow must already be exactly 0.00 CFM,
    # not a first-order residual.
    Invoke-SimPost -Path "/api/simulation/speed/60" | Out-Null
    Set-InstructorForce "ACI-SIM-VAV-3" "damper_position_command" 100
    Set-InstructorForce "ACI-SIM-AHU-1" "sa_fan_ss" 1
    Start-Sleep -Seconds 3
    Invoke-SimPost -Path "/api/simulation/speed/1" | Out-Null
    Set-InstructorForce "ACI-SIM-AHU-1" "sa_fan_ss" 0

    $proofLost = $null
    $deadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 250
        $candidate = Get-FocusSnapshot -Stage "03-proof-transition"
        if (-not [bool]$candidate.ahu.fan_proven) {
            $proofLost = $candidate
        }
    } while ($null -eq $proofLost -and (Get-Date) -lt $deadline)

    if ($null -eq $proofLost) {
        Add-Check "AHU fan proof drops" $false "Fan proof did not drop within 15 seconds."
    }
    else {
        $offFlow = [double]$proofLost.vav.air_delivery.airflow_cfm
        Add-Check "AHU off forces exact zero airflow" ($offFlow -eq 0.0) `
            "fan_proven=$($proofLost.ahu.fan_proven); supply_available=$($proofLost.ahu.supply_air_available); flow=$offFlow CFM"
    }
}
finally {
    $released = @{}
    for ($index = $forcedPoints.Count - 1; $index -ge 0; $index--) {
        $point = $forcedPoints[$index]
        $key = "$($point.group_id).$($point.alias)"
        if ($released.ContainsKey($key)) {
            continue
        }
        try {
            Invoke-SimPost -Path "/api/release" -Body @{
                group_id = $point.group_id
                alias = $point.alias
            } | Out-Null
            $released[$key] = $true
        }
        catch {
            Add-Check "Release $key" $false $_.Exception.Message
        }
    }
    Invoke-SimPost -Path "/api/simulation/speed/$originalSpeed" | Out-Null
    Start-Sleep -Seconds 3
    $cleanup = Get-FocusSnapshot -Stage "99-cleanup"
    $remainingForces = @(Invoke-SimGet -Path "/api/points" | Where-Object forced)
    Add-Check "Instructor override rollback" ($remainingForces.Count -eq 0) `
        "remaining forced points=$($remainingForces.Count); speed=$($cleanup.simulation.speed_multiplier)"

    $report = [ordered]@{
        started_at = $startedAt.ToString("o")
        completed_at = (Get-Date).ToString("o")
        base_url = $BaseUrl
        evidence_directory = $evidenceDir
        passed = (@($results | Where-Object { -not $_.passed }).Count -eq 0)
        checks = @($results)
    }
    $report | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath (Join-Path $evidenceDir "acceptance-report.json") -Encoding UTF8
    $report | ConvertTo-Json -Depth 8
}
