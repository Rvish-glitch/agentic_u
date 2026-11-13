# Agentic Universe - Pipeline Automation Script
param(
    [ValidateSet("all", "generate", "drop1", "drop2", "report", "test")]
    [string]$Action = "all"
)

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "   Agentic Universe - Data Pipeline Backend Runner      " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

# Activate venv if exists
if (Test-Path ".venv\Scripts\Activate.ps1") {
    Write-Host "[INFO] Activating virtual environment (.venv)..." -ForegroundColor Yellow
    & .venv\Scripts\Activate.ps1
}

function Run-Step($name, [scriptblock]$cmd) {
    Write-Host "`n>>> Running: $name" -ForegroundColor Green
    & $cmd
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed at step: $name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

switch ($Action) {
    "generate" {
        Run-Step "Generate Synthetic Data" { python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data }
    }
    "drop1" {
        Run-Step "Ingest Drop 1" { python run.py ingest data/drop_1 }
    }
    "drop2" {
        Run-Step "Ingest Drop 2" { python run.py ingest data/drop_2 }
    }
    "report" {
        Run-Step "Generate Reports" { python run.py report --out reports }
    }
    "test" {
        Run-Step "Run Tests" { pytest }
    }
    "all" {
        Run-Step "Generate Data" { python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data }
        Run-Step "Ingest Drop 1" { python run.py ingest data/drop_1 }
        Run-Step "Ingest Drop 2" { python run.py ingest data/drop_2 }
        Run-Step "Generate Reports" { python run.py report --out reports }
        Write-Host "`n[SUCCESS] Full pipeline completed! Reports generated in reports/" -ForegroundColor Cyan
    }
}

