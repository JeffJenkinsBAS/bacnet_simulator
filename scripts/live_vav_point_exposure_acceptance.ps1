param(
    [string]$BaseUrl = "http://127.0.0.1:8001",
    [string]$EvidenceRoot = "C:\bacnet_simulator-main\artifacts"
)

$ErrorActionPreference = "Stop"
$startedAt = Get-Date
$runStamp = $startedAt.ToString("yyyyMMdd-HHmmss")
$evidenceDir = Join-Path $EvidenceRoot "live-vav-point-exposure-acceptance-$runStamp"
$projectRoot = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

$forcedPoints = [System.Collections.Generic.List[object]]::new()
$results = [System.Collections.Generic.List[object]]::new()
$catalogRows = [System.Collections.Generic.List[object]]::new()
$designRows = [System.Collections.Generic.List[object]]::new()
$baselineForcedKeys = @()
$mutationStarted = $false
$fatalError = $null
$reportPassed = $false

function Invoke-SimGet {
    param([string]$Path)
    Invoke-RestMethod -Method Get -Uri "$BaseUrl$Path" -TimeoutSec 10
}

function Invoke-SimPost {
    param(
        [string]$Path,
        [object]$Body = $null
    )
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method Post -Uri "$BaseUrl$Path" -TimeoutSec 10
    }
    Invoke-RestMethod -Method Post -Uri "$BaseUrl$Path" `
        -ContentType "application/json" `
        -Body ($Body | ConvertTo-Json -Depth 8) `
        -TimeoutSec 10
}

function Get-SimItems {
    param([string]$Path)
    $response = Invoke-SimGet -Path $Path
    if ($null -eq $response) {
        return
    }
    # Windows PowerShell 5.1 can retain a top-level JSON array as one nested
    # pipeline object. Explicit enumeration keeps filtering/counting stable.
    foreach ($item in $response) {
        $item
    }
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

function Save-JsonArtifact {
    param(
        [string]$Name,
        [object]$Data
    )
    $Data | ConvertTo-Json -Depth 16 |
        Set-Content -LiteralPath (Join-Path $evidenceDir "$Name.json") -Encoding UTF8
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
    $forcedPoints.Add([pscustomobject]@{
        group_id = $GroupId
        alias = $Alias
    }) | Out-Null
}

function Test-NumericEqual {
    param(
        [object]$Left,
        [object]$Right,
        [double]$Tolerance = 0.01
    )
    if ($null -eq $Left -or $null -eq $Right) {
        return $false
    }
    return [Math]::Abs(([double]$Left) - ([double]$Right)) -le $Tolerance
}

$pointSpecs = @(
    [pscustomobject]@{
        alias = "heating_min_airflow"
        local_instance = 81
        units = "cubic-feet-per-minute"
        model_key = "occupied_minimum_airflow_cfm"
    },
    [pscustomobject]@{
        alias = "heating_max_airflow"
        local_instance = 82
        units = "cubic-feet-per-minute"
        model_key = "heating_maximum_airflow_cfm"
    },
    [pscustomobject]@{
        alias = "cooling_min_airflow"
        local_instance = 83
        units = "cubic-feet-per-minute"
        model_key = "occupied_minimum_airflow_cfm"
    },
    [pscustomobject]@{
        alias = "cooling_max_airflow"
        local_instance = 84
        units = "cubic-feet-per-minute"
        model_key = "max_airflow_cfm"
    },
    [pscustomobject]@{
        alias = "damper_position_feedback"
        local_instance = 85
        units = "percent"
        model_key = $null
    }
)

try {
    $baselineStatus = Invoke-SimGet -Path "/api/status"
    $baselineGroups = @(Get-SimItems -Path "/api/groups")
    $baselinePoints = @(Get-SimItems -Path "/api/points")
    $baselineForcedKeys = @(
        $baselinePoints |
            Where-Object forced |
            ForEach-Object { "$($_.group).$($_.alias)" } |
            Sort-Object -Unique
    )

    $catalogReady = (
        [bool]$baselineStatus.simulation.running -and
        [int]$baselineStatus.fleet.group_count -eq 28 -and
        [int]$baselineStatus.fleet.total_point_count -eq 318
    )
    Add-Check "Required restarted 318-point catalog" $catalogReady `
        "running=$($baselineStatus.simulation.running); groups=$($baselineStatus.fleet.group_count); points=$($baselineStatus.fleet.total_point_count)"

    Save-JsonArtifact -Name "00-baseline" -Data ([ordered]@{
        captured_at = (Get-Date).ToString("o")
        status = $baselineStatus
        vav_groups = @($baselineGroups | Where-Object group_id -like "ACI-SIM-VAV-*")
        vav_exposure_points = @(
            $baselinePoints |
                Where-Object {
                    $_.group -like "ACI-SIM-VAV-*" -and
                    $_.alias -in @($pointSpecs.alias)
                }
        )
        preexisting_forced_points = $baselineForcedKeys
    })

    if (-not $catalogReady) {
        throw "Acceptance requires the restarted 28-group / 318-point build. No simulator command was changed."
    }

    $catalogIssues = [System.Collections.Generic.List[string]]::new()
    $designIssues = [System.Collections.Generic.List[string]]::new()

    foreach ($vavNumber in 1..17) {
        $groupId = "ACI-SIM-VAV-$vavNumber"
        $expectedOffset = (10 + $vavNumber) * 1000
        $configPath = Join-Path $projectRoot "config\devices\vav_$vavNumber.json"
        if (-not (Test-Path -LiteralPath $configPath)) {
            $catalogIssues.Add("$groupId config is missing at $configPath") | Out-Null
            continue
        }

        $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        $liveGroupMatches = @($baselineGroups | Where-Object group_id -eq $groupId)
        if ($liveGroupMatches.Count -ne 1) {
            $catalogIssues.Add("$groupId has $($liveGroupMatches.Count) live group rows") | Out-Null
            continue
        }
        $liveGroup = $liveGroupMatches[0]
        if (
            [int]$config.instance_offset -ne $expectedOffset -or
            [int]$liveGroup.instance_offset -ne $expectedOffset
        ) {
            $catalogIssues.Add(
                "$groupId offset mismatch: expected=$expectedOffset config=$($config.instance_offset) live=$($liveGroup.instance_offset)"
            ) | Out-Null
        }

        foreach ($spec in $pointSpecs) {
            $configMatches = @($config.points | Where-Object alias -eq $spec.alias)
            $liveMatches = @(
                $baselinePoints |
                    Where-Object {
                        $_.group -eq $groupId -and
                        $_.alias -eq $spec.alias
                    }
            )
            $expectedGlobal = $expectedOffset + [int]$spec.local_instance
            $configPoint = if ($configMatches.Count -eq 1) { $configMatches[0] } else { $null }
            $livePoint = if ($liveMatches.Count -eq 1) { $liveMatches[0] } else { $null }

            $metadataPassed = (
                $configMatches.Count -eq 1 -and
                $liveMatches.Count -eq 1 -and
                $null -ne $configPoint -and
                $null -ne $livePoint -and
                $configPoint.object_type -eq "analog-value" -and
                [int]$configPoint.object_instance -eq [int]$spec.local_instance -and
                -not [bool]$configPoint.writable -and
                -not [bool]$configPoint.commandable -and
                $configPoint.signal_direction -eq "sim_to_webctrl" -and
                $configPoint.units -eq $spec.units -and
                $livePoint.object_type -eq "analog-value" -and
                [int]$livePoint.object_instance -eq $expectedGlobal -and
                -not [bool]$livePoint.writable -and
                -not [bool]$livePoint.commandable -and
                $livePoint.direction -eq "sim_to_webctrl" -and
                $livePoint.units -eq $spec.units
            )

            if (-not $metadataPassed) {
                $catalogIssues.Add(
                    "$groupId.$($spec.alias) metadata/global-instance mismatch; expected AV:$expectedGlobal, local AV:$($spec.local_instance), read-only $($spec.units)"
                ) | Out-Null
            }

            $catalogRows.Add([pscustomobject]@{
                group_id = $groupId
                alias = $spec.alias
                expected_offset = $expectedOffset
                expected_local_instance = [int]$spec.local_instance
                expected_global_instance = $expectedGlobal
                config_local_instance = if ($null -ne $configPoint) { $configPoint.object_instance } else { $null }
                live_global_instance = if ($null -ne $livePoint) { $livePoint.object_instance } else { $null }
                live_present_value = if ($null -ne $livePoint) { $livePoint.present_value } else { $null }
                read_only_metadata_passed = $metadataPassed
            }) | Out-Null

            if ($null -ne $spec.model_key) {
                $modelProperty = $config.model_parameters.PSObject.Properties[$spec.model_key]
                $modelValue = if ($null -ne $modelProperty) { $modelProperty.Value } else { $null }
                $configValue = if ($null -ne $configPoint) { $configPoint.initial_value } else { $null }
                $liveValue = if ($null -ne $livePoint) { $livePoint.present_value } else { $null }
                $designPassed = (
                    (Test-NumericEqual -Left $configValue -Right $modelValue) -and
                    (Test-NumericEqual -Left $liveValue -Right $modelValue)
                )
                if (-not $designPassed) {
                    $designIssues.Add(
                        "$groupId.$($spec.alias) differs: model=$modelValue config_initial=$configValue live=$liveValue"
                    ) | Out-Null
                }
                $designRows.Add([pscustomobject]@{
                    group_id = $groupId
                    alias = $spec.alias
                    model_parameter = $spec.model_key
                    model_value = $modelValue
                    config_initial_value = $configValue
                    live_present_value = $liveValue
                    passed = $designPassed
                }) | Out-Null
            }
        }
    }

    Add-Check "All 17 VAVs expose read-only AV:81-85 at exact offsets" `
        ($catalogRows.Count -eq 85 -and $catalogIssues.Count -eq 0) `
        "validated=$($catalogRows.Count)/85; issues=$($catalogIssues.Count)"
    Add-Check "Four VAV design airflow values match config/model parameters" `
        ($designRows.Count -eq 68 -and $designIssues.Count -eq 0) `
        "validated=$($designRows.Count)/68; issues=$($designIssues.Count)"

    Save-JsonArtifact -Name "01-point-exposure-validation" -Data ([ordered]@{
        captured_at = (Get-Date).ToString("o")
        catalog_rows = @($catalogRows)
        catalog_issues = @($catalogIssues)
        design_value_rows = @($designRows)
        design_value_issues = @($designIssues)
    })

    if ($catalogIssues.Count -gt 0 -or $designIssues.Count -gt 0) {
        throw "VAV point-exposure validation failed. The VAV-3 command exercise was not started."
    }

    $baselineDamperMatches = @(
        $baselinePoints |
            Where-Object {
                $_.group -eq "ACI-SIM-VAV-3" -and
                $_.alias -eq "damper_position_command"
            }
    )
    if ($baselineDamperMatches.Count -ne 1) {
        throw "VAV-3 damper command point is missing or duplicated."
    }
    $baselineDamper = $baselineDamperMatches[0]
    if ([bool]$baselineDamper.forced) {
        throw "VAV-3 damper command already has an instructor force; refusing to replace another operator's override."
    }

    $testDamperPct = if ([double]$baselineDamper.present_value -ge 50.0) { 25.0 } else { 75.0 }
    Invoke-SimPost -Path "/api/simulation/speed/1" | Out-Null
    $mutationStarted = $true
    Set-InstructorForce -GroupId "ACI-SIM-VAV-3" -Alias "damper_position_command" -Value $testDamperPct

    $exercisePoints = @()
    $commandPoint = $null
    $feedbackPoint = $null
    $feedbackPublished = $false
    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 500
        $exercisePoints = @(Get-SimItems -Path "/api/points?group=ACI-SIM-VAV-3")
        $commandPoint = @(
            $exercisePoints | Where-Object alias -eq "damper_position_command"
        )[0]
        $feedbackPoint = @(
            $exercisePoints | Where-Object alias -eq "damper_position_feedback"
        )[0]
        $feedbackPublished = (
            $null -ne $commandPoint -and
            $null -ne $feedbackPoint -and
            [bool]$commandPoint.forced -and
            [bool]$commandPoint.instructor_priority_3 -and
            (Test-NumericEqual -Left $commandPoint.present_value -Right $testDamperPct -Tolerance 0.1) -and
            (Test-NumericEqual -Left $feedbackPoint.present_value -Right $testDamperPct -Tolerance 0.6)
        )
    } while (-not $feedbackPublished -and (Get-Date) -lt $deadline)

    Add-Check "Safe VAV-3 damper force publishes read-only feedback" $feedbackPublished `
        "target=$testDamperPct%; command=$($commandPoint.present_value)%; priority3=$($commandPoint.instructor_priority_3); feedback=$($feedbackPoint.present_value)%; feedback_object=analog-value:13085"

    Save-JsonArtifact -Name "02-vav3-damper-feedback" -Data ([ordered]@{
        captured_at = (Get-Date).ToString("o")
        test_target_pct = $testDamperPct
        command_point = $commandPoint
        feedback_point = $feedbackPoint
        passed = $feedbackPublished
    })
}
catch {
    $fatalError = $_.Exception
    Add-Check "Acceptance execution" $false $fatalError.Message
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

    if ($mutationStarted) {
        try {
            Invoke-SimPost -Path "/api/simulation/speed/1" | Out-Null
        }
        catch {
            Add-Check "Restore simulation speed" $false $_.Exception.Message
        }
    }

    try {
        if ($mutationStarted) {
            Start-Sleep -Seconds 2
        }
        $cleanupStatus = Invoke-SimGet -Path "/api/status"
        $cleanupPoints = @(Get-SimItems -Path "/api/points")
        $cleanupForcedKeys = @(
            $cleanupPoints |
                Where-Object forced |
                ForEach-Object { "$($_.group).$($_.alias)" } |
                Sort-Object -Unique
        )
        $vav3Damper = @(
            $cleanupPoints |
                Where-Object {
                    $_.group -eq "ACI-SIM-VAV-3" -and
                    $_.alias -eq "damper_position_command"
                }
        )[0]
        $forcedSetRestored = (
            ($baselineForcedKeys -join "|") -eq ($cleanupForcedKeys -join "|")
        )
        $cleanupPassed = (
            -not $mutationStarted -or
            (
                [double]$cleanupStatus.simulation.speed_multiplier -eq 1.0 -and
                $null -ne $vav3Damper -and
                -not [bool]$vav3Damper.instructor_priority_3 -and
                $forcedSetRestored
            )
        )
        Add-Check "VAV-3 override released and simulator restored to 1x" $cleanupPassed `
            "speed=$($cleanupStatus.simulation.speed_multiplier); VAV-3 priority3=$($vav3Damper.instructor_priority_3); baseline_forces=$($baselineForcedKeys.Count); final_forces=$($cleanupForcedKeys.Count)"

        Save-JsonArtifact -Name "99-cleanup" -Data ([ordered]@{
            captured_at = (Get-Date).ToString("o")
            status = $cleanupStatus
            vav3_damper_command = $vav3Damper
            baseline_forced_points = $baselineForcedKeys
            final_forced_points = $cleanupForcedKeys
            forced_set_restored = $forcedSetRestored
        })
    }
    catch {
        Add-Check "Cleanup verification" $false $_.Exception.Message
    }

    $reportPassed = (@($results | Where-Object { -not $_.passed }).Count -eq 0)
    $report = [ordered]@{
        started_at = $startedAt.ToString("o")
        completed_at = (Get-Date).ToString("o")
        base_url = $BaseUrl
        evidence_directory = $evidenceDir
        expected_fleet = @{
            group_count = 28
            total_point_count = 318
        }
        passed = $reportPassed
        checks = @($results)
    }
    Save-JsonArtifact -Name "acceptance-report" -Data $report
    $report | ConvertTo-Json -Depth 10
}

if ($null -ne $fatalError) {
    throw $fatalError
}
if (-not $reportPassed) {
    throw "VAV point-exposure acceptance completed with one or more failed checks. Review $evidenceDir."
}
