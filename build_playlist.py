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

GROUP_KEYWORDS = [
    ("Local", ["local now", "newson", "local news"]),
    ("News", ["news", "cnn", "msnbc", "nbc", "abc", "cbs", "fox", "weather", "newsmax", "cbsn", "bloomberg", "cheddar", "reuters"]),
    ("Sports", ["sport", "nfl", "nba", "mlb", "nhl", "golf", "fifa", "soccer", "racing", "ufc", "wwe", "poker"]),
    ("Kids", ["kids", "cartoon", "nick", "baby", "toon", "anime", "sesame", "pbs kids"]),
    ("Movies", ["movie", "film", "cinema", "horror", "western", "action", "thriller", "romance"]),
    ("Music", ["music", "mtv", "vevo", "radio", "concert", "karaoke"]),
]
# First channel number in each group (blocks of 1000 so groups never overflow)
GROUP_BASE = {"Local": 1000, "News": 2000, "Sports": 3000, "Movies": 4000,
              "Kids": 5000, "Music": 6000, "Entertainment": 7000}
OTHER_BASE = 8000


def fetch_bytes(url, timeout=120):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch(url, timeout=30):
    return fetch_bytes(url, timeout).decode("utf-8", errors="replace")


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
            head, _, name = line.partition(",")
            cur = {
                "attrs": dict(ATTR_RE.findall(head)),
                "name": name.strip(),
                "opts": [],
                "source": source,
                "priority": priority,
            }
        elif line.startswith("#"):
            if cur is not None:
                cur["opts"].append(line)  # e.g. #EXTVLCOPT user-agent lines
        elif cur is not None:
            cur["url"] = line
            channels.append(cur)
            cur = None
    return channels, epg_urls


def classify(ch):
    name = ch["name"]
    src_group = ch["attrs"].get("group-title", "")
    hay = f"{name} {src_group}".lower()
    for group, words in GROUP_KEYWORDS:
        if any(w in hay for w in words):
            if group == "News" and CALLSIGN_RE.search(name):
                return "Local"
            return group
    if CALLSIGN_RE.search(name):
        return "Local"
    return src_group.title() if src_group else "Entertainment"


def norm_name(name):
    n = re.sub(r"\b(hd|sd|us|usa|\(.*?\))\b", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", n)


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
    if group in GROUP_BASE:
        return GROUP_BASE[group]
    # Unknown groups get a deterministic block from 8000 up
    return OTHER_BASE + 1000 * (zlib.crc32(group.encode()) % 20)


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
    seen_channels, seen_progs = set(), set()
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sources.json")
    ap.add_argument("--out", default="tampam3u.m3u")
    ap.add_argument("--numbers", default="channel_numbers.json")
    ap.add_argument("--no-check", action="store_true")
    ap.add_argument("--no-epg", action="store_true")
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args()

    cfg = json.load(open(args.config))
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
    merged.sort(key=lambda c: (c["group"], c["name"].lower()))
    store = load_numbers(args.numbers)
    assign_numbers(merged, store, date.today(), cfg.get("retain_days", 90))
    save_numbers(args.numbers, store)
    merged.sort(key=lambda c: c["chno"])

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
        build_epg(epg_urls, wanted, cfg.get("epg_output", "epg.xml.gz"))


if __name__ == "__main__":
    main()
