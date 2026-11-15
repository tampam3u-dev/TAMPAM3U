"""Offline regression tests for XMLTV guide/channel matching."""
import gzip
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from unittest.mock import patch

import build_playlist as builder


class GuideTests(unittest.TestCase):
    def test_programme_without_channel_header_gets_valid_xmltv_channel(self):
        source = b'<tv><programme channel="station.id" start="20261115120000 +0000" stop="20261115130000 +0000"><title>Show</title></programme></tv>'
        with tempfile.TemporaryDirectory() as tmp, patch.object(builder, "fetch_bytes_cached", return_value=source):
            output = str(Path(tmp) / "guide.xml.gz")
            channels, programmes = builder.build_epg(["mock://guide"], {"station.id"}, output, {"station.id": "Station"})
            root = ET.parse(gzip.open(output, "rb")).getroot()
            self.assertEqual(root.find("channel/display-name").text, "Station")
            self.assertEqual(root.find("programme/title").text, "Show")
            self.assertEqual(channels, {"station.id"})
            self.assertEqual(programmes, {"station.id"})

    def test_alias_copies_verified_schedule_to_playlist_id(self):
        source = b'''<tv><channel id="ESPN.HD.us2"><display-name>ESPN HD</display-name></channel>
          <programme channel="ESPN.HD.us2" start="20261115120000 +0000" stop="20261115130000 +0000"><title>SportsCenter</title></programme></tv>'''
        with tempfile.TemporaryDirectory() as tmp, patch.object(builder, "fetch_bytes_cached", return_value=source):
            output = str(Path(tmp) / "guide.xml.gz")
            _, programmes = builder.build_epg(["mock://national"], {"ESPN.us@HD"}, output,
                                              {"ESPN.us@HD": "ESPN"},
                                              {"ESPN.us@HD": "ESPN.HD.us2"})
            root = ET.parse(gzip.open(output, "rb")).getroot()
        self.assertEqual(programmes, {"ESPN.us@HD"})
        self.assertEqual([p.get("channel") for p in root.findall("programme")], ["ESPN.us@HD"])
        self.assertEqual(root.find("programme/title").text, "SportsCenter")
        self.assertEqual(root.find("channel").get("id"), "ESPN.us@HD")

    def test_alias_can_reuse_another_playlist_channels_schedule(self):
        source = b'''<tv><channel id="plex"><display-name>24 Hour Free Movies</display-name></channel>
          <programme channel="plex" start="20261115120000 +0000" stop="20261115130000 +0000"><title>Movie</title></programme></tv>'''
        with tempfile.TemporaryDirectory() as tmp, patch.object(builder, "fetch_bytes_cached", return_value=source):
            path = str(Path(tmp) / "guide.xml.gz")
            builder.build_epg(["mock://plex"], {"plex", "iptv-org"}, path,
                              {"iptv-org": "24 Hour Free Movies"}, {"iptv-org": "plex"})
            root = ET.parse(gzip.open(path)).getroot()
        self.assertEqual({p.get("channel") for p in root.findall("programme")}, {"plex", "iptv-org"})

    def test_callsign_mapping_requires_real_programmes(self):
        source = b'''<tv><channel id="empty"><display-name>WFTV</display-name></channel>
            <channel id="working"><display-name>WFTV</display-name></channel>
            <programme channel="working" start="20261115120000 +0000" stop="20261115130000 +0000"><title>News</title></programme></tv>'''
        channel = {"source": "iptv-org US", "name": "WFTV Orlando", "attrs": {"tvg-id": "original"}}
        with patch.object(builder, "fetch_bytes_cached", return_value=source):
            builder.map_callsigns([channel], ["mock://guide"])
        self.assertEqual(channel["attrs"]["tvg-id"], "working")

    def test_one_callsign_schedule_cannot_cover_unrelated_subchannels(self):
        source = b'''<tv><channel id="kpvm"><display-name>KPVM-LD</display-name></channel>
            <programme channel="kpvm" start="20261115120000 +0000" stop="20261115130000 +0000"><title>News</title></programme></tv>'''
        names = ["Newsmax 2 KPVM-LD", "KPVM-LD 25.1", "Movies! KPVM-LD"]
        channels = [{"source": "iptv-org US", "name": n,
                     "attrs": {"tvg-id": f"original-{i}"}} for i, n in enumerate(names)]
        with patch.object(builder, "fetch_bytes_cached", return_value=source):
            builder.map_callsigns(channels, ["mock://guide"])
        self.assertEqual([c["attrs"]["tvg-id"] for c in channels],
                         ["original-0", "kpvm", "original-2"])

    def test_duplicate_number_is_stable_when_order_reverses(self):
        store = {"same": {"chno": 1001, "name": "A", "group": "Local", "last_seen": "2026-11-14"},
                 "same#2": {"chno": 1002, "name": "B", "group": "Local", "last_seen": "2026-11-14"}}
        channels = [{"name": name, "attrs": {"tvg-id": "same"}, "group": "Local"} for name in ["B", "A"]]
        builder.assign_numbers(channels, store, date(2026, 11, 15), 90)
        self.assertEqual([c["chno"] for c in channels], [1002, 1001])

    def test_unavailable_primary_stream_uses_lower_priority_backup(self):
        channels = [{"name": "Same channel", "url": "dead", "priority": 1},
                    {"name": "Same channel", "url": "working", "priority": 2}]
        with patch.object(builder, "alive", side_effect=lambda c: c["url"] == "working"):
            selected = builder.merge_channels(channels, workers=1)
        self.assertEqual([c["url"] for c in selected], ["working"])

    def test_report_calls_out_remaining_duplicate_guide_ids(self):
        channels = [{"name": n, "source": "Test", "attrs": {"tvg-id": "shared"}}
                    for n in ["First", "Second"]]
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "guide_report.md"
            builder.write_report(channels, {"shared"}, str(report))
            text = report.read_text(encoding="utf-8")
        self.assertIn("1 XMLTV IDs are assigned", text)
        self.assertIn("First, Second", text)


if __name__ == "__main__":
    unittest.main()
