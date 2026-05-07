"""Weekly research digest — LLM-generated research summary.

On each Agent startup, checks whether the current ISO week already has a
digest.  If not, assembles the week's events + paper metadata, calls the
user's configured LLM, and stores the result.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from paperprism_agent import llm as llm_module
from paperprism_agent.config import Config

log = logging.getLogger("paperprism_agent.weekly_digest")

SYSTEM_PROMPT = """\
你是一个学术研究助手。你的任务是根据用户本周的研究活动数据，用中文撰写一段 200-400 字的研究周报。

周报应包含：
1. 本周研究概览：主要关注了哪些方向
2. 关键论文摘要：简要介绍本周新增或重点阅读的论文（提及论文标题和核心贡献）
3. 研究节奏观察：阅读深度和广度的变化
4. 方向洞察：是否有新的研究兴趣出现，或某些方向在持续深入

语气：专业但亲切，像一位了解你研究背景的学长在写总结。不要只是罗列数据，要有洞察和叙事。

请以 JSON 格式输出，key 为 "digest"，value 为周报正文。"""

USER_PROMPT_TEMPLATE = """\
以下是本周的研究活动数据：

## 本周活动统计
- 新入库论文：{ingested_count} 篇
- 打开阅读：{opened_count} 篇
- 深度阅读（≥30秒）：{read_sessions} 次
- 预计阅读时长：{read_minutes} 分钟

## 本周涉及的论文
{papers_section}

## 上周周报（供参考，保持叙事连贯）
{prev_digest}

