# GitHub Actions 로 매일 자동 발송 설정

PC 전원과 무관하게 매일 아침 8시(KST) 클라우드에서 뉴스 클리핑을 발송합니다.
GitHub 무료 요금제로 충분합니다.

---

## 준비물

- GitHub 계정
- 네이버 API `clientId` / `clientSecret`
- Gmail **앱 비밀번호** 16자리 (이미 발급받으신 것)

---

## 1. GitHub 저장소 만들기 (Private 권장)

[github.com/new](https://github.com/new) 에서:
- Repository name: 예) `tech-news-clipper`
- **Private** 선택 (공개 안 함)
- 나머지 기본값 → **Create repository**

## 2. 코드 올리기

로컬 폴더에서 아래를 순서대로 실행합니다.
(`<본인계정>` 부분만 본인 것으로 바꾸세요)

```bash
cd D:\ClaudeWork\tech-news-clipper
git init
git add .
git commit -m "Add tech news clipper"
git branch -M main
git remote add origin https://github.com/<본인계정>/tech-news-clipper.git
git push -u origin main
```

> `.gitignore` 가 `smtp.cred`, `logs/`, `preview.html` 을 제외하므로 비밀번호 파일은 올라가지 않습니다.
> `config.json` 의 네이버 키도 이미 자리표시자로 바꿔놨으니, 실제 키는 아래 Secrets 로만 들어갑니다.

## 3. Secrets 3개 등록

저장소 페이지 → **Settings** → 왼쪽 **Secrets and variables** → **Actions** → **New repository secret**

아래 3개를 각각 추가합니다.

| Name | Value |
|---|---|
| `NAVER_CLIENT_ID` | 네이버 clientId |
| `NAVER_CLIENT_SECRET` | 네이버 clientSecret |
| `SMTP_PASSWORD` | Gmail 앱 비밀번호 16자리 (공백 없이) |

## 4. 즉시 테스트 (8시까지 안 기다리고)

저장소 → **Actions** 탭 → 왼쪽 **Daily Tech News Clipping** → **Run workflow** 버튼 → **Run workflow**

- 초록 체크(✅) = 성공 → Gmail 확인
- 빨간 X(❌) = 실패 → 해당 실행 클릭 → `Run clipper` 로그에서 오류 확인

## 5. 완료

이후 매일 **오전 8시(KST)** 자동 발송됩니다.
설정은 끝났고, 별도로 켜둘 것도 없습니다.

---

## 설정 바꾸기

- **키워드/수신자/시간대 필터** → `config.json` 수정 후 다시 `git add . && git commit -m "..." && git push`
- **발송 시각** → `.github/workflows/daily-clip.yml` 의 `cron` 수정
  - cron 은 **UTC** 기준입니다. `분 시 * * *` 형식.
  - 08:00 KST = `0 23 * * *` (전날 23시 UTC)
  - 07:00 KST = `0 22 * * *`
  - 09:00 KST = `0 0 * * *`

## 알아둘 점

- GitHub 무료 스케줄은 트래픽에 따라 **정시보다 몇 분~십수 분 지연**될 수 있습니다 (아침 브리핑엔 무해).
- 저장소에 **60일간 활동(커밋)이 없으면** GitHub 이 스케줄을 자동 중지합니다.
  가끔 커밋하거나, 중지되면 Actions 탭에서 다시 활성화하세요.
- 스케줄은 **기본 브랜치(main)** 의 워크플로 파일만 동작합니다.
- Private 저장소도 Actions 무료 사용량(월 2,000분) 안에서 충분히 돌아갑니다. 이 작업은 회당 1분 미만입니다.
