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

    for keyword in clip["keywords"]:
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
            log(f"'{keyword}' 검색 실패: {e}")
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
                "keyword": keyword, "title": title, "desc": desc,
                "link": link, "pub": pub, "domain": domain, "score": score,
            })
        log(f"'{keyword}' → {kept} 건")

    collected.sort(key=lambda a: (a["score"], a["pub"]), reverse=True)
    return collected[: cfg["clipping"]["maxTotal"]]


def build_html(cfg, articles):
    e = html.escape
    within = cfg["clipping"]["withinHours"]
    today = datetime.now(KST).strftime("%Y년 %m월 %d일")
    parts = [f"""<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f5f7;">
<div style="max-width:720px;margin:0 auto;padding:24px 16px;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;color:#1a1a1a;">
  <div style="background:#ffffff;border:1px solid #e3e5e8;border-radius:10px;overflow:hidden;">
    <div style="background:#12263f;padding:20px 24px;">
      <div style="color:#ffffff;font-size:19px;font-weight:bold;">데일리 테크 뉴스 클리핑</div>
      <div style="color:#9fb3c8;font-size:13px;margin-top:5px;">{today} &middot; 최근 {within}시간 · 총 {len(articles)}건</div>
    </div>
    <div style="padding:8px 24px 4px 24px;">"""]

    # 키워드별 그룹
    groups = {}
    for a in articles:
        groups.setdefault(a["keyword"], []).append(a)

    for kw in sorted(groups):
        items = sorted(groups[kw], key=lambda a: a["pub"], reverse=True)
        parts.append(
            f"<div style='margin:20px 0 8px 0;font-size:14px;font-weight:bold;color:#12263f;"
            f"border-left:4px solid #2f80ed;padding-left:9px;'># {e(kw)} "
            f"<span style='color:#98a2b3;font-weight:normal;'>({len(items)})</span></div>"
        )
        for a in items:
            desc = a["desc"]
            if len(desc) > 130:
                desc = desc[:130] + "…"
            when = a["pub"].strftime("%m/%d %H:%M")
            parts.append(f"""
      <div style="padding:12px 0;border-bottom:1px solid #eef0f3;">
        <a href="{e(a['link'])}" style="font-size:15px;font-weight:bold;color:#1849a9;text-decoration:none;line-height:1.45;">{e(a['title'])}</a>
        <div style="font-size:13px;color:#525c6b;margin-top:6px;line-height:1.55;">{e(desc)}</div>
        <div style="font-size:11px;color:#98a2b3;margin-top:6px;">{e(a['domain'])} &nbsp;|&nbsp; {when}</div>
      </div>""")

    parts.append(f"""
    </div>
    <div style="padding:16px 24px;background:#fafbfc;border-top:1px solid #eef0f3;font-size:11px;color:#98a2b3;">
      네이버 검색 API 기반 자동 수집 &middot; 생성 {datetime.now(KST):%Y-%m-%d %H:%M} (KST)
    </div>
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
    if not cfg["naver"]["clientId"] or cfg["naver"]["clientId"].startswith("YOUR_"):
        raise SystemExit("네이버 clientId 가 설정되지 않았습니다 (환경변수 NAVER_CLIENT_ID).")

    log(f"클리핑 시작 (키워드 {len(cfg['clipping']['keywords'])}개)")
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
