param(
    [string]$BaseUrl = "http://127.0.0.1:8001",
    [string]$EvidenceRoot = "C:\bacnet_simulator-main\artifacts"
)

$ErrorActionPreference = "Stop"
$startedAt = Get-Date
$runStamp = $startedAt.ToString("yyyyMMdd-HHmmss")
$evidenceDir = Join-Path $EvidenceRoot "live-ahu-sat-acceptance-$runStamp"
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
    $points = Invoke-SimGet -Path "/api/points"
    $snapshot = [ordered]@{
        stage = $Stage
        captured_at = (Get-Date).ToString("o")
        simulation = $status.simulation
        fleet = $status.fleet
        bacnet = $status.bacnet
        air_handler = $cc.systems.air_handler
        ahu_location = $cc.locations | Where-Object id -eq "ahu-1"
        forced_points = @($points | Where-Object forced)
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
    Add-Check "Expanded point catalog" `
        ($baselineStatus.fleet.group_count -eq 28 -and
         $baselineStatus.fleet.total_point_count -eq 318) `
        "groups=$($baselineStatus.fleet.group_count); points=$($baselineStatus.fleet.total_point_count)"

    $allPoints = Invoke-SimGet -Path "/api/points"
    $saSetpointMatches = [System.Collections.Generic.List[object]]::new()
    foreach ($point in $allPoints) {
        if (
            $point.group -eq "ACI-SIM-AHU-1" -and
            $point.alias -eq "sa_temp_setpoint"
        ) {
            $saSetpointMatches.Add($point) | Out-Null
        }
    }
    $saSetpoint = if ($saSetpointMatches.Count -gt 0) {
        $saSetpointMatches[0]
    }
    else {
        $null
    }
    Add-Check "Single writable AHU SAT setpoint" `
        ($saSetpointMatches.Count -eq 1 -and
         $null -ne $saSetpoint -and
         $saSetpoint.object_type -eq "analog-value" -and
         $saSetpoint.object_instance -eq 9001 -and
         $saSetpoint.writable) `
        "matches=$($saSetpointMatches.Count); object=$($saSetpoint.object_type):$($saSetpoint.object_instance); writable=$($saSetpoint.writable)"

    Invoke-SimPost -Path "/api/simulation/speed/60" | Out-Null
    Set-InstructorForce "ACI-SIM-SITE" "oa_temp" 70
    Set-InstructorForce "ACI-SIM-AHU-1" "sa_fan_ss" 1
    Set-InstructorForce "ACI-SIM-AHU-1" "economizer" 0
    Set-InstructorForce "ACI-SIM-AHU-1" "preheat_valve" 0
    Set-InstructorForce "ACI-SIM-AHU-1" "cooling_valve" 0
    Set-InstructorForce "ACI-SIM-AHU-1" "heating_valve" 50
    Set-InstructorForce "ACI-SIM-AHU-1" "sa_temp_setpoint" 85
    Set-InstructorForce "ACI-SIM-BOILER-2" "boiler_ss" 1
    Set-InstructorForce "ACI-SIM-BOILER-2" "circ_pump_ss" 1
    Set-InstructorForce "ACI-SIM-BOILER-2" "hw_pump_ss" 1
    Set-InstructorForce "ACI-SIM-BOILER-2" "hws_stpt_reset" 180
    Start-Sleep -Seconds 15
    $normalHeating = Save-Snapshot -Stage "01-normal-heating"
    $normalAhu = $normalHeating.air_handler
    Add-Check "Normal-condition 50 percent heating calibration" `
        ($normalAhu.hot_water_available -and
         $normalAhu.requested_conditioning -eq "heating" -and
         $normalAhu.supply_air_temp_f -ge 84.0 -and
         $normalAhu.supply_air_temp_f -le 86.0 -and
         $normalAhu.heating_valve_effective_pct -ge 49.0 -and
         $normalAhu.heating_valve_effective_pct -le 51.0) `
        "OA=70 F; SATSP=$($normalAhu.supply_air_temp_setpoint_f) F; SAT=$($normalAhu.supply_air_temp_f) F; heating=$($normalAhu.heating_valve_effective_pct)%"

    Set-InstructorForce "ACI-SIM-SITE" "oa_temp" 40
    Start-Sleep -Seconds 15
    $coldHalf = Save-Snapshot -Stage "02-cold-weather-half-open"
    $coldHalfAhu = $coldHalf.air_handler
    Add-Check "Cold OA increases heating load" `
        ($coldHalfAhu.outside_air_fraction -eq 0.15 -and
         $coldHalfAhu.supply_air_temp_f -lt 82.0 -and
         $coldHalfAhu.supply_air_temp_error_f -gt 3.0) `
        "OA=40 F; OA fraction=$($coldHalfAhu.outside_air_fraction); SAT=$($coldHalfAhu.supply_air_temp_f) F; error=$($coldHalfAhu.supply_air_temp_error_f) F"

    Set-InstructorForce "ACI-SIM-AHU-1" "heating_valve" 72
    Start-Sleep -Seconds 15
    $coldDesign = Save-Snapshot -Stage "03-cold-weather-design"
    $coldDesignAhu = $coldDesign.air_handler
    Add-Check "Cold-weather valve capacity reaches setpoint" `
        ($coldDesignAhu.supply_air_temp_f -ge 84.0 -and
         $coldDesignAhu.supply_air_temp_f -le 86.0 -and
         $coldDesignAhu.heating_valve_effective_pct -ge 71.0 -and
         $coldDesignAhu.heating_valve_effective_pct -le 73.0) `
        "OA=40 F; SATSP=$($coldDesignAhu.supply_air_temp_setpoint_f) F; SAT=$($coldDesignAhu.supply_air_temp_f) F; heating=$($coldDesignAhu.heating_valve_effective_pct)%"

    Set-InstructorForce "ACI-SIM-SITE" "oa_temp" 80
    Set-InstructorForce "ACI-SIM-AHU-1" "sa_temp_setpoint" 55
    Set-InstructorForce "ACI-SIM-AHU-1" "heating_valve" 0
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chiller_enable" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chiller_ss" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chw_iso_valve" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chw_pump_ss" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "cw_pump_ss" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "ct_fan_ss" 1
    Set-InstructorForce "ACI-SIM-CHILLER-1" "chws_stpt_reset" 44
    Set-InstructorForce "ACI-SIM-AHU-1" "cooling_valve" 100
    Start-Sleep -Seconds 15
    $cooling = Save-Snapshot -Stage "04-cooling-setpoint"
    $coolingAhu = $cooling.air_handler
    Add-Check "Cooling valve follows the same SAT setpoint" `
        ($coolingAhu.mechanical_cooling_available -and
         $coolingAhu.mechanical_cooling_active -and
         $coolingAhu.requested_conditioning -eq "cooling" -and
         $coolingAhu.supply_air_temp_f -ge 52.0 -and
         $coolingAhu.supply_air_temp_f -le 58.0) `
        "OA=80 F; SATSP=$($coolingAhu.supply_air_temp_setpoint_f) F; SAT=$($coolingAhu.supply_air_temp_f) F; source=$($coolingAhu.conditioning_source)"

    # A real cross-ramp is allowed during actuator travel.
    Invoke-SimPost -Path "/api/simulation/speed/1" | Out-Null
    Set-InstructorForce "ACI-SIM-AHU-1" "cooling_valve" 40
    Set-InstructorForce "ACI-SIM-AHU-1" "heating_valve" 40
    Start-Sleep -Milliseconds 1200
    $crossover = Save-Snapshot -Stage "05-legitimate-crossover"
    $crossoverAhu = $crossover.air_handler
    Add-Check "Cross-ramp grace window" `
        (-not $crossoverAhu.simultaneous_heating_cooling -and
         $crossoverAhu.valve_changeover_remaining_seconds -gt 0) `
        "changeover=$($crossoverAhu.valve_changeover_active); remaining=$($crossoverAhu.valve_changeover_remaining_seconds)s; overlap=$($crossoverAhu.valve_overlap_pct)%"

    # Once both commands remain steady beyond the travel window, the
    # wall-clock diagnostic must identify the energy-waste fault.
    Invoke-SimPost -Path "/api/simulation/speed/60" | Out-Null
    Start-Sleep -Seconds 18
    $overlap = Save-Snapshot -Stage "06-persistent-overlap"
    $overlapAhu = $overlap.air_handler
    $overlapLocation = $overlap.ahu_location
    Add-Check "Persistent simultaneous heating/cooling indication" `
        ($overlapAhu.simultaneous_heating_cooling -and
         $overlapLocation.state -eq "failure" -and
         $overlapLocation.diagnostic_type -eq "simultaneous_heating_cooling" -and
         $overlapLocation.message -match "priority locks") `
        "state=$($overlapLocation.state); type=$($overlapLocation.diagnostic_type); message=$($overlapLocation.message)"

    Set-InstructorForce "ACI-SIM-AHU-1" "cooling_valve" 0
    Set-InstructorForce "ACI-SIM-AHU-1" "heating_valve" 50
    Start-Sleep -Seconds 2
    $recovery = Save-Snapshot -Stage "07-overlap-recovery"
    Add-Check "Overlap clears on clean handoff" `
        (-not $recovery.air_handler.simultaneous_heating_cooling -and
         $recovery.ahu_location.state -ne "failure") `
        "state=$($recovery.ahu_location.state); changeover=$($recovery.air_handler.valve_changeover_active)"
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
    Add-Check "Instructor override rollback" `
        ($cleanup.forced_points.Count -eq 0 -and
         $cleanup.simulation.speed_multiplier -eq $originalSpeed) `
        "remaining forced points=$($cleanup.forced_points.Count); speed=$($cleanup.simulation.speed_multiplier)"

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
