@echo off
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
cd /d "%~dp0"
if not exist bin mkdir bin
cl /nologo /LD /O2 /EHsc /W4 TrackLoaderCPS.cpp /Fo:bin\TrackLoaderCPS.obj /Fe:bin\TrackLoaderCPS.dll
if errorlevel 1 exit /b 1
cl /nologo /LD /O2 /EHsc /W4 /DTRACKLOADER_STANDALONE TrackLoaderCPS.cpp /Fo:bin\TrackLoader.obj /Fe:bin\TrackLoader.dll
exit /b %errorlevel%
