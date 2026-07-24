# 데일리 테크 뉴스 클리핑 에이전트

네이버 검색 API로 최신 테크 뉴스를 수집해 매일 아침 SMTP(Office365)로 메일 발송합니다.
PowerShell만 사용하므로 별도 런타임 설치가 필요 없습니다.

## 구성

| 파일 | 역할 |
|---|---|
| `config.json` | API 키, SMTP, 수신자, 키워드, 필터 설정 |
| `Send-TechNewsClipping.ps1` | 수집 → HTML 생성 → SMTP 발송 |
| `Set-SmtpPassword.ps1` | 발신 계정 앱 비밀번호를 암호화 저장 |
| `Register-Task.ps1` | 작업 스케줄러 등록/해제 |
| `logs/` | 실행 로그, 미리보기 HTML |

## 1. 네이버 API 키 입력

`config.json` 의 `naver.clientId` / `naver.clientSecret` 을 채웁니다.
([네이버 개발자센터](https://developers.naver.com/apps) → 애플리케이션 등록 → **검색** API 사용 신청)

## 1-2. SMTP(발신 계정) 설정

`config.json` 의 `smtp` 블록을 채웁니다.

- `host` / `port` — Office365 는 `smtp.office365.com` / `587` (기본값)
- `from`, `username` — 보내는 Outlook 주소
- `fromDisplayName` — 표시 이름

**비밀번호는 config.json 에 넣지 않습니다.** 아래 중 하나를 쓰세요.

```powershell
# (권장) 암호화 파일로 저장 — 현재 Windows 사용자 계정에서만 복호화됨
.\Set-SmtpPassword.ps1
```

또는 환경변수:

```powershell
[Environment]::SetEnvironmentVariable('TECHNEWS_SMTP_PWD','앱비밀번호','User')
```

> Office365 는 보안 정책상 SMTP AUTH 가 막혀 있거나 **앱 비밀번호**가 필요할 수 있습니다.
> 조직 계정이면 관리자에게 SMTP AUTH 허용 여부를 확인하세요.
> MFA 를 쓰는 계정은 로그인 비밀번호가 아니라 **앱 비밀번호**를 발급받아 사용해야 합니다.

## 2. 키워드 조정

`config.json` 의 `clipping` 블록에서 조정합니다.

- `keywords` — 검색할 키워드 목록
- `perKeyword` — 키워드당 API 조회 건수 (최대 100)
- `maxPerKeyword` — 키워드당 메일에 담을 최대 건수
- `maxTotal` — 메일 전체 최대 건수
- `withinHours` — 최근 몇 시간 내 기사만 (기본 24)
- `excludeKeywords` — 제목에 포함되면 걸러낼 단어
- `preferredPress` — 우선 노출할 매체 도메인

## 3. 테스트

발송하지 않고 HTML 만 생성 (수집 결과 확인용):

```powershell
cd D:\ClaudeWork\tech-news-clipper
.\Send-TechNewsClipping.ps1 -Preview
```

`logs\` 에 저장된 HTML 을 브라우저로 열어보세요. 실제로 한 통 보내보려면 `-Preview` 없이 실행합니다.

## 4. 스케줄 등록

```powershell
.\Register-Task.ps1 -Time "08:00"
```

즉시 한 번 실행해보기:

```powershell
Start-ScheduledTask -TaskName TechNewsClipping
```

해제:

```powershell
.\Register-Task.ps1 -Unregister
```

## 동작 참고

- **PC가 켜져 있어야 합니다.** 작업 스케줄러가 실행돼야 발송됩니다.
  기본 등록은 '로그온 여부와 무관하게 실행'(S4U)이고, 8시에 꺼져 있었다면
  `StartWhenAvailable` 로 부팅 직후 밀린 실행이 돌아갑니다.
  (Outlook 앱은 켜져 있을 필요 없습니다 — SMTP 직접 발송입니다.)
- **비밀번호 저장 방식과 스케줄 모드 주의:** `Set-SmtpPassword.ps1`(DPAPI 암호화 파일)은
  로그온 세션이 없는 S4U 실행에서 복호화가 실패할 수 있습니다. 로그오프 상태에서도
  확실히 보내려면 **환경변수 방식**을 쓰거나, `.\Register-Task.ps1 -WhenLoggedOn` 으로
  로그온 상태에서만 실행되게 등록하세요.
- 최근 24시간 내 기사가 하나도 없으면 메일을 보내지 않고 종료합니다.
- 링크와 제목 앞 20자로 중복을 제거하므로, 같은 기사가 여러 키워드에 걸려도 한 번만 나옵니다.
- 네이버 검색 API 무료 한도는 일 25,000회로, 이 용도에는 충분합니다.
- `smtp.cred` 는 현재 Windows 사용자 계정에 묶여 암호화됩니다. 다른 PC/계정으로 복사해도 열리지 않습니다.
