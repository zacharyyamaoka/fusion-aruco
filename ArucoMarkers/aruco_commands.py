"""The three toolbar commands, in an ARUCO panel on the Solid tab.

    ArUco Tag     pick a closed sketch profile (-> ArUco Extrude: tile + tag)
                  or a planar face of a body you modelled (-> tag that face).
                  Single marker, grid board or ChArUco board.
    ArUco IDs     pick the assembly level (the design, or a sub-assembly such
                  as Arm) and give each copy under it its own id, by hand or
                  with "Number duplicates". OK writes the overrides at that
                  level and rebuilds the per-copy tag parts.
    ArUco Export  one type per export, picked at the top (like Fusion's Save
                  As Mesh): PrusaSlicer project, STL files or PDF print
                  sheet, into ~/CADExporter/<design>/; each also writes the
                  tag records (aruco.json, joint_origins.json).

Every model change happens inside a command's execute, so each command is one
undo step. Handlers re-import the worker modules, so the dev bridge's
hot-reload covers the dialogs too.

Every dialog remembers its options (aruco_settings: one JSON file in
~/CADExporter, never a Fusion attribute): a dialog opens with what was there
at the last OK, and Cancel remembers nothing. Selections, the ArUco Tag id
(next free) and the tag name always start fresh.
"""
from __future__ import annotations

import importlib
import os
import traceback

import adsk.core
import adsk.fusion

import aruco_core
import aruco_settings
import aruco_tags

importlib.reload(aruco_settings)    # the dev bridge reloads this module, not that one

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL_ID = "bamArucoPanel"
TAB_ID = "SolidTab"
WS_ID = "FusionSolidEnvironment"
# Fusion holds event handlers weakly: drop the last Python reference and the
# button silently does nothing. The dev bridge re-imports this module on every
# request, which would rebind a plain `_handlers = []` -- so keep the list
# that is already there.
try:
    _handlers  # noqa: B018
except NameError:
    _handlers = []


def _survives_reload(cls):
    """The same for a dialog's state class (_EditState, _IdsState,
    _ExportState): a bridge request -- even a ping -- re-imports this module,
    possibly while that dialog is open, and a fresh empty class would make its
    OK act on nothing. Carry the old class's values over."""
    old = globals().get(cls.__name__)
    if isinstance(old, type) and old is not cls:
        for k, v in vars(old).items():
            if not k.startswith("__"):
                setattr(cls, k, v)
    return cls

LAYOUT_LABELS = [("Single marker", "single"), ("Grid board", "grid"),
                 ("ChArUco board", "charuco")]
# Where the black layer sits. Inlaid: cut into the tile, top flush with the
# face (one flat surface, the default for two-colour prints). Raised: on top
# of the face, nothing cut. Half: half of each.
# Where the white comes from when a RECTANGLE is picked (aruco_tags.TILE_MODES).
TILE_LABELS = [("Auto (inlay if drawn on a body)", "auto"),
               ("Inlay into the body under it", "inlay"),
               ("Raised tile joined to the body", "join"),
               ("Separate tile body", "separate")]
SITS = [("Inlaid (flush with the face)", "inlaid"), ("Raised (on top of the face)", "raised"),
        ("Half in, half out", "half")]


def _split_black(thickness_m, sits):
    """(depth_m, raise_m) for a black layer of `thickness_m`."""
    if sits == "raised":
        return 0.0, thickness_m
    if sits == "half":
        return thickness_m / 2.0, thickness_m / 2.0
    return thickness_m, 0.0


def _sits_of(spec):
    if spec.raise_m and not spec.depth_m:
        return "raised"
    if spec.raise_m and spec.depth_m:
        return "half"
    return "inlaid"


def _mm(cm):
    """A length in Fusion's internal cm -> a ValueInput expression in mm."""
    return adsk.core.ValueInput.createByString(f"{round(cm * 10.0, 6):g} mm")


def _add_black_inputs(inputs, black_cm=0.06, sits="inlaid"):
    inputs.addValueInput("black", "Black thickness", "mm", _mm(black_cm))
    _dropdown(inputs, "sits", "Black sits", SITS, sits)


def _black_from(inputs):
    return _split_black(inputs.itemById("black").value / 100.0,     # cm -> m
                        _dd_value(inputs.itemById("sits"), SITS))
PAGES = [("US Letter", "letter"), ("A4", "a4"), ("US Legal", "legal"),
         ("A3", "a3"), ("Tabloid", "tabloid"), ("A5", "a5")]
ORIENT_LABELS = [("Flat: pattern down (auto)", "flat"), ("As modelled", "cad")]
ROTATE = [("0°", 0), ("90°", 90), ("180°", 180), ("270°", 270)]
# dropdowns remembered by their value code, so renaming a label keeps memory
TABLES = {"layout": LAYOUT_LABELS, "tile": TILE_LABELS, "sits": SITS, "page": PAGES,
          "orient": ORIENT_LABELS, "rotate": ROTATE}

TAG_CMD, EDIT_CMD, IDS_CMD, EXPORT_CMD, SEND_CMD = (
    "bamArucoTag", "bamArucoEdit", "bamArucoIds", "bamArucoExport", "bamArucoSendPrusa")


# Zach (2026-10-08): at Fusion's default width every label was cut off
# ("Auto si...", "Dictior..."). A wider first size for every ArUco dialog.
DIALOG_SIZE = (440, 600)
DIALOG_MIN = (380, 380)


def _size(cmd, size=DIALOG_SIZE):
    try:
        cmd.setDialogInitialSize(*size)
        cmd.setDialogMinimumSize(*DIALOG_MIN)
    except Exception:                              # noqa: BLE001
        pass


def _ui():
    return adsk.core.Application.get().userInterface


def _fail(where):
    _ui().messageBox(f"ArUco Tags — {where} failed:\n\n{traceback.format_exc()}")


def _log(msg):
    try:
        adsk.core.Application.get().log(msg)
    except Exception:                              # noqa: BLE001
        pass


# ---------------------------------------------------- remembered options

def _design_key():
    """(settings key, document name) of the active design: its file id once
    saved, else its name -- or (None, None) when there is no design."""
    try:
        doc = aruco_tags.design().parentDocument
        fid = None
        try:
            if doc.isSaved and doc.dataFile is not None:
                fid = doc.dataFile.id
        except Exception:                          # noqa: BLE001
            fid = None
        return aruco_settings.design_key(doc.name, fid), doc.name
    except Exception:                              # noqa: BLE001
        return None, None


def _recall(cmd_id, design=False):
    """The dialog's remembered values ({} when nothing or unreadable)."""
    try:
        return aruco_settings.recall(cmd_id, _design_key()[0] if design else None)
    except Exception:                              # noqa: BLE001
        _log("ArUco Tags: remembered options not read\n" + traceback.format_exc())
        return {}


def _restore(inputs, cmd_id, keep, design=False, values=None):
    """Put the remembered values of the inputs in `keep` into the dialog.
    -> everything recalled (callers use the extras, e.g. Export's parts)."""
    values = _recall(cmd_id, design) if values is None else values
    try:
        aruco_settings.apply(inputs, {k: v for k, v in values.items() if k in keep}, TABLES)
    except Exception:                              # noqa: BLE001
        _log("ArUco Tags: remembered options not applied\n" + traceback.format_exc())
    return values


