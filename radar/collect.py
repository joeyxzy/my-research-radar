"""Low-overhead, best-effort collection from public technical primary sources."""
from __future__ import annotations

import calendar
import datetime as dt
import html
import logging
import os
import re
from urllib.parse import urlparse, urlunparse
from email.utils import parsedate_to_datetime

import requests
try:
    import feedparser
except ImportError:  # XML fallback for minimal/offline environments
    feedparser = None
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = logging.getLogger(__name__)
TIMEOUT = 18
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ResearchRadar/0.2 (personal research; source links preserved)"})
retry = Retry(total=2, backoff_factor=0.7, status_forcelist=[429, 500, 502, 503, 504])
SESSION.mount("https://", HTTPAdapter(max_retries=retry))


def iso_utc(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc)
        except ValueError:
            try:
                return parsedate_to_datetime(value).astimezone(dt.timezone.utc)
            except (TypeError, ValueError, IndexError):
                return None
    return None


def clean(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def cutoff(hours=84):
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)


def record(title, url, summary, date, source, kind):
    title = clean(title)
    url = (url or "").strip()
    parsed = urlparse(url)
    if not title or parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    match = re.search(r"(?:arxiv\.org/(?:abs|pdf|html)/|huggingface\.co/papers/)(\d{4}\.\d{4,5})", url)
    canonical = urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))
    key = "arxiv:" + match.group(1) if match else canonical
    return {"id": key, "title": title[:320], "url": url,
            "summary": clean(summary)[:3800], "published": date.isoformat() if date else "",
            "source": source, "kind": kind}


def from_feed(name, url, since, kind="blog"):
    response = SESSION.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    if feedparser:
        parsed = feedparser.parse(response.content)
        if getattr(parsed, "bozo", False) and not parsed.entries:
            raise ValueError(f"Malformed feed: {parsed.get('bozo_exception')}")
        entries = parsed.entries
    else:
        entries = xml_fallback(response.content)
    result = []
    for entry in entries:
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        when = dt.datetime.fromtimestamp(calendar.timegm(stamp), dt.timezone.utc) if stamp else iso_utc(entry.get("date"))
        if when and when < since:
            continue
        obj = record(entry.get("title", ""), entry.get("link", ""),
                     entry.get("summary") or entry.get("description", ""), when, name, kind)
        if obj:
            result.append(obj)
    return result



