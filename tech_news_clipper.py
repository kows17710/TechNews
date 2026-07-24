#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 검색 API로 최신 테크 뉴스를 수집해 Gmail SMTP 로 발송한다.
GitHub Actions(Ubuntu) 에서 표준 라이브러리만으로 동작한다.

민감정보는 환경변수(=GitHub Secrets)에서 읽는다:
    NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, SMTP_PASSWORD
그 외 설정(키워드/수신자 등)은 config.json 에서 읽는다.

로컬 미리보기(발송 안 함): PREVIEW=1 환경변수로 실행.
"""
import os
import re
import sys
import json
import html
import smtplib
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime, formataddr
from email.mime.text import MIMEText

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(f"{datetime.now(KST):%Y-%m-%d %H:%M:%S} | {msg}", flush=True)


def clean_text(text):
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", "", text)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def get_domain(url):
    try:
        host = urllib.parse.urlparse(url).netloc
        return re.sub(r"^www\.", "", host)
    except Exception:
        return ""


def title_matches(keyword, title):
    """키워드의 모든 토큰이 제목 안에 실제로 존재할 때만 True.
    본문에만 스쳐 지나가는 무관한 기사(예: 갤럭시 기사 속 '스마트시티' 언급)를 걸러낸다."""
    tnorm = re.sub(r"\s+", "", title).lower()
    for tok in keyword.split():
        if re.sub(r"\s+", "", tok).lower() not in tnorm:
            return False
    return True


def load_config():
    path = os.path.join(HERE, "config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 민감정보는 환경변수 우선
    cfg["naver"]["clientId"] = os.environ.get("NAVER_CLIENT_ID") or cfg["naver"]["clientId"]
    cfg["naver"]["clientSecret"] = os.environ.get("NAVER_CLIENT_SECRET") or cfg["naver"]["clientSecret"]
    cfg["_smtpPassword"] = os.environ.get("SMTP_PASSWORD", "")

    # 수신자/발신자도 환경변수로 덮어쓸 수 있게(선택)
    cfg["mail"]["to"] = os.environ.get("MAIL_TO") or cfg["mail"]["to"]
    return cfg


def search_news(cfg):
    naver = cfg["naver"]
    clip = cfg["clipping"]
    cutoff = datetime.now(KST) - timedelta(hours=clip["withinHours"])

    seen_links, seen_titles = set(), set()
    collected = []

    for cat in clip["categories"]:
        cat_name = cat["name"]
        for keyword in cat["keywords"]:
            url = (
                "https://openapi.naver.com/v1/search/news.json?"
                + urllib.parse.urlencode(
                    {"query": keyword, "display": clip["perKeyword"], "start": 1, "sort": "date"}
                )
            )
            req = urllib.request.Request(url, headers={
                "X-Naver-Client-Id": naver["clientId"],
                "X-Naver-Client-Secret": naver["clientSecret"],
            })
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                log(f"[{cat_name}] '{keyword}' 검색 실패: {e}")
                continue

            kept = 0
            for item in data.get("items", []):
                if kept >= clip["maxPerKeyword"]:
                    break

                title = clean_text(item.get("title"))
                desc = clean_text(item.get("description"))

                try:
                    pub = parsedate_to_datetime(item.get("pubDate"))
                    if pub.tzinfo is None:
                        pub = pub.replace(tzinfo=KST)
                except Exception:
                    continue
                if pub < cutoff:
                    continue

                # 정확도: 키워드가 제목에 실제로 포함된 기사만 채택
                if clip.get("requireKeywordInTitle", True) and not title_matches(keyword, title):
                    continue

                link = item.get("originallink") or item.get("link") or ""
                title_key = re.sub(r"[^\w가-힣]", "", title)[:20]
                if link in seen_links or title_key in seen_titles:
                    continue

                if any(ex and ex in title for ex in clip["excludeKeywords"]):
                    continue

                seen_links.add(link)
                seen_titles.add(title_key)
                kept += 1

                domain = get_domain(link)
                score = 1 if any(p in domain for p in clip["preferredPress"]) else 0

                collected.append({
                    "category": cat_name, "keyword": keyword, "title": title, "desc": desc,
                    "link": link, "pub": pub, "domain": domain, "score": score,
                })
            log(f"[{cat_name}] '{keyword}' → {kept} 건")

    collected.sort(key=lambda a: (a["score"], a["pub"]), reverse=True)
    return collected[: cfg["clipping"]["maxTotal"]]


# 도메인 → 매체명(한글) 매핑. 없으면 도메인 그대로 표기.
PRESS_MAP = {
    "fnnews.com": "파이낸셜뉴스", "dnews.co.kr": "대한경제", "sedaily.com": "서울경제",
    "mk.co.kr": "매일경제", "hankyung.com": "한국경제", "asiae.co.kr": "아시아경제",
    "donga.com": "동아일보", "chosun.com": "조선일보", "joongang.co.kr": "중앙일보",
    "joins.com": "중앙일보", "etnews.com": "전자신문", "zdnet.co.kr": "ZDNet코리아",
    "edaily.co.kr": "이데일리", "mt.co.kr": "머니투데이", "yna.co.kr": "연합뉴스",
    "yonhapnews": "연합뉴스", "news1.kr": "뉴스1", "newsis.com": "뉴시스",
    "heraldcorp.com": "헤럴드경제", "khan.co.kr": "경향신문", "hani.co.kr": "한겨레",
    "seoul.co.kr": "서울신문", "kmib.co.kr": "국민일보", "hankookilbo.com": "한국일보",
    "munhwa.com": "문화일보", "thelec.kr": "디일렉", "bloter.net": "블로터",
    "ddaily.co.kr": "디지털데일리", "ajunews.com": "아주경제", "biz.chosun.com": "조선비즈",
    "newspim.com": "뉴스핌", "ekn.kr": "에너지경제", "g-enews.com": "글로벌이코노믹",
    "housingnews.co.kr": "하우징헤럴드", "r-news.co.kr": "리얼티뉴스", "ceoscoredaily.com": "CEO스코어데일리",
}


def press_name(domain):
    if not domain:
        return "-"
    for key, name in PRESS_MAP.items():
        if key in domain:
            return name
    return domain


# KT Flow 서체 (수신 PC에 설치돼 있으면 적용, 없으면 맑은 고딕으로 대체)
F_BOLD = "'KT Flow Bold','Malgun Gothic',sans-serif"
F_MEDIUM = "'KT Flow Medium','Malgun Gothic',sans-serif"
F_THIN = "'KT Flow Thin','Malgun Gothic',sans-serif"


def build_html(cfg, articles):
    e = html.escape
    today = datetime.now(KST).strftime("%Y년 %m월 %d일")
    n = len(articles)

    # 설정에 정의된 4개 카테고리 순서를 유지한 그룹
    cat_order = [c["name"] for c in cfg["clipping"]["categories"]]
    groups = {name: [] for name in cat_order}
    for a in articles:
        groups.setdefault(a["category"], []).append(a)
    groups = {name: groups[name] for name in cat_order if groups.get(name)}

    C_HEAD = "#c9c9c9"   # 회색 헤더
    C_SUB = "#e9e9e9"    # 카테고리 소제목
    B = "1px solid #000000"

    parts = [f"""<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#ffffff;">
