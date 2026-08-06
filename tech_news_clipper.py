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
HISTORY_FILE = os.path.join(HERE, "sent_history.json")


def log(msg):
    print(f"{datetime.now(KST):%Y-%m-%d %H:%M:%S} | {msg}", flush=True)


def title_key(title):
    return re.sub(r"[^\w가-힣]", "", title)[:20]


def load_history():
    """이전에 발송한 기사 이력(링크·제목키)을 불러온다."""
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            items = (json.load(f) or {}).get("items", [])
    except Exception:
        items = []
    links = {it.get("link") for it in items if it.get("link")}
    titles = {it.get("title_key") for it in items if it.get("title_key")}
    return links, titles, items


def save_history(cfg, prev_items, sent_items):
    """발송한 기사들을 이력에 추가하고, 오래된 항목은 정리해 저장한다."""
    days = int(cfg["clipping"].get("historyDays", 7))
    today = datetime.now(KST).strftime("%Y-%m-%d")
    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    kept = [it for it in prev_items if str(it.get("sent", "")) >= cutoff]
    existing = {it.get("link") for it in kept}
    for link, tkey in sent_items:
        if link and link not in existing:
            kept.append({"link": link, "title_key": tkey, "sent": today})
            existing.add(link)
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"items": kept}, f, ensure_ascii=False, indent=0)
        log(f"발송 이력 저장: 누적 {len(kept)}건 (최근 {days}일)")
    except Exception as e:
        log(f"발송 이력 저장 실패: {e}")


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
    본문에만 스쳐 지나가는 무관한 기사(예: 갤럭시 기사 속 '스마트시티' 언급)를 걸러낸다.
    라틴/숫자 토큰(KT, AI, ICT 등)은 단어경계로 매칭해 SKT·KTX 안의 'kt' 같은 오탐을 막는다."""
    low = title.lower()
    low_nospace = re.sub(r"\s+", "", low)
    for tok in keyword.split():
        t = tok.lower()
        if re.fullmatch(r"[a-z0-9]+", t):
            if not re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low):
                return False
        else:
            if re.sub(r"\s+", "", t) not in low_nospace:
                return False
    return True


# 근접 중복 판정용: 제목 편집 표시어(내용과 무관)
_DEDUP_STOP = {"단독", "종합", "속보", "포토", "영상", "인터뷰", "오늘", "내일", "그래픽"}


def title_tokens(title):
    """제목에서 유의미한 단어 집합을 뽑는다 (2글자 이상, 표시어 제외)."""
    words = re.findall(r"[0-9A-Za-z가-힣]+", (title or "").lower())
    return frozenset(w for w in words if len(w) >= 2 and w not in _DEDUP_STOP)


def is_near_duplicate(toks, existing_tokensets, threshold):
    """제목이 달라도 단어 구성이 크게 겹치면(같은 사건) 중복으로 본다."""
    if not toks:
        return False
    for ex in existing_tokensets:
        if not ex:
            continue
        inter = len(toks & ex)
        if inter == 0:
            continue
        jac = inter / len(toks | ex)
        if jac >= threshold or (inter >= 4 and jac >= 0.4):
            return True
    return False


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


def search_news(cfg, seed_links=None, seed_titles=None):
    naver = cfg["naver"]
    clip = cfg["clipping"]
    now = datetime.now(KST)
    cutoff = now - timedelta(hours=clip["withinHours"])
    fb_hours = clip.get("fallbackHours")
    fb_cutoff = now - timedelta(hours=fb_hours) if fb_hours else None

    seen_links = set(seed_links or ())
    seen_titles = set(seed_titles or ())
    seen_tokens = []
    dedup_thr = float(clip.get("dedupSimilarity", 0.6))
    collected = []

    def fetch(keyword, cut, cat_name, cat_excludes, major_only):
        """한 키워드로 조건을 만족하는 기사를 수집. seen_* 를 갱신한다."""
        url = ("https://openapi.naver.com/v1/search/news.json?"
               + urllib.parse.urlencode({"query": keyword, "display": clip["perKeyword"],
                                         "start": 1, "sort": "date"}))
        req = urllib.request.Request(url, headers={
            "X-Naver-Client-Id": naver["clientId"],
            "X-Naver-Client-Secret": naver["clientSecret"],
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            log(f"[{cat_name}] '{keyword}' 검색 실패: {e}")
            return []

        kept = []
        for item in data.get("items", []):
            if len(kept) >= clip["maxPerKeyword"]:
                break
            title = clean_text(item.get("title"))
            desc = clean_text(item.get("description"))
            try:
                pub = parsedate_to_datetime(item.get("pubDate"))
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=KST)
            except Exception:
                continue
            if pub < cut:
                continue
            if clip.get("requireKeywordInTitle", True) and not title_matches(keyword, title):
                continue
            link = item.get("originallink") or item.get("link") or ""
            domain = get_domain(link)
            if major_only and not any(p in domain for p in clip.get("majorPress", [])):
                continue
            tkey = title_key(title)
            if link in seen_links or tkey in seen_titles:
                continue
            if any(ex and ex.lower() in title.lower() for ex in cat_excludes):
                continue
            toks = title_tokens(title)
            if is_near_duplicate(toks, seen_tokens, dedup_thr):
                continue
            seen_links.add(link)
            seen_titles.add(tkey)
            seen_tokens.append(toks)
            score = 1 if any(p in domain for p in clip["preferredPress"]) else 0
            kept.append({"category": cat_name, "keyword": keyword, "title": title, "desc": desc,
                         "link": link, "pub": pub, "domain": domain, "score": score})
        return kept

    for cat in clip["categories"]:
        cat_name = cat["name"]
        cat_excludes = list(clip["excludeKeywords"]) + list(cat.get("excludeKeywords", []))
        major_only = cat.get("majorPressOnly", clip.get("majorPressOnly"))
        cat_arts = []
        for keyword in cat["keywords"]:
            arts = fetch(keyword, cutoff, cat_name, cat_excludes, major_only)
            cat_arts += arts
            log(f"[{cat_name}] '{keyword}' → {len(arts)} 건")
        # 폴백: 카테고리가 24시간 내 0건이면 확대 시간으로 재검색
        if not cat_arts and fb_cutoff:
            log(f"[{cat_name}] 24시간 내 0건 → {fb_hours}시간으로 확대 검색")
            for keyword in cat["keywords"]:
                arts = fetch(keyword, fb_cutoff, cat_name, cat_excludes, major_only)
                if arts:
                    log(f"[{cat_name}] '{keyword}' → {len(arts)} 건 (확대)")
                cat_arts += arts
        collected += cat_arts

    # 선별: 점수·최신순 정렬 후, 카테고리별 상한을 지켜 전체 상한까지 채운다
    collected.sort(key=lambda a: (a["score"], a["pub"]), reverse=True)
    max_total = int(clip["maxTotal"])
    max_cat = int(clip.get("maxPerCategory", 0) or 0)
    selected, cat_count = [], {}
    for a in collected:
        if len(selected) >= max_total:
            break
        if max_cat and cat_count.get(a["category"], 0) >= max_cat:
            continue
        selected.append(a)
        cat_count[a["category"]] = cat_count.get(a["category"], 0) + 1
    return selected


def search_insight(cfg, articles, seed_links=None, seed_titles=None):
    """위 카테고리와 별개로, 현재 이슈가 되는 테크 주제를 따로 검색해
    표에 이미 실린 기사·이전 발송분과 중복되지 않는 기사를 골라 반환한다."""
    naver, clip = cfg["naver"], cfg["clipping"]
    ins = clip.get("insight") or {}
    keywords = ins.get("keywords") or []
    count = int(ins.get("count", 0) or 0)
    if not keywords or count <= 0:
        return []

    now = datetime.now(KST)
    cutoff = now - timedelta(hours=clip["withinHours"])
    fb_hours = clip.get("fallbackHours")
    seen_links = {a["link"] for a in articles} | set(seed_links or ())
    seen_titles = {title_key(a["title"]) for a in articles} | set(seed_titles or ())
    seen_tokens = [title_tokens(a["title"]) for a in articles]
    dedup_thr = float(clip.get("dedupSimilarity", 0.6))

    def run(cut):
        res = []
        for keyword in keywords:
            url = ("https://openapi.naver.com/v1/search/news.json?"
                   + urllib.parse.urlencode({"query": keyword, "display": clip["perKeyword"],
                                             "start": 1, "sort": "date"}))
            req = urllib.request.Request(url, headers={
                "X-Naver-Client-Id": naver["clientId"],
                "X-Naver-Client-Secret": naver["clientSecret"],
            })
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                log(f"[인사이트] '{keyword}' 검색 실패: {e}")
                continue
            for item in data.get("items", []):
                title = clean_text(item.get("title"))
                desc = clean_text(item.get("description"))
                try:
                    pub = parsedate_to_datetime(item.get("pubDate"))
                    if pub.tzinfo is None:
                        pub = pub.replace(tzinfo=KST)
                except Exception:
                    continue
                if pub < cut:
                    continue
                if clip.get("requireKeywordInTitle", True) and not title_matches(keyword, title):
                    continue
                link = item.get("originallink") or item.get("link") or ""
                domain = get_domain(link)
                if clip.get("majorPressOnly") and not any(p in domain for p in clip.get("majorPress", [])):
                    continue
                tkey = title_key(title)
                if link in seen_links or tkey in seen_titles:
                    continue
                if any(ex and ex.lower() in title.lower() for ex in clip["excludeKeywords"]):
                    continue
                toks = title_tokens(title)
                if is_near_duplicate(toks, seen_tokens, dedup_thr):
                    continue
                seen_links.add(link)
                seen_titles.add(tkey)
                seen_tokens.append(toks)
                score = 1 if any(p in domain for p in clip["preferredPress"]) else 0
                res.append({"keyword": keyword, "title": title, "desc": desc,
                            "link": link, "pub": pub, "domain": domain, "score": score})
        return res

    out = run(cutoff)
    if not out and fb_hours:
        log(f"[인사이트] 24시간 내 0건 → {fb_hours}시간으로 확대 검색")
        out = run(now - timedelta(hours=fb_hours))

    out.sort(key=lambda a: (a["score"], a["pub"]), reverse=True)
    log(f"[인사이트] {len(out)} 건 중 상위 {min(count, len(out))} 건 선정")
    return out[:count]


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

# 요일별 인사말 (0=월 … 6=일)
WEEKDAY_MSG = {
    0: "새로운 한 주가 시작됐어요. 활기차게 출발해볼까요?",
    1: "화요일입니다. 좋은 흐름 그대로 이어가요!",
    2: "벌써 한 주의 절반, 수요일이에요. 조금만 더 힘내세요!",
    3: "목요일입니다. 주말이 성큼 다가왔어요!",
    4: "금요일이에요! 한 주 잘 마무리하고 즐거운 주말 맞으세요.",
    5: "여유로운 토요일입니다. 오늘도 좋은 하루 되세요.",
    6: "일요일이에요. 편안하고 든든한 하루 보내세요.",
}

# 오늘의 한마디 (인생격언) — 날짜에 따라 하나씩 순환
QUOTES = [
    "\"시작이 반이다.\" — 아리스토텔레스",
    "\"오늘 할 수 있는 일에 집중하라.\" — 파울로 코엘료",
    "\"천 리 길도 한 걸음부터.\" — 노자",
    "\"성공은 매일 반복한 작은 노력의 합이다.\" — 로버트 콜리어",
    "\"할 수 있다고 믿으면 이미 절반은 이룬 것이다.\" — 시어도어 루스벨트",
    "\"기회는 준비된 사람에게 온다.\" — 루이 파스퇴르",
    "\"어제보다 나은 오늘이면 충분하다.\"",
    "\"느리게 가도 괜찮다. 멈추지만 않는다면.\" — 공자",
    "\"행동은 모든 성공의 기초다.\" — 파블로 피카소",
    "\"작은 기회로부터 위대한 일이 시작된다.\" — 데모스테네스",
    "\"가장 좋은 때는 바로 지금이다.\"",
    "\"긍정적인 생각이 긍정적인 하루를 만든다.\"",
]


def daily_greeting():
    now = datetime.now(KST)
    greeting = f"좋은 아침입니다! {WEEKDAY_MSG[now.weekday()]}"
    quote = QUOTES[now.timetuple().tm_yday % len(QUOTES)]
    return greeting, quote


def build_html(cfg, articles, insights=None):
    e = html.escape
    today = datetime.now(KST).strftime("%Y년 %m월 %d일")
    n = len(articles)
    insights = insights or []

    # 설정에 정의된 4개 카테고리 순서를 유지한 그룹
    cat_order = [c["name"] for c in cfg["clipping"]["categories"]]
    groups = {name: [] for name in cat_order}
    for a in articles:
        groups.setdefault(a["category"], []).append(a)
    groups = {name: groups[name] for name in cat_order if groups.get(name)}

    C_HEAD = "#c9c9c9"   # 회색 헤더
    C_SUB = "#e9e9e9"    # 카테고리 소제목
    B = "1px solid #000000"
    greeting, quote = daily_greeting()

    parts = [f"""<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  @media only screen and (max-width:600px) {{
    .wrap {{ padding:10px 6px !important; }}
    .t-head {{ font-size:15px !important; }}
    table.clip {{ font-size:11px !important; }}
    .c {{ padding:4px 5px !important; }}
    .c-press, .c-note {{ font-size:10px !important; }}
    .c-num {{ width:24px !important; }}
    .c-press {{ width:64px !important; }}
    .c-note {{ width:58px !important; }}
  }}
