"""Two-stage editorial review plus reproducible daily/weekly history."""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import bleach
from markdown_it import MarkdownIt
import yaml

from radar.collect import balanced_pool, collect, fetch_detail, relevance
from radar.send import send_email

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "archive"
DAILY = ARCHIVE / "daily"
WEEKLY = ARCHIVE / "weekly"
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
TZ = ZoneInfo(CONFIG.get("timezone", "Asia/Shanghai"))
# DeepSeek officially recommends deepseek-flash and deepseek-v4-pro (Nov 2026).
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
MODEL = os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-pro"
SCREEN_MODEL = os.getenv("DEEPSEEK_SCREEN_MODEL") or "deepseek-flash"

EDITOR_RULES = """你是具身智能、机器人控制、MLSys、GPU/分布式推理系统交叉领域的严谨科研情报编辑。
目标不是总结新闻，而是判定值得关注的技术方向：问题设置、技术机制、先前路线、证据与局限、对系统研究的启发。
中文表达，保留必要英文术语。仅可引用输入的真实原始 URL，严禁捏造论文、源码、实验数值或出处。
严格区分论文或开发者自述、实验证据、推测；如果仅有摘要/剪裁的 HTML，就承认未核验全文。
媒体转载与 Google News 仅作发现线索，不要把二手报道当成技术证据。
不要盲目称突破、SOTA 或颠覆。重要性优先于热度，也不应只重复狭窄关键词命中的研究。
每条关键结论必须提供可点击原始链接，提供主要验证依据与下一步值得查证的问题。"""


