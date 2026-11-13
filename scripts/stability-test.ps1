<#
.SYNOPSIS
    60-minute stability test for Nivos and Ollama on Windows.

.DESCRIPTION
    Runs for 60 minutes (default), sampling every 30 seconds:
    - Ollama API reachability (http://127.0.0.1:11434/api/version)
    - Nivos API /health reachability (http://127.0.0.1:8000/health)
    - Free system RAM (GB)
    - Any Ollama restart events from Nivos Activity / supervisor status
    Prints a clean summary report at the end (or on Ctrl+C).

.PARAMETER DurationMinutes
    Duration of the stability test in minutes. Default: 60.

.PARAMETER IntervalSeconds
    Sampling interval in seconds. Default: 30.

.PARAMETER OllamaUrl
    Ollama base URL. Default: http://127.0.0.1:11434.

.PARAMETER NivosUrl
    Nivos server base URL. Default: http://127.0.0.1:8000.
#>

[CmdletBinding()]
param(
    [int]$DurationMinutes = 60,
    [int]$IntervalSeconds = 30,
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [string]$NivosUrl = "http://127.0.0.1:8000",
    [string]$LogFile = ""
)

$ErrorActionPreference = "Continue"

$totalSeconds = $DurationMinutes * 60
$startTime = Get-Date
$endTime = $startTime.AddMinutes($DurationMinutes)
$samples = @()
$seenEvents = [System.Collections.Generic.HashSet[string]]::new()
$restartEvents = [System.Collections.Generic.List[PSObject]]::new()

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " Nivos Stability Test" -ForegroundColor Green
Write-Host " Duration : $DurationMinutes minutes (every $IntervalSeconds s)" -ForegroundColor Gray
Write-Host " Started  : $($startTime.ToString('yyyy-MM-dd HH:mm:ss'))" -ForegroundColor Gray
Write-Host " Ollama   : $OllamaUrl" -ForegroundColor Gray
Write-Host " Nivos    : $NivosUrl" -ForegroundColor Gray
Write-Host " Press Ctrl+C at any time to finish early and see summary." -ForegroundColor DarkGray
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ("{0,-20} {1,-10} {2,-10} {3,-12} {4}" -f "Time", "Ollama", "Nivos", "Free RAM", "Activity / Events") -ForegroundColor Yellow
Write-Host ("-" * 75) -ForegroundColor DarkGray

function Get-FreeRamGB {
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        return [math]::Round($os.FreePhysicalMemory / 1048576, 2)
    } catch {
        return $null
    }
}

function Check-HttpEndpoint {
    param([string]$Url, [int]$TimeoutSec = 3)
    try {
        $req = [System.Net.WebRequest]::Create($Url)
        $req.Timeout = $TimeoutSec * 1000
        $req.Method = "GET"
        $resp = $req.GetResponse()
        $resp.Close()
        return $true
    } catch {
        return $false
    }
}

function Get-NivosOllamaStatus {
    param([string]$BaseUrl)
    try {
        $uri = "$($BaseUrl.TrimEnd('/'))/v1/engine/ollama/status"
        $resp = Invoke-RestMethod -Uri $uri -TimeoutSec 3 -ErrorAction Stop
        return $resp
    } catch {
        return $null
    }
}

function Print-Summary {
    $now = Get-Date
    $elapsed = $now - $startTime
    $sampleCount = $samples.Count

    Write-Host ""
    Write-Host "=================================================================" -ForegroundColor Cyan
    Write-Host " Stability Test Summary" -ForegroundColor Green
    Write-Host "=================================================================" -ForegroundColor Cyan
    Write-Host (" Elapsed Time    : {0:hh\:mm\:ss} ({1} samples)" -f $elapsed, $sampleCount)

    if ($sampleCount -gt 0) {
        $ollamaUp = ($samples | Where-Object { $_.OllamaUp }).Count
        $nivosUp = ($samples | Where-Object { $_.NivosUp }).Count
        $ollamaPct = [math]::Round(($ollamaUp / $sampleCount) * 100, 1)
        $nivosPct = [math]::Round(($nivosUp / $sampleCount) * 100, 1)

        $ramValues = $samples | Where-Object { $_.FreeRamGB -ne $null } | ForEach-Object { $_.FreeRamGB }
        $minRam = if ($ramValues) { ($ramValues | Measure-Object -Minimum).Minimum } else { "N/A" }
        $maxRam = if ($ramValues) { ($ramValues | Measure-Object -Maximum).Maximum } else { "N/A" }
        $avgRam = if ($ramValues) { [math]::Round(($ramValues | Measure-Object -Average).Average, 2) } else { "N/A" }

        Write-Host (" Ollama Uptime   : {0}/{1} ({2}%)" -f $ollamaUp, $sampleCount, $ollamaPct) -ForegroundColor $(if ($ollamaPct -ge 99) { "Green" } else { "Red" })
        Write-Host (" Nivos Uptime    : {0}/{1} ({2}%)" -f $nivosUp, $sampleCount, $nivosPct) -ForegroundColor $(if ($nivosPct -ge 99) { "Green" } else { "Red" })
        Write-Host (" Free RAM (GB)   : Min $minRam | Max $maxRam | Avg $avgRam") -ForegroundColor Gray
        Write-Host (" Restart Events  : {0}" -f $restartEvents.Count) -ForegroundColor $(if ($restartEvents.Count -eq 0) { "Green" } else { "Yellow" })

        if ($restartEvents.Count -gt 0) {
            Write-Host ""
            Write-Host " Logged Restart Events:" -ForegroundColor Yellow
            foreach ($ev in $restartEvents) {
                Write-Host ("   [{0}] {1}: {2} ({3})" -f $ev.Time, $ev.Kind, $ev.Message, $ev.Reason) -ForegroundColor DarkYellow
            }
        }

        $isStable = ($ollamaPct -ge 99.0) -and ($nivosPct -ge 99.0) -and ($restartEvents.Count -eq 0)
        Write-Host ""
        if ($isStable) {
            Write-Host " VERDICT: PASSED - System is STABLE (Zero unexpected crashes/restarts)" -ForegroundColor Green
        } else {
            Write-Host " VERDICT: ATTENTION - Outages or restarts detected during test run" -ForegroundColor Yellow
        }
    } else {
        Write-Host " No samples recorded." -ForegroundColor Yellow
    }
    Write-Host "=================================================================" -ForegroundColor Cyan
}

try {
    $iteration = 0
    while ((Get-Date) -lt $endTime) {
        $iteration++
        $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        $ollamaUp = Check-HttpEndpoint -Url "$($OllamaUrl.TrimEnd('/'))/api/version"
        $nivosUp = Check-HttpEndpoint -Url "$($NivosUrl.TrimEnd('/'))/health"
        $freeRam = Get-FreeRamGB
        $eventNote = ""

        # Query Nivos supervisor for new restart/stopped events
        $sup = Get-NivosOllamaStatus -BaseUrl $NivosUrl
        if ($sup -and $sup.events) {
            foreach ($ev in $sup.events) {
                $evKey = "$($ev.timestamp)-$($ev.kind)-$($ev.message)"
                if (-not $seenEvents.Contains($evKey)) {
                    $seenEvents.Add($evKey) | Out-Null
                    if ($ev.kind -in @("restarting", "restart_failed", "stopped")) {
                        $restartEvents.Add([PSCustomObject]@{
                            Time    = $ev.time
                            Kind    = $ev.kind
                            Message = $ev.message
                            Reason  = $ev.reason
                        })
                        $eventNote = "! $($ev.kind): $($ev.message)"
                    }
                }
            }
        }

        $sample = [PSCustomObject]@{
            SampleIndex = $iteration
            Timestamp   = $ts
            OllamaUp    = $ollamaUp
            NivosUp     = $nivosUp
            FreeRamGB   = $freeRam
            Event       = $eventNote
        }
        $samples += $sample

        $ollamaColor = if ($ollamaUp) { "Green" } else { "Red" }
        $nivosColor = if ($nivosUp) { "Green" } else { "Red" }
        $ollamaStr = if ($ollamaUp) { "UP" } else { "DOWN" }
        $nivosStr = if ($nivosUp) { "UP" } else { "DOWN" }
        $ramStr = if ($freeRam -ne $null) { "$freeRam GB" } else { "N/A" }

        $line = "{0,-20} {1,-10} {2,-10} {3,-12} {4}" -f $ts, $ollamaStr, $nivosStr, $ramStr, $eventNote
        Write-Host $line

        if ($LogFile) {
            $line | Out-File -FilePath $LogFile -Append -Encoding utf8
        }

        $nextSleep = [math]::Min($IntervalSeconds, [math]::Max(1, [int]($endTime - (Get-Date)).TotalSeconds))
        if ($nextSleep -gt 0) {
            Start-Sleep -Seconds $nextSleep
        }
    }
} finally {
    Print-Summary
}

