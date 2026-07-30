param([string]$OutputPath = '')

$ErrorActionPreference = 'Stop'
$sourceUrl = 'https://sg.qq.com/webplat/info/news_version3/159/23162/23166/23182/m14774/201604/453762.shtml'
$utf8 = [System.Text.Encoding]::UTF8
$monsterHeader = $utf8.GetString([System.Convert]::FromBase64String('5oCq54mp5ZCN5a2X'))
$monsterFileName = $utf8.GetString([System.Convert]::FromBase64String('5a6Y5pa55oCq54mp6K+N6KGoLmpzb24='))
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot "..\src\guoling_task_ocr\data\$monsterFileName"
}
$client = [System.Net.WebClient]::new()
$html = [System.Text.Encoding]::GetEncoding("gb18030").GetString($client.DownloadData($sourceUrl))

function Convert-HtmlCellToText([string]$Html) {
    $text = [regex]::Replace($Html, '<br\s*/?>', ',', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    $text = [regex]::Replace($text, '<[^>]+>', '')
    return [System.Net.WebUtility]::HtmlDecode($text).Replace("`r", '').Replace("`n", '').Trim()
}

$monsters = [System.Collections.Generic.List[object]]::new()
foreach ($tableMatch in [regex]::Matches($html, '<table\b[^>]*>.*?</table>', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor [System.Text.RegularExpressions.RegexOptions]::Singleline)) {
    $rows = [regex]::Matches($tableMatch.Value, '<tr\b[^>]*>(.*?)</tr>', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor [System.Text.RegularExpressions.RegexOptions]::Singleline)
    if ($rows.Count -lt 2) { continue }

    $headerCells = @([regex]::Matches($rows[0].Groups[1].Value, '<t[dh]\b[^>]*>(.*?)</t[dh]>', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor [System.Text.RegularExpressions.RegexOptions]::Singleline) | ForEach-Object { Convert-HtmlCellToText $_.Groups[1].Value })
    if ($headerCells.Count -lt 3 -or $headerCells[0] -notmatch $monsterHeader) { continue }

    $hasDrops = $headerCells.Count -ge 4
    foreach ($rowMatch in ($rows | Select-Object -Skip 1)) {
        $cells = @([regex]::Matches($rowMatch.Groups[1].Value, '<t[dh]\b[^>]*>(.*?)</t[dh]>', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor [System.Text.RegularExpressions.RegexOptions]::Singleline) | ForEach-Object { Convert-HtmlCellToText $_.Groups[1].Value })
        if ($cells.Count -lt 3 -or [string]::IsNullOrWhiteSpace($cells[0])) { continue }
        $monsters.Add([ordered]@{
            name = $cells[0]
            level = $cells[1]
            location = $cells[2]
            drops = if ($hasDrops -and $cells.Count -ge 4) { $cells[3] } else { "" }
        })
    }
}

$payload = [ordered]@{
    description = 'Local monster vocabulary extracted from the QQ SG official public reference.'
    fetched_at = (Get-Date -Format 'yyyy-MM-dd')
    official_sources = @($sourceUrl)
    monsters = @($monsters)
}

$target = [System.IO.Path]::GetFullPath($OutputPath)
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($target)) | Out-Null
$payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $target -Encoding UTF8
Write-Host "Saved $($payload.monsters.Count) monster entries: $target"
