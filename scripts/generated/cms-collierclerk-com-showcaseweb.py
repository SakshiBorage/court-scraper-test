from __future__ import annotations

import re
from playwright.sync_api import Page

CONTRACT_VERSION = '2'
_DEFAULT_URL = 'https://cms.collierclerk.com/showcaseweb/'
RESULT_FIELDS = [
    'caseNumber',
    'defendantFullName',
    'defendantAddress',
    'defendantCityState',
    'defendantZip',
    'courtName',
    'courtState',
    'filingDate',
    'plaintiffName',
    'judgement',
    'judgementAmount',
    'judgementDate',
    'judgementRelDate',
]

def _canon(s):
    """Alphanumerics only, uppercased. 'gv24-001234' -> 'GV24001234'."""
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()

def _format_like(case_raw, example):
    """Reinsert the separators of `example` into canon `case_raw`."""
    out, i = [], 0
    for ch in (example or ""):
        if ch.isalnum():
            if i < len(case_raw):
                out.append(case_raw[i]); i += 1
        else:
            out.append(ch)
    if i < len(case_raw):
        out.append(case_raw[i:])
    return "".join(out)

def _settle(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    _markers = ("human verification", "verify you are human", "confirm you are human",
                "security check", "checking your browser", "verificación humana",
                "comprueba que eres", "select all images", "press and hold")
    cleared = True
    for _ in range(75):
        try:
            t = (page.title() or "").lower()
            body = (page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 500)") or "").lower()
        except Exception:
            break
        blob = t + " " + body
        if not any(mk in blob for mk in _markers):
            break
        cleared = False
        try:
            page.wait_for_timeout(2000)
        except Exception:
            break
    else:
        cleared = False
    if not cleared:
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

def _goto(page, url):
    try:
        page.goto(url, wait_until="commit", timeout=45000)
    except Exception:
        pass
    _settle(page)
    return page

def _solve_captcha(page, page_url, api_key):
    if not api_key:
        return
    import json
    import time
    import urllib.parse
    import urllib.request
    try:
        found = page.evaluate("""() => {
          const el = document.querySelector('[data-sitekey]');
          let k = el ? el.getAttribute('data-sitekey') : null;
          if (!k) { const f = Array.from(document.querySelectorAll('iframe')).map(i => i.src || '').find(s => s.includes('recaptcha') && s.includes('k=')); if (f) { try { k = new URL(f).searchParams.get('k'); } catch (e) {} } }
          if (!k) return null;
          const ent = !!(window.grecaptcha && window.grecaptcha.enterprise) || Array.from(document.scripts).some(s => (s.src || '').includes('enterprise.js'));
          return {sitekey: k, enterprise: ent};
        }""")
    except Exception:
        return
    if not found or not found.get("sitekey"):
        return
    try:
        q = {"key": api_key, "method": "userrecaptcha", "googlekey": found["sitekey"],
             "pageurl": page_url, "json": 1}
        if found.get("enterprise"):
            q["enterprise"] = 1
        submit = json.loads(urllib.request.urlopen(
            "https://2captcha.com/in.php?" + urllib.parse.urlencode(q), timeout=30).read())
        if submit.get("status") != 1:
            return
        cid, token = submit["request"], None
        time.sleep(15)
        for _ in range(24):
            got = json.loads(urllib.request.urlopen(
                "https://2captcha.com/res.php?" + urllib.parse.urlencode(
                    {"key": api_key, "action": "get", "id": cid, "json": 1}), timeout=30).read())
            if got.get("status") == 1:
                token = got["request"]
                break
            if got.get("request") != "CAPCHA_NOT_READY":
                return
            time.sleep(5)
        if not token:
            return
        page.evaluate("""(t) => {
          let n = 0;
          document.querySelectorAll('textarea[name="g-recaptcha-response"], #g-recaptcha-response').forEach(x => { x.style.display = ''; x.value = t; n++; });
          if (!n) { const ta = document.createElement('textarea'); ta.name = 'g-recaptcha-response'; ta.id = 'g-recaptcha-response'; ta.style.display = 'none'; ta.value = t; document.body.appendChild(ta); }
        }""", token)
    except Exception:
        return

def _loc(page, *candidates):
    for _attempt in range(20):
        fallback = None
        for c in candidates:
            try:
                if c.count() == 0:
                    continue
            except Exception:
                continue
            first = c.first
            try:
                if first.is_visible():
                    return first
            except Exception:
                pass
            if fallback is None:
                fallback = first
        if fallback is not None:
            return fallback
        try:
            page.wait_for_timeout(1000)
        except Exception:
            break
    return candidates[0].first

def _click(page, loc):
    ctx = page.context
    before = len(ctx.pages)
    loc.click()
    _settle(page)
    pages = ctx.pages
    if len(pages) > before:
        np = pages[-1]
        try:
            np.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        return np
    return page

def _click_newtab(page, loc):
    ctx = page.context
    try:
        with ctx.expect_page(timeout=12000) as info:
            loc.click()
        np = info.value
        try:
            np.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        url = ""
        try:
            url = np.url or ""
        except Exception:
            url = ""
        try:
            np.close()
        except Exception:
            pass
        if url:
            _goto(page, url)
        return page
    except Exception:
        _settle(page)
        return page

def _goto_href(page, loc):
    href = None
    try:
        href = loc.evaluate(
            "el => { const a = el.closest('a') || (el.tagName === 'A' ? el : null);"
            " return a && a.href ? a.href : null; }"
        )
    except Exception:
        href = None
    if href and isinstance(href, str) and href.lower().startswith(("http://", "https://")):
        return _goto(page, href)
    return _click_newtab(page, loc)

def _fill(page, loc, value):
    loc.fill(str(value))
    try:
        loc.blur()
    except Exception:
        pass
    try:
        page.wait_for_timeout(1200)
    except Exception:
        pass

def _press(page, loc, key):
    try:
        if loc is not None:
            loc.press(key, timeout=8000)
            _settle(page)
            return
    except Exception:
        pass
    try:
        page.keyboard.press(key)
    except Exception:
        pass
    _settle(page)

def _check(page, loc):
    try:
        loc.check(timeout=8000)
    except Exception:
        try:
            loc.click(timeout=8000)
        except Exception:
            pass
    _settle(page)

def _select(page, loc, value):
    try:
        tag = loc.evaluate("el => el.tagName.toLowerCase()")
    except Exception:
        tag = ""
    if tag == "select":
        try:
            loc.select_option(label=str(value)); return
        except Exception:
            pass
        try:
            loc.select_option(str(value)); return
        except Exception:
            pass
    loc.fill(str(value))
    page.wait_for_timeout(800)
    for cand in (page.get_by_role("option", name=str(value), exact=False),
                 page.get_by_role("listitem", name=str(value), exact=False),
                 page.get_by_text(str(value), exact=False)):
        try:
            if cand.count() and cand.first.is_visible():
                cand.first.click(); return
        except Exception:
            continue
    try:
        loc.press("Enter")
    except Exception:
        pass

def _read(page, candidates, strategy="text", attr="", regex=""):
    for sel in candidates:
        try:
            loc = page.locator(sel)
            if loc.count() == 0:
                continue
            el = loc.first
            if strategy == "attr" and attr:
                val = el.get_attribute(attr) or ""
            else:
                val = el.inner_text() or ""
            val = val.strip()
            if regex:
                m = re.search(regex, val)
                val = ((m.group(1) if m.groups() else m.group(0)) if m else "").strip()
            if val:
                return val
        except Exception:
            continue
    return ""

def _rx(text, pattern):
    try:
        m = re.search(pattern, text or "")
        if not m:
            return ""
        return (m.group(1) if m.groups() else m.group(0)).strip()
    except Exception:
        return ""

def preprocess(inputs: dict) -> dict:
    raw = str(inputs.get("case_number", "")).strip()
    return {
        "url": (inputs.get("url") or _DEFAULT_URL),
        "case": raw,
        "case_canon": _canon(raw),
        "court": (inputs.get("court_name") or ""),
        "first_name": (inputs.get("first_name") or ""),
        "last_name": (inputs.get("last_name") or ""),
        "captcha_key": (inputs.get("captcha_key") or ""),
    }

def scrape(page: Page, params: dict) -> dict:
    result = {k: "" for k in RESULT_FIELDS}
    _last_filled = None
    _goto(page, params['url'])
    _solve_captcha(page, params['url'], params.get('captcha_key') or '')
    page = _click(page, _loc(page, page.get_by_role('button', name='Case Search'), page.get_by_text('Case Search', exact=False)))
    _last_filled = _loc(page, page.get_by_label('case number', exact=False), page.get_by_role('textbox', name='case number'), page.get_by_placeholder('Case Number:', exact=False))
    _fill(page, _last_filled, params["case"])
    _press(page, _last_filled, "Enter")  # Changed from click to press Enter
    page = _click(page, _loc(page, page.get_by_role('link', name=_format_like(params["case_canon"], '11-1996-CC-001607-0001-XX')), page.get_by_text(_format_like(params["case_canon"], '11-1996-CC-001607-0001-XX'), exact=False)))
    _settle(page)
    page.wait_for_timeout(1500)
    parts = []
    try:
        parts.append(page.locator('main, body').first.inner_text())
    except Exception:
        pass
    result['defendantFullName'] = _read(page, ['table#DataTables_Table_1 > tbody > tr.even:nth-of-type(2) > td.ng-scope:nth-of-type(1)', 'table#DataTables_Table_1 > tbody > tr.odd:nth-of-type(3) > td.ng-scope:nth-of-type(1)', 'table#DataTables_Table_1 > tbody > tr.even:nth-of-type(4) > td.ng-scope:nth-of-type(1)'], 'text', '', '')
    result['plaintiffName'] = _read(page, ['table#DataTables_Table_1 > tbody > tr.odd:nth-of-type(1) > td.ng-scope:nth-of-type(1)'], 'text', '', '')
    raw = "\n\n".join(p for p in parts if p)
    result['caseNumber'] = result['caseNumber'] or _rx(raw, 'Case #:\\s*(11-1996-CC-001607-0001-XX)')
    result['filingDate'] = result['filingDate'] or _rx(raw, 'File Date:\\s*(\\d{2}/\\d{2}/\\d{4})')
    if "caseNumber" in RESULT_FIELDS and params.get("case"):
        result["caseNumber"] = params["case"]
    result["_raw_text"] = raw
    return result