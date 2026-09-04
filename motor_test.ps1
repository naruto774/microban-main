# Run motor_test.py with Miniconda Python (avoids WindowsApps / MSYS python mismatch).
$Python = "C:\ProgramData\miniconda3\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "Miniconda not found at $Python — edit motor_test.ps1 or use: python -m pip install rustypot"
    exit 1
}
& $Python "$PSScriptRoot\src\debug\motor_test.py" @args
