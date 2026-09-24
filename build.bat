@echo off
rem Build BatchCrop.exe with Nuitka + MSVC: one standalone file (Python, Qt, Pillow bundled), no console.
rem Output: dist\BatchCrop.exe
cd /d "%~dp0"
rem Python 3.13 + Visual Studio 2022 (MSVC 14.3) are the combination Nuitka fully supports.
set PY=py -3.13

rem Load the VS 2022 C++ environment so Nuitka finds cl.exe.
set VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -version [17.0^,18.0^) -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VS2022=%%i
if not defined VS2022 (echo Visual Studio 2022 with C++ build tools not found. & exit /b 1)
call "%VS2022%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1

%PY% -m pip install -r requirements.txt nuitka ordered-set zstandard || exit /b 1
%PY% -m nuitka ^
  --onefile ^
  --msvc=14.3 ^
  --enable-plugin=pyside6 ^
  --windows-console-mode=disable ^
  --assume-yes-for-downloads ^
  --output-dir=build ^
  --output-filename=BatchCrop.exe ^
  batch_crop.py || exit /b 1
if not exist dist mkdir dist
move /y build\BatchCrop.exe dist\BatchCrop.exe