def _remember(cmd_id, inputs, keep, design_ids=(), design_extra=None):
    """Save the dialog's options (called from execute = OK only). `keep` go
    to the dialog's last-used set, `design_ids` + `design_extra` to the
    active design's own section. Never stops the command."""
    try:
        vals = aruco_settings.snapshot(inputs, tuple(keep) + tuple(design_ids), TABLES)
        dvals = {k: vals[k] for k in design_ids if k in vals}
        dvals.update(design_extra or {})
        key, name = _design_key() if dvals else (None, None)
        aruco_settings.remember(cmd_id, {k: vals[k] for k in keep if k in vals},
                                design=key, design_values=dvals or None, design_name=name)
    except Exception:                              # noqa: BLE001
        _log("ArUco Tags: options not remembered\n" + traceback.format_exc())


def _reset_button(inputs):
    b = inputs.addBoolValueInput("reset", "Reset to defaults", False, "", False)
    b.tooltip = ("Put every option back to the add-in's defaults. They are "
                 "remembered when you press OK; Cancel keeps what was remembered.")
    return b


def _all_inputs(args):
    """The command's top-level inputs, also when the change was in a group."""
    try:
        return args.input.parentCommand.commandInputs
    except Exception:                              # noqa: BLE001
        return args.inputs


def _dropdown(inputs, cid, label, items, selected):
    dd = inputs.addDropDownCommandInput(
        cid, label, adsk.core.DropDownStyles.TextListDropDownStyle)
    for text, value in items:
        dd.listItems.add(text, value == selected)
    return dd


def _dd_value(dd, items):
    name = dd.selectedItem.name if dd.selectedItem else items[0][0]
    return dict(items).get(name, items[0][1])


# ================================================================ ArUco Tag

# remembered; NOT the target, the id (always the next free one) or the name
TAG_KEEP = ("layout", "dictionary", "cols", "rows", "tile", "thickness", "black",
            "sits", "grow", "quiet", "rotate")


def _tag_defaults():
    """The dialog's own defaults (lengths in cm), for a first run and Reset."""
    return {"layout": "single",
            "dictionary": aruco_core.module("markers").DEFAULT_DICTIONARY,
            "cols": 3, "rows": 2, "tile": "auto", "thickness": 0.3, "black": 0.06,
            "sits": "inlaid", "grow": False, "quiet": 1.0, "rotate": 0}


def _fresh_marker_id(inputs):
    """The ID spinner -> the lowest free id (block) for the current type."""
    try:
        spec = _spec_from(inputs)
        first = aruco_tags.next_free_id(spec.dictionary, spec.marker_count)
    except Exception:                              # noqa: BLE001
        first = aruco_tags.next_free_id(inputs.itemById("dictionary").selectedItem.name)
    inputs.itemById("marker_id").value = first


class _TagCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            cmd = args.command
            _size(cmd)
            inputs = cmd.commandInputs
            d = _tag_defaults()
            dicts = list(aruco_core.module("fiducials").ARUCO_DICTS)
            sel = inputs.addSelectionInput(
                "target", "Rectangle or face",
                "A closed sketch profile (extruded into a tag tile) or a planar "
                "face of a body you already modelled")
            sel.addSelectionFilter("Profiles")
            sel.addSelectionFilter("PlanarFaces")
            sel.setSelectionLimits(1, 1)
            _dropdown(inputs, "layout", "Type", LAYOUT_LABELS, d["layout"])
            _dropdown(inputs, "dictionary", "Dictionary",
                      [(n, n) for n in dicts], d["dictionary"])
            inputs.addIntegerSpinnerCommandInput("marker_id", "ID (first id of a board)",
                                                 0, 2319, 1, 0)
            inputs.addIntegerSpinnerCommandInput("cols", "Columns", 1, 40, 1, d["cols"])
            inputs.addIntegerSpinnerCommandInput("rows", "Rows", 1, 40, 1, d["rows"])
            _dropdown(inputs, "tile", "Tile", TILE_LABELS, d["tile"])
            inputs.addValueInput("thickness", "Tile thickness", "mm", _mm(d["thickness"]))
            _add_black_inputs(inputs, d["black"], d["sits"])
            inputs.addBoolValueInput("grow", "Fuse corner-touching cells (+10 µm)",
                                     True, "", d["grow"])
            inputs.addFloatSpinnerCommandInput("quiet", "Quiet zone (modules)", "",
                                               0.0, 4.0, 0.5, d["quiet"])
            _dropdown(inputs, "rotate", "Rotate pattern", ROTATE, d["rotate"])
            inputs.addStringValueInput("key", "Tag name (optional)", "")
            _reset_button(inputs)
            inputs.addTextBoxCommandInput("info", "", "Pick a rectangle or a face.", 6, True)
            _restore(inputs, TAG_CMD, TAG_KEEP)
            _fresh_marker_id(inputs)               # for the remembered dictionary/type
            _visibility(inputs)
            for ev, h in ((cmd.execute, _TagExecute()), (cmd.inputChanged, _TagChanged())):
                ev.add(h)
                _handlers.append(h)
        except Exception:                          # noqa: BLE001
            _fail("ArUco Tag dialog")


def _visibility(inputs):
    layout = _dd_value(inputs.itemById("layout"), LAYOUT_LABELS)
    for cid in ("cols", "rows"):
        inputs.itemById(cid).isVisible = layout != "single"
    sel = inputs.itemById("target")
    is_profile = bool(sel.selectionCount) and isinstance(sel.selection(0).entity,
                                                         adsk.fusion.Profile)
    inputs.itemById("tile").isVisible = is_profile
    mode = _tile_mode(inputs)
    inputs.itemById("thickness").isVisible = is_profile and mode in ("join", "separate")


def _tile_mode(inputs):
    """The tile mode with 'auto' resolved against what is under the rectangle."""
    mode = _dd_value(inputs.itemById("tile"), TILE_LABELS)
    sel = inputs.itemById("target")
    if mode != "auto" or not sel.selectionCount:
        return mode
    ent = sel.selection(0).entity
    if not isinstance(ent, adsk.fusion.Profile):
        return mode
    body = aruco_tags.profile_frame(ent)[7]
    return "inlay" if body is not None else "separate"


def _spec_from(inputs):
    markers = aruco_core.module("markers")
    return markers.TagSpec(
        dictionary=inputs.itemById("dictionary").selectedItem.name,
        marker_id=inputs.itemById("marker_id").value,
        layout=_dd_value(inputs.itemById("layout"), LAYOUT_LABELS),
        rows=inputs.itemById("rows").value, cols=inputs.itemById("cols").value,
        quiet_modules=inputs.itemById("quiet").value,
        depth_m=_black_from(inputs)[0], raise_m=_black_from(inputs)[1],
        grow_m=markers.CELL_OVERLAP_M if inputs.itemById("grow").value else 0.0)


_RECT_CACHE = {}


def _target_size_cm(entity, spec=None):
    """The space the tag gets: the drawn rectangle, or the face's largest
    uninterrupted rectangle (cached per face and aspect -- it takes ~0.5 s)."""
    if isinstance(entity, adsk.fusion.Profile):
        return aruco_tags.profile_frame(entity)[4:6]
    if spec is None:
        return aruco_tags.face_frame(entity)[4:6]
    key = (entity.entityToken, round(aruco_core.module("markers").tile_aspect(spec), 6))
    if key not in _RECT_CACHE:
        _RECT_CACHE[key] = aruco_tags.face_best_rect(entity, spec)[4:6]
    return _RECT_CACHE[key]


