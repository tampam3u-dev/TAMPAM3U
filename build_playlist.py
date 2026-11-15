#!/usr/bin/env python3
"""Build tampam3u.m3u and a matching guide (epg.xml.gz) from free US TV sources.

What it does:
  1. Downloads every playlist listed in sources.json
  2. Merges them, removes duplicates, optionally drops dead links
  3. Groups channels (Local, News, Sports, Movies, Kids, Music, Entertainment)
     and gives each a permanent channel number (tvg-chno, remembered in
     channel_numbers.json) so Kodi shows a Plex-like list
  4. Reads the guide (XMLTV) links the source playlists declare, downloads them,
     keeps only entries for channels in the final playlist, and writes one
     combined, compressed guide file

Usage:
    python build_playlist.py              # full build
    python build_playlist.py --no-check   # skip link testing (faster)
    python build_playlist.py --no-epg     # skip the guide
"""
import argparse
import gzip
import io
import json
import re
import sys
import urllib.request
import zlib
from datetime import date, timedelta
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0 (TAMPAM3U playlist builder)"}
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
# Heuristic: US call signs like KABC, WXYZ-TV, KTLA 5
CALLSIGN_RE = re.compile(r"\b[KW][A-Z]{2,3}(?:-?(?:TV|DT))?\b")

CATS = {"order": [], "default_group": "Entertainment", "overrides": {}, "rules": []}
# Words that look like US call signs (K***/W***) but are not stations
NOT_CALLSIGNS = {"KIDS", "KING", "KONG", "KUNG", "KORE", "KICK", "KIND", "KOOL", "KIDZ", "KNOW",
                 "WWE", "WILD", "WORK", "WEEK", "WEST", "WINE", "WIRE", "WISH", "WIN", "WAR", "WAY",
                 "WEB", "WOW", "WORLD", "WEAT", "WELL", "WITH", "WILL", "WOOD", "WORD"}
NETWORK_RE = re.compile(r"\b(ABC|NBC|CBS|FOX|CW|PBS|ION|MYNETWORK|MY NETWORK|MYTV|TELEMUNDO|UNIVISION|UNIMAS|IND)\b")


def load_categories(path):
    global CATS
    try:
        CATS = json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"ERROR: {path} not found. It must sit next to build_playlist.py.")
    for rule in CATS["rules"]:
        parts = []
        for kw in rule["keywords"]:
            kw = kw.lower()
            if kw.endswith("!"):
                parts.append(r"(?<![a-z0-9])" + re.escape(kw[:-1]) + r"(?![a-z0-9])")
            else:
                parts.append(r"(?<![a-z0-9])" + re.escape(kw))
        rule["_re"] = re.compile("|".join(parts)) if parts else None


def fetch_bytes(url, timeout=120):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch(url, timeout=30):
    return fetch_bytes(url, timeout).decode("utf-8", errors="replace")


def split_extinf(line):
    """Split '#EXTINF:-1 attrs...,Name' at the first comma that is not inside quotes."""
    in_q = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_q = not in_q
        elif ch == "," and not in_q:
            return line[:i], line[i + 1:]
    return line, ""


