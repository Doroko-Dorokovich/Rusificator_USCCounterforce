$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$buildRoot = Join-Path ([IO.Path]::GetTempPath()) ('usc-setup-build-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $buildRoot -ErrorAction Stop | Out-Null
try {
    $dump = Join-Path $project 'I2Languages_RU.txt'
    $entry = Join-Path $PSScriptRoot 'setup_wizard.py'
    $dataOptions = @('--add-data', "$dump;.")
    foreach ($image in @('setup.ico','welcome-art.png','wizard-badge.png')) {
        $dataOptions += @('--add-data', "$(Join-Path $PSScriptRoot $image);.")
    }
    $icon = Join-Path $PSScriptRoot 'setup.ico'
    $pythonRoot = python -c 'import sys; print(sys.base_prefix)'
    if ($LASTEXITCODE -ne 0) { throw 'Could not find the Python installation' }
    foreach ($library in @('tcl','tk')) {
        $zip = Get-ChildItem -LiteralPath (Join-Path $pythonRoot 'tcl') -Filter "lib$library*.zip" -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($zip) {
            $extracted = Join-Path $buildRoot $library
            python -m zipfile -e $zip.FullName $extracted
            if ($LASTEXITCODE -ne 0) { throw "Could not unpack the $library library" }
            $dataOptions += @('--add-data', "$(Join-Path $extracted ($library + '_library'));_$($library)_data")
        }
    }
    python -m PyInstaller --noconfirm --windowed --onefile --name Setup --icon $icon @dataOptions --workpath (Join-Path $buildRoot 'work') --specpath $buildRoot --distpath (Join-Path $buildRoot 'dist') $entry
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
    Copy-Item -LiteralPath (Join-Path $buildRoot 'dist\Setup.exe') -Destination (Join-Path $project 'Setup.exe') -Force
} finally {
    $target = (Resolve-Path -LiteralPath $buildRoot).Path
    $parent = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path
    if ((Split-Path -Parent $target) -eq $parent -and (Split-Path -Leaf $target) -match '^usc-setup-build-[a-f0-9]{32}$') {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}
