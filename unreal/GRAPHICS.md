# GTA 6 level — yes or no

**The engine: yes. The GTA 6 files: no.**

| Question | Answer |
| --- | --- |
| Can Unreal Engine 5 draw at GTA 6 *class* (Nanite geo, Lumen GI, virtual shadows, cinematic dusk city)? | **Yes.** That is what UE5 is for. |
| Can we download GTA 6 graphics, maps, cars, audio, or trademarks? | **No.** Rockstar / Take-Two own them. There is no legal pack. |
| Does any open-source racing game ship at that fidelity today? | **No.** AAA city density is art and capture work, not a git clone. |
| What is the legal path to that look? | UE5 as the coding source + Epic **City Sample** (Fab / Epic launcher) + your own cars and roads. |

City Sample is Epic’s dense Nanite city. It is the closest public stand-in for a GTA-6-scale street. It is **not** Vice City, Leonida, or any Rockstar map.

This repo’s coding source is `unreal/AureliaDrive/` (C++, Unreal Engine 5.5). Renderer defaults are already on Lumen, Nanite, and virtual shadow maps. Open that project in the Linux Unreal Editor. Pull City Sample from Epic when you want city scale. Do not import GTA 6 archives.
