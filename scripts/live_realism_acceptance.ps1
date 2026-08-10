param(
    [string]$BaseUrl = "http://127.0.0.1:8001",
    [string]$EvidenceRoot = "C:\bacnet_simulator-main\artifacts"
)

$ErrorActionPreference = "Stop"
$startedAt = Get-Date
$runStamp = $startedAt.ToString("yyyyMMdd-HHmmss")
$evidenceDir = Join-Path $EvidenceRoot "live-realism-acceptance-$runStamp"
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

$forcedPoints = [System.Collections.Generic.List[object]]::new()
$results = [System.Collections.Generic.List[object]]::new()
$originalSpeed = 1.0

function Invoke-SimGet {
    param([string]$Path)
    Invoke-RestMethod -Method Get -Uri "$BaseUrl$Path"
}

function Invoke-SimPost {
    param(
        [string]$Path,
        [object]$Body = $null
    )
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method Post -Uri "$BaseUrl$Path"
    }
    Invoke-RestMethod -Method Post -Uri "$BaseUrl$Path" `
        -ContentType "application/json" `
        -Body ($Body | ConvertTo-Json -Depth 8)
}

function Set-InstructorForce {
    param(
        [string]$GroupId,
        [string]$Alias,
        [object]$Value
    )
    Invoke-SimPost -Path "/api/force" -Body @{
        group_id = $GroupId
        alias = $Alias
        value = $Value
    } | Out-Null
    $forcedPoints.Add([pscustomobject]@{group_id=$GroupId; alias=$Alias}) | Out-Null
}

function Save-Snapshot {
    param([string]$Stage)
    $cc = Invoke-SimGet -Path "/api/command-center"
    $status = Invoke-SimGet -Path "/api/status"
    $faults = Invoke-SimGet -Path "/api/faults"
    $focusIds = @("ahu-1", "chiller-1", "boiler-1", "boiler-2", "vav-3")
    $snapshot = [ordered]@{
        stage = $Stage
        captured_at = (Get-Date).ToString("o")
        simulation = $status.simulation
        bacnet = $status.bacnet
        command_center_summary = $cc.summary
        air_summary = $cc.air_summary
        systems = $cc.systems
        focus_locations = @($cc.locations | Where-Object { $_.id -in $focusIds })
        active_faults = @($faults)
    }
    $snapshot | ConvertTo-Json -Depth 12 |
        Set-Content -LiteralPath (Join-Path $evidenceDir "$Stage.json") -Encoding UTF8
    return $snapshot
}

function Add-Check {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Evidence
    )
    $results.Add([pscustomobject]@{
        name = $Name
        passed = $Passed
        evidence = $Evidence
    }) | Out-Null
}