</style></head>
<body style="margin:0;padding:0;background:#ffffff;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#ffffff;"><tr>
<td align="center" class="wrap" style="padding:18px 12px;">
<!--[if mso]><table role="presentation" width="860" cellspacing="0" cellpadding="0"><tr><td><![endif]-->
<div class="container" style="max-width:860px;margin:0 auto;text-align:left;font-family:{F_MEDIUM};color:#111111;">

  <div style="padding:2px 2px 4px 2px;margin:0 0 12px 0;">
    <div class="t-head" style="font-family:{F_BOLD};font-size:15px;color:#12263f;">{greeting}</div>
    <div style="font-family:{F_THIN};font-size:14px;color:#555555;margin-top:6px;">오늘의 한마디 — {quote}</div>
  </div>

  <div class="t-head" style="font-size:18px;font-family:{F_BOLD};margin:6px 0 10px 0;">○ 부동산 개발 ICT 테크 뉴스 스크랩</div>

  <table class="clip" cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%;border:1.5px solid #000000;font-size:13px;">
    <tr>
      <td class="c c-num" bgcolor="{C_HEAD}" style="border:{B};padding:7px 6px;text-align:center;font-family:{F_BOLD};width:56px;">1~{n}</td>
      <td class="c" bgcolor="{C_HEAD}" colspan="3" style="border:{B};padding:7px 10px;text-align:center;font-family:{F_BOLD};">부동산 개발 ICT 테크 뉴스 · {today}</td>
    </tr>
    <tr>
      <td class="c c-num" bgcolor="{C_HEAD}" style="border:{B};padding:7px 6px;text-align:center;font-family:{F_BOLD};width:56px;">페이지</td>
      <td class="c" bgcolor="{C_HEAD}" style="border:{B};padding:7px 10px;text-align:center;font-family:{F_BOLD};">기사제목</td>
      <td class="c c-press" bgcolor="{C_HEAD}" style="border:{B};padding:7px 6px;text-align:center;font-family:{F_BOLD};width:110px;">매체명</td>
      <td class="c c-note" bgcolor="{C_HEAD}" style="border:{B};padding:7px 6px;text-align:center;font-family:{F_BOLD};width:110px;">비고</td>
    </tr>"""]

    seq = 1
    for cat_name, items in groups.items():
        items = sorted(items, key=lambda a: a["pub"], reverse=True)
        parts.append(
            f'<tr><td class="c" bgcolor="{C_SUB}" colspan="4" '
            f'style="border:{B};padding:6px 8px;font-family:{F_BOLD};">&lt;{e(cat_name)}&gt; '
            f'<span style="font-family:{F_THIN};color:#555555;">({len(items)})</span></td></tr>'
        )
        for a in items:
            when = a["pub"].strftime("%m/%d %H:%M")
            press = press_name(a["domain"])
            parts.append(f"""
      <tr>
        <td class="c c-num" style="border:{B};padding:6px;text-align:center;font-family:{F_THIN};">{seq}</td>
        <td class="c" style="border:{B};padding:6px 10px;line-height:1.5;font-family:{F_MEDIUM};">
          <a href="{e(a['link'])}" style="color:#111111;text-decoration:none;">{e(a['title'])}</a>
        </td>
        <td class="c c-press" style="border:{B};padding:6px;text-align:center;font-family:{F_MEDIUM};">{e(press)}</td>
        <td class="c c-note" style="border:{B};padding:6px;text-align:center;font-family:{F_THIN};color:#333333;">{when}</td>
      </tr>""")
            seq += 1

    parts.append("</table>")

    # ── 오늘자 테크 인사이트 (위 표와 별개로, 지금 이슈가 되는 테크 기사) ──
    if insights:
        desc_max = int((cfg["clipping"].get("insight") or {}).get("descMaxLen", 240))
        parts.append(
            f'<div class="t-head" style="font-size:18px;font-family:{F_BOLD};margin:22px 0 10px 0;">○ 오늘자 테크 인사이트</div>'
            f'<table class="clip" cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%;border:1.5px solid #000000;font-size:13px;">'
        )
        for i, a in enumerate(insights, 1):
            desc = a["desc"]
            if len(desc) > desc_max:
                desc = desc[:desc_max] + "…"
            press = press_name(a["domain"])
            when = a["pub"].strftime("%m/%d %H:%M")
            parts.append(f"""
      <tr>
        <td class="c c-num" style="border:{B};padding:9px 6px;text-align:center;vertical-align:top;width:36px;font-family:{F_BOLD};">{i}</td>
        <td class="c" style="border:{B};padding:9px 12px;line-height:1.6;">
          <a href="{e(a['link'])}" style="color:#12263f;font-family:{F_BOLD};text-decoration:none;">{e(a['title'])}</a>
          <span style="font-family:{F_THIN};color:#888888;">&nbsp;({e(press)})</span>
          <div style="font-family:{F_MEDIUM};color:#333333;margin-top:5px;">{e(desc)}</div>
        </td>
      </tr>""")
        parts.append("</table>")

    parts.append(f"""
  <div style="font-size:11px;font-family:{F_THIN};color:#999999;margin-top:14px;">
    네이버 검색 API 기반 자동 수집 · 최근 {cfg['clipping']['withinHours']}시간 · 총 {n}건 · 생성 {datetime.now(KST):%Y-%m-%d %H:%M} (KST)
  </div>
