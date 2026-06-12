#!/usr/bin/env python3
"""
飞书群机器人每日推送
用法:
  python scripts/notify_feishu.py                   # 推送最新一天
  python scripts/notify_feishu.py --date 2026-05-13 # 推送指定日期
  python scripts/notify_feishu.py --dry-run          # 只打印卡片 JSON，不发送
环境变量:
  FEISHU_WEBHOOK  飞书群机器人 Webhook URL（必填，除 --dry-run 外）
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

SCRIPT_DIR       = Path(__file__).parent
DATA_DIR         = SCRIPT_DIR.parent / "data" / "papers"
SITE_URL         = "https://wsybb252237.github.io/rs-arxiv-daily/"
PAPERS_PER_CHUNK = 3       # 每个 div 放几篇（完整摘要约 900 字/篇，3 篇 ≈ 2700 < 4000 上限）
MAX_CARD_BYTES   = 25_000  # 飞书单卡片 JSON 大小上限（实测约 30KB，留 5KB 余量）
MAX_AUTHORS      = 4       # 最多显示几位作者


# ── 工具 ─────────────────────────────────────────────────────────────────────

def load_daily(target_date: str) -> dict:
    path = DATA_DIR / f"{target_date}.json"
    if not path.exists():
        raise FileNotFoundError(f"找不到 {path}")
    with open(path) as f:
        return json.load(f)


def latest_date() -> str:
    files = sorted(DATA_DIR.glob("????-??-??.json"))
    if not files:
        raise FileNotFoundError("data/papers/ 下没有日报文件")
    return files[-1].stem


def clean_code_url(raw: str) -> str:
    m = re.search(r'https?://[^\s{}\[\]\\]+', raw or "")
    return m.group() if m else ""


def fmt_authors(authors: list) -> str:
    if not authors:
        return ""
    shown = authors[:MAX_AUTHORS]
    suffix = " 等" if len(authors) > MAX_AUTHORS else ""
    return "👥 " + "、".join(shown) + suffix


def fmt_paper(idx: int, p: dict) -> str:
    """单篇论文完整格式（不截断摘要）"""
    title_zh  = (p.get("title_zh") or p["title"]).replace("[", "\\[").replace("]", "\\]")
    title_en  = p["title"].replace("[", "\\[").replace("]", "\\]")
    abs_url   = p.get("abs_url", "")
    code_url  = clean_code_url(p.get("code_url", ""))
    authors   = fmt_authors(p.get("authors", []))
    comment   = (p.get("comment") or "").strip()
    labels    = " · ".join(p.get("labels", [])[:4])
    summary   = (p.get("summary_zh") or p.get("summary", "")).strip()

    # 标题行
    line_title = f"**{idx}. [{title_zh}]({abs_url})**"
    if code_url:
        line_title += f"　[📦]({code_url})"

    lines = [line_title]

    # 英文原题
    lines.append(f"*{title_en}*")

    # 作者 + 会议/备注
    meta_parts = []
    if authors:
        meta_parts.append(authors)
    if comment:
        meta_parts.append(f"📌 {comment}")
    if meta_parts:
        lines.append("　".join(meta_parts))

    # 标签
    if labels:
        lines.append(labels)

    # 完整摘要（不截断）
    if summary:
        lines.append(summary)

    return "\n".join(lines)


# ── 卡片构建 ─────────────────────────────────────────────────────────────────

def build_summary_elements(data: dict) -> list:
    """首张卡片顶部的统计摘要"""
    papers      = data["papers"]
    total       = data["total_matched"]
    code_count  = sum(1 for p in papers if p.get("has_code"))
    topic_counter = Counter(p.get("topic") or "其他" for p in papers)
    top_topics  = " · ".join(f"{t}({n})" for t, n in topic_counter.most_common(5))

    text = (
        f"今日收录 **{total}** 篇遥感相关论文"
        + (f"，含开源代码 **{code_count}** 篇" if code_count else "")
        + f"\n主题分布：{top_topics}"
    )
    return [
        {"tag": "div", "text": {"content": text, "tag": "lark_md"}},
        {"tag": "hr"},
    ]


def make_card(header_title: str, elements: list) -> dict:
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"content": header_title, "tag": "plain_text"},
                "template": "blue",
            },
            "elements": elements,
        },
    }


def build_cards(data: dict) -> list[dict]:
    papers      = data["papers"]
    target_date = data["date"]
    total       = data["total_matched"]

    # 把论文切成每 PAPERS_PER_CHUNK 篇一组
    chunks = [papers[i:i + PAPERS_PER_CHUNK]
              for i in range(0, len(papers), PAPERS_PER_CHUNK)]

    # 逐卡片装填：超过大小上限就换新卡片
    cards        = []
    cur_elements = build_summary_elements(data)  # 第一张卡片带统计头
    card_no      = 1
    paper_cursor = 0  # 全局论文编号

    for chunk_idx, chunk in enumerate(chunks):
        chunk_text = "\n\n".join(
            fmt_paper(paper_cursor + j + 1, p)
            for j, p in enumerate(chunk)
        )
        paper_cursor += len(chunk)

        new_div = {"tag": "div", "text": {"content": chunk_text, "tag": "lark_md"}}
        is_last_chunk = chunk_idx == len(chunks) - 1

        # 预估加入这个 div 后的卡片大小
        trial_elements = cur_elements + [new_div]
        if is_last_chunk:
            trial_elements += [{"tag": "hr"}, _btn()]
        trial_card = make_card(_title(target_date, card_no, None), trial_elements)
        trial_size = len(json.dumps(trial_card, ensure_ascii=False).encode())

        if trial_size > MAX_CARD_BYTES and len(cur_elements) > 2:
            # 当前卡片已满，封存后开新卡片
            cur_elements.append({"tag": "hr"})
            cur_elements.append(_btn())
            cards.append(make_card(_title(target_date, card_no, 0), cur_elements))
            card_no  += 1
            cur_elements = [new_div]
        else:
            if cur_elements and cur_elements[-1]["tag"] != "hr" and not is_last_chunk:
                cur_elements.append({"tag": "hr"})
            cur_elements.append(new_div)

    # 收尾：最后一张卡片加按钮
    cur_elements.append({"tag": "hr"})
    cur_elements.append(_btn())
    total_cards = card_no  # 确定总张数后回填标题
    cards.append(make_card(_title(target_date, card_no, total_cards if card_no > 1 else None), cur_elements))

    # 回填之前卡片的总张数
    if len(cards) > 1:
        for i, c in enumerate(cards[:-1]):
            c["card"]["header"]["title"]["content"] = _title(target_date, i + 1, len(cards))

    return cards


def _title(date: str, n: int, total) -> str:
    suffix = f" ({n}/{total})" if total and total > 1 else ""
    return f"📡 遥感 arXiv 日报 · {date}{suffix}"


def _btn() -> dict:
    return {
        "tag": "action",
        "actions": [{
            "tag":  "button",
            "text": {"content": "查看完整日报 →", "tag": "plain_text"},
            "url":  SITE_URL,
            "type": "primary",
        }],
    }


# ── 发送 ──────────────────────────────────────────────────────────────────────

def send_one(webhook_url: str, payload: dict) -> None:
    body   = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req    = urllib.request.Request(
        webhook_url, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result.get("code", 0) != 0:
        raise RuntimeError(f"飞书返回错误: {result}")


def send_all(webhook_url: str, cards: list[dict]) -> None:
    for i, card in enumerate(cards):
        send_one(webhook_url, card)
        size_kb = len(json.dumps(card, ensure_ascii=False).encode()) / 1024
        print(f"✅ 第 {i + 1}/{len(cards)} 条消息发送成功（{size_kb:.1f} KB）")
        if i < len(cards) - 1:
            time.sleep(1)


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",    help="指定日期 YYYY-MM-DD，默认最新")
    parser.add_argument("--dry-run", action="store_true", help="只打印 JSON，不发送")
    args = parser.parse_args()

    target_date = args.date or latest_date()
    print(f"推送日期: {target_date}")

    try:
        data = load_daily(target_date)
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    if data["total_matched"] == 0:
        print("今日无匹配论文，跳过推送。")
        return 0

    cards = build_cards(data)
    sizes = [len(json.dumps(c, ensure_ascii=False).encode()) / 1024 for c in cards]
    print(f"共 {data['total_matched']} 篇论文 → {len(cards)} 条消息，"
          f"大小: {' / '.join(f'{s:.1f}KB' for s in sizes)}")

    if args.dry_run:
        for i, c in enumerate(cards):
            print(f"\n── 消息 {i + 1} ({sizes[i]:.1f} KB) ──")
            print(json.dumps(c, ensure_ascii=False, indent=2)[:2000], "...[truncated for preview]")
        return 0

    webhook = os.environ.get("FEISHU_WEBHOOK")
    if not webhook:
        print("❌ 未设置环境变量 FEISHU_WEBHOOK", file=sys.stderr)
        return 1

    try:
        send_all(webhook, cards)
    except Exception as e:
        print(f"❌ 推送失败: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
