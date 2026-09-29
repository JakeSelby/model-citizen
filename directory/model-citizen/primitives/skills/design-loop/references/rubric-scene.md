# Scene rubric

For 3D scenes, game views, rendered assets and Blender output.

## Scored axes — 10 total

### Composition and scale (0-3)

Camera position, framing and focal length. Silhouette readability — the strongest single predictor
of whether an asset reads well. Proportion within an asset and between assets.

**Real-world scale is judged here, not assumed.** A vehicle that is subtly too large relative to a
building is a composition failure even when every surface looks correct.

### Lighting and atmosphere (0-3)

Colour palette, exposure, shadow softness and direction, contrast, ambient occlusion. Reflections,
speculars, glows and bloom where the target has them. Atmospheric depth — fog, haze, aerial
perspective — doing distance separation.

Common failure: the scene reads uniformly too dark or too flat against the target. Check overall
exposure before chasing individual materials.

### Materials and texture (0-2)

Every surface should read as the substance it is. Roughness variation, wear, edge damage,
translucency, wetness. Normal and roughness maps present where they matter.

Losing points: blocky, plasticky, uniformly smooth or flatly-coloured surfaces — unless the target
deliberately does that too. Procedural noise standing in for a real texture is a fail, not a
shortcut.

### Readability at game zoom (0-2)

**The axis a hero shot will not give you.** Judge the scene at the actual zoom levels the player
uses, not only at the framing that flatters it.

Does the silhouette still read? Does the detail survive, or does it turn to mush? Does it turn to
noise and shimmer? Do assets remain distinguishable from each other at a glance? A scene that
scores 8 on the first three axes and 0 here is not shippable.

## Hard gates — pass/fail, not scored

- **Runtime budget met** at the target resolution and zoom. Frame time and draw calls measured on
  the composed scene, not estimated per asset.
- **Every asset has a recorded qualifying licence**, a third-party manifest entry and shipped
  notices. See the chosen `licensing` stance and any path-scoped asset rule the project carries.
  Unresolved material fails this gate and stays out.
- **Real-world scale preserved.** Where the target and scale disagree, scale wins.
- **LOD present** where the class requires it, and LOD transitions do not pop visibly.
- **Style coherent with existing assets.** Imported assets are normalised to our direction rather
  than mixing styles. One photoreal asset in a stylised set is a regression, however good it is.

## Notes

Optimise only after the score clears the bar, then **re-judge** — lossless wins first, then
changes with minimal visual cost. An optimisation pass that quietly drops the score has not
succeeded.

Label evidence **Mock**, **Renderer**, or **Both**. A pretty offline render proves nothing about
the game. Sourcing an asset is not by itself a visual upgrade.