def llm(prompt, *, screening=False):
    """Analyze with DeepSeek's OpenAI-compatible Chat Completions API.

    Shortlisting/ledger updates produce JSON in fast non-thinking mode, while
    long-form digests use the stronger model's thinking mode. Reasoning text
    is *never* treated as the publishable answer.
    """
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("Missing DEEPSEEK_API_KEY in GitHub Actions Secrets")
    from openai import OpenAI  # SDK transport only; all requests go to DeepSeek.
    client = OpenAI(
        api_key=api_key, base_url=DEEPSEEK_BASE_URL,
        timeout=240, max_retries=2,
    )
    options = {
        "model": SCREEN_MODEL if screening else MODEL,
        "messages": [
            {"role": "system", "content": EDITOR_RULES},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "reasoning_effort": "none" if screening else "high",
        "extra_body": {"thinking": {"type": "disabled" if screening else "enabled"}},
        "max_tokens": 3500 if screening else 24000,
    }
    if screening:
        options["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**options)
    if not response.choices:
        raise RuntimeError("DeepSeek returned no choices")
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("DeepSeek response truncated; adjust max_tokens")
    # In thinking mode reasoning_content is separate; only final content is used.
    content = choice.message.content
    value = content.strip() if isinstance(content, str) else ""
    if not value:
        raise RuntimeError("DeepSeek returned empty final content")
    return value


def parse_json_payload(s):
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I | re.M)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", s, re.S)
        if not match:
            raise
        return json.loads(match.group())


def choose_shortlist(pool, max_count):
    """Model filters by technical significance; fallback is deterministic and documented."""
    inputs = [{"id": r["id"], "title": r["title"], "source": r["source"],
               "kind": r["kind"], "published": r["published"],
               "summary": r["summary"][:450]} for r in pool]
    prompt = f"""下面是最近84小时的新材料，进行科研编辑的*候选筛选*，只输出合法 JSON，格式：{{"ids": ["原始id", ...]}}。
最多选 {max_count} 项，力争有 4-6 篇具备清晰技术创新的论文/深入技术文章，并覆盖机器人/VLA、AI Infra/MLSys、跨领域新技术；
可以选一个未被证实但潜在影响大的弱信号。少量优质材料优于强行凑数。
从 Github PR 中优先选择对架构、算法、性能、训练/推理范式有实质改变的事项，而非普通修复。
尽量避开媒体转载，除非是唯一的发现线索。不要凭空给出不存在的 ID。
候选记录：{json.dumps(inputs, ensure_ascii=False)[:90000]}"""
    try:
        picked = parse_json_payload(llm(prompt, screening=True)).get("ids") or []
        allowed = {r["id"]: r for r in pool}
        found = []
        for key in picked:
            if isinstance(key, str) and key in allowed and key not in [r["id"] for r in found]:
                found.append(allowed[key])
            if len(found) >= max_count:
                break
        if found:
            return found, "model"
    except Exception as e:
        LOG.warning("LLM shortlist failed, fallback ranking: %s", e)
    return pool[:min(max_count, 7)], "score-fallback"


def email_html(title, md):
    raw = MarkdownIt("default", {"html": False}).enable("table").render(md)
    tags = {"h1", "h2", "h3", "h4", "p", "a", "strong", "em", "ul", "ol", "li",
            "pre", "code", "table", "thead", "tbody", "tr", "td", "th", "blockquote", "hr", "br"}
    body = bleach.clean(raw, tags=tags, attributes={"a": ["href", "title"]},
                        protocols=["http", "https", "mailto"], strip=True)
    return f'''<!doctype html><html lang="zh"><body style="margin:0;padding:24px;background:#f4f6f9;color:#17202c;font-family:Arial,-apple-system,sans-serif">
    <div style="margin:0 auto;max-width:820px;padding:28px;background:white;border-radius:12px;line-height:1.75">
    <h1 style="font-size:22px;border-bottom:1px solid #e5e9ef;padding-bottom:15px">{html.escape(title)}</h1>
    {body}</div></body></html>'''


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def today():
    return dt.datetime.now(TZ).date()


def daily(dry_run=False):
    day = today().isoformat()
    dest = DAILY / f"{day}.json"
    if dest.exists() and not dry_run:
        LOG.info("Already delivered daily report %s; skipping", day)
        return
    records, source_health = collect(CONFIG)
    seen_file = ARCHIVE / "seen.json"
    seen = load_json(seen_file, {})
    unseen = [r for r in records if r["id"] not in seen]
    pool = balanced_pool(unseen, CONFIG)
    shortlist = []
    selection_method = "empty"
    if pool:
        shortlist, selection_method = choose_shortlist(pool, int(CONFIG.get("shortlist_max", 9)))
    # Enrich only selected, not all raw items. Explicitly label excerpt / abstract coverage.
    evidence = []
    for r in shortlist[:int(CONFIG.get("fulltext_max", 8))]:
        evidence.append({**r, "initial_score": relevance(r, CONFIG), **fetch_detail(r)})
    for r in shortlist[int(CONFIG.get("fulltext_max", 8)) :]:
        evidence.append({**r, "initial_score": relevance(r, CONFIG),
                         "text": r["summary"], "evidence_level": "metadata only"})
    if not evidence:
        md = (f"# {day} 技术雷达\n\n本次检索未发现未推送的高价值新材料。"
              "这是系统筛选结果，不代表相关领域今日没有研究活动。")
    else:
        previous = sorted(DAILY.glob("*.json"), reverse=True)[:3]
        recent_titles = [r.get("selected", []) for r in (load_json(p, {}) for p in previous)]
        prompt = f"""撰写北京时间 {day} 的技术雷达（1700-2600 中文字）。材料来自分级证据，避免臆造。
结构：开头100字说明今天最重要变化；随后优先分析 4-7 个研究点，每个必须包含：
1 原始材料标题/发布日期/类型与来源 URL；2 试图解决什么旧问题；3 核心技术机制（不能只说用了Transformer/端到端）；
4 与已有路线的差别；5 *明确可信程度*，例如摘要/博客自述、论文实验或真实代码；6 对VLA推理、异步控制、机器人的任务成功率/时间、GPU推理系统的启示。
最后：跨领域弱信号、2-3条趋势假设（置信度与可反证实验）、本周值得精读的2篇。
不要杜撰不存在的基准数据，必要时写「没有查证到」。给出每条实际链接。
近期每日已推荐主题，尽量不要重复（仅为背景）：{json.dumps(recent_titles, ensure_ascii=False)[:7000]}
来源健康：{json.dumps(source_health, ensure_ascii=False)[:2000]}
本次入选材料（部分全文为截取片段，必须按 evidence_level 描述证据范围）：
{json.dumps(evidence, ensure_ascii=False)[:132000]}"""
        md = llm(prompt)
    # Operational visibility: don't call an outage 'no progress'.
    summary = (f"\n\n---\n**本次采集**：共采集 {source_health['raw_count']} 条，"
               f"去重后 {source_health['unique_count']} 条；候选池 {len(pool)} 条，"
               f"本次精选 {len(shortlist)} 条。"
               f"源异常 {len(source_health['errors'])} 处（见仓库 Actions 日志）。")
    md += summary
    title = f"Research Radar｜每日科研技术简报｜{day}"
    if dry_run:
        print(md)
        return
    send_email(title, email_html(title, md), md)
    write_json(dest, {"date": day, "report": md, "selected": evidence,
                      "selection_method": selection_method, "source_health": source_health,
                      "candidate_count": len(pool)})
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    # Crucial: only published recommendations are marked seen. Unselected candidates remain eligible.
    for r in shortlist:
        seen[r["id"]] = stamp
    threshold = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=240)
    kept = {}
    for key, value in seen.items():
        when = dt.datetime.fromisoformat(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        if when >= threshold:
            kept[key] = value
    write_json(seen_file, kept)
    LOG.info("Delivered and archived daily report: %s", dest)


def weekly(dry_run=False):
    end = today()
    start = end - dt.timedelta(days=6)
    dest = WEEKLY / f"{end.isoformat()}.json"
    if dest.exists() and not dry_run:
        LOG.info("Already delivered weekly report %s; skipping", end)
        return
    reports = []
    for i in range(7):
        day = (start + dt.timedelta(days=i)).isoformat()
        doc = load_json(DAILY / f"{day}.json", None)
        if doc:
            selected = [{"id": v.get("id"), "title": v.get("title"), "url": v.get("url"),
                         "published": v.get("published"), "source": v.get("source"),
                         "kind": v.get("kind"), "summary": v.get("summary", "")[:1400],
                         "evidence_level": v.get("evidence_level")} for v in doc.get("selected", [])]
            reports.append({"date": day, "report": doc.get("report", "")[:18000],
                            "primary_materials": selected})
    trend_ledger_path = ARCHIVE / "trends.json"
    trend_ledger = load_json(trend_ledger_path, {"themes": {}})
    previous_reports = [load_json(p, {}) for p in sorted(WEEKLY.glob("*.json"), reverse=True)[:4]]
    context = [{"period": f"{v.get('start')}—{v.get('end')}", "report": v.get("report", "")[:6500]}
               for v in previous_reports]
    if not reports:
        md = (f"# {start.isoformat()}—{end.isoformat()} 周度技术综述\n\n"
              "本周没有归档日报，因此无法基于可靠材料形成周度趋势判断。"
              "请先运行 daily 并确认邮件和仓库归档成功。")
    else:
        prompt = f"""对北京时间 {start.isoformat()} 至 {end.isoformat()} 的科研技术进展进行真正的周度趋势分析，2500-3800中文字。
不是七篇日报的拼贴，请综合同主题的多篇原始工作并相互比较。所有重要判断附输入中已存在的一手来源 URL。
请写：
A. 本周趋势总览：最值得重视的3个技术主题，每个按「支持证据-路线变化-竞争路线-为什么重要-置信度」组织；
B. 技术路线图：Direct VLA、Agent/Planner + Skills、ICL/World Model、VLA inference runtime、real-time scheduling 等如何分叉、竞争、融合；若本周无相关新证据，不强行谈及；
C. 和最近4周同主题观点比较：哪些得到增强、证据不足、需要修正；严格禁止捏造过去趋势；
D. 对VLA Systems/MLSys研究的3个可实验问题，明确自变量、系统指标、机器人任务指标、必要仿真或真机条件；
E. 精读推荐5项（原始URL+为什么+最需要核验的实验/代码）。
区分发布日与论文本身首次日期，避免把同一研究跨平台发布算成独立证据。
往期周报摘要：{json.dumps(context, ensure_ascii=False)[:25000]}
长期趋势账本：{json.dumps(trend_ledger, ensure_ascii=False)[-16000:]}
本周日报及选中原始资料：{json.dumps(reports, ensure_ascii=False)[:175000]}"""
        md = llm(prompt)
    # Maintain a machine-readable ledger with stable trend names and timestamped evidence.
    next_ledger = trend_ledger
    if reports:
        try:
            trend_request = f"""将本周报告转为机器可读趋势账本更新。只返回 JSON：
{{"themes":[{{"slug":"stable-english-kebab-case", "name":"中文主题名", "direction":"emerging|growing|mixed|weakening", "confidence":"low|medium|high", "claim":"本周支持或反证了什么，不超过120字", "evidence_urls":["输入中出现的原始URL"], "next_question":"下一步证伪问题"}}]}}。
选择最多5个有长期意义的技术主题；尽量延续账本中同主题slug，不要把同一话题重复新建。
每一项必须有一手原始证据链接，不得发明来源。
现有主题：{json.dumps(trend_ledger.get('themes', {}), ensure_ascii=False)[-13000:]}
本周可用一手链接：{json.dumps(sorted({v.get('url') for r in reports for v in r.get('primary_materials', []) if v.get('url')}), ensure_ascii=False)}
周报：{md[:24000]}"""
            payload = parse_json_payload(llm(trend_request, screening=True))
            allowed_urls = {v.get("url") for r in reports for v in r.get("primary_materials", [])}
            next_ledger = json.loads(json.dumps(trend_ledger))
            themes = next_ledger.setdefault("themes", {})
            for t in (payload.get("themes") or [])[:5]:
                if not isinstance(t, dict):
                    continue
                slug = re.sub(r"[^a-z0-9-]", "-", str(t.get("slug", "")).lower()).strip("-")[:70]
                urls = [u for u in (t.get("evidence_urls") or []) if isinstance(u, str) and u in allowed_urls]
                if not slug or not urls:
                    continue
                entry = themes.setdefault(slug, {"name": str(t.get("name") or slug)[:120], "history": []})
                entry["history"].append({"week": end.isoformat(),
                                         "direction": str(t.get("direction") or "mixed")[:30],
                                         "confidence": str(t.get("confidence") or "low")[:20],
                                         "claim": str(t.get("claim") or "")[:360],
                                         "evidence_urls": urls[:8],
                                         "next_question": str(t.get("next_question") or "")[:250]})
        except Exception as e:
            LOG.warning("Trend ledger update unavailable, weekly email still sent: %s", e)
    title = f"Research Radar｜每周技术趋势综述｜{start.isoformat()}—{end.isoformat()}"
    if dry_run:
        print(md)
        return
    send_email(title, email_html(title, md), md)
    write_json(dest, {"start": start.isoformat(), "end": end.isoformat(),
                      "report": md, "daily_reports_included": len(reports)})
    if next_ledger != trend_ledger:
        write_json(trend_ledger_path, next_ledger)
    LOG.info("Delivered and archived weekly report: %s", dest)
