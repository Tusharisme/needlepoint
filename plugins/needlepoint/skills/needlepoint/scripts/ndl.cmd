@echo off
rem needlepoint launcher for Windows PowerShell and cmd.exe (the sh launcher `ndl` covers Git Bash, Linux and macOS).
rem Prefers the py launcher, since python/python3 on Windows can be Microsoft Store stubs that do not run Python.
setlocal
set "NDL_SCRIPT=%~dp0ndl.py"
where py >nul 2>nul && (
  py -3 -I "%NDL_SCRIPT%" %*
  goto :done
)
where python >nul 2>nul && (
  python -I "%NDL_SCRIPT%" %*
  goto :done
)
where python3 >nul 2>nul && (
  python3 -I "%NDL_SCRIPT%" %*
  goto :done
)
echo needlepoint: Python 3.8+ is required but py/python/python3 was not found on PATH 1>&2
exit /b 127
:done
exit /b %ERRORLEVEL%
