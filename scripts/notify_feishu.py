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

SCRIPT_DIR      = Path(__file__).parent
DATA_DIR        = SCRIPT_DIR.parent / "data" / "papers"
SITE_URL        = "https://wsybb252237.github.io/rs-arxiv-daily/"
PAPERS_PER_CHUNK = 15   # 每个 div 放几篇，控制单元素字数不超 4000
SUMMARY_LEN     = 55    # 中文摘要截断字数


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


def truncate(text: str, n: int) -> str:
    text = (text or "").strip()
    return text[:n] + "…" if len(text) > n else text


def clean_code_url(raw: str) -> str:
    """从可能含多余文字的 code_url 中提取第一个 http URL"""
    m = re.search(r'https?://\S+', raw or "")
    return m.group() if m else ""


def fmt_paper(idx: int, p: dict) -> str:
    """把单篇论文格式化成 lark_md 紧凑两行"""
    title    = (p.get("title_zh") or p["title"]).replace("[", "\\[").replace("]", "\\]")
    abs_url  = p.get("abs_url", "")
    code_url = clean_code_url(p.get("code_url", ""))
    labels   = " · ".join(p.get("labels", [])[:3])
    summary  = truncate(p.get("summary_zh") or p.get("summary", ""), SUMMARY_LEN)

    line1 = f"**{idx}. [{title}]({abs_url})**"
    if code_url:
        line1 += f"　[📦]({code_url})"

    line2_parts = []
    if labels:
        line2_parts.append(labels)
    if summary:
        line2_parts.append(summary)
    line2 = "　".join(line2_parts)

    return f"{line1}\n{line2}" if line2 else line1


# ── 卡片构建 ─────────────────────────────────────────────────────────────────

def build_cards(data: dict) -> list[dict]:
    """
    返回一个或多个卡片 payload 列表。
    通常所有论文压入一张卡片；如果 paper 数量极大（>90），自动拆成多张。
    """
    papers      = data["papers"]
    target_date = data["date"]
    total       = data["total_matched"]
    code_count  = sum(1 for p in papers if p.get("has_code"))

    topic_counter = Counter(p.get("topic") or "其他" for p in papers)
    top_topics    = " · ".join(f"{t}({n})" for t, n in topic_counter.most_common(5))

    # 把论文切成 chunk，每 PAPERS_PER_CHUNK 篇一个 div
    chunks = [papers[i:i + PAPERS_PER_CHUNK]
              for i in range(0, len(papers), PAPERS_PER_CHUNK)]

    # 每张卡片最多放 MAX_CHUNKS_PER_CARD 个 chunk（6 个 div = 90 篇，足够用）
    MAX_CHUNKS_PER_CARD = 6
    card_chunks = [chunks[i:i + MAX_CHUNKS_PER_CARD]
                   for i in range(0, len(chunks), MAX_CHUNKS_PER_CARD)]

    cards = []
    for card_idx, card_chunk_list in enumerate(card_chunks):
        is_first = card_idx == 0
        is_last  = card_idx == len(card_chunks) - 1
        card_no  = f" ({card_idx + 1}/{len(card_chunks)})" if len(card_chunks) > 1 else ""

        header = {
            "title": {
                "content": f"📡 遥感 arXiv 日报 · {target_date}{card_no}",
                "tag": "plain_text",
            },
            "template": "blue",
        }

        elements = []

        # 第一张卡片：显示统计摘要
        if is_first:
            summary_md = (
                f"今日收录 **{total}** 篇遥感相关论文"
                + (f"，含开源代码 **{code_count}** 篇" if code_count else "")
                + f"\n主题分布：{top_topics}"
            )
            elements.append({"tag": "div", "text": {"content": summary_md, "tag": "lark_md"}})
            elements.append({"tag": "hr"})

        # 论文 div：每个 div 包含 PAPERS_PER_CHUNK 篇
        # 计算当前卡片的起始编号
        start_idx = card_idx * MAX_CHUNKS_PER_CARD * PAPERS_PER_CHUNK
        for chunk_pos, chunk in enumerate(card_chunk_list):
            chunk_start = start_idx + chunk_pos * PAPERS_PER_CHUNK
            chunk_text  = "\n\n".join(
                fmt_paper(chunk_start + j + 1, p)
                for j, p in enumerate(chunk)
            )
            elements.append({"tag": "div", "text": {"content": chunk_text, "tag": "lark_md"}})
            if chunk_pos < len(card_chunk_list) - 1:
                elements.append({"tag": "hr"})

        # 最后一张卡片：加跳转按钮
        if is_last:
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "action",
                "actions": [{
                    "tag":  "button",
                    "text": {"content": "查看完整日报 →", "tag": "plain_text"},
                    "url":  SITE_URL,
                    "type": "primary",
                }],
            })

        cards.append({
            "msg_type": "interactive",
            "card": {"header": header, "elements": elements},
        })

    return cards


# ── 发送 ──────────────────────────────────────────────────────────────────────

def send_one(webhook_url: str, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req  = urllib.request.Request(
        webhook_url,
        data=body,
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
        print(f"✅ 第 {i + 1}/{len(cards)} 条消息发送成功")
        if i < len(cards) - 1:
            time.sleep(1)   # 避免连发触发频率限制


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

    total = data["total_matched"]
    if total == 0:
        print("今日无匹配论文，跳过推送。")
        return 0

    cards = build_cards(data)
    print(f"共 {total} 篇论文，生成 {len(cards)} 条消息")

    if args.dry_run:
        for i, c in enumerate(cards):
            print(f"\n── 消息 {i + 1} ──")
            print(json.dumps(c, ensure_ascii=False, indent=2))
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
