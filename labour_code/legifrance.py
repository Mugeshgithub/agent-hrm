# labour_code/legifrance.py: the Code du travail from the official Légifrance API (PISTE), cleaned into articles.
# One call (consult/legiPart) returns every article in force on a date WITH its text: about 33 MB in a few seconds,
# so a load is one request, not one per article. The data is under the Licence Ouverte 2.0: credit Légifrance and the date.
# Only the standard library at the top: CI's unit-test job (no packages) tests the cleaning below.
import html
import os
import re
from collections import Counter
from datetime import date, datetime, timezone

CODE_DU_TRAVAIL = "LEGITEXT000006072050"
HOSTS = {   # PISTE environment -> (token URL, API base). Each has its own keys; a sandbox token is refused in production
    "sandbox": ("https://sandbox-oauth.piste.gouv.fr/api/oauth/token",
                "https://sandbox-api.piste.gouv.fr/dila/legifrance/lf-engine-app/"),
    "production": ("https://oauth.piste.gouv.fr/api/oauth/token",
                   "https://api.piste.gouv.fr/dila/legifrance/lf-engine-app/"),
}
BOILERPLATE_MIN = 5        # the same text in this many articles or more is reported (kept: each is still a real article)
RETRY_WAIT = 30            # seconds before the 2nd try, twice that before the 3rd
BLOCK_TAGS = r"p|br|div|tr|td|th|li|table|tbody|thead|center|h\d"


def fetch_code(day, env=None, tries=3):
    # the whole code as in force on `day`; keys come from .env and are never printed
    import time
    import httpx
    env = env or os.environ.get("PISTE_ENV", "sandbox")
    token_url, api = HOSTS[env]
    for attempt in range(1, tries + 1):
        try:
            token = httpx.post(token_url, timeout=30, data={
                "grant_type": "client_credentials", "scope": "openid",
                "client_id": os.environ["PISTE_CLIENT_ID"], "client_secret": os.environ["PISTE_CLIENT_SECRET"],
            })
            token.raise_for_status()
            at_noon = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
            resp = httpx.post(api + "consult/legiPart", timeout=300,
                              json={"textId": CODE_DU_TRAVAIL, "date": int(at_noon.timestamp() * 1000)},
                              headers={"Authorization": f"Bearer {token.json()['access_token']}", "accept": "application/json"})
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.TransportError):
            if attempt == tries:                  # over quota (429), down or slow: the caller keeps the data it has
                raise
            time.sleep(RETRY_WAIT * attempt)


def to_text(content):
    # Légifrance sends HTML. Our search text is one plain line, like the copy we compare with: a paragraph, line or
    # table cell becomes a space; a link or an exponent (m<sup>2</sup>) is unwrapped in place
    text = re.sub(rf"</?(?:{BLOCK_TAGS})\b[^>]*>", " ", content or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"Ancre(?=[LRD]\.\s?\d)|(?<=\d)Ancre\b", "", html.unescape(text))   # a link label left in the source text (D1221-23)
    return re.sub(r"\s+", " ", text).strip()


def to_day(value):
    # the API gives dates as milliseconds since 1970, as a number or as text
    if value in (None, ""):
        return None
    if str(value).lstrip("-").isdigit():
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date()
    return date.fromisoformat(str(value)[:10])


def articles_from(code, today):
    """The articles in force `today`, cleaned, in the shape of articles.jsonl (so chunk.py cuts them the same way),
    and a report of everything that was left out or merged."""
    report = Counter()
    entries = []

    def walk(node, titles):
        for a in node.get("articles") or []:
            entries.append((a, titles))
        for s in node.get("sections") or []:
            walk(s, titles + [s.get("title") or ""])
    walk(code, [])
    report["entries"] = len(entries)

    by_num, seen_ids, annexes = {}, set(), []
    for a, titles in entries:
        num = (a.get("num") or "").strip()
        start, end = to_day(a.get("dateDebut")), to_day(a.get("dateFin"))
        if a.get("id") in seen_ids:
            report["same entry twice"] += 1               # listed in two places: one copy is enough
            continue
        seen_ids.add(a.get("id"))
        if a.get("etat") not in ("VIGUEUR", "ABROGE_DIFF") or (start and start > today) or (end and end <= today):
            report["not in force today"] += 1             # the code decides what applies, never the model
            continue
        if titles and "ancienne" in titles[0].lower():
            report["old pre-2008 code"] += 1              # "Partie législative ancienne": old numbering, left out like our current copy
            continue
        if not num:
            report["no article number"] += 1
            continue
        if not re.match(r"[LRDA]\*?\d", num):
            annexes.append(num)                           # "Annexe I à l'article R4312-1": tables, not a citable article
            continue
        text = to_text(a.get("content") or a.get("texte"))
        if not text:
            report["empty text"] += 1
            continue
        article = {"id": a.get("id"), "cid": a.get("cid"), "num": num, "etat": a.get("etat"),
                   "dateDebut": start.isoformat() if start else None, "dateFin": end.isoformat() if end else None,
                   "texte": text, "fullSectionsTitre": " > ".join(titles)}
        kept = by_num.get(num)
        if kept is None:
            by_num[num] = article
        else:
            report["two versions, latest kept"] += 1      # same number, two ids: the one that started last applies today
            if (article["dateDebut"] or "") > (kept["dateDebut"] or ""):
                by_num[num] = article
    report["annexes left out"] = len(annexes)
    articles = list(by_num.values())
    report["kept"] = len(articles)

    repeats = Counter(a["texte"] for a in articles)
    boilerplate = [(text, n) for text, n in repeats.most_common() if n >= BOILERPLATE_MIN]
    report["articles with a repeated text"] = sum(n for _, n in boilerplate)
    return articles, report, boilerplate, annexes
