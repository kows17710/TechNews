<#
.SYNOPSIS
    매일 아침 뉴스 클리핑을 발송하도록 Windows 작업 스케줄러에 등록합니다.

.EXAMPLE
    .\Register-Task.ps1                  # 매일 08:00
    .\Register-Task.ps1 -Time "07:30"    # 시각 변경
    .\Register-Task.ps1 -Unregister      # 등록 해제
#>
[CmdletBinding()]
param(
    [string]$Time = '08:00',
    [string]$TaskName = 'TechNewsClipping',
    [switch]$WhenLoggedOn,
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = Join-Path $ScriptDir 'Send-TechNewsClipping.ps1'

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "작업 '$TaskName' 등록을 해제했습니다."
    return
}

if (-not (Test-Path $Target)) { throw "스크립트를 찾을 수 없습니다: $Target" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $Target) `
    -WorkingDirectory $ScriptDir

$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($Time, 'HH:mm', $null))

# SMTP 발송은 로그인 세션이 필요 없으므로 기본은 '로그온 여부와 무관하게 실행'(S4U).
# 단, 비밀번호를 smtp.cred(DPAPI) 로 저장한 경우 S4U 에서는 복호화가 실패할 수 있습니다.
#  → 그럴 땐 환경변수(TECHNEWS_SMTP_PWD)를 쓰거나, -WhenLoggedOn 으로 등록하세요.
if ($WhenLoggedOn) {
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
} else {
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description '네이버 API 기반 테크 뉴스 클리핑을 SMTP로 발송' -Force | Out-Null

$mode = if ($WhenLoggedOn) { '로그온 상태에서만' } else { '로그온 여부와 무관하게' }
Write-Host "작업 '$TaskName' 등록 완료 — 매일 $Time 실행 ($mode)"
Write-Host "즉시 테스트: Start-ScheduledTask -TaskName $TaskName"
Write-Host "상태 확인:   Get-ScheduledTaskInfo -TaskName $TaskName"
