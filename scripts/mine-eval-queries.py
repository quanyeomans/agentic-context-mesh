#!/usr/bin/env python3
"""
mine-eval-queries.py — Phase 5A: Mine real memory_search queries from agent session logs.

Walks all agent session JSONL files (including .deleted.* and .reset.* variants),
extracts memory_search tool calls, cross-references with search.jsonl for intent/latency,
deduplicates, filters boot fixtures, outputs mined-queries-raw.jsonl.
"""
import json
import os
import glob
import sys
import re
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

SESSIONS_BASE = "/home/openclaw/.openclaw/agents"
SEARCH_LOG = "/data/mnemosyne/logs/search.jsonl"
OUTPUT_FILE = "scripts/mined-queries-raw.jsonl"

BOOT_FIXTURE_EXACT = {
    "recent work decisions patterns",
    "current tasks in progress recent work",
    "current tasks in progress",
    "recent priorities active work in progress",
    "current work in progress tasks Builder board",
    "current task in progress recent work",
    "current tasks in progress board status",
    "current tasks in progress board cards builder",
    "current tasks in progress gtm pipeline",
    "current priorities gtm pipeline tasks",
}

MIN_QUERY_LEN = 10
MAX_FREQUENCY_FOR_INCLUSION = 20


def iso_to_epoch(ts):
    """Convert ISO timestamp or millisecond epoch to Unix epoch seconds."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # Could be milliseconds
        if ts > 1e12:
            return ts / 1000.0
        return float(ts)
    if isinstance(ts, str):
        ts = ts.rstrip("Z").replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except ValueError:
                continue
    return None


def load_search_log(path):
    """Load search.jsonl indexed by (agent, ts_bucket) for fast lookup."""
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                entries.append(e)
            except Exception:
                pass
    return entries


def find_search_log_match(search_entries, agent, ts_epoch, window=10):
    """Find closest search.jsonl entry for given agent and timestamp within window seconds."""
    if ts_epoch is None:
        return None
    best = None
    best_diff = window + 1
    for e in search_entries:
        if e.get("agent") != agent:
            continue
        e_ts = e.get("ts")
        if e_ts is None:
            continue
        diff = abs(float(e_ts) - ts_epoch)
        if diff < best_diff:
            best_diff = diff
            best = e
    return best if best_diff <= window else None


def extract_path_from_tool_result(entry):
    """Extract result paths from a toolResult message entry."""
    paths = []
    msg = entry.get("message", {})
    if not isinstance(msg, dict):
        return paths
    role = msg.get("role", "")
    if role not in ("toolResult", "tool_result"):
        # Also check content for toolResult items
        pass
    content = msg.get("content", [])
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                try:
                    parsed = json.loads(text)
                    results = parsed.get("results", [])
                    for r in results:
                        p = r.get("path") or r.get("file") or ""
                        if p:
                            # Strip qmd:// prefix and collection prefix if present
                            p = re.sub(r"^qmd://[^/]+/", "", p)
                            paths.append(p)
                except Exception:
                    pass
    elif isinstance(content, str):
        try:
            parsed = json.loads(content)
            for r in parsed.get("results", []):
                p = r.get("path", "")
                if p:
                    p = re.sub(r"^qmd://[^/]+/", "", p)
                    paths.append(p)
        except Exception:
            pass
    return paths


def parse_session_file(filepath, agent):
    """Parse a session JSONL file and yield (query, ts_epoch, candidate_paths) tuples."""
    entries = []
    try:
        with open(filepath, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        ts_raw = entry.get("timestamp") or msg.get("timestamp")
        ts_epoch = iso_to_epoch(ts_raw)

        content = msg.get("content", [])
        if not isinstance(content, list):
            continue

        for item in content:
            if not isinstance(item, dict):
                continue
            # Check for toolCall with name memory_search
            if item.get("name") == "memory_search" or (
                item.get("type") == "toolCall" and item.get("name") == "memory_search"
            ):
                args = item.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                query = args.get("query", "").strip()
                if not query:
                    continue

                # Look ahead for tool result
                candidate_paths = []
                for j in range(i + 1, min(i + 4, len(entries))):
                    next_paths = extract_path_from_tool_result(entries[j])
                    if next_paths:
                        candidate_paths = next_paths
                        break

                yield query, ts_epoch, candidate_paths


def main():
    print(f"Loading search log from {SEARCH_LOG}...")
    search_entries = load_search_log(SEARCH_LOG)
    print(f"  Loaded {len(search_entries)} search.jsonl entries")

    # Collect all queries: query_text -> list of occurrences
    query_occurrences = defaultdict(list)

    agents_base = Path(SESSIONS_BASE)
    session_count = 0
    for agent_dir in sorted(agents_base.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent = agent_dir.name
        sessions_dir = agent_dir / "sessions"
        if not sessions_dir.exists():
            continue

        # Glob all JSONL files including deleted/reset variants
        patterns = [
            str(sessions_dir / "*.jsonl"),
            str(sessions_dir / "*.jsonl.deleted.*"),
            str(sessions_dir / "*.jsonl.reset.*"),
        ]
        session_files = []
        for pat in patterns:
            session_files.extend(glob.glob(pat))

        for fpath in session_files:
            session_id = Path(fpath).name.split(".")[0][:8]
            session_count += 1
            for query, ts_epoch, candidate_paths in parse_session_file(fpath, agent):
                # Cross-reference with search log
                sl_match = find_search_log_match(search_entries, agent, ts_epoch)
                query_occurrences[query].append({
                    "agent": agent,
                    "ts_epoch": ts_epoch,
                    "candidate_paths": candidate_paths,
                    "has_tool_result": len(candidate_paths) > 0,
                    "session_id": session_id,
                    "intent_from_log": sl_match.get("intent") if sl_match else None,
                    "bm25_count": sl_match.get("bm25_count") if sl_match else None,
                    "vec_count": sl_match.get("vec_count") if sl_match else None,
                    "latency_ms": sl_match.get("latency_ms") if sl_match else None,
                    "vec_failed": sl_match.get("vec_failed", False) if sl_match else False,
                })

    print(f"Scanned {session_count} session files across {agents_base} agents")
    print(f"Found {len(query_occurrences)} unique query strings ({sum(len(v) for v in query_occurrences.values())} total occurrences)")

    # Filter and deduplicate
    output_entries = []
    skipped_fixture = 0
    skipped_short = 0
    skipped_high_freq = 0
    skipped_vec_failed = 0

    for idx, (query, occurrences) in enumerate(sorted(query_occurrences.items())):
        freq = len(occurrences)

        # Filter: boot fixtures
        if query.lower() in {f.lower() for f in BOOT_FIXTURE_EXACT}:
            skipped_fixture += 1
            continue

        # Filter: too short
        if len(query) < MIN_QUERY_LEN:
            skipped_short += 1
            continue

        # Filter: high frequency (session-start fixtures not in the exact list)
        if freq > MAX_FREQUENCY_FOR_INCLUSION:
            skipped_high_freq += 1
            continue

        # Use first occurrence for metadata; merge session_ids and paths
        first = occurrences[0]
        all_paths = list({p for occ in occurrences for p in occ.get("candidate_paths", [])})
        all_sessions = list({occ["session_id"] for occ in occurrences})
        any_vec_failed = any(occ.get("vec_failed") for occ in occurrences)

        # Filter: if ALL occurrences had vec_failed (pre-vector era)
        if any_vec_failed and all(occ.get("vec_failed") for occ in occurrences):
            skipped_vec_failed += 1
            continue

        output_entries.append({
            "id": f"M{len(output_entries)+1:03d}",
            "query": query,
            "agent": first["agent"],
            "intent_from_log": first.get("intent_from_log"),
            "bm25_count": first.get("bm25_count"),
            "vec_count": first.get("vec_count"),
            "latency_ms": first.get("latency_ms"),
            "session_ids": all_sessions,
            "frequency": freq,
            "candidate_gold_paths": all_paths,
            "has_tool_result": any(occ.get("has_tool_result") for occ in occurrences),
            "source": "session_log",
        })

    print(f"\nFiltering results:")
    print(f"  Skipped (boot fixture): {skipped_fixture}")
    print(f"  Skipped (too short):    {skipped_short}")
    print(f"  Skipped (high freq):    {skipped_high_freq}")
    print(f"  Skipped (all vec_fail): {skipped_vec_failed}")
    print(f"  Output entries:         {len(output_entries)}")

    os.makedirs("scripts", exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        for entry in output_entries:
            f.write(json.dumps(entry) + "\n")

    print(f"\nWrote {len(output_entries)} entries to {OUTPUT_FILE}")

    # Print intent distribution
    from collections import Counter
    intents = Counter(e.get("intent_from_log") or "unknown" for e in output_entries)
    agents = Counter(e.get("agent") for e in output_entries)
    print(f"Intent distribution: {dict(intents)}")
    print(f"Agent distribution:  {dict(agents)}")


if __name__ == "__main__":
    main()