def xml_fallback(data):
    """Minimal Atom/RSS parser used only when feedparser isn't present (offline tests)."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(data)
    out = []
    for element in root.iter():
        if element.tag.split("}")[-1].lower() not in ("item", "entry"):
            continue
        fields = {}
        for child in element:
            tag = child.tag.split("}")[-1].lower()
            if tag == "link" and not (child.text or "").strip():
                val = child.attrib.get("href", "")
            else:
                val = "".join(child.itertext()).strip()
            fields[tag] = val
        out.append({"title": fields.get("title", ""), "link": fields.get("link", ""),
                    "summary": fields.get("summary") or fields.get("description") or fields.get("content", ""),
                    "date": fields.get("published") or fields.get("updated") or fields.get("pubdate", "")})
    return out

def hf_papers(since):
    results = []
    date = since.date()
    until = dt.datetime.now(dt.timezone.utc).date()
    while date <= until:
        response = SESSION.get("https://huggingface.co/api/daily_papers",
                               params={"date": str(date), "limit": 100}, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Unexpected HF Daily Papers response")
        for entry in payload:
            paper = entry.get("paper") or {}
            paper_id = paper.get("id") or entry.get("paperId")
            if not paper_id:
                continue
            when = iso_utc(entry.get("publishedAt") or paper.get("publishedAt"))
            if when and when < since:
                continue
            obj = record(entry.get("title") or paper.get("title"),
                         f"https://huggingface.co/papers/{paper_id}",
                         paper.get("summary", ""), when,
                         "Hugging Face Daily Papers", "hf")
            if obj:
                results.append(obj)
        date += dt.timedelta(days=1)
    return results


def gh_get(path, params=None):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if os.environ.get("GH_API_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GH_API_TOKEN']}"
    response = SESSION.get("https://api.github.com" + path, params=params,
                           headers=headers, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def github_updates(repo, since, errors):
    out = []
    try:
        for release in gh_get(f"/repos/{repo}/releases", {"per_page": 10}):
            when = iso_utc(release.get("published_at"))
            if when and when >= since:
                obj = record(f"[{repo} release] {release.get('name') or release.get('tag_name')}",
                             release.get("html_url"), release.get("body"), when, repo, "release")
                if obj:
                    out.append(obj)
    except Exception as e:
        errors.append(f"GitHub releases {repo}: {type(e).__name__}")
        LOG.warning("GitHub releases %s: %s", repo, e)
    try:
        query = f"repo:{repo} is:pr is:merged merged:>={since.date().isoformat()}"
        payload = gh_get("/search/issues", {"q": query, "per_page": 50,
                                              "sort": "updated", "order": "desc"})
        for pr in payload.get("items", []):
            when = iso_utc(pr.get("closed_at"))
            if when and when >= since:
                obj = record(f"[{repo} PR] {pr.get('title')}", pr.get("html_url"),
                             pr.get("body"), when, repo, "pr")
                if obj:
                    out.append(obj)
    except Exception as e:
        errors.append(f"GitHub merged PRs {repo}: {type(e).__name__}")
        LOG.warning("GitHub merged PRs %s: %s", repo, e)
    return out


def collect(config):
    since = cutoff(config.get("lookback_hours", 84))
    records = []
    errors = []
    ok_sources = 0
    for cat in config.get("arxiv_categories", []):
        try:
            records += from_feed("arXiv " + cat, f"https://rss.arxiv.org/atom/{cat}", since, "arxiv")
            ok_sources += 1
        except Exception as e:
            errors.append(f"arXiv {cat}: {type(e).__name__}")
            LOG.warning("arXiv %s failed: %s", cat, e)
    if config.get("huggingface_daily_papers", True):
        try:
            records += hf_papers(since)
            ok_sources += 1
        except Exception as e:
            errors.append(f"HF Daily Papers: {type(e).__name__}")
            LOG.warning("HF Daily Papers failed: %s", e)
    for repo in config.get("github_repositories", []):
        records += github_updates(repo, since, errors)
        # GitHub failures are recorded per endpoint and are not silently counted as success.
    for feed in config.get("rss_feeds", []):
        try:
            records += from_feed(feed["name"], feed["url"], since,
                                 feed.get("kind", "blog"))
            ok_sources += 1
        except Exception as e:
            errors.append(f"{feed['name']}: {type(e).__name__}")
            LOG.warning("RSS %s failed: %s", feed["name"], e)
    if ok_sources == 0 and not records:
        raise RuntimeError("Every non-GitHub source failed; cannot produce a reliable digest")
    unique = {}
    for obj in records:
        older = unique.get(obj["id"])
        if not older or len(obj["summary"]) > len(older["summary"]):
            unique[obj["id"]] = obj
    LOG.info("Collected %d records, %d unique; %d source errors", len(records), len(unique), len(errors))
    return list(unique.values()), {"raw_count": len(records), "unique_count": len(unique),
                                    "feed_sources_ok": ok_sources, "errors": errors}


def relevance(rec, config):
    keys = config.get("keywords", {})
    title = rec["title"].lower()
    body = rec["summary"].lower()
    score = 0
    for group, weight in (("core", 6), ("adjacent", 3), ("explore", 1)):
        for term in keys.get(group, []):
            word = term.lower()
            score += weight * (2 if word in title else 1 if word in body else 0)
    if rec["kind"] in ("release", "blog", "hf"):
        score += 3
    if rec["kind"] == "discovery":
        score = max(0, score - 5)
    return score


def balanced_pool(unseen, config):
    """Reserve slots per evidence channel before filling by relevance, to avoid feed monopoly."""
    max_total = int(config.get("selection_pool_max", 120))
    fractions = {"arxiv": 0.26, "hf": 0.15, "pr": 0.18, "release": 0.09,
                 "blog": 0.22, "discovery": 0.10}
    groups = {}
    for rec in unseen:
        groups.setdefault(rec["kind"], []).append(rec)
    result = []
    used = set()
    for kind, fraction in fractions.items():
        n = max(1, int(max_total * fraction)) if max_total >= 10 else int(max_total * fraction)
        ranked = sorted(groups.get(kind, []),
                        key=lambda r: (relevance(r, config), r["published"]), reverse=True)
        for rec in ranked[:n]:
            if rec["id"] not in used and len(result) < max_total:
                result.append(rec)
                used.add(rec["id"])
    remainder = sorted((rec for rec in unseen if rec["id"] not in used),
                       key=lambda r: (relevance(r, config), r["published"]), reverse=True)
    result += remainder[:max(0, max_total - len(result))]
    return sorted(result, key=lambda r: (relevance(r, config), r["published"]), reverse=True)


def fetch_detail(rec):
    """Best-effort article extraction. Every entry is explicitly labeled as excerpt or summary."""
    if rec["kind"] in ("release", "pr", "discovery"):
        return {"text": rec["summary"][:9000], "evidence_level": "release/PR body" if rec["kind"] != "discovery" else "secondary headline only"}
    url = rec["url"]
    match = re.search(r"(?:arxiv\.org/abs/|huggingface\.co/papers/)(\d{4}\.\d{4,5})", url)
    if match:
        url = "https://arxiv.org/html/" + match.group(1)
    try:
        from bs4 import BeautifulSoup
        response = SESSION.get(url, timeout=TIMEOUT, headers={"Accept": "text/html"})
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", "").lower():
            raise ValueError("Article is not HTML")
        soup = BeautifulSoup(response.text[:2_000_000], "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.body or soup
        text = " ".join(main.stripped_strings)
        text = re.sub(r"\s+", " ", text)
        if len(text) < 200:
            raise ValueError("Insufficient article text")
        return {"text": text[:14500], "evidence_level": f"HTML text excerpt ({url})"}
    except Exception as e:
        LOG.info("Fulltext fallback for %s: %s", rec["id"], e)
        return {"text": rec["summary"][:3800], "evidence_level": "abstract/feed excerpt only"}