try {
    $baselineStatus = Invoke-SimGet -Path "/api/status"
    $originalSpeed = [double]$baselineStatus.simulation.speed_multiplier
    $baseline = Save-Snapshot -Stage "00-baseline"

    # A stopped AHU with a VAV demanding airflow must be inhibited, not blamed
    # as a terminal-box failure.
    Invoke-SimPost -Path "/api/simulation/speed/1" | Out-Null
    Set-InstructorForce "ACI-SIM-AHU-1" "sa_fan_ss" 0
    Set-InstructorForce "ACI-SIM-AHU-1" "cooling_valve" 0
    Set-InstructorForce "ACI-SIM-AHU-1" "heating_valve" 0
    Set-InstructorForce "ACI-SIM-VAV-3" "damper_position_command" 100
    Set-InstructorForce "ACI-SIM-VAV-3" "airflow_setpoint" 350
    Set-InstructorForce "ACI-SIM-VAV-3" "hw_valve_command" 0
    Start-Sleep -Seconds 17
    $inhibit = Save-Snapshot -Stage "01-upstream-inhibit"
    $inhibitVav = $inhibit.focus_locations | Where-Object id -eq "vav-3"
    Add-Check "Upstream inhibit classification" ($inhibitVav.state -eq "inhibited") `
        "VAV-3 state=$($inhibitVav.state); message=$($inhibitVav.message)"

    # Deliberately withhold the pumps and isolation valve. The chiller S/S
    # mismatch must cross the real-time 15-second threshold and turn red.
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chiller_enable" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chiller_ss" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chw_iso_valve" 0
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chw_pump_ss" 0
    Set-InstructorForce "ACI-SIM-CHILLER-1" "cw_pump_ss" 0
    Set-InstructorForce "ACI-SIM-CHILLER-1" "ct_fan_ss" 0
    Start-Sleep -Seconds 17
    $failure = Save-Snapshot -Stage "02-proof-failure"
    $failedChiller = $failure.focus_locations | Where-Object id -eq "chiller-1"
    Add-Check "15-second command/proof failure" ($failedChiller.state -eq "failure") `
        "Chiller-1 state=$($failedChiller.state); mismatch_seconds=$($failedChiller.mismatch_seconds)"

    # Neutral supply air: proven AHU and real airflow, but neither plant is
    # conditioning the airstream.
    Invoke-SimPost -Path "/api/simulation/speed/60" | Out-Null
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chiller_enable" 0
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chiller_ss" 0
    Set-InstructorForce "ACI-SIM-AHU-1" "sa_fan_ss" 1
    Set-InstructorForce "ACI-SIM-AHU-1" "cooling_valve" 0
    Set-InstructorForce "ACI-SIM-AHU-1" "heating_valve" 0
    Set-InstructorForce "ACI-SIM-AHU-1" "preheat_valve" 0
    Set-InstructorForce "ACI-SIM-AHU-1" "economizer" 0
    Set-InstructorForce "ACI-SIM-VAV-3" "damper_position_command" 100
    Set-InstructorForce "ACI-SIM-VAV-3" "airflow_setpoint" 350
    Set-InstructorForce "ACI-SIM-VAV-3" "hw_valve_command" 0
    Start-Sleep -Seconds 6
    $ventilation = Save-Snapshot -Stage "03-ventilation"
    $ventVav = $ventilation.focus_locations | Where-Object id -eq "vav-3"
    Add-Check "Neutral ventilation mode" `
        ($ventVav.air_delivery.active -and $ventVav.air_delivery.mode -eq "ventilation") `
        "mode=$($ventVav.air_delivery.mode); airflow=$($ventVav.air_delivery.airflow_cfm) CFM"

    # Full cooling chain: condenser/evaporator flow + chiller proof + CHW
    # header + AHU coil + terminal airflow.
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chiller_enable" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chiller_ss" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chw_iso_valve" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chw_pump_ss" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "cw_pump_ss" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "ct_fan_ss" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chws_stpt_reset" 44
    Set-InstructorForce "ACI-SIM-AHU-1" "cooling_valve" 100
    Start-Sleep -Seconds 12
    $cooling = Save-Snapshot -Stage "04-cooling"
    $coolVav = $cooling.focus_locations | Where-Object id -eq "vav-3"
    $coolDelta = [double]$coolVav.air_delivery.zone_temp_f - [double]$coolVav.air_delivery.discharge_temp_f
    Add-Check "Chilled-water parent chain" `
        ($cooling.systems.chilled_water.available -and $cooling.systems.air_handler.mechanical_cooling_active) `
        "CHW available=$($cooling.systems.chilled_water.available); AHU source=$($cooling.systems.air_handler.conditioning_source)"
    Add-Check "Cooling terminal delivery" `
        ($coolVav.air_delivery.mode -eq "cooling" -and $coolVav.air_delivery.airflow_cfm -gt 300 -and $coolDelta -gt 2) `
        "mode=$($coolVav.air_delivery.mode); airflow=$($coolVav.air_delivery.airflow_cfm) CFM; DAT=$($coolVav.air_delivery.discharge_temp_f) F; zone=$($coolVav.air_delivery.zone_temp_f) F"

    # Full heating chain: boiler purge/ignition + circulation/distribution
    # pumps + hot-water header + low-airflow terminal reheat.
    Set-InstructorForce "ACI-SIM-AHU-1" "cooling_valve" 0
    Set-InstructorForce "ACI-SIM-BOILER-2" "boiler_ss" 1
    Set-InstructorForce "ACI-SIM-BOILER-2" "circ_pump_ss" 1
    Set-InstructorForce "ACI-SIM-BOILER-2" "hw_pump_ss" 1
    Set-InstructorForce "ACI-SIM-BOILER-2" "hws_stpt_reset" 180
    Set-InstructorForce "ACI-SIM-VAV-3" "hw_valve_command" 100
    Set-InstructorForce "ACI-SIM-VAV-3" "airflow_setpoint" 300
    Set-InstructorForce "ACI-SIM-VAV-3" "damper_position_command" 25
    Start-Sleep -Seconds 15
    $heating = Save-Snapshot -Stage "05-heating"
    $heatVav = $heating.focus_locations | Where-Object id -eq "vav-3"
    $heatDelta = [double]$heatVav.air_delivery.discharge_temp_f - [double]$heatVav.air_delivery.zone_temp_f
    Add-Check "Hot-water parent chain" $heating.systems.hot_water.available `
        "HW available=$($heating.systems.hot_water.available); supply=$($heating.systems.hot_water.supply_temp_f) F"
    Add-Check "Heating terminal delivery" `
        ($heatVav.air_delivery.mode -eq "heating" -and
         $heatVav.air_delivery.airflow_cfm -le 325 -and
         $heatVav.air_delivery.discharge_temp_f -ge 88 -and
         $heatVav.air_delivery.discharge_temp_f -le 95 -and
         $heatDelta -gt 2) `
        "mode=$($heatVav.air_delivery.mode); airflow=$($heatVav.air_delivery.airflow_cfm) CFM; DAT=$($heatVav.air_delivery.discharge_temp_f) F; zone=$($heatVav.air_delivery.zone_temp_f) F"
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
            $results.Add([pscustomobject]@{
                name = "Release $key"
                passed = $false
                evidence = $_.Exception.Message
            }) | Out-Null
        }
    }
    Invoke-SimPost -Path "/api/simulation/speed/$originalSpeed" | Out-Null
    Start-Sleep -Seconds 3
    $cleanup = Save-Snapshot -Stage "99-cleanup"
    $remainingForces = @(
        Invoke-SimGet -Path "/api/points" |
            Where-Object forced
    )
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
