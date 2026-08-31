# Aurelia Drive (browser preview)

The **coding source is Unreal Engine 5**: `unreal/AureliaDrive/`.
This folder is only a Three.js preview you can open with `make race`.

It is **not** Grand Theft Auto and is **not affiliated with Rockstar Games**.
GTA 6 assets are proprietary; this game uses original procedural art and the
MIT-licensed Three.js engine. The look is a golden-hour subtropical waterfront:
wet roads, palms, neon, and a sports car — the legal way to chase that mood.

## Play

```bash
make race
```

Then open http://127.0.0.1:8080

| Key | Action |
| --- | --- |
| W / ↑ | Accelerate |
| S / ↓ | Brake / reverse |
| A D / ← → | Steer |
| Space | Handbrake |
| C | Camera (chase / hood / cinematic) |

Hit every checkpoint. Three laps.

## Stack

- Three.js 0.170 (Sky, Water, Unreal Bloom)
- No build step; served as static files
- License: MIT (`LICENSE`)
