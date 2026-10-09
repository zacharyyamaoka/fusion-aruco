# Research: prior art for an ArUco / ChArUco generator in Fusion

Date: 2026-10-02. Every URL below was checked and loads.

Question: does anything already do what Zach wants (decal or in-place markers, unique auto IDs, smart dictionary and size choices, assembly-wide ID handling)?

## Hits

| Name | Platform | Output | Dictionaries | IDs | Status |
|---|---|---|---|---|---|
| [Code Generator – QR, Barcode, AprilTag, and ArUco](https://marketplace.autodesk.com/apps/d8d3a13e-0de0-4f9c-adbb-528fd276cca9) ("Toby") | Fusion add-in, paid | Sketch or bodies, on a planar face or plane; optional own component | All OpenCV ArUco (4x4_50 to 7x7_1000), AprilTag 16h5/25h9/36h10/36h11, plus QR and barcodes | One ID typed by hand, "validated against the selected dictionary" | v1.0.3, 2026-08-11. $24.99, closed source, 0 reviews |
| [arnoldad/apriltag3D](https://github.com/arnoldad/apriltag3D) | Fusion add-in | Re-editable Custom Feature solid (white and black bodies), Joint Origin at the tag face, socket cutter | All 9 AprilTag families, no ArUco | One ID typed by hand | 2026-06-03, MIT, 0 stars, 18 open issues |
| [wglockner/ArUco-Generator-Fusion360-ADD-IN](https://github.com/wglockner/ArUco-Generator-Fusion360-ADD-IN) | Fusion add-in | OpenCV PNG inserted as a **Canvas** (not a decal, not geometry) | 6 ArUco dicts; marker, GridBoard or ChArUco; border bits | One ID. Its "Start ID" only goes into a caption | 2025-09-08, MIT. Likely broken: calls OpenCV APIs that were removed |
| [Revise-Robotics/fusion360-apriltag-3d-code-generator](https://github.com/Revise-Robotics/fusion360-apriltag-3d-code-generator) | Fusion script | Two-color solid | tag36h11 | Input box | 2026-04-22, no license |
| [jamesemerick/Fusion360AprilTag2CAD](https://github.com/jamesemerick/Fusion360AprilTag2CAD) | Fusion add-in | Solid built from a pre-made PNG | Any PNG | None | 2023-03-10, MIT, 1 star |
| [Wa-Araki/quARtet-marker](https://github.com/Wa-Araki/quARtet-marker) | Fusion add-in ([paper](https://arxiv.org/abs/2610.01072)) | One four-tag marker, built from a JSON file the detector also reads | tag36h11 | Fixed IDs 0-3 | 2026-10-02, MIT |
| QR relatives: [tapnair/QRCoder](https://github.com/tapnair/QRCoder), [QR Code Creator Pro](https://marketplace.autodesk.com/apps/abd2172e-a563-47d6-8c32-336f56c961f1), [JoshWaldner/Autodesk-Fusion-QR-Code](https://github.com/JoshWaldner/Autodesk-Fusion-QR-Code) | Fusion add-ins | Extruded QR blocks | QR only | Pro can make "a sequence of QR codes" | QRCoder 2021, MIT, 22 stars. Pro $4.99/month |
| [ZHN0019/SolidworksArucoGenerator](https://github.com/ZHN0019/SolidworksArucoGenerator) | SolidWorks 2025 add-in | Two-body part, PNG, STEP | DICT_4X4_50, IDs 0-30 | Hand-typed | 2026-07-25, no license |
| [dogadogan/BrightMarker](https://github.com/dogadogan/BrightMarker) | Blender add-on (MIT CSAIL) | Embeds many markers into one mesh | Bundled ArUco set | **Sequential IDs from a start ID**, on picked faces or spread evenly | 2024-07-18, MIT, 6 stars |
| [dogadogan/InfraredTags](https://github.com/dogadogan/InfraredTags) | Rhino 6 + Grasshopper | Hides a QR or ArUco SVG inside a print | From SVG | None | 2023-11-18, MIT, 36 stars |
| [younghyopark/aprilcube](https://github.com/younghyopark/aprilcube) | Python CLI | Multi-color 3MF cube with a tag per face, plus detector config | ArUco, AprilTag 36h11 | Auto IDs within one cube | 2026-09-08, MIT (README), 121 stars |
| [MakerWorld "ArUco Markers Generator"](https://makerworld.com/en/models/2801571-aruco-markers-generator) | OpenSCAD | Two-part printable tiles | 10 ArUco/AprilTag dicts | First-to-last ID grid | 2026-05-14, CC BY-NC-SA, 124 customizations |
| Other printables: [imontesino/3D-aruco-generator](https://github.com/imontesino/3D-aruco-generator) (CadQuery, AGPL, 19 stars), [arpruss OpenSCAD](https://www.thingiverse.com/thing:6778951) (CC BY), [ShrishChou/CAD_Engraver](https://github.com/ShrishChou/CAD_Engraver), FreeCAD macros [ataerdem](https://github.com/ataerdem/Aruco-QR-STL-Generator) and [fpadula](https://github.com/fpadula/aruco_cube_generator) | Python, OpenSCAD, FreeCAD | STL or STEP files | Varies | One ID per run | Small, mostly 0 stars |
| [chev.me/arucogen](https://chev.me/arucogen/) | Web | SVG, PDF | 10 dicts | One ID | MIT, 590 stars |
| [calib.io generator](https://calib.io/pages/camera-calibration-pattern-generator) | Web | PDF | ChArUco, AprilGrid | "Start ID" offset | Non-commercial only |
| [lyehe/aruco_3d](https://github.com/lyehe/aruco_3d) | Web | 3MF, SVG, PNG, JSON | ArUco, AprilTag, ChArUco boards | ID lists | AGPL, 12 stars |

Sheet generators ([hayyazul](https://github.com/hayyazul/apriltag-generator), [o16s](https://github.com/o16s/aruco-tag-generator), [a-nooj](https://github.com/a-nooj/tag-youre-it)) pack ID ranges onto printable pages. No generator found exports DXF.

## Not hits (checked and ruled out)

Blender "Face Tag" (text labels). BenHNW2002/ArUcoBlenderGenerator (renders video, makes no markers). Taarun-Srinivas cube generator (AR overlay). CGrassin/kicad-auto-silkscreen (reference designators). No marker tool turned up for KiCad, Onshape or Inventor; Onshape has only a QR custom feature. The parth3d OpenSCAD page is now 404.

## Demand signals (forum wishes, not tools)

No thread asks for ArUco in Fusion by name; a forum-wide "aruco" search finds a handful of posts, none about Fusion. Reddit was not searchable from here. Nearby asks:

- [Fusion API forum, 2025](https://forums.autodesk.com/t5/fusion-api-and-scripts-forum/cutting-qr-code-stuck-in-floating-point-hell/m-p/13652607): an engineer at a 3D-print firm tried to build "stamp any face with a code" and got stuck. Pixel rectangles made extra closed profiles, and the code came out flipped. No accepted solution.
- [Fusion API forum, Jan 2025](https://forums.autodesk.com/t5/fusion-api-and-scripts-forum/qr-code-add-in/m-p/13264781): Brian Ekins told a QR add-in author to union TemporaryBRep boxes, not extrude each pixel.
- [Fusion Electronics forum, Nov 2024](https://forums.autodesk.com/t5/fusion-electronics-forum/shortcut-to-add-file-name-version-number-to-pcb-and-request-for/m-p/13170872): a request for QR codes on PCB silkscreen.
- [Fusion French forum, Oct 2025](https://forums.autodesk.com/t5/fusion-forum-francais/probleme-ouverture-svg-d-un-qr-code-instalation-de-pyqrcode-pour/m-p/13871483): a user could not install `pyqrcode` for QRCoder. Pip dependencies hurt.
- apriltag3D's open issues ask for ArUco (#7), batches (#5), and a live detection-range estimate (#13).

## Verdict

**Partly covered. Nothing covers the wishlist.** Drawing a marker is solved many times. No tool found, in any CAD package, does ID bookkeeping.

- **Closest: Code Generator.** It has the dictionaries and puts a sketch or body on a face. It misses unique or auto IDs, assembly awareness, decals, "convert this rectangle", ChArUco, border bits, suggestions, and a re-editable feature (its docs show only a timeline group). Closed source.
- **Best open base: apriltag3D.** It has the Custom Feature and the Joint Origin. It lacks ArUco, batches and ID logic.
- Auto-numbering exists only inside one object (BrightMarker, aprilcube, ID-range grids). Nothing tracks IDs across a design, a component inserted twice, or an assembly. Nothing suggests a dictionary or a minimum size.

## Code worth borrowing

1. **apriltag3D (MIT), the main donor.**
   - `apriltag_builder.py`: TemporaryBRep boxes, unioned into a BaseFeature, with touching cells merged (`_find_clusters`). This is Ekins' fast path, and it skips the profile trap.
   - `apriltag_feature.py`: the Custom Feature wrapper. Fill a BaseFeature, then wrap it with `customFeatures.add`. Values live in custom parameters and attributes. Read its issues #1, #2 and #17 first; they record Fusion gotchas.
   - Joint Origin (about line 1252): face center, Z into the tag, named `<family> id=<n> origin`. Named frames like this make it cheap to find every marker and its ID.
   - `apriltag_render.py` and `apriltag_families.py`: pure-Python AprilTag bit matrices. No OpenCV.
2. **arucogen `dict.json` (MIT):** all 10 code tables as JSON. The smaller OpenCV dictionaries are the first N entries of the _1000 tables. Unpack the last partial byte with care; a MakerWorld reviewer found that bug broke two tag36h11 IDs. Check every ID against OpenCV.
3. **wglockner (MIT):** only the dialog layout (Marker / GridBoard / ChArUco, border bits) and the Canvas insert. Skip its OpenCV bootstrap.
4. **jamesemerick (MIT):** a pure-stdlib PNG reader, if we ever import images.

Do not copy Revise-Robotics, ZHN0019 or CAD_Engraver (no license), or Code Generator (closed).