class _TagChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            inputs = args.inputs
            changed = args.input.id
            if changed == "reset":
                aruco_settings.apply(inputs, _tag_defaults(), TABLES)
            _visibility(inputs)
            markers = aruco_core.module("markers")
            spec = _spec_from(inputs)
            if changed in ("dictionary", "layout", "rows", "cols", "reset"):
                inputs.itemById("marker_id").value = aruco_tags.next_free_id(
                    spec.dictionary, spec.marker_count)
                spec = _spec_from(inputs)
            sel = inputs.itemById("target")
            lines = []
            if sel.selectionCount:
                ent = sel.selection(0).entity
                w, h = _target_size_cm(ent, spec)
                spec = markers.fit_board(w / 100.0, h / 100.0, spec)
                lines.append(f"<b>{spec.label()}</b>")
                if isinstance(ent, adsk.fusion.Profile):
                    body = aruco_tags.profile_frame(ent)[7]
                    where = {"inlay": f"cells cut into '{body.name}'" if body else "?",
                             "join": f"tile joined to '{body.name}'" if body else "?",
                             "separate": "a separate tile body"}[_tile_mode(inputs)]
                    lines.append(f"rectangle {w * 10:.1f} × {h * 10:.1f} mm → {where}")
                else:
                    lines.append(f"largest clear rectangle on the face: "
                                 f"{w * 10:.1f} × {h * 10:.1f} mm")
                lines.append(f"black {spec.black_thickness_m * 1000:.2f} mm: "
                             f"{spec.depth_m * 1000:.2f} in, {spec.raise_m * 1000:.2f} up")
                lines.append(f"module {spec.module_m * 1000:.2f} mm · tile "
                             f"{spec.tile_size_m[0] * 1000:.1f} × {spec.tile_size_m[1] * 1000:.1f} mm")
                adv = markers.suggest(spec.marker_count, marker_length_m=spec.marker_length_m)
                lines.append(f"readable to ~{adv['max_distance_m']} m at 1080p/70° "
                             f"(5 px per module)")
                if spec.module_m < 0.0012:
                    lines.append("<span style='color:#c60'>module under 1.2 mm — "
                                 "fine on paper, hard for a 0.4 mm nozzle</span>")
            else:
                lines.append("Pick a closed rectangle (ArUco Extrude) or a planar face.")
            cap = aruco_core.module("fiducials").ARUCO_DICTS[spec.dictionary]
            if spec.ids[-1] >= cap:
                lines.append(f"<span style='color:#c00'>ids {spec.ids[0]}..{spec.ids[-1]} "
                             f"exceed {spec.dictionary} (0..{cap - 1})</span>")
            inputs.itemById("info").formattedText = "<br>".join(lines)
        except Exception:                          # noqa: BLE001
            pass


class _TagExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            inputs = args.command.commandInputs
            _remember(TAG_CMD, inputs, TAG_KEEP)
            ent = inputs.itemById("target").selection(0).entity
            spec = _spec_from(inputs)
            kw = dict(dictionary=spec.dictionary, marker_id=spec.marker_id,
                      key=inputs.itemById("key").value.strip(),
                      quiet_modules=spec.quiet_modules,
                      depth_mm=spec.depth_m * 1000.0, raise_mm=spec.raise_m * 1000.0,
                      rotate_deg=_dd_value(inputs.itemById("rotate"), ROTATE),
                      layout=spec.layout, rows=spec.rows, cols=spec.cols,
                      grow_m=spec.grow_m)
            if isinstance(ent, adsk.fusion.Profile):
                aruco_tags.aruco_extrude(
                    ent, thickness_mm=inputs.itemById("thickness").value * 10.0,
                    tile_mode=_dd_value(inputs.itemById("tile"), TILE_LABELS), **kw)
            else:
                aruco_tags.add_tag(ent, **kw)
        except Exception:                          # noqa: BLE001
            _fail("ArUco Tag")


# =============================================================== ArUco Edit

def _tag_owner(entity, des):
    """Whatever was picked -> (component that MODELS the tag, its records)."""
    if isinstance(entity, adsk.fusion.Occurrence):
        comp = entity.component
    else:
        native = entity.nativeObject if getattr(entity, "assemblyContext", None) else entity
        body = native if isinstance(native, adsk.fusion.BRepBody) else native.body
        comp = body.parentComponent
    a = comp.attributes.itemByName(aruco_tags.G, aruco_tags.VARIANT_ATTR)
    if a is not None:                              # a per-copy part -> its template
        import json
        tmpl = json.loads(a.value).get("template")
        comp = next((c for c in des.allComponents if c.name == tmpl), comp)
    return comp, aruco_tags.read_tag_records(comp)


@_survives_reload
class _EditState:
    comp = None
    records = []


EDIT_KEEP = ("rebuild",)


def _fill_edit(inputs, rec):
    markers = aruco_core.module("markers")
    spec = markers.TagSpec.from_dict(rec.get("spec") or {}, lambda m: None, "tag")
    inputs.itemById("dictionary").listItems.item(
        [i.name for i in inputs.itemById("dictionary").listItems].index(spec.dictionary)
    ).isSelected = True
    inputs.itemById("marker_id").value = spec.marker_id
    inputs.itemById("black").value = spec.black_thickness_m * 100.0          # m -> cm
    sits = _sits_of(spec)
    dd = inputs.itemById("sits")
    for i in range(dd.listItems.count):
        dd.listItems.item(i).isSelected = (dict(SITS)[dd.listItems.item(i).name] == sits)
    inputs.itemById("info").formattedText = (
        f"<b>{spec.label()}</b><br>black {spec.black_thickness_m * 1000:.2f} mm "
        f"({spec.depth_m * 1000:.2f} in, {spec.raise_m * 1000:.2f} up)<br>"
        "Size and position are the face's; edit the tile to change them.")


class _EditCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            cmd = args.command
            _size(cmd)
            inputs = cmd.commandInputs
            dicts = list(aruco_core.module("fiducials").ARUCO_DICTS)
            sel = inputs.addSelectionInput(
                "target", "Tag", "Pick any body of a tag part (tile or black cells), "
                "or the part itself — a copy's tag edits its template")
            sel.addSelectionFilter("SolidBodies")
            sel.addSelectionFilter("Occurrences")
            sel.setSelectionLimits(1, 1)
            inputs.addDropDownCommandInput(
                "key", "Which tag", adsk.core.DropDownStyles.TextListDropDownStyle)
            _dropdown(inputs, "dictionary", "Dictionary", [(d, d) for d in dicts],
                      "DICT_4X4_50")
            inputs.addIntegerSpinnerCommandInput("marker_id", "ID (model value)",
                                                 0, 2319, 1, 0)
            _add_black_inputs(inputs)
            inputs.addBoolValueInput("rebuild", "Rebuild every copy's tag part", True, "", True)
            inputs.addTextBoxCommandInput("info", "", "Pick a tag.", 4, True)
            # dictionary / id / black come from the picked tag, so only this
            _restore(inputs, EDIT_CMD, EDIT_KEEP)
            for ev, h in ((cmd.execute, _EditExecute()), (cmd.inputChanged, _EditChanged())):
                ev.add(h)
                _handlers.append(h)
        except Exception:                          # noqa: BLE001
            _fail("ArUco Edit dialog")


