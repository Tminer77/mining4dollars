# Open-source racing games

This folder is a **download target**, not a vendor of Grand Theft Auto.

**GTA 6 graphics cannot be downloaded.** Rockstar / Take-Two own that game,
its city, vehicles, characters, audio, and trademarks. Fan reverse-engineering
projects (re3, OpenRW, and similar) still require a copy of the original game
and are under active legal pressure. They are not fetched here.

## What is here

| Game | License | Why it is listed |
| --- | --- | --- |
| [HexGL](https://github.com/BKcore/HexGL) | MIT | Complete HTML5 racer; small enough to clone on a laptop |
| [Stunt Rally 3](https://github.com/stuntrally/stuntrally3) | GPL-3.0 | Best *complete* open-source 3D racer (Ogre-Next PBR, 232 tracks) |
| [VDrift](https://github.com/VDrift/vdrift) | GPL-3.0 | Drift-focused sim; physics used by Stunt Rally |
| [Speed Dreams](https://forge.a-lec.org/speed-dreams/speed-dreams-code) | GPL-2.0-or-later | TORCS-derived motorsport sim |

Clones are gitignored so this proprietary Python service is not GPL-contaminated.

```bash
scripts/download_oss_racing_game.sh        # HexGL
scripts/download_oss_racing_game.sh stuntrally
```

## Closest thing to “GTA 6 graphics” that is legal

Nothing open-source matches GTA 6. The honest options:

1. **Aurelia Drive** (`apps/aurelia-drive/`) — original dusk coastal street
   racer in this repo. Same *mood* as a Vice-City-at-sunset trailer (wet
   asphalt, palms, neon, golden hour). Original art only.
2. **Stunt Rally 3** — real standalone OSS game with modern PBR, not a city
   crime sandbox.
3. **Unreal Engine 5 Vehicle / City Sample** — photoreal, but Epic’s samples
   are not GTA assets and need the Epic launcher.

Play Aurelia Drive with `make race`.
