#!/usr/bin/env python3
"""Fetch remote sensing large model papers from arXiv and build static archive data."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import date as dt_date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
NS = {"atom": ATOM_NS, "arxiv": ARXIV_NS}
API_URL = "https://export.arxiv.org/api/query"
CODE_HOST_RE = re.compile(
  r"https?://(?:www\.)?"
  r"(?:github\.com|gitlab\.com|bitbucket\.org|codeberg\.org|huggingface\.co)/[^\s\]\)>,;]+",
  re.IGNORECASE,
)


@dataclass(frozen=True)
class ScoredPaper:
  paper: dict[str, Any]
  score: int
  labels: list[str]


def normalize_text(value: str | None) -> str:
  return re.sub(r"\s+", " ", value or "").strip()


def clean_url(value: str) -> str:
  return value.rstrip(".,;:)]}>\"'")


def extract_code_url(text: str) -> str:
  match = CODE_HOST_RE.search(text)
  return clean_url(match.group(0)) if match else ""


def load_json(path: Path) -> dict[str, Any]:
  with path.open("r", encoding="utf-8") as handle:
    return json.load(handle)


def write_json(path: Path, payload: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2)
    handle.write("\n")


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Fetch remote sensing large model arXiv papers.")
  parser.add_argument("--config", default="config.json", help="Path to crawler config JSON.")
  parser.add_argument("--output-dir", default="data/papers", help="Directory for daily paper JSON.")
  parser.add_argument("--index-path", default="data/index.json", help="Path to generated archive index JSON.")
  parser.add_argument("--date", help="Archive date in YYYY-MM-DD. Defaults to today in --timezone.")
  parser.add_argument("--days", type=int, default=1, help="Fetch N days ending at --date.")
  parser.add_argument("--timezone", default=None, help="Timezone used for default date and metadata.")
  parser.add_argument("--max-results", type=int, default=700, help="Maximum arXiv records to inspect per day.")
  parser.add_argument("--minimum-score", type=int, default=None, help="Override config minimum_score.")
  parser.add_argument("--no-date-filter", action="store_true", help="Fetch latest category papers without submittedDate.")
  parser.add_argument("--rebuild-index", action="store_true", help="Only rebuild data/index.json from local daily files.")
  return parser.parse_args()


def parse_target_date(value: str | None, tz_name: str) -> dt_date:
  if value:
    return dt_date.fromisoformat(value)
  return datetime.now(ZoneInfo(tz_name)).date()


def build_query(config: dict[str, Any], target_date: dt_date, use_date_filter: bool) -> str:
  categories = config.get("categories", [])
  category_query = " OR ".join(f"cat:{category}" for category in categories)
  if not category_query:
    category_query = "cat:cs.RO OR cat:cs.AI OR cat:cs.CV"

  if not use_date_filter:
    return f"({category_query})"

  stamp = target_date.strftime("%Y%m%d")
  return f"({category_query}) AND submittedDate:[{stamp}0000 TO {stamp}2359]"


def build_category_query(category: str, target_date: dt_date, use_date_filter: bool) -> str:
  if not use_date_filter:
    return f"cat:{category}"
  stamp = target_date.strftime("%Y%m%d")
  return f"cat:{category} AND submittedDate:[{stamp}0000 TO {stamp}2359]"


def make_api_url(query: str, max_results: int) -> str:
  params = {
    "search_query": query,
    "start": 0,
    "max_results": max_results,
    "sortBy": "submittedDate",
    "sortOrder": "descending",
  }
  return f"{API_URL}?{urllib.parse.urlencode(params)}"


def fetch_feed(query: str, max_results: int, attempts: int = 6) -> str:
  url = make_api_url(query, max_results)
  request = urllib.request.Request(
    url,
    headers={
      "User-Agent": "remote-sensing-arxiv-daily/0.1 (+https://github.com/)",
      "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    },
  )
  last_error: Exception | None = None
  for attempt in range(1, attempts + 1):
    try:
      with urllib.request.urlopen(request, timeout=75) as response:
        return response.read().decode("utf-8")
    except Exception as error:
      last_error = error
      if attempt == attempts:
        break
      time.sleep(8 * attempt)
  raise RuntimeError(f"Failed to fetch arXiv feed after {attempts} attempts: {last_error}") from last_error


def child_text(entry: ET.Element, path: str) -> str:
  node = entry.find(path, NS)
  return normalize_text(node.text if node is not None else "")


def parse_entry(entry: ET.Element) -> dict[str, Any]:
  abs_url = child_text(entry, "atom:id")
  arxiv_id = abs_url.rstrip("/").split("/")[-1] if abs_url else ""

  authors = [
    normalize_text(author.findtext("atom:name", default="", namespaces=NS))
    for author in entry.findall("atom:author", NS)
  ]
  authors = [author for author in authors if author]

  primary_node = entry.find("arxiv:primary_category", NS)
  primary_category = primary_node.attrib.get("term", "") if primary_node is not None else ""
  categories = [node.attrib.get("term", "") for node in entry.findall("atom:category", NS)]
  categories = [category for category in categories if category]

  pdf_url = ""
  for link in entry.findall("atom:link", NS):
    if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
      pdf_url = link.attrib.get("href", "")
      break

  title = normalize_text(child_text(entry, "atom:title"))
  summary = normalize_text(child_text(entry, "atom:summary"))
  comment = child_text(entry, "arxiv:comment")
  code_url = extract_code_url(" ".join([summary, comment]))

  return {
    "title": title,
    "authors": authors,
    "summary": summary,
    "published": child_text(entry, "atom:published"),
    "updated": child_text(entry, "atom:updated"),
    "arxiv_id": arxiv_id,
    "abs_url": abs_url,
    "pdf_url": pdf_url,
    "primary_category": primary_category,
    "categories": categories,
    "comment": comment,
    "code_url": code_url,
    "has_code": bool(code_url),
  }


def parse_feed(xml_text: str) -> list[dict[str, Any]]:
  root = ET.fromstring(xml_text)
  return [parse_entry(entry) for entry in root.findall("atom:entry", NS)]


def term_hits(text: str, terms: list[str]) -> list[str]:
  hits = []
  for term in terms:
    normalized = term.lower()
    if not normalized:
      continue
    if len(normalized) <= 3 and re.fullmatch(r"[a-z0-9]+", normalized):
      matched = re.search(rf"\b{re.escape(normalized)}\b", text) is not None
    else:
      matched = normalized in text
    if matched:
      hits.append(term)
  return hits


def label_definitions(config: dict[str, Any]) -> list[dict[str, Any]]:
  return config.get("labels") or config.get("topics", [])


def matching_config(config: dict[str, Any]) -> dict[str, Any]:
  return config.get("matching", {})


def configured_minimum_score(config: dict[str, Any]) -> int:
  matching = matching_config(config)
  return int(matching.get("minimum_score", config.get("minimum_score", 3)))


def strong_anchor_terms(config: dict[str, Any]) -> list[str]:
  matching = matching_config(config)
  return matching.get("strong_anchor_terms") or config.get("anchor_terms", [])


def weak_anchor_rules(config: dict[str, Any]) -> list[dict[str, Any]]:
  return matching_config(config).get("weak_anchor_rules", [])


def anchor_hits(text: str, config: dict[str, Any]) -> list[str]:
  hits = term_hits(text, strong_anchor_terms(config))
  for rule in weak_anchor_rules(config):
    term = rule.get("term", "")
    contexts = rule.get("requires_any", [])
    if term_hits(text, [term]) and term_hits(text, contexts):
      hits.append(term)
  return sorted(set(hits), key=str.lower)


def label_order(config: dict[str, Any]) -> list[str]:
  return ["开源"] + [label.get("name", "") for label in label_definitions(config)]


def sort_labels(labels: list[str], config: dict[str, Any]) -> list[str]:
  order = {name: index for index, name in enumerate(label_order(config))}
  unique = list(dict.fromkeys(label for label in labels if label))
  return sorted(unique, key=lambda label: (order.get(label, len(order)), label))


def raw_paper_text(paper: dict[str, Any]) -> str:
  return " ".join(
    [
      paper.get("title", ""),
      paper.get("summary", ""),
      paper.get("comment", ""),
      " ".join(paper.get("categories", [])),
    ]
  )


def paper_text(paper: dict[str, Any]) -> str:
  return raw_paper_text(paper).lower()


def classify_labels(text: str, config: dict[str, Any], has_code: bool) -> list[str]:
  labels = ["开源"] if has_code else []
  matched_group: set[str] = set()

  for label in label_definitions(config):
    name = label.get("name", "")
    if not name:
      continue
    if term_hits(text, label.get("terms", [])):
      labels.append(name)
      group_label = label.get("group", "")
      if group_label:
        matched_group.add(group_label)

  domain_signal = any(kw in text for kw in config.get("domain_boost_keywords", []))
  has_catch_all = any(label.get("name") == "其他" for label in label_definitions(config))
  if domain_signal and "image_type" not in matched_group and has_catch_all:
    labels.append("其他")

  return sort_labels(labels, config)


def primary_topic(labels: list[str]) -> str:
  for label in labels:
    if label != "开源":
      return label
  return "其他"


def enrich_labels(paper: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
  enriched = dict(paper)
  text = paper_text(enriched)
  code_url = clean_url(enriched.get("code_url") or extract_code_url(raw_paper_text(enriched)))
  enriched["code_url"] = code_url
  enriched["has_code"] = bool(code_url)
  labels = classify_labels(text, config, enriched["has_code"])
  enriched["labels"] = labels
  enriched["topic"] = primary_topic(labels)
  return enriched


def score_paper(paper: dict[str, Any], config: dict[str, Any], minimum_score: int) -> ScoredPaper | None:
  title = paper.get("title", "")
  summary = paper.get("summary", "")
  text = paper_text(paper)
  title_text = title.lower()

  exclude_hits = term_hits(text, config.get("exclude_terms", []))
  matched_anchors = anchor_hits(text, config)
  strong_hits = term_hits(text, config.get("strong_terms", []))
  keyword_hits = term_hits(text, config.get("keywords", []))

  if not matched_anchors:
    return None

  score = 0
  score += len(strong_hits) * 3
  score += len(keyword_hits)
  score += sum(2 for term in strong_hits if term.lower() in title_text)
  score += sum(1 for term in keyword_hits if term.lower() in title_text)

  category_boost = config.get("category_boost", {})
  for cat in [paper.get("primary_category")] + paper.get("categories", []):
    if cat in category_boost:
      score += category_boost[cat]
      break
  for kw in config.get("domain_boost_keywords", []):
    if kw in text:
      score += 1
  if exclude_hits:
    return None
  if score < minimum_score:
    return None

  enriched = enrich_labels(paper, config)
  enriched["score"] = score
  enriched["anchor_terms"] = matched_anchors
  enriched["matched_terms"] = sorted(set(matched_anchors + strong_hits + keyword_hits), key=str.lower)[:12]
  return ScoredPaper(enriched, score, enriched["labels"])


def paper_filter_labels(paper: dict[str, Any]) -> list[str]:
  labels = list(paper.get("labels") or [])
  if not labels:
    if paper.get("has_code") or paper.get("code_url"):
      labels.append("开源")
    labels.append(paper.get("topic", "其他"))
  return list(dict.fromkeys(labels))


def translate_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
  """Translate title and summary to Chinese via Google Translate (deep-translator).

  Adds title_zh and summary_zh fields. Falls back to original text on any error.
  Skips papers that already have title_zh (e.g. loaded from an existing JSON).
  """
  to_translate = [p for p in papers if not p.get("title_zh")]
  if not to_translate:
    return papers

  try:
    from deep_translator import GoogleTranslator  # noqa: PLC0415
  except ImportError:
    print("deep-translator not installed — skipping translation.", file=sys.stderr)
    for paper in to_translate:
      paper.setdefault("title_zh", paper.get("title", ""))
      paper.setdefault("summary_zh", paper.get("summary", ""))
    return papers

  translator = GoogleTranslator(source="en", target="zh-CN")

  def safe_translate_batch(texts: list[str]) -> list[str]:
    results: list[str] = []
    for text in texts:
      try:
        result = translator.translate(text)
        results.append(result or text)
      except Exception as exc:
        print(f"Translation error: {exc}", file=sys.stderr)
        results.append(text)
      time.sleep(0.5)
    return results

  batch_size = 20
  for i in range(0, len(to_translate), batch_size):
    batch = to_translate[i : i + batch_size]
    titles = [p.get("title") or "" for p in batch]
    summaries = [p.get("summary") or "" for p in batch]

    translated_titles = safe_translate_batch(titles)
    translated_summaries = safe_translate_batch(summaries)

    for j, paper in enumerate(batch):
      paper["title_zh"] = translated_titles[j]
      paper["summary_zh"] = translated_summaries[j]

    print(f"  翻译进度 {min(i + batch_size, len(to_translate))}/{len(to_translate)}")
    if i + batch_size < len(to_translate):
      time.sleep(2)

  return papers


def dedupe_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
  seen = set()
  unique = []
  for paper in papers:
    key = paper.get("arxiv_id") or paper.get("abs_url") or paper.get("title")
    if key in seen:
      continue
    seen.add(key)
    unique.append(paper)
  return unique


def fetch_daily(
  target_date: dt_date,
  config: dict[str, Any],
  output_dir: Path,
  max_results: int,
  minimum_score: int,
  use_date_filter: bool,
  tz_name: str,
) -> dict[str, Any]:
  query = build_query(config, target_date, use_date_filter)
  try:
    xml_text = fetch_feed(query, max_results)
    raw_papers = parse_feed(xml_text)
  except Exception as error:
    if not use_date_filter:
      raise
    print(f"{target_date.isoformat()}: combined query failed, falling back to per-category queries: {error}", file=sys.stderr)
    raw_papers = []
    for category in config.get("categories", []):
      category_query = build_category_query(category, target_date, use_date_filter)
      try:
        raw_papers.extend(parse_feed(fetch_feed(category_query, max_results)))
      except Exception as category_error:
        print(
          f"{target_date.isoformat()}: category {category} failed and was skipped: {category_error}",
          file=sys.stderr,
        )
      time.sleep(3)
  matched = []
  for paper in raw_papers:
    scored = score_paper(paper, config, minimum_score)
    if scored is not None:
      matched.append(scored.paper)

  matched = dedupe_papers(matched)
  matched.sort(
    key=lambda item: (bool(item.get("has_code")), item.get("published", ""), item.get("score", 0)),
    reverse=True,
  )

  if matched:
    print(f"{target_date.isoformat()}: translating {len(matched)} papers...")
    matched = translate_papers(matched)

  payload = {
    "date": target_date.isoformat(),
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "timezone": tz_name,
    "source": "arXiv",
    "source_url": "https://arxiv.org/",
    "query": query,
    "total_fetched": len(raw_papers),
    "total_matched": len(matched),
    "papers": matched,
  }
  write_json(output_dir / f"{target_date.isoformat()}.json", payload)
  return payload


def read_daily_files(output_dir: Path) -> list[dict[str, Any]]:
  if not output_dir.exists():
    return []

  daily_files = []
  for path in sorted(output_dir.glob("*.json"), reverse=True):
    try:
      payload = load_json(path)
    except json.JSONDecodeError as error:
      print(f"Skipping invalid JSON {path}: {error}", file=sys.stderr)
      continue
    if "date" in payload and "papers" in payload:
      daily_files.append(payload)
  return daily_files


def build_index(config: dict[str, Any], output_dir: Path, index_path: Path, tz_name: str) -> dict[str, Any]:
  daily_files = read_daily_files(output_dir)
  daily_files.sort(key=lambda item: item.get("date", ""), reverse=True)

  label_counter: Counter[str] = Counter()
  total_papers = 0
  dates = []

  for daily in daily_files:
    papers = [enrich_labels(paper, config) for paper in daily.get("papers", [])]
    daily["papers"] = sorted(
      papers,
      key=lambda item: (bool(item.get("has_code")), item.get("published", ""), item.get("score", 0)),
      reverse=True,
    )
    if daily.get("date"):
      write_json(output_dir / f"{daily.get('date')}.json", daily)
    total_papers += len(papers)
    label_counts: Counter[str] = Counter()
    for paper in papers:
      label_counts.update(paper_filter_labels(paper))
    label_counter.update(label_counts)
    dates.append(
      {
        "date": daily.get("date"),
        "count": len(papers),
        "path": f"{output_dir.as_posix()}/{daily.get('date')}.json",
        "labels": dict(sorted(label_counts.items())),
        "topics": dict(sorted(label_counts.items())),
      }
    )

  labels = [
    {"name": name, "count": count}
    for name, count in sorted(
      label_counter.items(),
      key=lambda item: (
        label_order(config).index(item[0]) if item[0] in label_order(config) else len(label_order(config)),
        -item[1],
        item[0],
      ),
    )
  ]

  index = {
    "site": config.get("site", {}),
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "timezone": tz_name,
    "latest_date": dates[0]["date"] if dates else None,
    "total_papers": total_papers,
    "label_order": label_order(config),
    "labels": labels,
    "topics": labels,
    "dates": dates,
  }
  write_json(index_path, index)
  return index


def main() -> int:
  args = parse_args()
  config_path = Path(args.config)
  config = load_json(config_path)
  tz_name = args.timezone or config.get("site", {}).get("timezone", "Asia/Shanghai")
  output_dir = Path(args.output_dir)
  index_path = Path(args.index_path)
  minimum_score = args.minimum_score if args.minimum_score is not None else configured_minimum_score(config)

  if args.rebuild_index:
    index = build_index(config, output_dir, index_path, tz_name)
    print(f"Rebuilt {index_path} with {index['total_papers']} papers across {len(index['dates'])} dates.")
    return 0

  if args.days < 1:
    raise ValueError("--days must be >= 1")

  end_date = parse_target_date(args.date, tz_name)
  start_date = end_date - timedelta(days=args.days - 1)
  current_date = start_date
  while current_date <= end_date:
    payload = fetch_daily(
      current_date,
      config,
      output_dir,
      args.max_results,
      minimum_score,
      not args.no_date_filter,
      tz_name,
    )
    print(
      f"{current_date.isoformat()}: fetched {payload['total_fetched']} records, "
      f"matched {payload['total_matched']} remote-sensing papers."
    )
    current_date += timedelta(days=1)
    if current_date <= end_date:
      time.sleep(3)

  index = build_index(config, output_dir, index_path, tz_name)
  print(f"Updated {index_path} with {index['total_papers']} papers across {len(index['dates'])} dates.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