请基于以上数据，撰写本周的研究周报。"""

# Per-paper detail line
PAPER_LINE = '- **{title}**（{arxiv_id}）{tags}：{abstract}'


def _current_week() -> tuple[str, str]:
    """Return (ISO week label, Monday date string) for the current week."""
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    week_label = f"{iso[0]}-W{iso[1]:02d}"
    # Monday of the current week
    from datetime import timedelta
    monday = now - timedelta(days=now.weekday())
    return week_label, monday.strftime("%Y-%m-%d")


def _fetch_week_events(conn: sqlite3.Connection, week_start: str) -> dict:
    """Aggregate events for the week starting at week_start."""
    rows = conn.execute(
        f"""
        SELECT event_type, COUNT(*) AS cnt
        FROM events
        WHERE ts >= '{week_start}'
          AND ts < date('{week_start}', '+7 days')
        GROUP BY event_type
        """
    ).fetchall()
    counts = {r[0]: r[1] for r in rows}

    # Read session durations
    dur_rows = conn.execute(
        f"""
        SELECT payload
        FROM events
        WHERE event_type = 'paper.read_session'
          AND ts >= '{week_start}'
          AND ts < date('{week_start}', '+7 days')
        """
    ).fetchall()
    total_seconds = 0
    for r in dur_rows:
        try:
            payload = json.loads(r[0]) if r[0] else {}
            total_seconds += payload.get("duration_seconds", 0)
        except (ValueError, TypeError):
            pass

    return {
        "ingested": sum(counts.get(k, 0) for k in (
            "paper.ingested.downloaded",
            "paper.ingested.uploaded",
            "paper.ingested.bulk_imported",
        )),
        "opened": counts.get("paper.opened", 0),
        "read_sessions": counts.get("paper.read_session", 0),
        "read_minutes": round(total_seconds / 60, 1),
    }


def _fetch_week_papers(conn: sqlite3.Connection, week_start: str) -> list[dict]:
    """Fetch papers that were ingested OR opened this week, with tags + abstract."""
    rows = conn.execute(
        f"""
        SELECT DISTINCT p.id, p.arxiv_id, p.title, p.abstract
        FROM papers p
        WHERE p.deleted_at IS NULL
          AND (
            p.id IN (
              SELECT CAST(subject_id AS INTEGER)
              FROM events
              WHERE event_type IN ('paper.ingested.downloaded','paper.ingested.uploaded','paper.ingested.bulk_imported','paper.opened')
                AND ts >= '{week_start}'
                AND ts < date('{week_start}', '+7 days')
            )
            OR p.ingested_at >= '{week_start}'
          )
        ORDER BY p.ingested_at DESC
        LIMIT 20
        """
    ).fetchall()

    if not rows:
        return []

    paper_ids = [r[0] for r in rows]

    # Fetch tags for these papers
    placeholders = ",".join("?" * len(paper_ids))
    tag_rows = conn.execute(
        f"""
        SELECT pt.paper_id, t.name
        FROM paper_tags pt
        JOIN tags t ON t.id = pt.tag_id
        WHERE pt.paper_id IN ({placeholders})
        ORDER BY t.name
        """,
        paper_ids,
    ).fetchall()
    tags_map: dict[int, list[str]] = {}
    for r in tag_rows:
        tags_map.setdefault(r[0], []).append(r[1])

    result = []
    for r in rows:
        tags = tags_map.get(r[0], [])
        abstract = (r[3] or "")[:200]  # truncate to keep prompt short
        result.append({
            "id": r[0],
            "arxiv_id": r[1],
            "title": r[2] or "Untitled",
            "abstract": abstract,
            "tags": tags,
        })
    return result


def _fetch_prev_digest(conn: sqlite3.Connection, before_week: str) -> str:
    """Return the content of the most recent digest before the given week."""
    row = conn.execute(
        "SELECT content FROM weekly_digests WHERE week < ? ORDER BY week DESC LIMIT 1",
        (before_week,),
    ).fetchone()
    return row[0] if row else "（这是第一份周报，没有上周记录。）"


def maybe_generate_digest(cfg: Config, conn: sqlite3.Connection) -> None:
    """Check if the current week needs a digest, and generate one if so."""
    week_label, week_start = _current_week()

    # Check if digest already exists
    exists = conn.execute(
        "SELECT 1 FROM weekly_digests WHERE week = ?", (week_label,)
    ).fetchone()
    if exists:
        log.debug("Digest for %s already exists, skipping.", week_label)
        return

    # Check if there are any events this week
    events = _fetch_week_events(conn, week_start)
    if events["ingested"] == 0 and events["opened"] == 0 and events["read_sessions"] == 0:
        log.info("No activity this week (%s), skipping digest generation.", week_label)
        return

    # Load LLM config
    try:
        llm_cfg = llm_module.LLMConfig.load(cfg.paths.llm_config_file)
        client = llm_module.LLMClient(llm_cfg)
    except (llm_module.LLMConfigError, llm_module.LLMError) as exc:
        log.warning("Cannot generate weekly digest: LLM not configured (%s)", exc)
        return

    # Assemble prompt
    papers = _fetch_week_papers(conn, week_start)
    papers_section = ""
    if papers:
        lines = []
        for p in papers:
            tag_str = f"[{', '.join(p['tags'])}]" if p["tags"] else ""
            abs_str = p["abstract"] + ("..." if len(p["abstract"]) >= 200 else "")
            lines.append(PAPER_LINE.format(
                title=p["title"],
                arxiv_id=p["arxiv_id"],
                tags=tag_str,
                abstract=abs_str,
            ))
        papers_section = "\n".join(lines)
    else:
        papers_section = "本周无新增或阅读的论文。"

    prev_digest = _fetch_prev_digest(conn, week_label)

    user_prompt = USER_PROMPT_TEMPLATE.format(
        ingested_count=events["ingested"],
        opened_count=events["opened"],
        read_sessions=events["read_sessions"],
        read_minutes=events["read_minutes"],
        papers_section=papers_section,
        prev_digest=prev_digest,
    )

    log.info("Generating weekly digest for %s …", week_label)
    try:
        raw = client.chat_json(system=SYSTEM_PROMPT, user=user_prompt)
    except (llm_module.LLMError, llm_module.LLMTransientError) as exc:
        log.error("LLM call for weekly digest failed: %s", exc)
        return

    # Parse: chat_json returns raw text; try to extract JSON, fall back to raw
    content = raw.strip()
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict) and "digest" in parsed:
            content = parsed["digest"]
        elif isinstance(parsed, dict) and "content" in parsed:
            content = parsed["content"]
    except (ValueError, TypeError):
        pass  # not JSON, use as-is (some providers ignore response_format)

    # Store
    conn.execute(
        "INSERT INTO weekly_digests (week, week_start, content) VALUES (?, ?, ?)",
        (week_label, week_start, content),
    )
    conn.commit()
    log.info("Weekly digest for %s generated (%d chars).", week_label, len(content))