def parse_m3u(text, source, priority):
    """Return (channels, epg_urls) from M3U text."""
    channels, epg_urls, cur = [], [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            attrs = dict(ATTR_RE.findall(line))
            for key in ("url-tvg", "x-tvg-url"):
                for u in attrs.get(key, "").split(","):
                    if u.strip():
                        epg_urls.append(u.strip())
            continue
        if line.startswith("#EXTINF"):
            head, name = split_extinf(line)
            cur = {
                "attrs": dict(ATTR_RE.findall(head)),
                "name": name.strip(),
                "opts": [],
                "source": source,
                "priority": priority,
            }
            # Keep per-channel request headers so streams that need them still play
            ua = cur["attrs"].get("http-user-agent")
            if ua:
                cur["opts"].append(f"#EXTVLCOPT:http-user-agent={ua}")
            ref = cur["attrs"].get("http-referrer") or cur["attrs"].get("http-referer")
            if ref:
                cur["opts"].append(f"#EXTVLCOPT:http-referrer={ref}")
        elif line.startswith("#"):
            if cur is not None:
                cur["opts"].append(line)  # e.g. #EXTVLCOPT user-agent lines
        elif cur is not None:
            cur["url"] = line
            channels.append(cur)
            cur = None
    return channels, epg_urls


def is_local_station(ch):
    """US station call sign (KABC, WXYZ-TV) on a network affiliate or an iptv-org channel."""
    name = ch["name"]
    for m in CALLSIGN_RE.finditer(name):
        tok = re.sub(r"-?(TV|DT)$", "", m.group())
        if tok in NOT_CALLSIGNS:
            continue
        if NETWORK_RE.search(name) or ch["source"].lower().startswith("iptv-org"):
            return True
    return False


def classify(ch):
    name = ch["name"]
    over = CATS["overrides"].get(name.lower())
    if over:
        return over
    if is_local_station(ch):
        return "Local"
    hay = f"{name} {ch['attrs'].get('group-title', '')}".lower()
    for rule in CATS["rules"]:
        if rule["_re"] and rule["_re"].search(hay):
            return rule["group"]
    return CATS["default_group"]


def norm_name(name):
    n = re.sub(r"\b(hd|sd|us|usa|\(.*?\))\b", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", n)


def sort_name(name):
    """Sort key that ignores leading 'The', 'A', 'An' (so 'The Weather Channel' sorts under W)."""
    n = re.sub(r"^(the|an|a)\s+", "", name.strip().lower())
    return n or name.lower()


def alive(ch, timeout=10):
    """True if the stream URL responds and looks like HLS/video."""
    try:
        req = urllib.request.Request(ch["url"], headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status >= 400:
                return False
            head = r.read(512)
            ctype = (r.headers.get("Content-Type") or "").lower()
            return b"#EXTM3U" in head or "mpegurl" in ctype or "video" in ctype or "octet-stream" in ctype
    except Exception:
        return False


def chan_key(c):
    """Stable identity for a channel: its guide id, else its cleaned-up name."""
    tid = c["attrs"].get("tvg-id")
    return tid if tid else "name:" + norm_name(c["name"])


def group_base(group):
    """Each group gets its own block of 1000 numbers, in the order set in categories.json."""
    order = CATS["order"]
    if group in order:
        return (order.index(group) + 1) * 1000
    return (len(order) + 1 + zlib.crc32(group.encode()) % 20) * 1000


def load_numbers(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_numbers(path, store):
    items = sorted(store.items(), key=lambda kv: kv[1]["chno"])
    with open(path, "w", encoding="utf-8") as f:
        f.write("{\n")
        f.write(",\n".join(f"  {json.dumps(k)}: {json.dumps(v)}" for k, v in items))
        f.write("\n}\n")


def assign_numbers(channels, store, today, retain_days):
    """Give each channel a permanent number (tvg-chno), remembered in `store`.

    Existing channels keep their number. New channels get the next free number
    in their group's block. Channels unseen for `retain_days` are forgotten and
    their numbers freed.
    """
    cutoff = today - timedelta(days=retain_days)
    for k in [k for k, v in store.items() if date.fromisoformat(v["last_seen"]) < cutoff]:
        del store[k]
    used = {v["chno"] for v in store.values()}
    used_keys = set()
    new_count = 0
    for c in channels:
        key = base_key = chan_key(c)
        n = 2
        while key in used_keys:  # two channels sharing one id
            key = f"{base_key}#{n}"
            n += 1
        used_keys.add(key)
        entry = store.get(key)
        if entry is None:
            num = group_base(c["group"])
            while num in used:
                num += 1
            entry = {"chno": num, "group": c["group"]}
            store[key] = entry
            used.add(num)
            new_count += 1
        entry["name"] = c["name"]
        entry["last_seen"] = today.isoformat()
        c["chno"] = entry["chno"]
    print(f"Channel numbers: {len(channels) - new_count} kept, {new_count} new, {len(store) - len(channels)} reserved for missing channels")


def build_epg(epg_urls, wanted_ids, out_path):
    """Download each guide, keep entries for wanted channel ids, write one .xml.gz."""
    root = ET.Element("tv", {"generator-info-name": "TAMPAM3U"})
    seen_channels, seen_progs, prog_channels = set(), set(), set()
    for url in epg_urls:
        try:
            data = fetch_bytes(url, timeout=300)
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            kept_ch = kept_pr = 0
            for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
                if el.tag == "channel":
                    cid = el.get("id")
                    if cid in wanted_ids and cid not in seen_channels:
                        seen_channels.add(cid)
                        root.insert(len(seen_channels) - 1, el)
                        kept_ch += 1
                    else:
                        el.clear()
                elif el.tag == "programme":
                    key = (el.get("channel"), el.get("start"))
                    if el.get("channel") in wanted_ids and key not in seen_progs:
                        seen_progs.add(key)
                        prog_channels.add(key[0])
                        root.append(el)
                        kept_pr += 1
                    else:
                        el.clear()
            print(f"Guide {url}: kept {kept_ch} channels, {kept_pr} programmes")
        except Exception as e:
            print(f"WARNING: could not use guide {url}: {e}", file=sys.stderr)
    with gzip.open(out_path, "wb") as f:
        ET.ElementTree(root).write(f, encoding="utf-8", xml_declaration=True)
    print(f"Guide covers {len(seen_channels)} of {len(wanted_ids)} channels with IDs -> {out_path}")
    return seen_channels, prog_channels


def write_report(channels, prog_ids, path, sample=15):
    """Write guide_report.md: how many channels per source have real listings."""
    by_src = {}
    for c in channels:
        tid = c["attrs"].get("tvg-id")
        r = by_src.setdefault(c["source"], {"total": 0, "ok": 0, "no_id": [], "no_listings": []})
        r["total"] += 1
        if not tid:
            r["no_id"].append(c["name"])
        elif tid not in prog_ids:
            r["no_listings"].append(f'{c["name"]} ({tid})')
        else:
            r["ok"] += 1
    total = sum(r["total"] for r in by_src.values())
    ok = sum(r["ok"] for r in by_src.values())
    lines = ["# Guide match report", "", f"Generated {date.today()}. {ok} of {total} channels have guide listings.", "",
             "| Source | Channels | With listings | No guide ID | ID but no listings |",
             "|---|---|---|---|---|"]
    for name, r in by_src.items():
        lines.append(f'| {name} | {r["total"]} | {r["ok"]} | {len(r["no_id"])} | {len(r["no_listings"])} |')
    for name, r in by_src.items():
        if r["no_id"] or r["no_listings"]:
            lines += ["", f"## {name}"]
            if r["no_id"]:
                lines.append(f'No guide ID (first {sample}): ' + ", ".join(r["no_id"][:sample]))
            if r["no_listings"]:
                lines.append(f'ID but no listings (first {sample}): ' + ", ".join(r["no_listings"][:sample]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Guide report: {ok} of {total} channels have listings -> {path}")


def write_category_report(channels, path, sample=40):
    """Write category_report.md: channels per group and what ended up unsorted."""
    counts = {}
    for c in channels:
        counts[c["group"]] = counts.get(c["group"], 0) + 1
    order = CATS["order"]
    names = sorted(counts, key=lambda g: order.index(g) if g in order else 999)
    lines = ["# Category report", "", f"Generated {date.today()}. {len(channels)} channels.", "",
             "| Group | Channels |", "|---|---|"]
    lines += [f"| {g} | {counts[g]} |" for g in names]
    default = CATS["default_group"]
    left = [c for c in channels if c["group"] == default]
    if left:
        by_cat = {}
        for c in left:
            by_cat.setdefault(c["attrs"].get("group-title", "") or "(none)", []).append(c["name"])
        lines += ["", f"## Still in {default} ({len(left)})", ""]
        for k, names in sorted(by_cat.items(), key=lambda kv: -len(kv[1]))[:12]:
            lines.append(f"### {k} ({len(names)})")
            lines.append(", ".join(names[:25]) + (" ..." if len(names) > 25 else ""))
            lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Category report -> {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sources.json")
    ap.add_argument("--out", default="tampam3u.m3u")
    ap.add_argument("--numbers", default="channel_numbers.json")
    ap.add_argument("--categories", default="categories.json")
    ap.add_argument("--renumber", action="store_true", help="ignore saved numbers and renumber every channel")
    ap.add_argument("--no-check", action="store_true")
    ap.add_argument("--no-epg", action="store_true")
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args()

    cfg = json.load(open(args.config))
    load_categories(args.categories)
    all_ch, epg_urls = [], []
    for s in cfg["sources"]:
        try:
            chans, guides = parse_m3u(fetch(s["url"]), s["name"], s.get("priority", 99))
            print(f"{s['name']}: {len(chans)} channels, {len(guides)} guide link(s)")
            all_ch.extend(chans)
            epg_urls.extend(guides)
        except Exception as e:
            print(f"WARNING: could not fetch {s['name']}: {e}", file=sys.stderr)
    epg_urls.extend(cfg.get("extra_epg", []))
    epg_urls = list(dict.fromkeys(epg_urls))  # de-duplicate, keep order

    # De-duplicate by URL, then by channel name (lowest priority number wins)
    all_ch.sort(key=lambda c: c["priority"])
    seen_url, seen_name, merged = set(), set(), []
    for c in all_ch:
        key = norm_name(c["name"])
        if c["url"] in seen_url or (key and key in seen_name):
            continue
        seen_url.add(c["url"])
        seen_name.add(key)
        merged.append(c)
    print(f"After de-duplication: {len(merged)}")

    if not args.no_check:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            ok = list(ex.map(alive, merged))
        merged = [c for c, good in zip(merged, ok) if good]
        print(f"After link check: {len(merged)}")

    for c in merged:
        c["group"] = classify(c)
    merged.sort(key=lambda c: (c["group"], sort_name(c["name"])))
    store = {} if args.renumber else load_numbers(args.numbers)
    assign_numbers(merged, store, date.today(), cfg.get("retain_days", 90))
    save_numbers(args.numbers, store)
    merged.sort(key=lambda c: c["chno"])
    write_category_report(merged, "category_report.md")

    epg_public = cfg.get("epg_public_url", "")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(f'#EXTM3U url-tvg="{epg_public}" x-tvg-url="{epg_public}"\n')
        for c in merged:
            a = c["attrs"]
            parts = ["-1"]
            if a.get("tvg-id"):
                parts.append(f'tvg-id="{a["tvg-id"]}"')
            parts.append(f'tvg-name="{c["name"]}"')
            parts.append(f'tvg-chno="{c["chno"]}"')
            if a.get("tvg-logo"):
                parts.append(f'tvg-logo="{a["tvg-logo"]}"')
            parts.append(f'group-title="{c["group"]}"')
            f.write(f'#EXTINF:{" ".join(parts)},{c["name"]}\n')
            for o in c["opts"]:
                f.write(o + "\n")
            f.write(c["url"] + "\n")
    print(f"Wrote {len(merged)} channels to {args.out}")

    if not args.no_epg:
        wanted = {c["attrs"]["tvg-id"] for c in merged if c["attrs"].get("tvg-id")}
        _, prog_ids = build_epg(epg_urls, wanted, cfg.get("epg_output", "epg.xml.gz"))
        write_report(merged, prog_ids, "guide_report.md")


if __name__ == "__main__":
    main()
