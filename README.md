# TAMPAM3U

A free US TV channel playlist (Pluto TV, Samsung TV Plus, Plex, Roku, Stirr, Tubi and iptv-org US) that rebuilds itself every day and works in **VLC** and **Kodi**.

## Use it

Playlist URL (after you push this repo and the first Action run finishes):

```
https://raw.githubusercontent.com/tampam3u-dev/TAMPAM3U/main/tampam3u.m3u
```

**VLC:** Media > Open Network Stream > paste the URL > Play. Open the playlist view (Ctrl+L) to browse channels by group.

**Kodi:**
1. Install *PVR IPTV Simple Client* (Add-ons > My add-ons > PVR clients).
2. Configure it: General > Location = Remote path (Internet address), M3U playlist URL = the URL above.
3. EPG settings > XMLTV URL = `https://raw.githubusercontent.com/tampam3u-dev/TAMPAM3U/main/epg.xml.gz`
4. Restart Kodi and open TV.

## How it works

- `sources.json` lists the playlists to merge. Add or remove sources there.
- `build_playlist.py` downloads them, removes duplicates, drops dead links, sorts channels into groups (Local 1000s, News 2000s, Sports 3000s, Movies 4000s, Kids 5000s, Music 6000s, Entertainment 7000s), gives each a permanent channel number, and writes `tampam3u.m3u`.
- It also collects the guide links the source playlists declare, merges them (keeping only channels in the playlist) and writes `epg.xml.gz`, which gives Kodi its Plex-style TV Guide grid.
- `guide_report.md` is rewritten each run and shows how many channels per source have guide listings.
- `channel_numbers.json` remembers each channel's number so it never changes. Channels missing for 90 days are forgotten and their numbers freed (`retain_days` in `sources.json`). To renumber everything on purpose (for example after changing how channels are sorted), run the workflow manually from the Actions tab and tick **renumber**. This breaks saved Kodi favorites.
- `.github/workflows/update.yml` runs the script daily and commits the playlist, guide and number file. You can also run it manually from the Actions tab.

Run locally: `python build_playlist.py` (add `--no-check` to skip link testing).

## Notes

- Streams from Pluto TV and similar services generally only work from US IP addresses.
- This does not include live ABC/NBC/CBS/Fox local stations; those are not freely streamable. The "Local" group holds local news and station streams that broadcasters publish openly.
- Only free, publicly offered streams are included.