<div style="max-width:860px;margin:0 auto;padding:20px 16px;font-family:{F_MEDIUM};color:#111111;">

  <div style="font-size:18px;font-family:{F_BOLD};margin:6px 0 10px 0;">○ 부동산 개발 ICT 테크 뉴스 스크랩</div>

  <table cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%;border:1.5px solid #000000;font-size:13px;">
    <tr>
      <td bgcolor="{C_HEAD}" style="border:{B};padding:7px 6px;text-align:center;font-family:{F_BOLD};width:56px;">1~{n}</td>
      <td bgcolor="{C_HEAD}" colspan="3" style="border:{B};padding:7px 10px;text-align:center;font-family:{F_BOLD};">부동산 개발 ICT 테크 뉴스 · {today}</td>
    </tr>
    <tr>
      <td bgcolor="{C_HEAD}" style="border:{B};padding:7px 6px;text-align:center;font-family:{F_BOLD};width:56px;">페이지</td>
      <td bgcolor="{C_HEAD}" style="border:{B};padding:7px 10px;text-align:center;font-family:{F_BOLD};">기사제목</td>
      <td bgcolor="{C_HEAD}" style="border:{B};padding:7px 6px;text-align:center;font-family:{F_BOLD};width:110px;">매체명</td>
      <td bgcolor="{C_HEAD}" style="border:{B};padding:7px 6px;text-align:center;font-family:{F_BOLD};width:110px;">비고</td>
    </tr>"""]

    seq = 1
    insight_pool = []
    for cat_name, items in groups.items():
        items = sorted(items, key=lambda a: a["pub"], reverse=True)
        parts.append(
            f'<tr><td bgcolor="{C_SUB}" colspan="4" '
            f'style="border:{B};padding:6px 8px;font-family:{F_BOLD};">&lt;{e(cat_name)}&gt; '
            f'<span style="font-family:{F_THIN};color:#555555;">({len(items)})</span></td></tr>'
        )
        for a in items:
            when = a["pub"].strftime("%m/%d %H:%M")
            press = press_name(a["domain"])
            parts.append(f"""
      <tr>
        <td style="border:{B};padding:6px;text-align:center;font-family:{F_THIN};">{seq}</td>
        <td style="border:{B};padding:6px 10px;line-height:1.5;font-family:{F_MEDIUM};">
          <a href="{e(a['link'])}" style="color:#111111;text-decoration:none;">{e(a['title'])}</a>
        </td>
        <td style="border:{B};padding:6px;text-align:center;font-family:{F_MEDIUM};">{e(press)}</td>
        <td style="border:{B};padding:6px;text-align:center;font-family:{F_THIN};color:#333333;">{when}</td>
      </tr>""")
            insight_pool.append(a)
            seq += 1

    parts.append("</table>")

    # ── 오늘자 테크 인사이트 (상위 기사 요약) ──
    k = int(cfg["clipping"].get("insightCount", 0) or 0)
    if k > 0:
        top = sorted(insight_pool, key=lambda a: (a["score"], a["pub"]), reverse=True)[:k]
        if top:
            parts.append(
                f'<div style="font-size:18px;font-family:{F_BOLD};margin:22px 0 10px 0;">○ 오늘자 테크 인사이트</div>'
                f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%;border:1.5px solid #000000;font-size:13px;">'
            )
            for i, a in enumerate(top, 1):
                desc = a["desc"]
                if len(desc) > 180:
                    desc = desc[:180] + "…"
                press = press_name(a["domain"])
                parts.append(f"""
      <tr>
        <td style="border:{B};padding:9px 6px;text-align:center;vertical-align:top;width:36px;font-family:{F_BOLD};">{i}</td>
        <td style="border:{B};padding:9px 12px;line-height:1.6;">
          <a href="{e(a['link'])}" style="color:#12263f;font-family:{F_BOLD};text-decoration:none;">{e(a['title'])}</a>
          <span style="font-family:{F_THIN};color:#888888;">&nbsp;({e(press)})</span>
          <div style="font-family:{F_MEDIUM};color:#333333;margin-top:5px;">{e(desc)}</div>
        </td>
      </tr>""")
            parts.append("</table>")

    parts.append(f"""
  <div style="font-size:11px;font-family:{F_THIN};color:#999999;margin-top:14px;">
    네이버 검색 API 기반 자동 수집 · 최근 {cfg['clipping']['withinHours']}시간 · 총 {n}건 · 생성 {datetime.now(KST):%Y-%m-%d %H:%M} (KST)<br>
    ※ '비고'는 지면정보 대신 발행시각을 표기합니다.
  </div>
