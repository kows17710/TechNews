<#
.SYNOPSIS
    SMTP 비밀번호(앱 비밀번호)를 현재 사용자 계정으로 암호화해 smtp.cred 에 저장합니다.
    DPAPI 로 암호화되므로 이 파일은 같은 Windows 사용자 계정에서만 복호화됩니다.

.EXAMPLE
    .\Set-SmtpPassword.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$credFile = Join-Path $ScriptDir 'smtp.cred'

$sec = Read-Host -AsSecureString "SMTP 비밀번호(앱 비밀번호)를 입력하세요"
if ($sec.Length -eq 0) { throw "비밀번호가 비어 있습니다." }

$sec | ConvertFrom-SecureString | Set-Content -Path $credFile -Encoding utf8
Write-Host "저장 완료: $credFile"
Write-Host "이 파일은 현재 사용자($env:USERNAME) 계정에서만 복호화됩니다."
