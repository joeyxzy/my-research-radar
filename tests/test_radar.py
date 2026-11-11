import datetime as dt
import json
import os
import tempfile
from pathlib import Path

import pytest

from radar import collect
from radar import report


def test_arxiv_hf_dedup_identity():
    a = collect.record("Test", "https://arxiv.org/abs/2611.12345v2", "x", None, "arxiv", "arxiv")
    b = collect.record("Test", "https://huggingface.co/papers/2611.12345", "long", None, "hf", "hf")
    assert a["id"] == b["id"] == "arxiv:2611.12345"


def test_iso_utc_dates():
    assert collect.iso_utc("2026-11-10T10:00:00Z").tzinfo == dt.timezone.utc
    assert collect.iso_utc("not-a-date") is None


def test_balanced_pool_retains_out_of_field():
    cfg = {"selection_pool_max": 8, "keywords": {"core": ["vla"]}}
    primary = [collect.record(f"VLA {i}", f"https://example.org/{i}", "vla", None, "arxiv", "arxiv") for i in range(25)]
    cross = collect.record("New control paradigm", "https://test.org/a", "", None, "news", "blog")
    pool = collect.balanced_pool(primary + [cross], cfg)
    assert len(pool) == 8
    # A larger pool would include cross-field items even when arXiv is saturated.
    cfg["selection_pool_max"] = 40
    assert cross in collect.balanced_pool(primary + [cross], cfg)


def test_parse_json_payload_and_unsafe_html():
    assert report.parse_json_payload('```json\n{"ids": ["a"]}\n```')["ids"] == ["a"]
    rendered = report.email_html("Title", "**Hi** <script>bad()</script> [test](javascript:alert(1))")
    assert "<script>" not in rendered and 'href="javascript:' not in rendered


