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
- `build_playlist.py` downloads them, removes duplicates, drops dead links, sorts channels into groups (Local 100s, News 200s, Sports 300s, Movies 400s, Kids 500s, Music 600s, Entertainment 700s), numbers them, and writes `tampam3u.m3u`.
- It also collects the guide links the source playlists declare, merges them (keeping only channels in the playlist) and writes `epg.xml.gz`, which gives Kodi its Plex-style TV Guide grid.
- `.github/workflows/update.yml` runs the script daily and commits both files. You can also run it manually from the Actions tab.

Run locally: `python build_playlist.py` (add `--no-check` to skip link testing).

## Notes

- Streams from Pluto TV and similar services generally only work from US IP addresses.
- This does not include live ABC/NBC/CBS/Fox local stations; those are not freely streamable. The "Local" group holds local news and station streams that broadcasters publish openly.
- Only free, publicly offered streams are included. Do not add paid or pirated sources.
