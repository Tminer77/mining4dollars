# Aurelia Drive — Unreal Engine 5

This folder is the **coding source**. Not Three.js. Not GTA 6.

Unreal Engine 5.5, C++, Lumen + Nanite + virtual shadow maps. That is the
renderer class GTA 6 lives in. The *assets* in GTA 6 are still Rockstar’s.
You cannot download them. Use Epic’s City Sample when you want city density.

## Open (Linux)

1. Install Unreal Engine 5.5 (Epic installer or [Linux tarball](https://www.unrealengine.com/linux)).
2. Double-click `AureliaDrive.uproject`, or:

   ```bash
   /path/to/UnrealEditor AureliaDrive.uproject
   ```

3. Play In Editor. You spawn in an original dusk coastal city. WASD drive, Space handbrake, R reset. Hit the gold/cyan gates, three laps.
4. Fab → **City Sample** when you want Nanite city density. Do not import GTA 6 archives.

Windows game builds are out of scope here.

## What you get

| Piece | Role |
| --- | --- |
| `AAureliaVehiclePawn` | Arcade street car on stock Engine meshes |
| `AAureliaCityBuilder` | Original dusk grid: roads, towers, palms, water, neon |
| `AAureliaDriveGameMode` | Dusk sun, Lumen post-process, 3-lap gate race |
| `AAureliaDriveHud` | Speed / lap / gate overlay |
| `Config/DefaultEngine.ini` | Lumen GI/reflections, Nanite, virtual shadows |
| Chaos Vehicles plugin | Enabled; attach a skeletal wheeled mesh when you have one |

## Graphics

See [`../GRAPHICS.md`](../GRAPHICS.md). Short version: engine level **yes**,
GTA 6 files **no**.
