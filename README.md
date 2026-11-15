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

**If the guide grid shows blank time slots:** In Kodi, confirm that PVR IPTV
Simple Client's EPG source is **Remote path** and that the XMLTV URL above is
configured (the playlist URL goes in the M3U setting, not the EPG setting).
Then use Kodi **Settings > PVR & Live TV > Guide > Clear data**, and restart
Kodi so it downloads the current XMLTV data. Kodi's menu names vary by version.
Make sure the device clock and time zone are correct. The guide timestamps are
in UTC and Kodi converts them to local time automatically; do not manually
offset the guide to Eastern time.

**Not every free stream has schedule data.** Check `guide_report.md` for the
actual guide coverage by source. A channel with no matching listings cannot
display show titles in its time slots; the builder does not invent show names.

## How it works

- `sources.json` lists the playlists to merge. Add or remove sources there.
- `build_playlist.py` downloads them, removes duplicates, drops dead links, sorts channels into groups (Local, News, Weather, Sports, Movies, Classic TV, Comedy, Drama, Crime & Mystery, Reality, Sci-Fi & Fantasy, Documentary, Lifestyle, Game Shows, Kids & Family, Anime & Gaming, Music, Spanish, Entertainment; each has its own block of 1000 numbers), gives each a permanent channel number, and writes `tampam3u.m3u`.
- It also collects the guide links the source playlists declare, merges them (keeping only channels in the playlist) and writes `epg.xml.gz`, which gives Kodi its Plex-style TV Guide grid.
- `sources.json` also supports `epg_aliases`, explicit mappings between a playlist's existing `tvg-id` and a differently named channel in an XMLTV feed. Verified listings from EPGShare US2 cover ESPN, FS1, NBA TV, several news channels, and the Cinemax West, Classics and Hits feeds; the existing Plex guide supplies the matching 24 Hour Free Movies feed. Channel IDs and numbers stay unchanged. Missing upstream listings stay blank rather than showing guessed schedules.
- After loading the service guides, the builder automatically reuses a schedule for another playlist channel with the same exact name (ignoring only quality labels such as `(720p)` and `[Geo-blocked]`). It requires exactly one scheduled source for that name; ambiguous names and regional variations such as East/West are not automatically matched. This does not add new streaming sources or manufacture listings for channels absent from all guides.
- `favorites.json` pins your favorite channels to numbers 1, 2, 3... in a Favorites group at the top. `channel_list.txt` lists every channel with its number, so you can search it (Ctrl+F) to find exact names.
- **Remove unwanted channels:** Edit `excluded_channels.json` on GitHub. Add exact `tvg-id` values to `channel_ids`, exact names from `channel_list.txt` to `channel_names`, or whole category names (such as `"Shopping"` or `"Religious"`) to `groups`. For example, `{"channel_ids": ["some.id"], "channel_names": ["Example Channel"], "groups": ["Shopping"]}`. All three lists start empty and are optional. Changes to this file trigger a rebuild automatically; excluded channels disappear from both the playlist and its guide. Existing channel numbers are retained in `channel_numbers.json` while the channels remain in upstream sources, so bringing them back retains their numbers. On Kodi, clear PVR channel data under Settings > PVR & Live TV > General after updating the exclusion list.
- `categories.json` holds the category rules and group order. Edit it on GitHub to move channels around: add keywords to a rule, or pin one channel under `overrides`. `category_report.md` shows how many channels landed in each group and what is still unsorted.
- Local stations (the iptv-org ones) get their guide by matching call signs such as KAAL or WFTV against the US local-stations guide listed under `callsign_epg` in `sources.json`. The Actions log line "Local guide: matched X of Y" shows how many matched.
- `guide_report.md` is rewritten each run and shows how many channels per source have guide listings.
- The report also lists conflicting guide IDs. Local-station matching assigns a callsign-based guide ID to at most one station feed, instead of showing the main station's schedule on every branded subchannel. Other channels retain their original IDs unless a matching guide exists.
- If a high-priority stream fails its availability check, the builder tries a lower-priority duplicate before removing that channel. A stream can still be blocked by region, DRM, or Kodi codec support even if its URL passes the check.
- `minimum_channels` and `minimum_epg_channels` in `sources.json` stop a failed upstream refresh from publishing an almost-empty playlist or guide. Lower them only if you intentionally reduce the source list substantially.
- `channel_numbers.json` remembers each channel's number so it never changes. Channels missing for 90 days are forgotten and their numbers freed (`retain_days` in `sources.json`). To renumber everything on purpose (for example after changing how channels are sorted), run the workflow manually from the Actions tab and tick **renumber**. This breaks saved Kodi favorites.
- `.github/workflows/update.yml` runs the script daily, and automatically after changes to guide code, source settings, categories, favorites, or the workflow. It commits the playlist, guide and number file. You can also run it manually from the Actions tab.

Run locally: `python build_playlist.py` (add `--no-check` to skip link testing).

## Notes

- Streams from Pluto TV and similar services generally only work from US IP addresses.
- This does not include live ABC/NBC/CBS/Fox local stations; those are not freely streamable. The "Local" group holds local news and station streams that broadcasters publish openly.
- Only free, publicly offered streams are included. Do not add paid or pirated sources.
