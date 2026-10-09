# Research: architecture donors for a Fusion ArUco / ChArUco add-in

Date: 2026-10-02. API facts were checked on help.autodesk.com or in donor source code. Anything else is marked (unverified).

## Key API facts (read this first)

- **Custom Features are still Preview.** `CustomFeatures`, `CustomFeatureDefinition` and `CustomFeature` all carry the preview banner (since January 2021). Autodesk says not to ship preview APIs. Recompute works in "one specific case": the feature holds only base features, sketches and combine features, and compute may only edit base-feature bodies, sketch contents and parameters. `addDependency` accepts bodies, faces, edges, vertices, sketches, sketch entities, profiles, construction geometry and parameters. So a feature that owns a generated sketch plus bodies and rebuilds when the user's rectangle moves is allowed on paper. No donor ships that shape. In Part designs, custom features get only right-click > Configure, not Edit Feature (apriltag3D issue #18).
- **Decals are released API** (September 2024). The collection hangs off the component: `comp.decals.createInput(imagePath, [face], pointOnFace)` returns a `DecalInput` (`faces`, `isChainFaces`, `opacity`, `transform`, `targetBaseFeature`), then `comp.decals.add(input)` returns a `Decal`. The image must be a PNG, JPEG or TIFF file. Size, rotation and flip all ride on `transform`, whose origin must sit on the first face. There is no width field. After creation, `imageFilename` and `opacity` can be set directly. Faces and transform change only via `Decal.redefine()`, which needs the timeline marker placed just before the decal. Proxy-face input is not documented (unverified). A forum report (Jan 2025) says the API fails in direct-modeling designs and sometimes ignores `transform`.
- **Occurrence attributes are stored per assembly path.** A Fusion developer explains: "The attributes of occurrence are saving into its target component." An attribute written on the proxy `root > A:1 > B:1` lives in the root and is invisible from `A`'s own `B:1`. So per-instance IDs work if we write them on occurrence proxies from the top design. Attributes on a component, or on its native sketch entities, are shared by every instance.
- Attribute values are strings (use JSON). `design.findAttributes(group, name)` accepts exact names or `re:` regex. `attr.otherParents` lists the pieces after a split. `design.findEntityByToken(token)` resolves `entityToken` and returns a list.

## 1. Sketch-geometry generators

Only apriltag3D, ParametricText and TabGen regenerate on edit.