class _EditChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            inputs = args.inputs
            des = aruco_tags.design()
            if args.input.id == "target":
                sel = inputs.itemById("target")
                keys = inputs.itemById("key")
                keys.listItems.clear()
                if not sel.selectionCount:
                    return
                _EditState.comp, _EditState.records = _tag_owner(sel.selection(0).entity, des)
                if not _EditState.records:
                    inputs.itemById("info").formattedText = (
                        f"'{_EditState.comp.name}' has no ArUco tag.")
                    return
                for i, r in enumerate(_EditState.records):
                    keys.listItems.add(r.get("key", "?"), i == 0)
                _fill_edit(inputs, _EditState.records[0])
            elif args.input.id == "key" and _EditState.records:
                name = inputs.itemById("key").selectedItem.name
                _fill_edit(inputs, next(r for r in _EditState.records if r.get("key") == name))
        except Exception:                          # noqa: BLE001
            _fail("ArUco Edit")


class _EditExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            inputs = args.command.commandInputs
            _remember(EDIT_CMD, inputs, EDIT_KEEP)
            if _EditState.comp is None or not _EditState.records:
                return
            depth, rise = _black_from(inputs)
            aruco_tags.set_tag_spec(
                _EditState.comp, inputs.itemById("key").selectedItem.name,
                dictionary=inputs.itemById("dictionary").selectedItem.name,
                marker_id=inputs.itemById("marker_id").value,
                depth_m=depth, raise_m=rise)
            if inputs.itemById("rebuild").value:
                aruco_tags.apply_variants(aruco_tags.design())
        except Exception:                          # noqa: BLE001
            _fail("ArUco Edit")


# ================================================================ ArUco IDs

def _levels(des):
    """(label, component) for every place an override can be authored."""
    out = [(f"Design ({des.rootComponent.name})", des.rootComponent)]
    for comp in des.allComponents:
        if comp == des.rootComponent or aruco_tags.is_variant(comp):
            continue
        if comp.occurrences.count:
            out.append((comp.name, comp))
    return out


def _subtree_components(comp):
    names = {comp.name}
    for o in comp.allOccurrences:
        names.add(o.component.name)
    return names


def _level_resolution(des, comp):
    """The tags as seen FROM `comp`: its subtree, its own and deeper overrides."""
    markers = aruco_core.module("markers")
    raw = aruco_tags.collect(des)
    tags, ovs = markers.tags_from_payload(raw, lambda m: None)
    is_root = comp == des.rootComponent
    inside = _subtree_components(comp)
    own = "" if is_root else comp.name
    ovs = [o for o in ovs if (o.owner or "") == own or o.owner in inside]
    occs = []
    for o in comp.allOccurrences:
        if aruco_tags.is_variant(o.component):
            continue
        occs.append(markers.OccurrenceRef(o.fullPathName, o.component.name, None,
                                          bool(o.isVisible)))
    res = markers.resolve(tags, ovs, occs, comp.name if not is_root else des.rootComponent.name)
    return res, tags, ovs, occs


@_survives_reload
class _IdsState:
    rows = []       # [(rel_path, tag_key, original_id, spinner_id)]
    comp = None
    gen = 0         # input ids must be unique for the dialog's whole life
    cmd = None      # the open dialog (bridge tests drive it)
    last = {}       # the last preview's numbers


def _fill_table(inputs, des, comp, values=None):
    tbl = inputs.itemById("table")
    tbl.clear()
    res, _, _, _ = _level_resolution(des, comp)
    _IdsState.rows, _IdsState.comp = [], comp
    _IdsState.gen += 1
    g = _IdsState.gen
    dups = {i.name for v in res.duplicates().values() for i in v}
    for r, inst in enumerate(res.instances):
        if not inst.path:
            continue
        ci = tbl.commandInputs
        where = ci.addTextBoxCommandInput(f"p{g}_{r}", "", f"{inst.path} · {inst.tag.key}", 1, True)
        val = (values or {}).get((inst.path, inst.tag.key), inst.spec.marker_id)
        spin = ci.addIntegerSpinnerCommandInput(f"i{g}_{r}", "", 0, 2319, 1, val)
        src = inst.winning
        label = ("model" if src.level == "model" else
                 "this level" if src.owner == ("" if comp == des.rootComponent else comp.name)
                 else src.owner or "design")
        if inst.name in dups:
            label = "⚠ duplicate · " + label
        ci_src = ci.addTextBoxCommandInput(f"s{g}_{r}", "", label, 1, True)
        row = tbl.rowCount
        tbl.addCommandInput(where, row, 0)
        tbl.addCommandInput(spin, row, 1)
        tbl.addCommandInput(ci_src, row, 2)
        _IdsState.rows.append((inst.path, inst.tag.key, inst.spec.marker_id, spin.id))
    n = len(_IdsState.rows)
    inputs.itemById("status").formattedText = (
        f"{n} tag(s) under <b>{comp.name}</b>; "
        f"{len(dups)} share a marker with another copy." if dups else
        f"{n} tag(s) under <b>{comp.name}</b>; every copy prints its own marker.")


def _ids_values(inputs):
    """{spinner id: its value} for the table's rows."""
    out = {}
    for _p, _k, _o, sid in _IdsState.rows:
        inp = inputs.itemById(sid)
        if inp is not None:
            out[sid] = int(inp.value)
    return out


def ids_write(des, comp, rows, values):
    """What OK writes: every row whose id changed becomes an override AT the
    dialog's level (`comp`). -> how many. aruco_ids_preview.with_table is
    the same rule without writing."""
    markers = aruco_core.module("markers")
    owner = "" if comp == des.rootComponent else comp.name
    changed = 0
    for path, key, original, spin_id in rows:
        new = values.get(spin_id, original)
        if new == original:
            continue
        parts = markers.split_path(path)
        aruco_tags.write_override(owner, parts[0], markers.slot_key(parts[1:], key),
                                  {"marker_id": int(new)})
        changed += 1
    return changed


# The handlers only call these module functions: the dev bridge re-imports
# this module, and a handler Fusion holds keeps the class it was made from.

def ids_created(cmd):
    import aruco_ids_preview
    aruco_ids_preview.reset()
    _size(cmd)
    inputs = cmd.commandInputs
    des = aruco_tags.design()
    levels = _levels(des)
    dd = inputs.addDropDownCommandInput(
        "level", "Assembly level", adsk.core.DropDownStyles.TextListDropDownStyle)
    for i, (label, _comp) in enumerate(levels):
        dd.listItems.add(label, i == 0)
    inputs.addTextBoxCommandInput("status", "", "", 2, True)
    inputs.addBoolValueInput("auto", "Number duplicates", False, "", False)
    tbl = inputs.addTableCommandInput("table", "Tags", 3, "4:1:2")
    tbl.maximumVisibleRows = 14
    tbl.minimumVisibleRows = 4
    inputs.addBoolValueInput("rebuild", "Rebuild tag parts on OK", True, "", True)
    inputs.addTextBoxCommandInput(
        "legend", "",
        "<span style='color:#a06800'>Preview:</span> every copy shows the marker it will "
        "print. <b style='color:#a06800'>Amber</b> = its own id (a tag part of its own), "
        "white + black = the modelled one.", 2, True)
    # the level is this design's (by name); "rebuild" is everyone's
    _restore(inputs, IDS_CMD, ("level", "rebuild"), design=True)
    picked = dd.selectedItem.name if dd.selectedItem else levels[0][0]
    _fill_table(inputs, des, dict(levels).get(picked, levels[0][1]))
    cmd.setDialogInitialSize(560, 560)
    for ev, h in ((cmd.execute, _IdsExecute()), (cmd.inputChanged, _IdsChanged()),
                  (cmd.executePreview, _IdsPreview()), (cmd.destroy, _IdsDestroy()),
                  (cmd.activate, _IdsActivate())):
        ev.add(h)
        _handlers.append(h)
    _IdsState.cmd = cmd


