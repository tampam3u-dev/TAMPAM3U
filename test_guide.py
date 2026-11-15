"""Offline regression tests for XMLTV guide/channel matching."""
import gzip
import tempfile
import unittest
import xml.etree.ElementTree as ET
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

    def test_callsign_mapping_requires_real_programmes(self):
        source = b'''<tv><channel id="empty"><display-name>WFTV</display-name></channel>
            <channel id="working"><display-name>WFTV</display-name></channel>
            <programme channel="working" start="20261115120000 +0000" stop="20261115130000 +0000"><title>News</title></programme></tv>'''
        channel = {"source": "iptv-org US", "name": "WFTV Orlando", "attrs": {"tvg-id": "original"}}
        with patch.object(builder, "fetch_bytes_cached", return_value=source):
            builder.map_callsigns([channel], ["mock://guide"])
        self.assertEqual(channel["attrs"]["tvg-id"], "working")


if __name__ == "__main__":
    unittest.main()