- [Voronoi](https://github.com/hanskellner/Fusion360Voronoi). **Adopt.** `addSelectionInput` with filters `Sketches`, `Profiles`, `PlanarFaces`. A face gets `sketches.add(face)` plus `sketch.project(face)`. A palette edits the pattern. Publish writes an SVG, sets `isComputeDeferred`, calls `sketch.importSVG(path, x_cm, y_cm, scale)`, then unfixes the points the import pins. One-shot.
- [QRCoder](https://github.com/tapnair/QRCoder). **Adapt** for solids. Takes a `SketchPoint` as center. Builds cells with `TemporaryBRepManager.createBox` + `booleanOperation`. Previews with `CustomGraphicsGroup.addBRepBody`. Commits via `baseFeatures.add()`, `startEdit`, `bRepBodies.add(body, baseFeature)`, `finishEdit`, then `combineFeatures`. One-shot.
- [apriltag3D](https://github.com/arnoldad/apriltag3D). **Adapt.** A custom feature wraps a base feature. Compute rebuilds via `baseFeature.updateBody`. Edit runs through `editCommandId`. State is kept in `addCustomParameter` values, because attribute writes "lag the compute fire by one transaction" (issue #17). It never calls `addDependency`; tags sit at a new component's origin. Its stated issues: severed body refs (#2), Part-mode edit (#18). Pure stdlib.
- [ParametricText](https://github.com/thomasa88/ParametricText). **Adopt.** Regenerates without custom features. It hooks `ui.commandTerminated` and switches on `commandId` (`ChangeParameterCommand`, `SketchEditDimensionCmdDef`, `FusionPasteNewCommand`, `RenameCommand`, and more), plus `documentOpened` and `documentSaving`. It then runs a hidden `isAutoExecute` command so all edits are one undo step.
- [Image2Surface](https://github.com/hanskellner/Fusion360Image2Surface). **Adopt the split.** JS in the palette processes the image and sends OBJ text. Python calls `meshBodies.add(path, units, baseFeature)`.
- [SpurGear](https://github.com/AutodeskFusion360/SpurGear). Command-dialog skeleton only: no selection input, no attributes, no regen.
- **Avoid:**
  - [Revise-Robotics](https://github.com/Revise-Robotics/fusion360-apriltag-3d-code-generator): an `inputBox` script. Borrow its moms_apriltag table and appearance trick.
  - [wglockner ArUco](https://github.com/wglockner/ArUco-Generator-Fusion360-ADD-IN): pip-installs OpenCV at runtime and places a canvas.
  - [TabGen](https://github.com/hobbyistmaker/TabGen): abandoned; uses parameter expressions, which cannot change topology.
  - [WrapSketch](https://github.com/hanskellner/Fusion360WrapSketch): cylinder-only work in progress.

## 2. Custom Features

**Adapt, opt-in only.** The flow is `CustomFeatureDefinition.create(id, name, icons)` at startup, then `comp.features.customFeatures.createInput(def)`, `addDependency`, `addCustomParameter`, `setStartAndEndFeatures`, `add`. apriltag3D adds two lessons. Register the edit command before setting `editCommandId`. Wrap the call in try/except and fall back to a plain base feature. Autodesk samples: RoundEmbossExample.zip and CustomPocketExample.zip (Custom Features guide).

## 3. Decals and canvases

**Decals: adopt for image mode.** Write the PNG with stdlib `zlib` + `struct`. Measure the default decal size once, then scale through `transform`. A decal inside a shared component shows on every instance, and it is appearance only, never physical. **Canvases: avoid.** `Canvases.createInput(imageFilename, planarEntity)` (May 2023) makes a tracing image, not a marker.

## 4. Attributes as ID registry

**Adopt ParametricText's layout.**

- Registry on `design.attributes`: `nextId`, `storageVersion`, one value per ID.
- A back-pointer tag on each native entity.
- Lookup with `findAttributes` across every `Design` product in the document, reading `parent` and `otherParents`.
- A migration command for old storage versions.

Copy/paste behavior is undocumented (unverified). ParametricText re-runs its attribute lookup after `FusionPasteNewCommand` to refresh the pasted copy's texts. That suggests attributes travel with Paste New, so expect duplicated IDs after paste. Dedupe by keeping `markerId -> entityToken`: the entity whose token does not match is the copy. B-Rep attributes are never auto-deleted, so sweep orphans (`parent` is None).

## 5. Assembly traversal

**Our own code; no donor handles per-instance IDs.**

- `root.allOccurrences` covers all levels in root context. `comp.allOccurrencesByComponent(c)` gives the instance count. `occ.fullPathName` serves as a key.
- Proxies: `createForAssemblyContext`, `nativeObject`, `assemblyContext`. [Dogbone2](https://github.com/pludikar/dogbone2) shows the habit.
- Xrefs: check `occ.isReferencedComponent`. `occ.documentReference` gives `dataFile` and `isOutOfDate`. External components are read-only in the host. The design-intent guide adds that the API does not automatically "Edit in Place". So xref instance IDs go on host-side occurrence attributes (unverified for xrefs).
- No insert event exists. `documentOpened` fires only for the top-level document. Rescan on open, save and `commandTerminated`. Paste New's id is known; the insert command's id is unverified, so log ids to find it.
- Design intent (January 2026, `design.designIntent`): Part designs have no occurrences. Assembly designs allow no sketches or features. So generate markers in parts, keep the instance-ID map in the assembly, and treat Hybrid as before.

## 6. HTML palettes

**Adopt with quirks handled.**

- Python: `ui.palettes.add(id, name, htmlFileURL, isVisible, showCloseButton, isResizable, width, height)`. The engine is always Qt WebEngine now; `useNewWebBrowser` is ignored. `incomingFromHTML` gives `HTMLEventArgs` (`action`, `data`, `returnData`). `sendInfoToHTML(action, data)` goes the other way.
- JS: `adsk.fusionSendData` returns a Promise. `window.fusionJavaScriptHandler.handle` must return a non-empty string.
- Quirks:
  - Messages sent at creation are lost, so Voronoi waits for a `started` ping.
  - Docking lost the palette, so Voronoi disables it.
  - Handlers run on the main thread, so slow work freezes the page.
  - Palettes die on a workspace switch.
- Donors: Autodesk's add-in template (`paletteShow`/`paletteSend`), Voronoi, Image2Surface, [FusionReactr](https://github.com/tapnair/FusionReactr) (React, work in progress). No Vue or Vite donor turned up.

## 7. Packaging

**Adopt stdlib plus JSON tables.**

- Fusion moved from Python 3.12 to 3.14 in January 2026. Ship `.py`; compiled wheels break on each upgrade.
- apriltag3D ships families as a 3.4 MB `.py`. [moms_apriltag](https://github.com/MomsFriendlyRobotCompany/moms_apriltag) has plain-tuple AprilTag tables but no ArUco tables, and its package needs numpy and OpenCV, so copy the tables rather than installing it.
- If we must vendor, apper's `lib_import` puts `./lib` on `sys.path` temporarily.
- Export OpenCV's ArUco dictionaries to JSON once, offline.

## 8. Outside Fusion

- **Onshape** [QR Code feature](https://forum.onshape.com/discussion/22768/qr-code-feature): regenerates by design, keeps geometry off until asked, and finds cut far faster than emboss. A thread titled "QRCode won't extrude because of barely touching corner?" names the diagonal-cell risk.
- **KiCad** [image injector](https://github.com/hoijui/kicad-image-injector): replaces user-drawn placeholder rectangles with QR pixels. Keep our tagged rectangle as source of truth.
- **Blender** [UUID thread](https://devtalk.blender.org/t/depsgraph-updates-and-basic-uuid-support-using-custom-properties/21223): duplicates copy their IDs, so detect copies by scanning.

## Gotchas

1. **Undo:** edits from palette or event handlers scatter across undo. Route them through a hidden auto-execute command. Group with `timeline.timelineGroups.add`.
2. **Speed:** set `isComputeDeferred` while drawing. Docs warn of "weird results" when editing a sketch that features depend on while deferred, so roll the timeline back first. Prefer one `importSVG`, or merged cell runs. Bodies via TBM skip sketches.
3. **Profiles:** pick black cells by centroid against the bit grid. Corner-only contacts make non-manifold solids, so cut white cells or merge outlines. `importSVG` adds fix constraints.
4. **Units:** the API works in cm. Display in `unitsManager.defaultLengthUnits`.
5. **Edit commands:** capture the selection at `commandStarting` (apriltag3D). Keep handler references alive.
6. **Design type:** check `designType` and `designIntent`. Decals need parametric designs.
7. **Local HTTP:** fine on a worker thread that never touches the Fusion API. Hand results back with `registerCustomEvent` + `fireCustomEvent`, as FusionMCPSample does for its localhost server. Reading Studio's JSON files needs no thread. Palette `fetch()` to localhost may hit CORS (unverified).

## Recommended reference architecture

| Layer | Copy from |
|---|---|
| Command UI | Autodesk add-in template + apriltag3D dialog (preview image, ranges follow dictionary) |
| Input selection | Voronoi selection filters; face becomes sketch |
| Geometry | Voronoi `importSVG` for sketches; QRCoder/apriltag3D TBM + base feature for emboss or cut; Decals for image mode |
| Regeneration | ParametricText event hooks + KiCad placeholder idea; custom feature only as opt-in "live (preview API)" |
| ID registry | ParametricText attribute layout + `entityToken` copy detection |
| Assembly uniqueness | Own code: occurrence-proxy attributes from the top design, `allOccurrencesByComponent`, xref checks, rescans |
| Palette | Voronoi handshake + Image2Surface split (FusionReactr if React) |
| External sync | Read Studio JSON files; any HTTP via the FusionMCPSample thread + custom-event pattern |