def ids_changed(inputs, input_id):
    des = aruco_tags.design()
    if input_id == "level":
        levels = dict(_levels(des))
        _fill_table(inputs, des, levels[inputs.itemById("level").selectedItem.name])
    elif input_id == "auto":
        markers = aruco_core.module("markers")
        comp = _IdsState.comp
        res, tags, ovs, occs = _level_resolution(des, comp)
        planned, after = markers.plan_serials(tags, ovs, occs, comp.name)
        values = {(i.path, i.tag.key): i.spec.marker_id for i in after.instances}
        # keep only what can be written AT this level: copies the
        # plan put deeper keep their current number here
        _fill_table(inputs, des, comp, values)


def ids_activate(cmd):
    """activate: the preview's frames, once (aruco_ids_preview.prepare: a
    stale tag record's node is evaluated here, never in executePreview)."""
    import aruco_ids_preview
    des = aruco_tags.design()
    if aruco_ids_preview.prepare(des) and aruco_ids_preview._st().group is not None:
        # a preview drawn before activate used the records as they were
        aruco_ids_preview.show(des, _IdsState.comp, _IdsState.rows,
                               _ids_values(cmd.commandInputs))


def ids_preview(cmd):
    """executePreview: the ghost of what OK will print (aruco_ids_preview);
    draws only."""
    import aruco_ids_preview
    if _IdsState.comp is None:
        return None
    res = aruco_ids_preview.show(aruco_tags.design(), _IdsState.comp, _IdsState.rows,
                                 _ids_values(cmd.commandInputs))
    _IdsState.last = res
    return res


def ids_execute(cmd):
    import aruco_ids_preview
    aruco_ids_preview.reset()
    inputs = cmd.commandInputs
    _remember(IDS_CMD, inputs, ("rebuild",), design_ids=("level",))
    des = aruco_tags.design()
    changed = ids_write(des, _IdsState.comp, _IdsState.rows, _ids_values(inputs))
    report = None
    if inputs.itemById("rebuild").value:
        report = aruco_tags.apply_variants(des)
        if report.get("warnings"):
            _log("ArUco IDs:\n  " + "\n  ".join(report["warnings"]))
    _IdsState.last = {"written": changed, "variants": report}
    return _IdsState.last


def ids_destroy():
    import aruco_ids_preview
    aruco_ids_preview.reset()
    _IdsState.cmd = None


class _IdsCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            ids_created(args.command)
        except Exception:                          # noqa: BLE001
            _fail("ArUco IDs dialog")


class _IdsChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            ids_changed(args.inputs, args.input.id)
        except Exception:                          # noqa: BLE001
            _fail("ArUco IDs")


