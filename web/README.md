# web/

A single self-contained HTML file: an interactive Three.js scene used as a
front page for this repository.

```bash
python3 -m http.server 8000 --directory web
# then open http://localhost:8000
```

A static server is required — the page is an ES module and `file://` will not
load it. Nothing else needs building: `index.html` is the whole thing.

## What it does

The scene is a procedurally grown branch, revealed by a scan and then annotated.
In order:

1. **The sweep.** Points sampled off the branch surface light up as an expanding
   front passes them, the way a field scan resolves terrain. The front keeps
   pinging afterwards, faintly, so the finished scene still reads as
   instrumented.
2. **The wireframe.** The tube topology draws itself in along the branch, from
   the root outwards.
3. **The surface.** Bark dissolves in behind the same front, then leaves unfurl.
4. **The cards.** Three panels load with a scan line walking down them — blocky
   and colour-split at the line, resolved a little below it. Their numbers are
   read from the scene itself at runtime, not hard-coded.
5. **The butterfly.** It flies to a branch tip, settles, and bolts if the
   pointer gets within about 115 px of it on screen. After a loiter it picks a
   different perch.

Everything the pointer does feeds one input model: parallax drift, drag-orbit,
a particle trail, card hover, and the butterfly's startle test.

| Input | Effect |
| --- | --- |
| Move | Parallax orbit, particle trail, hover states |
| Drag | Take over the orbit |
| Scroll | Dolly, clamped |
| Click a card | Reload that card's scan |
| `R` | Reload every card |
| `Esc` | Skip the intro |
| Run the scan | Replay the whole intro |

## How it is put together

One geometry attribute does most of the work. Every vertex on the branch carries
`aGrow` — its normalised path distance from the root — and each of the three
reveal stages thresholds against that same number. That is why they read as one
continuous growth rather than three separate effects.

The nav bar and the buttons are real DOM elements with transparent backgrounds.
Their fills are WebGL quads in an orthographic overlay scene, positioned from
each element's `getBoundingClientRect()`. Text stays crisp and accessible; the
material behind it is a shader.

Card imagery is rendered to a texture at startup by a fragment shader, so the
page ships no image files. Card labels are 2D canvas, because that is the one
thing a fragment shader has no business drawing.

## Dependencies and degradation

Three.js r169 is loaded from jsDelivr through an import map — the page needs
network access on first load and does not work offline. The post-processing
addons are imported dynamically inside a `try`; if they fail, bloom is skipped
and the additive passes carry the glow on their own.

- No WebGL: a message replaces the scene.
- `prefers-reduced-motion`: the intro is skipped to its end state.
- Under 42 fps after three seconds: device pixel ratio steps down to 1 once.

## Notes

- The branch is grown from a fixed seed (`CFG.seed`), so it is identical on
  every load. The intro is choreographed against that geometry.
- The copy is placeholder. The card values are real measurements of the scene.
- `window.__m4d` exposes the scene, camera, cards, butterfly and camera rig for
  poking at from the console.
