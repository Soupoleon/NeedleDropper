# Spotify bug report drafts (Web API play accepted, desktop client doesn't start)

## A. Comment for https://github.com/encodi/sonora-mcp/issues/34 (from GitHub account Soupoleon, after the owner approves)

> Same symptom here on the **Windows desktop** client (2026-11-05). A few data points that may help narrow it down:
>
> - Premium account (US). Development Mode app created this week; scopes `user-modify-playback-state user-read-playback-state user-read-private`.
> - `PUT /v1/me/player/play?device_id=<desktop>` with `{"uris":["spotify:track:…"]}` returns **204**. The desktop client then drops the item it had loaded and plays nothing. `GET /v1/me/player` shows `item: null` and `is_playing: false`, and lists every action in `actions.disallows` (pausing, resuming, skipping_next, skipping_prev, transferring_playback). A follow-up pause returns 403 "Restriction violated".
> - Reproduced on both the **Microsoft Store build (1.306.1921.0)** and a fresh **spotify.com installer** build.
> - Resuming a track the user started by hand (`PUT /v1/me/player/play`, no body) returned 200, but `progress_ms` never moved.
> - Transfer (`PUT /v1/me/player {"device_ids":[…], "play": true}`) and then play again gives the same result.
> - **The identical call targeting "Web Player (Chrome)" works immediately**, including `position_ms`, and pause works there too.
> - Opening `spotify:track:<id>` locally in the desktop client plays that track fine, so it isn't market or availability.
>
> So Connect play commands to native clients (Windows desktop here, Android in this issue) are accepted but not executed, while the web player is fine.

## B. Post for the Spotify for Developers board (community.spotify.com), posted by the owner

**Title:** Web API `/me/player/play` returns 204 but the Windows desktop client never starts playback (Web Player works)

**Body:** the same facts as A. Add the date and time range of the attempts (2026-11-05, about 18:30 to 22:30 US time), the desktop client versions, and the fact that the developer-mode app owner is the same Premium user. Ask whether native Connect clients currently ignore playback commands from Development Mode apps, and whether this is intended.

## Notes
- Spotify's bug bounty (HackerOne) covers security vulnerabilities only, so this functional bug is not eligible.
- No client ids, device ids, tokens or account names appear in either draft.