def test_marks_only_selected_as_seen(monkeypatch, tmp_path):
    docs = [collect.record("VLA " + str(i), f"https://example.org/{i}", "core", None, "src", "blog") for i in range(3)]
    monkeypatch.setattr(report, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(report, "DAILY", tmp_path / "archive" / "daily")
    monkeypatch.setattr(report, "WEEKLY", tmp_path / "archive" / "weekly")
    monkeypatch.setattr(report, "collect", lambda cfg: (docs, {"raw_count": 3, "unique_count": 3, "errors": [], "feed_sources_ok": 1}))
    monkeypatch.setattr(report, "choose_shortlist", lambda pool, max_count: ([pool[0]], "mock"))
    monkeypatch.setattr(report, "fetch_detail", lambda r: {"text": r["summary"], "evidence_level": "mock"})
    monkeypatch.setattr(report, "llm", lambda prompt, screening=False: "A report [source](https://example.org/0)")
    sent = []
    monkeypatch.setattr(report, "send_email", lambda *args: sent.append(args))
    report.daily()
    assert len(sent) == 1
    seen = json.loads((tmp_path / "archive" / "seen.json").read_text())
    assert set(seen) == {docs[0]["id"]}
    assert (tmp_path / "archive" / "daily" / f"{report.today().isoformat()}.json").exists()
    report.daily()
    assert len(sent) == 1  # Idempotent on the same day.


def test_mail_failure_does_not_advance_seen(monkeypatch, tmp_path):
    record = collect.record("one", "https://example.org/one", "x", None, "src", "blog")
    monkeypatch.setattr(report, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(report, "DAILY", tmp_path / "archive" / "daily")
    monkeypatch.setattr(report, "collect", lambda cfg: ([record], {"raw_count": 1, "unique_count": 1, "errors": [], "feed_sources_ok": 1}))
    monkeypatch.setattr(report, "choose_shortlist", lambda pool, max_count: (pool, "mock"))
    monkeypatch.setattr(report, "fetch_detail", lambda r: {"text": "excerpt", "evidence_level": "test"})
    monkeypatch.setattr(report, "llm", lambda prompt, screening=False: "digest")
    def fail(*args):
        raise OSError("SMTP failed")
    monkeypatch.setattr(report, "send_email", fail)
    with pytest.raises(OSError):
        report.daily()
    assert not (tmp_path / "archive" / "seen.json").exists()
    assert not (tmp_path / "archive" / "daily" / f"{report.today().isoformat()}.json").exists()


def test_beijing_schedule_and_source_coverage():
    content = (report.ROOT / ".github/workflows/research-radar.yml").read_text()
    assert "cron: '0 9 * * *'" in content
    assert "cron: '0 20 * * 0'" in content
    assert content.count("timezone: 'Asia/Shanghai'") == 2
    cfg = report.CONFIG
    assert len(cfg["arxiv_categories"]) >= 6
    assert len(cfg["github_repositories"]) >= 10
    assert len(cfg["rss_feeds"]) >= 8


def test_xml_fallback_atom_and_rss():
    atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Robot</title><link href="https://a.test/p"/><published>2026-11-10T10:00:00Z</published><summary>Test</summary></entry></feed>'''
    rows = collect.xml_fallback(atom)
    assert rows[0]["title"] == "Robot"
    assert rows[0]["link"] == "https://a.test/p"
    assert collect.iso_utc("Tue, 10 Nov 2026 10:00:00 +0000").year == 2026


def test_weekly_archives_and_trend_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(report, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(report, "DAILY", tmp_path / "archive" / "daily")
    monkeypatch.setattr(report, "WEEKLY", tmp_path / "archive" / "weekly")
    today = report.today().isoformat()
    item = {"id": "arxiv:2611.12345", "title": "Technical paper",
            "url": "https://arxiv.org/abs/2611.12345", "published": today,
            "source": "arXiv", "kind": "arxiv", "summary": "System research", "evidence_level": "abstract"}
    report.write_json(report.DAILY / f"{today}.json",
                      {"date": today, "selected": [item], "report": "A useful daily report"})
    def mock_llm(prompt, screening=False):
        if screening:
            return json.dumps({"themes": [{"slug": "robot-inference", "name": "机器人推理",
                "direction": "growing", "confidence": "medium", "claim": "新证据",
                "evidence_urls": [item["url"]], "next_question": "如何实验？"}]}, ensure_ascii=False)
        return "# Weekly [Original](https://arxiv.org/abs/2611.12345)"
    monkeypatch.setattr(report, "llm", mock_llm)
    sent = []
    monkeypatch.setattr(report, "send_email", lambda *args: sent.append(args))
    report.weekly()
    assert len(sent) == 1
    trends = report.load_json(report.ARCHIVE / "trends.json", {})
    assert trends["themes"]["robot-inference"]["history"][0]["evidence_urls"] == [item["url"]]
    report.weekly()
    assert len(sent) == 1


def test_deepseek_backend_uses_correct_key_and_modes(monkeypatch):
    """Mock API transport: no credentials or paid requests are needed."""
    import sys
    import types

    calls = []

    class MockCompletions:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                finish_reason="stop", message=types.SimpleNamespace(
                    content='{"ids": ["example"]}' if kwargs.get("response_format") else "# Verified report",
                    reasoning_content="internal reasoning must not be published"
                ))])

    class MockOpenAI:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "https://api.deepseek.com"
            assert kwargs["api_key"] == "sample-deepseek-key"
            self.chat = types.SimpleNamespace(completions=MockCompletions())

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sample-deepseek-key")
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=MockOpenAI))
    assert report.llm("select", screening=True) == '{"ids": ["example"]}'
    assert report.llm("analyze") == "# Verified report"
    assert calls[0]["model"] == "deepseek-flash"
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert calls[0]["reasoning_effort"] == "none"
    assert calls[1]["model"] == "deepseek-v4-pro"
    assert "response_format" not in calls[1]
    assert calls[1]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert calls[1]["reasoning_effort"] == "high"


def test_missing_deepseek_api_key_does_not_fall_back(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        report.llm("hello")


def test_workflow_uses_deepseek_secrets_not_openai():
    content = (report.ROOT / ".github/workflows/research-radar.yml").read_text()
    assert "secrets.DEEPSEEK_API_KEY" in content
    assert "secrets.OPENAI_API_KEY" not in content
    assert "vars.DEEPSEEK_MODEL" in content


def _set_smtp_env(monkeypatch, *, user="10235102432@stu.ecnu.edu.cn", host="smtphz.qiye.163.com", port="994", security="auto"):
    monkeypatch.setenv("SMTP_USER", user)
    monkeypatch.setenv("SMTP_PASSWORD", "fake-client-app-password")
    monkeypatch.setenv("TO_EMAIL", user)
    if host is None:
        monkeypatch.delenv("SMTP_HOST", raising=False)
    else:
        monkeypatch.setenv("SMTP_HOST", host)
    if port is None:
        monkeypatch.delenv("SMTP_PORT", raising=False)
    else:
        monkeypatch.setenv("SMTP_PORT", port)
    monkeypatch.setenv("SMTP_SECURITY", security)


def test_ecnu_student_mail_requires_verified_host_and_port(monkeypatch):
    from radar.send import smtp_settings
    _set_smtp_env(monkeypatch, host=None)
    with pytest.raises(RuntimeError, match="ECNU student SMTP_HOST"):
        smtp_settings()
    _set_smtp_env(monkeypatch, port=None)
    with pytest.raises(RuntimeError, match="ECNU student SMTP_HOST"):
        smtp_settings()


def test_smtp_994_is_implicit_ssl_and_not_starttls(monkeypatch):
    import radar.send as sender
    _set_smtp_env(monkeypatch)
    calls = []

    class FakeSSL:
        def __init__(self, host, port, timeout, context):
            assert context.verify_mode == __import__("ssl").CERT_REQUIRED
            calls.append(("ssl", host, port, timeout))
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_value, tb): return False
        def login(self, user, password): calls.append(("login", user, password))
        def send_message(self, message): calls.append(("sent", message["To"], message["Subject"]))

    monkeypatch.setattr(sender.smtplib, "SMTP_SSL", FakeSSL)
    monkeypatch.setattr(sender.smtplib, "SMTP", lambda *args, **kwargs: pytest.fail("STARTTLS used for 994"))
    sender.send_test_email()
    assert calls[0] == ("ssl", "smtphz.qiye.163.com", 994, 30)
    assert calls[-1][1] == "10235102432@stu.ecnu.edu.cn"


def test_smtp_587_uses_starttls_before_auth(monkeypatch):
    import radar.send as sender
    _set_smtp_env(monkeypatch, port="587")
    calls = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs): calls.append("connect")
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def ehlo(self): calls.append("ehlo")
        def starttls(self, context):
            assert context.verify_mode == __import__("ssl").CERT_REQUIRED
            calls.append("starttls")
        def login(self, *args): calls.append("login")
        def send_message(self, *args): calls.append("send")

    monkeypatch.setattr(sender.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(sender.smtplib, "SMTP_SSL", lambda *args, **kwargs: pytest.fail("SSL used for 587"))
    sender.send_test_email()
    assert calls == ["connect", "ehlo", "starttls", "ehlo", "login", "send"]


def test_invalid_smtp_security_and_port(monkeypatch):
    from radar.send import smtp_settings
    _set_smtp_env(monkeypatch, security="none")
    with pytest.raises(ValueError, match="SMTP_SECURITY"):
        smtp_settings()
    _set_smtp_env(monkeypatch, port="not-a-port")
    with pytest.raises(ValueError, match="SMTP_PORT"):
        smtp_settings()


def test_smtp_only_workflow_does_not_run_paid_analysis_or_commit():
    content = (report.ROOT / ".github/workflows/research-radar.yml").read_text()
    assert "options: [daily, weekly, smtp_test]" in content
    assert "python -m radar.send" in content
    assert "steps.choose.outputs.mode != 'smtp_test'" in content
    assert "SMTP_SECURITY: ${{ vars.SMTP_SECURITY }}" in content
