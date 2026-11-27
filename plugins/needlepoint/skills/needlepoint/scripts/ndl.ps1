# needlepoint launcher for PowerShell (Windows PowerShell 5.1 and PowerShell 7+).
# Calls Python directly with the arguments unchanged, so patterns like 'status|tuple' are never re-parsed by cmd.exe.
# Prefers the py launcher, since python/python3 on Windows can be Microsoft Store stubs that do not run Python.
$script = Join-Path $PSScriptRoot 'ndl.py'
foreach ($cand in @(@('py', '-3'), @('python'), @('python3'))) {
    $exe = Get-Command $cand[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $exe) { continue }
    $pre = @()
    if ($cand.Count -gt 1) { $pre = $cand[1..($cand.Count - 1)] }
    & $exe.Source @pre -I $script @args
    exit $LASTEXITCODE
}
[Console]::Error.WriteLine('needlepoint: Python 3.8+ is required but py/python/python3 was not found on PATH')
exit 127