class _IdsPreview(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            ids_preview(args.command)
        except Exception:                          # noqa: BLE001
            _log("ArUco IDs preview: " + traceback.format_exc())


class _IdsActivate(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            ids_activate(args.command)
        except Exception:                          # noqa: BLE001
            _log("ArUco IDs activate: " + traceback.format_exc())


class _IdsExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            ids_execute(args.command)
        except Exception:                          # noqa: BLE001
            _fail("ArUco IDs")


class _IdsDestroy(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            ids_destroy()
        except Exception:                          # noqa: BLE001
            pass


# ============================================================= ArUco Export

# Zach (2026-10-08): "don't let me export for 3D printing and 2D printing at
# the same time. Copy the way the normal one works: at the top you select
# what type it's going to be" -- Fusion's Save As Mesh: a type list at the
# top (each entry with its icon), below it only that type's options. Every
# type also writes aruco.json + joint_origins.json, the records the URDF
# rebuild reads (aruco_export.KINDS: what else each one writes).
EXPORT_TYPES = [("PrusaSlicer project", "prusaslicer"), ("STL files", "stl"),
                ("PDF print sheet", "pdf")]
TABLES["export_type"] = EXPORT_TYPES          # remembered by its code
# each entry's icon: a resource folder under resources/ (16x16.png, ...)
EXPORT_TYPE_ICONS = {"prusaslicer": "bamArucoSendPrusa", "stl": "bamArucoExportStl",
                     "pdf": "bamArucoExportPdf"}
# the inputs each type shows; the others are hidden and keep their values,
# so every type remembers its own options
EXPORT_SHOWS = {"prusaslicer": ("parts", "orient", "bed_w", "bed_d", "open_slicer"),
                "stl": ("parts", "orient", "open_folder"),
                "pdf": ("page", "landscape", "margin", "open")}
# The dialog's own defaults (lengths in cm), for a first run and Reset (which
# keeps the type). "stl" / "beds" are only read by the dialog of before the
# types (see _legacy_export_execute).
EXPORT_DEFAULTS = {"export_type": "prusaslicer",
                   "page": "letter", "landscape": False, "margin": 1.0,
                   "bed_w": 25.0, "bed_d": 21.0, "orient": "flat",
                   "open": True, "open_slicer": True, "open_folder": True,
                   "stl": True, "beds": True}
EXPORT_KEEP = tuple(EXPORT_DEFAULTS)
# also kept per design (with the ticked parts), and that design's copy wins
EXPORT_PER_DESIGN = ("page", "landscape", "bed_w", "bed_d", "orient")
# wider than DIALOG_SIZE, like ArUco IDs: the part names and the info lines
EXPORT_DIALOG_SIZE = (560, 620)


@_survives_reload
class _ExportState:
    parts = []      # [(component, visible copies, tag keys)] = the part_<i> boxes
    bodies = {}     # component -> solid bodies (one STL each)
    markers = 0     # visible tags (what the sheet prints)
    dups = 0        # markers printed by more than one copy
    out_dir = ""    # where the export goes
    cmd = None      # the open dialog (bridge tests drive it)
    last = None     # the last export's result
    test = None     # bridge tests: {"out_dir": ...} = write there, remember
                    # nothing, open nothing (cleared when the dialog closes)


def _export_recall():
    """The remembered options. Settings saved by the dialog of before the
    types (no export_type) open on the type that dialog exported (no STLs =
    the PDF), and their "open" (the PDF opened beside the PrusaSlicer
    project, off by default then) is dropped."""
    rem = _recall(EXPORT_CMD, design=True)
    if "export_type" not in rem:
        rem = dict(rem)
        if rem.get("stl") is False:
            rem["export_type"] = "pdf"
        rem.pop("open", None)
    return rem


def _export_type(inputs):
    return _dd_value(inputs.itemById("export_type"), EXPORT_TYPES)


def _part_on(inputs, i):
    inp = inputs.itemById(f"part_{i}")
    return bool(inp is not None and inp.value)


def _plural(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _export_info(inputs, kind):
    """The info line: exactly what OK writes, and where."""
    import html
    st = _ExportState
    s = os.sep
    ticked = [(c, n) for i, (c, n, _k) in enumerate(st.parts) if _part_on(inputs, i)]
    lines = [f"Into <b>{html.escape(os.path.normpath(st.out_dir) + s)}</b>"
             if st.out_dir else "Into ~/CADExporter/&lt;design&gt;/"]
    if kind == "pdf":
        page = _dd_value(inputs.itemById("page"), PAGES)
        land = bool(inputs.itemById("landscape").value)
        paper = dict((v, k) for k, v in PAGES).get(page, page)
        lines.append(f"<b>aruco{s}print_{page}{'_landscape' if land else ''}.pdf</b>: "
                     f"{_plural(st.markers, 'marker')} at true size, {paper} "
                     f"{'landscape' if land else 'portrait'}")
        lines.append(f"aruco{s}images{s} (a PNG + SVG per marker), aruco{s}markers.json")
    elif kind == "stl":
        flat = _dd_value(inputs.itemById("orient"), ORIENT_LABELS) == "flat"
        nb = sum(st.bodies.get(c, 0) for c, _n in ticked)
        lines.append(f"<b>aruco{s}stl{s}</b>: {_plural(nb, 'STL')}, one per body of "
                     f"{_plural(len(ticked), 'part')}, "
                     f"{'laid flat by the tag' if flat else 'as modelled'}, in mm")
    else:
        copies = sum(n for _c, n in ticked)
        bw = round(inputs.itemById("bed_w").value * 10.0, 1)
        bd = round(inputs.itemById("bed_d").value * 10.0, 1)
        lines.append(f"<b>aruco{s}print3d{s}aruco_tags.3mf</b>: "
                     f"{_plural(len(ticked), 'part')}, {_plural(copies, 'copy', 'copies')} "
                     f"on {bw:g} × {bd:g} mm beds, black on extruder 2")
        lines.append(f"aruco{s}print3d{s}: the STLs it is made from (one per body), "
                     f"beds{s}, parts{s}")
    lines.append("aruco.json + joint_origins.json: the tag records for the URDF")
    warn = []
    if kind != "pdf" and not ticked:
        warn.append("Tick at least one part to print.")
    if st.dups:
        warn.append(f"{_plural(st.dups, 'marker')} printed by more than one copy — "
                    "run ArUco IDs first.")
    lines += [f"<span style='color:#c00'>{w}</span>" for w in warn]
    return "<br>".join(lines)


def _export_refresh(inputs):
    """Show the chosen type's inputs only; rewrite the info line."""
    kind = _export_type(inputs)
    for cid in {c for shown in EXPORT_SHOWS.values() for c in shown}:
        inp = inputs.itemById(cid)
        if inp is not None:
            inp.isVisible = cid in EXPORT_SHOWS[kind]
    info = inputs.itemById("info")
    if info is not None:
        info.formattedText = _export_info(inputs, kind)


def _export_args(inputs):
    """The dialog -> aruco_export.export(**these)."""
    return {"kind": _export_type(inputs),
            "page": _dd_value(inputs.itemById("page"), PAGES),
            "landscape": bool(inputs.itemById("landscape").value),
            "margin_mm": max(0.0, inputs.itemById("margin").value * 10.0),     # cm -> mm
            "bed_w_mm": inputs.itemById("bed_w").value * 10.0,
            "bed_d_mm": inputs.itemById("bed_d").value * 10.0,
            "include": [c for i, (c, _n, _k) in enumerate(_ExportState.parts)
                        if _part_on(inputs, i)],
            "orientation": _dd_value(inputs.itemById("orient"), ORIENT_LABELS)}


# The handlers only call these module functions: the dev bridge re-imports
# this module, and a handler Fusion holds keeps the class it was made from.

def export_created(cmd):
    import aruco_export
    _size(cmd, EXPORT_DIALOG_SIZE)
    inputs = cmd.commandInputs
    d = EXPORT_DEFAULTS
    remembered = _export_recall()
    des = aruco_tags.design()
    st = _ExportState
    st.out_dir = aruco_export.export_dir(des)
    dd = inputs.addDropDownCommandInput("export_type", "Export",
                                        adsk.core.DropDownStyles.TextListDropDownStyle)
    for text, code in EXPORT_TYPES:
        try:
            dd.listItems.add(text, code == d["export_type"], _icon_dir(EXPORT_TYPE_ICONS[code]))
        except Exception:                          # noqa: BLE001
            # still a working list, without the icon; say why in TEXT COMMANDS
            _log(f"ArUco Export: no icon on '{text}'\n" + traceback.format_exc())
            dd.listItems.add(text, code == d["export_type"])
    dd.tooltip = "What to write: one type per export."
    dd.tooltipDescription = (
        "PrusaSlicer project: every ticked part on as many beds as needed, black on "
        "extruder 2.<br>STL files: one STL per body, turned for printing.<br>PDF print "
        "sheet: the markers at true size, for a paper printer.<br><br>Every type also "
        "writes aruco.json and joint_origins.json, the tag records the URDF rebuild reads.")
    # --- PDF print sheet
    pg = _dropdown(inputs, "page", "Paper", PAGES, d["page"])
    pg.tooltip = "Paper size. The markers print at their true size."
    inputs.addBoolValueInput("landscape", "Landscape", True, "", d["landscape"])
    # the printer's unprintable border (kept for every design: it is the printer's)
    mg = inputs.addValueInput("margin", "Page margin", "mm", _mm(d["margin"]))
    mg.tooltip = ("Nothing is printed closer than this to the paper edge. "
                  "Raise it if your printer cuts off the edges.")
    # --- PrusaSlicer project / STL files: which tag parts (Zach: "an option of
    # which ones I want to auto export"); a part a tag is inlaid into is listed
    # too -- untick it to print it yourself with Send to PrusaSlicer.
    # Remembered per design by component name; a new part starts ticked when
    # it has a visible copy.
    st.parts = aruco_export.tag_parts(des)
    names = {c for c, _n, _k in st.parts}
    st.bodies = {c.name: sum(1 for b in c.bRepBodies if b.isSolid)
                 for c in des.allComponents if c.name in names}
    ticked = remembered.get("parts") if isinstance(remembered.get("parts"), dict) else {}
    grp = inputs.addGroupCommandInput("parts", f"Parts to print ({len(st.parts)})")
    grp.tooltip = "The tag parts to write. A part a tag is inlaid into is listed too."
    for i, (comp, copies, keys) in enumerate(st.parts):
        on = ticked.get(comp)
        b = grp.children.addBoolValueInput(f"part_{i}", f"{comp}  ×{copies}", True, "",
                                           on if isinstance(on, bool) else copies > 0)
        b.tooltip = (f"{comp}: {_plural(copies, 'visible copy', 'visible copies')}, "
                     f"{_plural(st.bodies.get(comp, 0), 'body', 'bodies')}; "
                     f"tags {', '.join(str(k) for k in keys)}.")
    if not st.parts:
        grp.children.addTextBoxCommandInput("parts_none", "", "No tag parts in this design.",
                                            1, True)
    lay = _dropdown(inputs, "orient", "Lay parts", ORIENT_LABELS, d["orient"])
    lay.tooltip = "How each part is turned for printing."
    lay.tooltipDescription = (
        "Flat: laid down by its tag -- pattern on the bed when the black is inlaid "
        "flush, face up when it is raised.<br>As modelled: the CAD orientation, only "
        "dropped onto the bed.")
    for cid, label, cm in (("bed_w", "Bed width", d["bed_w"]), ("bed_d", "Bed depth", d["bed_d"])):
        bi = inputs.addValueInput(cid, label, "mm", _mm(cm))
        bi.tooltip = ("Your printer's bed (MK4: 250 × 210 mm). The parts go onto as many "
                      "beds as needed, each keeping room for the MMU wipe tower.")
    # --- open when done: one per type
    for cid, label, tip in (
            ("open_slicer", "Open in PrusaSlicer when done", "Opens aruco_tags.3mf as a project."),
            ("open_folder", "Open the folder when done", "Opens aruco\\stl\\ in Explorer."),
            ("open", "Open the PDF when done", "Opens the print sheet in your PDF viewer.")):
        inputs.addBoolValueInput(cid, label, True, "", d[cid]).tooltip = tip
    # a short label + the button's own text (as ArUco Extrude's)
    rs = inputs.addBoolValueInput("reset", "Defaults", False, "", False)
    try:
        rs.text = "Reset"
    except Exception:                              # noqa: BLE001
        pass
    rs.tooltip = ("Every option back to the add-in's defaults; the type stays. "
                  "Remembered at OK.")
    res = aruco_tags.resolve_live(des)
    st.markers = sum(1 for i in res.instances if not i.hidden)
    st.dups = len(res.duplicates())
    inputs.addTextBoxCommandInput("info", "", "", 6, True)
    _restore(inputs, EXPORT_CMD, EXPORT_KEEP, values=remembered)
    _export_refresh(inputs)
    for ev, h in ((cmd.execute, _ExportExecute()), (cmd.inputChanged, _ExportChanged()),
                  (cmd.validateInputs, _ExportValidate()), (cmd.destroy, _ExportDestroy())):
        ev.add(h)
        _handlers.append(h)
    st.cmd = cmd


def export_changed(inputs, input_id):
    legacy = inputs.itemById("export_type") is None
    if input_id == "reset":
        aruco_settings.apply(inputs, {k: v for k, v in EXPORT_DEFAULTS.items()
                                      if k != "export_type"}, TABLES)
        for i, (_c, copies, _k) in enumerate(_ExportState.parts):
            inp = inputs.itemById(f"part_{i}")
            if inp is not None:
                inp.value = copies > 0
    if legacy:
        _export_enable(inputs)
    else:
        _export_refresh(inputs)


def export_valid(inputs):
    """OK only when there is something to print: a 3D type needs a part."""
    if inputs.itemById("export_type") is None:
        return True
    return _export_type(inputs) == "pdf" or any(
        _part_on(inputs, i) for i in range(len(_ExportState.parts)))


def export_execute(cmd):
    import aruco_export
    inputs = cmd.commandInputs
    if inputs.itemById("export_type") is None:
        return _legacy_export_execute(inputs)
    test = _ExportState.test
    if not test:
        ticked = {c: _part_on(inputs, i) for i, (c, _n, _k) in enumerate(_ExportState.parts)}
        _remember(EXPORT_CMD, inputs, EXPORT_KEEP, design_ids=EXPORT_PER_DESIGN,
                  design_extra={"parts": ticked})
    args = _export_args(inputs)
    out = aruco_export.export(out_dir=(test or {}).get("out_dir") or None, **args)
    _ExportState.last = out
    if test:
        return out
    kind = args["kind"]
    if kind == "prusaslicer":
        if inputs.itemById("open_slicer").value:
            _open_project(out)
    elif kind == "stl":
        if not out.get("stl_files"):
            why = (out.get("stl") or {}).get("error") or "; ".join(
                (out.get("stl") or {}).get("warnings") or ()) or "nothing to write"
            _ui().messageBox(f"ArUco Export wrote no STL:\n\n{why}")
        elif inputs.itemById("open_folder").value and os.path.isdir(out.get("stl_dir") or ""):
            os.startfile(out["stl_dir"])                       # noqa: S606 (Windows)
    elif inputs.itemById("open").value and out.get("pdf"):
        os.startfile(os.path.join(out["out_dir"], *out["pdf"].split("/")))   # noqa: S606
    return out


def export_destroy():
    _ExportState.cmd = None
    _ExportState.test = None


class _ExportCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            export_created(args.command)
        except Exception:                          # noqa: BLE001
            _fail("ArUco Export dialog")


def _rescue_export_parts():
    """An Export dialog open while an OLDER copy of this module was re-imported
    lost its parts list (before _survives_reload). Rebuild it -- tag_parts
    walks des.allComponents, so the order matches the dialog's part_<i>."""
    try:
        if not _ExportState.parts and _ui().activeCommand == EXPORT_CMD:
            import aruco_export
            _ExportState.parts = aruco_export.tag_parts()
    except Exception:                              # noqa: BLE001
        pass


_rescue_export_parts()


class _ExportChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            export_changed(_all_inputs(args), args.input.id)
        except Exception:                          # noqa: BLE001
            _fail("ArUco Export")


class _ExportValidate(adsk.core.ValidateInputsEventHandler):
    def notify(self, args):
        try:
            args.areInputsValid = export_valid(args.inputs)
        except Exception:                          # noqa: BLE001
            pass


class _ExportExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            export_execute(args.command)
        except Exception:                          # noqa: BLE001
            _fail("ArUco Export")


class _ExportDestroy(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            export_destroy()
        except Exception:                          # noqa: BLE001
            pass


def _open_project(out):
    """Open the export's all-beds project (aruco_tags.3mf) in PrusaSlicer, or
    say why there is none."""
    project = out.get("project")
    if project and os.path.isfile(project):
        aruco_settings.open_in_prusaslicer(project, out.get("prusaslicer_exe"))
        return
    beds = out.get("beds") if isinstance(out.get("beds"), dict) else {}
    why = beds.get("error") or next(
        (w for w in beds.get("warnings") or ()
         if any(s in w for s in ("not written", "no multi-bed project", "vendor bundle",
                                 "presets unavailable"))),
        "no PrusaSlicer project was written")
    _ui().messageBox(f"ArUco Export finished, but PrusaSlicer was not opened:\n\n{why}")


# --- The dialog of before the types (STLs, PrusaSlicer project and PDF at
# once). A running Fusion keeps the created handler it registered, and that
# handler's code builds THAT dialog and wires _ExportExecute / _ExportChanged
# and _export_enable from this module -- so they keep serving it until the
# commands are registered again (add-in restart or the bridge's
# register_commands). Then this is dead code and can go.

def _export_enable(inputs):
    stl = inputs.itemById("stl")
    if stl is None:
        _export_refresh(inputs)
        return
    inputs.itemById("beds").isEnabled = stl.value
    inputs.itemById("open_slicer").isEnabled = stl.value and inputs.itemById("beds").value


def _legacy_export_execute(inputs):
    import aruco_export
    ticked = {c: _part_on(inputs, i) for i, (c, _n, _k) in enumerate(_ExportState.parts)}
    _remember(EXPORT_CMD, inputs, EXPORT_KEEP, design_ids=EXPORT_PER_DESIGN,
              design_extra={"parts": ticked})
    want_beds = inputs.itemById("stl").value and inputs.itemById("beds").value
    out = aruco_export.export(
        page=_dd_value(inputs.itemById("page"), PAGES),
        landscape=inputs.itemById("landscape").value,
        margin_mm=max(0.0, inputs.itemById("margin").value * 10.0),   # cm -> mm
        stl=inputs.itemById("stl").value, beds=want_beds,
        bed_w_mm=inputs.itemById("bed_w").value * 10.0,      # cm -> mm
        bed_d_mm=inputs.itemById("bed_d").value * 10.0,
        include=[c for i, (c, _n, _k) in enumerate(_ExportState.parts) if _part_on(inputs, i)],
        orientation=_dd_value(inputs.itemById("orient"), ORIENT_LABELS))
    pdf = next((f for f in out["files"] if f.endswith(".pdf")), None)
    if inputs.itemById("open").value and pdf:
        os.startfile(os.path.join(out["out_dir"], *pdf.split("/")))
    if want_beds and inputs.itemById("open_slicer").value:
        _open_project(out)
    return out


# ======================================================= Send to PrusaSlicer

SEND_KEEP = ("orient", "bed_w", "bed_d", "open")


class _SendCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            cmd = args.command
            _size(cmd)
            inputs = cmd.commandInputs
            sel = inputs.addSelectionInput(
                "targets", "Parts", "Bodies or components to print together. A part "
                "with an inlaid tag brings its black cells along automatically.")
            sel.addSelectionFilter("SolidBodies")
            sel.addSelectionFilter("Occurrences")
            sel.setSelectionLimits(1, 0)
            _dropdown(inputs, "orient", "Lay parts", ORIENT_LABELS, "cad")
            inputs.addValueInput("bed_w", "Bed width", "mm", _mm(25.0))
            inputs.addValueInput("bed_d", "Bed depth", "mm", _mm(21.0))
            inputs.addBoolValueInput("open", "Open in PrusaSlicer", True, "", True)
            inputs.addTextBoxCommandInput(
                "info", "", "Each pick becomes its own object; black cells go on "
                "extruder 2, everything else on extruder 1.", 3, True)
            _restore(inputs, SEND_CMD, SEND_KEEP)          # not the picks
            h = _SendExecute()
            cmd.execute.add(h)
            _handlers.append(h)
        except Exception:                          # noqa: BLE001
            _fail("Send to PrusaSlicer dialog")


class _SendExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            import aruco_export
            inputs = args.command.commandInputs
            _remember(SEND_CMD, inputs, SEND_KEEP)
            sel = inputs.itemById("targets")
            ents = [sel.selection(i).entity for i in range(sel.selectionCount)]
            aruco_export.send_selection(
                ents, orientation=_dd_value(inputs.itemById("orient"), ORIENT_LABELS),
                bed_w_mm=inputs.itemById("bed_w").value * 10.0,
                bed_d_mm=inputs.itemById("bed_d").value * 10.0,
                open_slicer=inputs.itemById("open").value)
        except Exception:                          # noqa: BLE001
            _fail("Send to PrusaSlicer")


# ================================================================ wiring

def _extrude_created():
    import aruco_extrude_cmd
    return aruco_extrude_cmd._Created()


EXTRUDE_CMD = "bamArucoExtrude"
# The panel shows these big; the rest sit in its dropdown (Zach 2026-10-08:
# ArUco Extrude "can be the main entry point").
PROMOTED = (EXTRUDE_CMD, EXPORT_CMD)
# Older command ids this add-in registered once; deleted on register.
RETIRED = (TAG_CMD,)

COMMANDS = [
    (EXTRUDE_CMD, "ArUco Extrude",
     "Like Extrude, but the face or sketch profile becomes a 3D-printable ArUco "
     "marker, grid board or ChArUco board: white + black, black only or white "
     "only, each with its own height. One feature in the timeline -- "
     "double-click it to change anything.", _extrude_created),
    (EDIT_CMD, "ArUco Edit",
     "Change a tag after the fact: black thickness and whether it is inlaid or "
     "raised, dictionary, id. Every copy's tag part is rebuilt to match.",
     _EditCreated),
    (IDS_CMD, "ArUco IDs",
     "Give every copy its own marker id at any assembly level — like a serial "
     "number — without making a new model.", _IdsCreated),
    (EXPORT_CMD, "ArUco Export",
     "Export the tags as a PrusaSlicer project (beds + MMU colours), STL files "
     "or a true-size PDF print sheet -- one type per export.",
     _ExportCreated),
    (SEND_CMD, "Send to PrusaSlicer",
     "Selected bodies or components -> one PrusaSlicer project, each its own "
     "object, black ArUco cells already on extruder 2, opened in PrusaSlicer.",
     _SendCreated),
]


CREATE_PANEL = "SolidCreatePanel"


def _icon_dir(cid):
    d = os.path.join(HERE, "resources", cid)
    return d if os.path.isdir(d) else os.path.join(HERE, "resources", "bamArucoTag")


def register():
    ui = _ui()
    ws = ui.workspaces.itemById(WS_ID)
    tab = ws.toolbarTabs.itemById(TAB_ID)
    panel = tab.toolbarPanels.itemById(PANEL_ID) or tab.toolbarPanels.add(
        PANEL_ID, "ARUCO", "SolidScriptsAddinsPanel", False)
    for cid in RETIRED:
        ctl = panel.controls.itemById(cid)
        if ctl:
            ctl.deleteMe()
        d = ui.commandDefinitions.itemById(cid)
        if d:
            d.deleteMe()
    for cid, name, tip, created in COMMANDS:
        d = ui.commandDefinitions.itemById(cid)
        if d:
            d.deleteMe()
        d = ui.commandDefinitions.addButtonDefinition(cid, name, tip, _icon_dir(cid))
        h = created()
        d.commandCreated.add(h)
        _handlers.append(h)
        ctl = panel.controls.itemById(cid)
        if ctl:
            ctl.deleteMe()
        ctl = panel.controls.addCommand(d)
        ctl.isPromoted = cid in PROMOTED
        ctl.isPromotedByDefault = cid in PROMOTED
    # ArUco Extrude also sits in SOLID > CREATE, right after Extrude
    create = tab.toolbarPanels.itemById(CREATE_PANEL)
    if create is not None:
        old = create.controls.itemById(EXTRUDE_CMD)
        if old:
            old.deleteMe()
        try:
            create.controls.addCommand(ui.commandDefinitions.itemById(EXTRUDE_CMD),
                                       "ExtrudeCommand", False)
        except Exception:                          # noqa: BLE001
            create.controls.addCommand(ui.commandDefinitions.itemById(EXTRUDE_CMD))


def unregister():
    ui = _ui()
    ws = ui.workspaces.itemById(WS_ID)
    tab = ws.toolbarTabs.itemById(TAB_ID) if ws else None
    panel = tab.toolbarPanels.itemById(PANEL_ID) if tab else None
    create = tab.toolbarPanels.itemById(CREATE_PANEL) if tab else None
    if create is not None:
        ctl = create.controls.itemById(EXTRUDE_CMD)
        if ctl:
            ctl.deleteMe()
    for cid, *_ in COMMANDS:
        if panel:
            ctl = panel.controls.itemById(cid)
            if ctl:
                ctl.deleteMe()
        d = ui.commandDefinitions.itemById(cid)
        if d:
            d.deleteMe()
    if panel:
        panel.deleteMe()
    del _handlers[:]