</div>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table>
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

    # 이전 발송 이력을 불러와 중복 기사를 제외한다
    hist_links, hist_titles, hist_items = load_history()
    log(f"발송 이력 {len(hist_links)}건 로드 (중복 제외용)")

    articles = search_news(cfg, hist_links, hist_titles)

    if not articles:
        log("새(중복 아닌) 기사가 없습니다. 발송하지 않고 종료.")
        return

    log(f"총 {len(articles)} 건 선별 완료")
    insights = search_insight(cfg, articles, hist_links, hist_titles)
    body = build_html(cfg, articles, insights)
    subject = f"{cfg['mail']['subjectPrefix']} {datetime.now(KST):%Y-%m-%d} ({len(articles)}건)"

    if os.environ.get("PREVIEW") == "1":
        out = os.path.join(HERE, "preview.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(body)
        log(f"미리보기 모드 — 발송 안 함. HTML 저장: {out}")
        # 로그로도 HTML 을 내보내 외부에서 회수할 수 있게 함
        print("=====PREVIEW_HTML_START=====")
        print(body)
        print("=====PREVIEW_HTML_END=====")
        return

    send_mail(cfg, subject, body)
    log(f"발송 완료 → {cfg['mail']['to']}")

    # 발송 성공 후, 이번에 보낸 기사들을 이력에 추가
    sent = [(a["link"], title_key(a["title"])) for a in (articles + insights)]
    save_history(cfg, hist_items, sent)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"오류: {e}")
        sys.exit(1)
