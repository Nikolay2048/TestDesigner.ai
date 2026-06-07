param(
    [ValidateSet("ollama", "openrouter")]
    [string]$Llm = "ollama",
    [string]$Model = "",
    [Alias("Scenario")]
    [string]$ScenarioName = "",
    [string]$OutputRoot = "",
    [string]$PythonCommand = "python",
    [int]$MaxFixerTries = 7,
    [switch]$SkipDependencyResolution,
    [switch]$SkipTestCases,
    [switch]$SkipPostman
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = (Get-Command $PythonCommand -ErrorAction Stop).Source
$baseUrl = "http://127.0.0.1:8080"
$ollamaUrl = "http://127.0.0.1:11434"

if (-not $OutputRoot) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputRoot = Join-Path $projectRoot "runs\carsharing_$timestamp"
}
elseif (-not [System.IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $projectRoot $OutputRoot
}

$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$stableDir = Join-Path $OutputRoot "stable"
$mockLogDir = Join-Path $OutputRoot "_mock"
$scenarioDir = Join-Path $projectRoot "data\carsharing\specs"
$openapiPath = Join-Path $projectRoot "data\carsharing\openapi\openapi.yaml"
$testDataPath = Join-Path $projectRoot "data\carsharing\test-data.yaml"
$serverDir = Join-Path $projectRoot "data\carsharing\server"

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
New-Item -ItemType Directory -Path $stableDir -Force | Out-Null
New-Item -ItemType Directory -Path $mockLogDir -Force | Out-Null

function Test-CarsharingMock {
    try {
        $response = Invoke-RestMethod -Method Get -Uri "$baseUrl/locations" -TimeoutSec 2
        return $null -ne $response.locations
    }
    catch {
        return $false
    }
}

function Wait-CarsharingMock {
    param([int]$TimeoutSeconds = 30)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-CarsharingMock) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Carsharing mock did not become ready at $baseUrl within $TimeoutSeconds seconds."
}

Write-Host "Output: $OutputRoot" -ForegroundColor Cyan

if ($Llm -eq "ollama") {
    try {
        Invoke-RestMethod -Method Get -Uri "$ollamaUrl/api/tags" -TimeoutSec 3 | Out-Null
    }
    catch {
        throw "Ollama is unavailable at $ollamaUrl. Start Ollama before running this script."
    }
}

$mockProcess = $null
$mockStartedHere = $false

try {
    if (Test-CarsharingMock) {
        Write-Host "Using the carsharing mock already running at $baseUrl"
    }
    else {
        Write-Host "Starting carsharing mock at $baseUrl"
        $mockProcess = Start-Process `
            -FilePath $python `
            -ArgumentList @("-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8080") `
            -WorkingDirectory $serverDir `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $mockLogDir "stdout.log") `
            -RedirectStandardError (Join-Path $mockLogDir "stderr.log") `
            -PassThru
        $mockStartedHere = $true
        Wait-CarsharingMock
        Write-Host "Carsharing mock started, PID $($mockProcess.Id)"
    }

    $scenarioFiles = @(Get-ChildItem -Path $scenarioDir -Filter "*.md" | Sort-Object Name)
    if ($ScenarioName) {
        $scenarioStem = [System.IO.Path]::GetFileNameWithoutExtension($ScenarioName)
        $scenarioFiles = @($scenarioFiles | Where-Object { $_.BaseName -eq $scenarioStem })
        if ($scenarioFiles.Count -eq 0) {
            throw "Scenario '$ScenarioName' was not found in $scenarioDir."
        }
    }
    if ($scenarioFiles.Count -eq 0) {
        throw "No scenarios found in $scenarioDir"
    }

    $results = @()

    foreach ($scenarioFile in $scenarioFiles) {
        $scenarioName = $scenarioFile.BaseName
        $runDir = Join-Path $OutputRoot $scenarioName
        $consoleLog = Join-Path $OutputRoot "$scenarioName.console.log"

        Write-Host ""
        Write-Host "=== $scenarioName ===" -ForegroundColor Cyan

        Invoke-RestMethod -Method Post -Uri "$baseUrl/mock/reset" -TimeoutSec 5 | Out-Null

        $arguments = @(
            (Join-Path $projectRoot "src\main.py"),
            "--scenario", $scenarioFile.FullName,
            "--openapi", $openapiPath,
            "--test-data", $testDataPath,
            "--out", $runDir,
            "--stable-dir", $stableDir,
            "--base-url", $baseUrl,
            "--llm", $Llm,
            "--max-attempts", "7",
            "--max-fixer-tries", "$MaxFixerTries"
        )

        if ($Model) {
            $arguments += @("--model", $Model)
        }
        if (-not $SkipDependencyResolution) {
            $arguments += "--resolve-dependencies"
        }

        if (-not $SkipTestCases) {
            $arguments += "--run-test-cases"
        }
        if (-not $SkipPostman) {
            $arguments += "--export-postman"
        }

        & $python @arguments 2>&1 | Tee-Object -FilePath $consoleLog
        $exitCode = $LASTEXITCODE
        $statePath = Join-Path $runDir "state.json"
        $status = "failed"
        if ($exitCode -eq 0 -and (Test-Path -LiteralPath $statePath)) {
            $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
            if ($state.stabilization -and $state.stabilization.status) {
                $status = $state.stabilization.status
            }
        }

        $results += [PSCustomObject]@{
            Scenario = $scenarioName
            Status = $status
            ExitCode = $exitCode
            Output = $runDir
            Log = $consoleLog
        }
    }

    $summaryPath = Join-Path $OutputRoot "batch-summary.json"
    $results | ConvertTo-Json -Depth 4 | Set-Content -Path $summaryPath -Encoding UTF8

    Write-Host ""
    Write-Host "=== Batch summary ===" -ForegroundColor Cyan
    $results | Format-Table Scenario, Status, ExitCode -AutoSize
    Write-Host "Summary: $summaryPath"

    if ($results.Where({ $_.Status -ne "passed" }).Count -gt 0) {
        exit 1
    }
}
finally {
    if ($mockStartedHere -and $mockProcess -and -not $mockProcess.HasExited) {
        Write-Host "Stopping carsharing mock, PID $($mockProcess.Id)"
        Stop-Process -Id $mockProcess.Id
        $mockProcess.WaitForExit()
    }
}
