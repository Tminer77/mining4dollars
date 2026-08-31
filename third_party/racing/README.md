# Open-source racing games

This folder is a **download target**. The coding source is
`unreal/AureliaDrive/` (Unreal Engine 5.5 C++).

**GTA 6 graphics cannot be downloaded.** Rockstar / Take-Two own that game.
See [`unreal/GRAPHICS.md`](../../unreal/GRAPHICS.md): engine level yes, files no.

## What this script fetches

| Game | License | Engine | Why |
| --- | --- | --- | --- |
| [UE4_Endless_Racer](https://github.com/Tomiinek/UE4_Endless_Racer) | MIT | Unreal Engine 4.21 | Classic endless vehicle racer (Blueprints) |
| [UETrafficGame](https://github.com/ScrappyCocco/UETrafficGame) | MIT | Unreal Engine 5 | Vehicle playground with Nanite, Lumen, Chaos |
| [HexGL](https://github.com/BKcore/HexGL) | MIT | WebGL | Small HTML5 racer |
| [Stunt Rally 3](https://github.com/stuntrally/stuntrally3) | GPL-3.0 | Ogre-Next | Complete OSS rally game |

Clones are gitignored so GPL trees never mix into the Python package.

```bash
make download-endless                         # Endless Racer (UE4.21 source)
make endless-racer                            # Open in Unreal Editor if installed
scripts/download_oss_racing_game.sh           # UETrafficGame (default)
scripts/download_oss_racing_game.sh hexgl
scripts/download_oss_racing_game.sh stuntrally
```

### Endless Racer

After `make download-endless`, open:

`third_party/racing/UE4_Endless_Racer/Endless.uproject`

Requires **Unreal Engine 4.21+**. Press Play on `Content/Level.umap` (arrow keys).
The author's Windows shipping build Drive link is often unavailable; use the editor.

City-scale streets: Epic **City Sample** from Fab / the Epic launcher. That is
the legal GTA-6-class city. It is not Vice City and not a Rockstar archive.
