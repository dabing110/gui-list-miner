# Windows built-in OCR (WinRT) over a folder of PNGs.
# Zero pip dependencies, no network. Writes <png>.txt next to each image.
#
# Usage (MUST run via the PowerShell tool - invoking powershell from Bash is blocked):
#   powershell -ExecutionPolicy Bypass -File ocr_winrt.ps1 -Dir "data\cropped" -Lang "zh-CN"
#
# Output format, per word:
#   L=<line-index> X=.. Y=.. H=..\t<word>
# plus one line-level record per OCR line:
#   L=<line-index> T\t<full line text>
#
# NOTE: OcrLine has NO BoundingRect in WinRT - only OcrWord does. Consumers must
# cluster words into rows themselves using the word-level X/Y/H.

param(
    [Parameter(Mandatory=$true)][string]$Dir,
    [string]$Lang = "zh-CN",
    [string]$Pattern = "*.png"
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Runtime.WindowsRuntime

$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

$ocr = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($Lang)
if (-not $ocr) { $ocr = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $ocr) {
    Set-Content -Path (Join-Path $Dir "_ocr_error.txt") -Value "NO_OCR_LANGUAGE_PACK" -Encoding UTF8
    exit 1
}

$files = Get-ChildItem -Path $Dir -Filter $Pattern | Sort-Object Name
$i = 0
foreach ($png in $files) {
    $i++
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($png.FullName)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await ($ocr.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

        $sb = New-Object System.Text.StringBuilder
        $li = 0
        foreach ($line in $result.Lines) {
            foreach ($word in $line.Words) {
                $rc = $word.BoundingRect
                [void]$sb.AppendLine("L=$li X=$([int]$rc.X) Y=$([int]$rc.Y) H=$([int]$rc.Height)`t$($word.Text)")
            }
            [void]$sb.AppendLine("L=$li T`t$($line.Text)")
            $li++
        }
        Set-Content -Path ($png.FullName + ".txt") -Value $sb.ToString() -Encoding UTF8

        $stream.Dispose()
        $bitmap.Dispose()
    }
    catch {
        Set-Content -Path ($png.FullName + ".err.txt") -Value $_.Exception.Message -Encoding UTF8
    }
    if ($i % 20 -eq 0) { Write-Output "OCR $i / $($files.Count)" }
}
Write-Output "OCR_DONE $i"
