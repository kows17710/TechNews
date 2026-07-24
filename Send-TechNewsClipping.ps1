<#
.SYNOPSIS
    네이버 검색 API로 최신 테크 뉴스를 수집해 Outlook으로 발송합니다.

.PARAMETER Preview
    메일을 보내지 않고 Outlook 창에 초안으로 띄웁니다 (테스트용).

.PARAMETER SaveHtml
    생성된 HTML을 파일로도 저장합니다.

.EXAMPLE
    .\Send-TechNewsClipping.ps1 -Preview
    .\Send-TechNewsClipping.ps1
#>
[CmdletBinding()]
param(
    [switch]$Preview,
    [switch]$SaveHtml,
    [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ConfigPath) { $ConfigPath = Join-Path $ScriptDir 'config.json' }
$LogDir = Join-Path $ScriptDir 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("run-{0}.log" -f (Get-Date -Format 'yyyy-MM'))

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

function Clear-NewsText {
    param([string]$Text)
    if (-not $Text) { return '' }
    $t = $Text -replace '<[^>]+>', ''
    $t = [System.Net.WebUtility]::HtmlDecode($t)
    return ($t -replace '\s+', ' ').Trim()
}

function ConvertTo-DateTimeSafe {
    param([string]$Value)
    try {
        return [datetime]::Parse($Value, [System.Globalization.CultureInfo]::InvariantCulture)
    } catch {
        return $null
    }
}

function Get-Domain {
    param([string]$Url)
    try { return ([uri]$Url).Host -replace '^www\.', '' } catch { return '' }
}

# ---------------------------------------------------------------- 설정 로드
if (-not (Test-Path $ConfigPath)) { throw "설정 파일을 찾을 수 없습니다: $ConfigPath" }
$cfg = Get-Content -Path $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json

if ($cfg.naver.clientId -like 'YOUR_*' -or [string]::IsNullOrWhiteSpace($cfg.naver.clientId)) {
    throw "config.json 의 naver.clientId / clientSecret 을 먼저 채워주세요."
}

Write-Log "클리핑 시작 (키워드 $($cfg.clipping.keywords.Count)개)"

# ---------------------------------------------------------------- 뉴스 수집
$headers = @{
    'X-Naver-Client-Id'     = $cfg.naver.clientId
    'X-Naver-Client-Secret' = $cfg.naver.clientSecret
}

$cutoff = (Get-Date).AddHours(-1 * $cfg.clipping.withinHours)
$seenLinks = @{}
$seenTitles = @{}
$collected = New-Object System.Collections.ArrayList

foreach ($keyword in $cfg.clipping.keywords) {
    $uri = 'https://openapi.naver.com/v1/search/news.json?query={0}&display={1}&start=1&sort=date' -f `
        [uri]::EscapeDataString($keyword), $cfg.clipping.perKeyword

    try {
        $res = Invoke-RestMethod -Uri $uri -Headers $headers -Method Get -TimeoutSec 30
    } catch {
        Write-Log "'$keyword' 검색 실패: $($_.Exception.Message)" 'WARN'
        continue
    }

    $kept = 0
    foreach ($item in $res.items) {
        if ($kept -ge $cfg.clipping.maxPerKeyword) { break }

        $title = Clear-NewsText $item.title
        $desc  = Clear-NewsText $item.description
        $pub   = ConvertTo-DateTimeSafe $item.pubDate
        if (-not $pub -or $pub -lt $cutoff) { continue }

        $link = $item.originallink
        if ([string]::IsNullOrWhiteSpace($link)) { $link = $item.link }

        # 중복 제거 (링크 + 제목 앞 20자)
        $titleKey = ($title -replace '[^\w가-힣]', '')
        if ($titleKey.Length -gt 20) { $titleKey = $titleKey.Substring(0, 20) }
        if ($seenLinks.ContainsKey($link) -or $seenTitles.ContainsKey($titleKey)) { continue }

        # 제외 키워드
        $skip = $false
        foreach ($ex in $cfg.clipping.excludeKeywords) {
            if ($ex -and ($title -like "*$ex*")) { $skip = $true; break }
        }
        if ($skip) { continue }

        $seenLinks[$link] = $true
        $seenTitles[$titleKey] = $true
        $kept++

        $domain = Get-Domain $link
        $score = 0
        foreach ($p in $cfg.clipping.preferredPress) {
            if ($domain -like "*$p*") { $score = 1; break }
        }

        [void]$collected.Add([pscustomobject]@{
            Keyword = $keyword
            Title   = $title
            Desc    = $desc
            Link    = $link
            PubDate = $pub
            Domain  = $domain
            Score   = $score
        })
    }
    Write-Log "'$keyword' → $kept 건"
}

if ($collected.Count -eq 0) {
    Write-Log "최근 $($cfg.clipping.withinHours)시간 내 수집된 기사가 없습니다. 메일을 보내지 않고 종료합니다." 'WARN'
    return
}

$articles = $collected |
    Sort-Object -Property @{Expression = 'Score'; Descending = $true}, @{Expression = 'PubDate'; Descending = $true} |
    Select-Object -First $cfg.clipping.maxTotal

Write-Log "총 $($articles.Count) 건 선별 완료"

# ---------------------------------------------------------------- HTML 생성
function HtmlEnc { param([string]$s) return [System.Net.WebUtility]::HtmlEncode($s) }

$today = Get-Date -Format 'yyyy년 M월 d일 (ddd)'
$sb = New-Object System.Text.StringBuilder

[void]$sb.Append(@"
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f5f7;">
<div style="max-width:720px;margin:0 auto;padding:24px 16px;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;color:#1a1a1a;">
  <div style="background:#ffffff;border:1px solid #e3e5e8;border-radius:10px;overflow:hidden;">
    <div style="background:#12263f;padding:20px 24px;">
      <div style="color:#ffffff;font-size:19px;font-weight:bold;">데일리 테크 뉴스 클리핑</div>
      <div style="color:#9fb3c8;font-size:13px;margin-top:5px;">$today &middot; 최근 $($cfg.clipping.withinHours)시간 · 총 $($articles.Count)건</div>
    </div>
    <div style="padding:8px 24px 4px 24px;">
"@)

$grouped = $articles | Group-Object Keyword | Sort-Object Name
foreach ($g in $grouped) {
    [void]$sb.Append("<div style='margin:20px 0 8px 0;font-size:14px;font-weight:bold;color:#12263f;border-left:4px solid #2f80ed;padding-left:9px;'># $(HtmlEnc $g.Name) <span style='color:#98a2b3;font-weight:normal;'>($($g.Count))</span></div>")

    foreach ($a in ($g.Group | Sort-Object PubDate -Descending)) {
        $desc = $a.Desc
        if ($desc.Length -gt 130) { $desc = $desc.Substring(0, 130) + '…' }
        $when = $a.PubDate.ToString('MM/dd HH:mm')
        [void]$sb.Append(@"
      <div style="padding:12px 0;border-bottom:1px solid #eef0f3;">
        <a href="$(HtmlEnc $a.Link)" style="font-size:15px;font-weight:bold;color:#1849a9;text-decoration:none;line-height:1.45;">$(HtmlEnc $a.Title)</a>
        <div style="font-size:13px;color:#525c6b;margin-top:6px;line-height:1.55;">$(HtmlEnc $desc)</div>
        <div style="font-size:11px;color:#98a2b3;margin-top:6px;">$(HtmlEnc $a.Domain) &nbsp;|&nbsp; $when</div>
      </div>
"@)
    }
}

[void]$sb.Append(@"
    </div>
    <div style="padding:16px 24px;background:#fafbfc;border-top:1px solid #eef0f3;font-size:11px;color:#98a2b3;">
      네이버 검색 API 기반 자동 수집 &middot; 생성 $(Get-Date -Format 'yyyy-MM-dd HH:mm')
    </div>
  </div>
</div>
</body></html>
"@)

$html = $sb.ToString()

if ($SaveHtml -or $Preview) {
    $htmlPath = Join-Path $LogDir ("clipping-{0}.html" -f (Get-Date -Format 'yyyyMMdd-HHmm'))
    Set-Content -Path $htmlPath -Value $html -Encoding UTF8
    Write-Log "HTML 저장: $htmlPath"
}

# ---------------------------------------------------------------- SMTP 발송
$subject = "{0} {1} ({2}건)" -f $cfg.mail.subjectPrefix, (Get-Date -Format 'yyyy-MM-dd'), $articles.Count

if ($Preview) {
    Write-Log "미리보기 모드 — 메일을 보내지 않았습니다. logs 폴더의 HTML을 확인하세요."
    return
}

# 비밀번호: 환경변수 우선, 없으면 자격증명 파일(DPAPI 암호화) 사용
$pwdPlain = $null
if ($cfg.smtp.passwordEnvVar) {
    $pwdPlain = [Environment]::GetEnvironmentVariable($cfg.smtp.passwordEnvVar, 'User')
    if (-not $pwdPlain) { $pwdPlain = [Environment]::GetEnvironmentVariable($cfg.smtp.passwordEnvVar, 'Machine') }
}
$credFile = Join-Path $ScriptDir 'smtp.cred'
if (-not $pwdPlain -and (Test-Path $credFile)) {
    try {
        $sec = Get-Content $credFile | ConvertTo-SecureString
        $pwdPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    } catch {
        Write-Log "smtp.cred 복호화 실패 (다른 사용자/PC에서 만든 파일일 수 있음): $($_.Exception.Message)" 'ERROR'
    }
}
if (-not $pwdPlain) {
    throw "SMTP 비밀번호를 찾을 수 없습니다. 환경변수 '$($cfg.smtp.passwordEnvVar)' 를 설정하거나 Set-SmtpPassword.ps1 을 실행하세요."
}

$fromDisplay = if ($cfg.smtp.fromDisplayName) { $cfg.smtp.fromDisplayName } else { $cfg.smtp.from }
$msg = New-Object System.Net.Mail.MailMessage
$msg.From = New-Object System.Net.Mail.MailAddress($cfg.smtp.from, $fromDisplay, [System.Text.Encoding]::UTF8)
foreach ($addr in ($cfg.mail.to -split '[;,]')) { if ($addr.Trim()) { $msg.To.Add($addr.Trim()) } }
if ($cfg.mail.cc) { foreach ($addr in ($cfg.mail.cc -split '[;,]')) { if ($addr.Trim()) { $msg.CC.Add($addr.Trim()) } } }
$msg.SubjectEncoding = [System.Text.Encoding]::UTF8
$msg.BodyEncoding    = [System.Text.Encoding]::UTF8
$msg.Subject  = $subject
$msg.Body     = $html
$msg.IsBodyHtml = $true

$client = New-Object System.Net.Mail.SmtpClient($cfg.smtp.host, [int]$cfg.smtp.port)
$client.EnableSsl = [bool]$cfg.smtp.useStartTls
$client.DeliveryMethod = [System.Net.Mail.SmtpDeliveryMethod]::Network
$client.Credentials = New-Object System.Net.NetworkCredential($cfg.smtp.username, $pwdPlain)

try {
    $client.Send($msg)
    Write-Log "발송 완료 → $($cfg.mail.to)"
} catch {
    Write-Log "SMTP 발송 실패: $($_.Exception.Message)" 'ERROR'
    if ($_.Exception.InnerException) { Write-Log "  ↳ $($_.Exception.InnerException.Message)" 'ERROR' }
    throw
} finally {
    $msg.Dispose(); $client.Dispose()
    $pwdPlain = $null
}
