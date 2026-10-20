# ELT - one bot, everything with `/`

`python main.py` starts ONE bot that loads every feature as a cog. Each panel/card has its own banner (`/banners`).

## Copy these files in UNCHANGED (they are not rewritten, so I could not include them)
| file | from |
|---|---|
| `server_logs.py` | your server-logs file (join/leave/ban/role/channel/voice logs) |
| `punishment_card.py`, `punishment_gif.py` | punishment bot |
| `fonts/Orbitron-*.ttf`, `fonts/Rajdhani-*.ttf` | punishment bot (put them in the `fonts/` folder that is already here) |
| `card_bg.gif` (optional backup) / `Liebe_Roxo.gif` (optional, your own welcome banner) | optional |

## Railway
1. ONE Railway service, start command `python main.py`. Variable `DISCORD_TOKEN`. Add a **Volume** at `/data` (cases, giveaways, XP, temp-VC settings live there).
2. **Stop / delete the old bot services** and use ONE token (one Discord application). Two bots on one token wipe each other's commands.
3. Developer Portal -> Bot: enable **Server Members**, **Message Content** (and Presence is not needed).
4. Invite permissions: Administrator is easiest. Otherwise: Manage Roles/Channels/Nicknames/Messages, Move Members, Moderate Members, Kick, Ban, View Audit Log, Manage Server (invites), Mention Everyone, Attach Files, Embed Links, Read Message History. Put the bot's role ABOVE the roles it gives/removes.
5. After the first start the log prints `synced N slash commands`.

## Commands (all `/`)
| feature | commands | banner |
|---|---|---|
| Study card | `/cardpanel` `/removecard` | studycard.gif |
| Welcome | `/testwelcome` (+ automatic on join) | welcome.gif (or your Liebe_Roxo.gif) |
| Verification + support | `/supportpanel` (rest is automatic: waiting VC, help VC, report rooms) | verify.gif, support.gif |
| Verify card | `/verifycard` | verify.gif |
| Staff applications | `/staffpanel` `/staffapps open\|close` | staffapps.gif |
| Punishments | `/warn /unwarn /mute /timeout /kick /ban /blacklist /unblacklist /blacklisted /warnings /history` | blacklist_banner.jpg (skull) |
| Staff points | `/xp_leaderboard /xp_week /xp_points /xp_add /xp_reset /xp_backup /xp_scan` | xp.gif |
| Boost | automatic + `/boosttest` | boost_bg.jpg |
| Giveaways | `/gstart /gend /greroll /gcancel /glist` | giveaway.gif |
| Esport | `/esportpanel` | esport.gif |
| Tickets | `/ticketpanel` | tickets.gif |
| Temp voice | `/tempvc-setup` | tempvc.gif |

Post each panel once with its command, in the channel you want. Change a banner: replace the file in `/banners` (same name), or edit `common.py -> BANNERS`. `python make_banners.py` re-creates the six animated ones (needs Pillow).

After changing banners, post the panel again with its command so the new image is attached (old messages keep the old file).

## What changed on purpose
- `!` commands are gone (`!cardpanel`, `!removecard`, `!staffpanel`, `!staffapps`, `!esportpanel`, `!boosttest`, `!g*`). Message-ID options of `/gend` etc. are text fields (paste the ID).
- `server_logs.py` had its own `/warn /warnings /unwarn`. They are removed at load: the punishment `/warn` family is the only one now (its log is the card). The other server logs keep working.
- Staff points: punishment points are given directly when a punishment card is posted. Verification / support points are read from the cards the bot itself posts (the old "ignore my own messages" rule is removed).
- Tickets, temp-voice and giveaways were ported from Node / a separate bot to Python.

## Not ported (needs files I did not get)
- Temp-VC **game channels** (`interactionHandler.js`, `gameInteractionHandler.js`, `gamePanelView.js` were not uploaded).
- Temp-VC **animated live banner** (needs `assets/lightning-*.png` + `bannerDraw.js` port): the dashboard is a normal embed with the banner, refreshed every 20 s.
- Custom application-emoji lookup: the panel uses the fixed emoji IDs from `customIcons.js`.

## Check after the first start
- `REPORT_CHANNEL_ID` (staff points for resolved reports) defaults to the issue-reports channel; set the variable if your report cards live elsewhere. `BACKUP_CHANNEL_ID` is optional.
- Esport needs `ESPORT_CATEGORY_ID` (+ `ESPORT_ROLE_ID`, `ESPORT_LOG_CHANNEL_ID`) like before.
- This was syntax- and logic-checked here but NOT run against Discord (no network in my sandbox). Start it on a test server first if you can, and read the first minute of logs: every cog prints `[main] loaded ...` or a clear FAILED line (one broken cog never stops the others).
