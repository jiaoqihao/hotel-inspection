param([Parameter(Mandatory)][string]$Directory)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $Directory -Force | Out-Null
$cases = Get-Content -Raw -Encoding utf8 (Join-Path $PSScriptRoot 'cases.json') | ConvertFrom-Json
$voice = New-Object -ComObject SAPI.SpVoice
$chinese = @($voice.GetVoices() | Where-Object { $_.GetDescription() -match 'Huihui|Chinese|Hui Hui' })[0]
if (!$chinese) { throw 'Chinese SAPI voice required' }
$voice.Voice=$chinese
for ($i=0; $i -lt $cases.Count; $i++) {
    $stream=New-Object -ComObject SAPI.SpFileStream
    $file=Join-Path $Directory ('call-{0:D2}.wav' -f ($i+1))
    if (Test-Path $file) { throw "Output exists: $file" }
    $stream.Open($file,3)
    $voice.AudioOutputStream=$stream
    $null=$voice.Speak($cases[$i][2])
    $stream.Close()
}
