param([string]$OutputPath = '')

$ErrorActionPreference = 'Stop'
$sourceUrl = 'https://sg.qq.com/webplat/info/news_version3/159/23162/23166/23182/m14774/201604/453763.shtml'
$utf8 = [System.Text.Encoding]::UTF8
$npcHeader = $utf8.GetString([System.Convert]::FromBase64String('TlBD5ZCN5a2X'))
$npcFileName = $utf8.GetString([System.Convert]::FromBase64String('5a6Y5pa5TlBD6K+N6KGoLmpzb24='))
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot "..\src\guoling_task_ocr\data\$npcFileName"
}

$client = [System.Net.WebClient]::new()
$html = [System.Text.Encoding]::GetEncoding('gb18030').GetString($client.DownloadData($sourceUrl))

function Convert-HtmlCellToText([string]$Html) {
    $text = [regex]::Replace($Html, '<br\s*/?>', ',', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    $text = [regex]::Replace($text, '<[^>]+>', '')
    return [System.Net.WebUtility]::HtmlDecode($text).Replace("`r", '').Replace("`n", '').Trim()
}

$npcs = [System.Collections.Generic.List[object]]::new()
foreach ($tableMatch in [regex]::Matches($html, '<table\b[^>]*>.*?</table>', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor [System.Text.RegularExpressions.RegexOptions]::Singleline)) {
    $rows = [regex]::Matches($tableMatch.Value, '<tr\b[^>]*>(.*?)</tr>', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor [System.Text.RegularExpressions.RegexOptions]::Singleline)
    if ($rows.Count -lt 2) { continue }

    $headerCells = @([regex]::Matches($rows[0].Groups[1].Value, '<t[dh]\b[^>]*>(.*?)</t[dh]>', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor [System.Text.RegularExpressions.RegexOptions]::Singleline) | ForEach-Object { Convert-HtmlCellToText $_.Groups[1].Value })
    if ($headerCells.Count -lt 4 -or $headerCells[0] -notmatch $npcHeader) { continue }

    foreach ($rowMatch in ($rows | Select-Object -Skip 1)) {
        $cells = @([regex]::Matches($rowMatch.Groups[1].Value, '<t[dh]\b[^>]*>(.*?)</t[dh]>', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor [System.Text.RegularExpressions.RegexOptions]::Singleline) | ForEach-Object { Convert-HtmlCellToText $_.Groups[1].Value })
        if ($cells.Count -lt 4 -or [string]::IsNullOrWhiteSpace($cells[0])) { continue }
        $npcs.Add([ordered]@{
            name = $cells[0]
            location = $cells[1]
            x = $cells[2]
            y = $cells[3]
        })
    }
}

$payload = [ordered]@{
    description = 'Local NPC vocabulary extracted from the QQ SG official public reference.'
    fetched_at = (Get-Date -Format 'yyyy-MM-dd')
    official_sources = @($sourceUrl)
    npcs = @($npcs)
}

$target = [System.IO.Path]::GetFullPath($OutputPath)
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($target)) | Out-Null
$payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $target -Encoding UTF8
Write-Host "Saved $($payload.npcs.Count) NPC entries: $target"