</div>
</body></html>""")
    return "".join(parts)


def send_mail(cfg, subject, body_html):
    smtp = cfg["smtp"]
    password = cfg["_smtpPassword"]
    if not password:
        raise SystemExit("SMTP_PASSWORD 환경변수(Gmail 앱 비밀번호)가 비어 있습니다.")

    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((str(smtp.get("fromDisplayName", "")), smtp["from"]))
    msg["To"] = cfg["mail"]["to"]
    if cfg["mail"].get("cc"):
        msg["Cc"] = cfg["mail"]["cc"]

    recipients = [x.strip() for x in re.split(r"[;,]", cfg["mail"]["to"]) if x.strip()]
    if cfg["mail"].get("cc"):
        recipients += [x.strip() for x in re.split(r"[;,]", cfg["mail"]["cc"]) if x.strip()]

    with smtplib.SMTP(smtp["host"], int(smtp["port"]), timeout=30) as server:
        server.ehlo()
        if smtp.get("useStartTls", True):
            server.starttls()
            server.ehlo()
        server.login(smtp["username"], password)
        server.sendmail(smtp["from"], recipients, msg.as_string())


def main():
    cfg = load_config()

    # 진단: 각 Secret 이 전달됐는지(값은 노출하지 않고 길이만) 출력
    def present(v):
        return f"있음(len={len(v)})" if v else "❌ 없음/빈값"
    log("Secret 확인 → "
        f"NAVER_CLIENT_ID: {present(os.environ.get('NAVER_CLIENT_ID',''))}, "
        f"NAVER_CLIENT_SECRET: {present(os.environ.get('NAVER_CLIENT_SECRET',''))}, "
        f"SMTP_PASSWORD: {present(os.environ.get('SMTP_PASSWORD',''))}")

    cid = cfg["naver"]["clientId"]
    if not cid or cid.startswith("YOUR_") or cid == "SET_VIA_GITHUB_SECRET":
        raise SystemExit(
            "네이버 clientId 가 설정되지 않았습니다. "
            "GitHub 저장소 Settings → Secrets → Actions 에 "
            "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 을 정확한 이름으로 등록했는지 확인하세요.")

    cats = cfg["clipping"]["categories"]
    kw_total = sum(len(c["keywords"]) for c in cats)
    log(f"클리핑 시작 (카테고리 {len(cats)}개 / 키워드 {kw_total}개)")
    articles = search_news(cfg)

    if not articles:
        log(f"최근 {cfg['clipping']['withinHours']}시간 내 기사가 없습니다. 발송하지 않고 종료.")
        return

    log(f"총 {len(articles)} 건 선별 완료")
    body = build_html(cfg, articles)
    subject = f"{cfg['mail']['subjectPrefix']} {datetime.now(KST):%Y-%m-%d} ({len(articles)}건)"

    if os.environ.get("PREVIEW") == "1":
        out = os.path.join(HERE, "preview.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(body)
        log(f"미리보기 모드 — 발송 안 함. HTML 저장: {out}")
        return

    send_mail(cfg, subject, body)
    log(f"발송 완료 → {cfg['mail']['to']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"오류: {e}")
        sys.exit(1)
