$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$reportDirectory = $PSScriptRoot
$mainFile = Join-Path $reportDirectory 'main.tex'
$compilerCommand = Get-Command xelatex -ErrorAction SilentlyContinue
if ($compilerCommand) {
    $compiler = $compilerCommand.Source
} else {
    $fallbackCompiler = 'C:\Users\22086\AppData\Local\Programs\MiKTeX\miktex\bin\x64\xelatex.exe'
    if (-not (Test-Path -LiteralPath $fallbackCompiler)) {
        throw 'XeLaTeX was not found. Install TeX Live/MiKTeX or add xelatex.exe to PATH.'
    }
    $compiler = $fallbackCompiler
}

if (-not (Test-Path -LiteralPath $mainFile)) {
    throw "Report source was not found: $mainFile"
}

Push-Location $reportDirectory
try {
    Write-Host 'Running XeLaTeX pass 1...' -ForegroundColor Cyan
    & $compiler -synctex=1 -interaction=nonstopmode -halt-on-error 'main.tex'
    if ($LASTEXITCODE -ne 0) {
        throw "XeLaTeX pass 1 failed with exit code $LASTEXITCODE"
    }

    Write-Host 'Running XeLaTeX pass 2 for the table of contents and references...' -ForegroundColor Cyan
    & $compiler -synctex=1 -interaction=nonstopmode -halt-on-error 'main.tex'
    if ($LASTEXITCODE -ne 0) {
        throw "XeLaTeX pass 2 failed with exit code $LASTEXITCODE"
    }

    $pdfFile = Join-Path $reportDirectory 'main.pdf'
    if (-not (Test-Path -LiteralPath $pdfFile)) {
        throw "The compiler did not generate the expected PDF: $pdfFile"
    }

    Write-Host "PDF generated: $pdfFile" -ForegroundColor Green
} finally {
    Pop-Location
}
