"""Regressions for the 2026-10-09 Fusion run (verify0509), without Fusion.

The add-in's own code runs on a stand-in `adsk` (the same idea as
tests/test_aruco_chamfer_bodies.py):

  A1  live_test "colours: after a structural edit": a Black only node
      rebuilt as White + black came back with a grey "tag tile ck (1)".
      Cause: the edit kept Black only's black offset (0 = the face), so the
      black lay UNDER the new tile and the pocket (the black's bottom up to
      the white top) went through the tile and cut it in two; paint() only
      painted the base feature's body, not the piece Fusion split off.
      -> keep_black_step (the black keeps its step to its own surface on a
      mode change, like the dialog), paint() paints every piece.
  A2  chamfer_test "Origin bottom right with the mark on": the record's x
      after a 180 turn was +y. The plate is SQUARE: face_frame's x is
      Fusion's first longest edge, -y there (live_test's square plates C and
      D read the same), so turned 180 it IS +y -- the test assumed +x. The
      code was right; the test now compares with the un-turned record. And
      keep_x: on a square face a perpendicular "longest" edge can no longer
      turn a tag by 90 (the creation's x wins), a reversed edge still flips.
  A3  chamfer_test [variant]: 'NoneType' has no 'customNamedValues', and the
      Joint Origin check inf. A copy's variant part has a record with its
      template's key and no node; lookups by key over des.allComponents hit
      it first. -> aruco_tags.template_records / find_record, settings_of /
      numbers_of take None, _jo_check skips variant parts.

Run:  python tests/test_aruco_extrude_fixes.py
"""
from __future__ import annotations

import json
import math
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ArucoMarkers"))

try:                                    # labels carry × ↻ °: never die on cp1252
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass
PASS = 0
FAIL = []


def check(label, condition, detail=""):
    global PASS
    if condition:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL.append(label)
        print(f"FAIL  {label}  {detail}")


# ---------------------------------------------------------- stand-in adsk

adsk = types.ModuleType("adsk")
core = types.ModuleType("adsk.core")
fusion = types.ModuleType("adsk.fusion")
adsk.core, adsk.fusion = core, fusion
sys.modules.update({"adsk": adsk, "adsk.core": core, "adsk.fusion": fusion})


def _attr(mod):
    def get(name):
        if name.endswith("Handler"):
            cls = type(name, (), {"__init__": lambda self, *a, **k: None})
            setattr(mod, name, cls)
            return cls
        raise AttributeError(name)
    return get


core.__getattr__ = _attr(core)
fusion.__getattr__ = _attr(fusion)


class _Vec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)

    @classmethod
    def create(cls, x=0.0, y=0.0, z=0.0):
        return cls(x, y, z)


core.Point3D = _Vec
core.Vector3D = _Vec
core.OrientedBoundingBox3D = types.SimpleNamespace(create=lambda *a: ("box",) + a)


class Body:
    def __init__(self, tree=None):
        self.tree = tree


class _TBM:
    def createBox(self, obb):
        return Body(obb)

    def createCylinderOrCone(self, *a):
        return Body(("cyl",) + a)

    def booleanOperation(self, target, tool, op):
        target.tree = (op, target.tree, tool.tree)
        return True


_TBM_ONE = _TBM()
fusion.TemporaryBRepManager = types.SimpleNamespace(get=lambda: _TBM_ONE)
fusion.BooleanTypes = types.SimpleNamespace(UnionBooleanType=0, DifferenceBooleanType=1,
                                            IntersectionBooleanType=2)


class Profile:                      # what evaluate() asks isinstance() about
    assemblyContext = None


class Face:                         # a planar face (evaluate's other path)
    assemblyContext = None


fusion.Profile = Profile
fusion.BRepFace = Face
fusion.CustomFeature = types.SimpleNamespace(cast=lambda x: x)

import aruco_core  # noqa: E402
import aruco_feature as F  # noqa: E402
import aruco_tags as T  # noqa: E402

M = aruco_core.module("markers")
SL = aruco_core.module("aruco_slabs")
OR = aruco_core.module("aruco_origin")


def dot(a, b):
    return sum(p * q for p, q in zip(a, b))


# ===================================================================== A1

print("== A1: a structural edit keeps the black's step (no pocket through the tile)")


class Param:
    def __init__(self, name, value_cm, expression=None):
        self.name, self.value, self.expression = name, value_cm, expression or f"{value_cm} cm"


def edit_once(s_old, n_old, s_new, n_new, linked=False):
    """aruco_feature.apply_edit on a stand-in node (settings / numbers as
    given; a linked black offset when `linked`)."""
    store = {"spec": json.dumps(s_old)}
    nv = types.SimpleNamespace(addOrSetValue=lambda k, v: store.__setitem__(k, v))
    cf = types.SimpleNamespace(customNamedValues=nv)
    params = {"white_offset": Param("aruco_k_white_offset", n_old["white_offset"] / 10.0),
              "white_height": Param("aruco_k_white_height", n_old["white_height"] / 10.0),
              "black_offset": Param("aruco_k_black_offset", n_old["black_offset"] / 10.0)}
    if linked:
        params["black_offset"].expression = (
            "max(aruco_k_white_offset; aruco_k_white_offset + aruco_k_white_height)")
    saved = {k: getattr(F, k) for k in ("settings_of", "numbers_of", "target_of", "_param")}
    F.settings_of = lambda c: F.normalise(json.loads(store["spec"]))
    F.numbers_of = lambda c: dict(n_old)
    F.target_of = lambda c: Profile()
    F._param = lambda c, pid: params.get(pid) if s_old["mode"] == "both" or pid.startswith(
        "black") else None
    try:
        return F.apply_edit(cf, s_new, n_new), json.loads(store["spec"])
    finally:
        for k, v in saved.items():
            setattr(F, k, v)


def node(mode, op="join", **numbers):
    s = F.default_settings()
    s.update(mode=mode, op=op, layout="single", has_body=True, source="face", key="ck",
             x_base=[1.0, 0.0, 0.0], x_ref=[1.0, 0.0, 0.0])
    s.pop("black_follows", None)
    n = F.default_numbers(mode)
    n = F.resolve_follow(dict(s, black_follows=True), n)       # what make() does
    n.update(size=30.0, **numbers)
    s["parts"] = list(F.plan_of(s, n).parts)
    return s, n


# live_test's "ck": Black only (black 0, height -0.6 -> -0.5 by the edit route)
s_ck, n_ck = node("black", black_height=-0.5)
res, _spec = edit_once(s_ck, n_ck, {"mode": "both", "op": "new"}, {"white_height": 1.0})
n = res["numbers"]
plan = F.plan_of(res["settings"], n)
check("ck black only -> white + black new body: a rebuild", res["rebuild"])
check("... the black starts at the new white top (1.0 mm), as the dialog would",
      abs(n["black_offset"] - 1.0) < 1e-9, n["black_offset"])
check("... the pocket stops above the tile's underside: the tile stays ONE body",
      plan.pocket is not None and plan.pocket[0] > plan.layers.white[0] + 1e-9,
      (plan.pocket, plan.layers.white))
old = dict(n, black_offset=n_ck["black_offset"])            # what the 2026-10-08 code did
old_plan = F.plan_of(res["settings"], old)
check("(the old numbers: the pocket ran from the black's bottom through the whole tile)",
      old_plan.pocket[0] <= old_plan.layers.white[0], (old_plan.pocket, old_plan.layers.white))

# white + black -> black only: the black keeps its step to the white top,
# now the face (a flush inlay stays a flush inlay)
s_b, n_b = node("both", op="new")
res, _ = edit_once(s_b, n_b, {"mode": "black"}, {})
check("white + black (flush) -> black only: the black at the face",
      abs(res["numbers"]["black_offset"] - 0.0) < 1e-9, res["numbers"]["black_offset"])
# a raised black keeps its raise across the change
s_r, n_r = node("both", op="new", black_offset=2.4, black_height=-0.6)
res, _ = edit_once(s_r, n_r, {"mode": "black"}, {})
check("a black raised 0.4 over the white top -> 0.4 over the face",
      abs(res["numbers"]["black_offset"] - 0.4) < 1e-9, res["numbers"]["black_offset"])
# White + black, the black linked to the white: a script that only moves the
# white carries the black (the compute path's expression does the same)
s_l, n_l = node("both", op="new")
res, _ = edit_once(s_l, n_l, {"op": "join"}, {"white_height": 1.0}, linked=True)
check("linked black, white height 2 -> 1 (with a rebuild): the black follows to 1.0",
      abs(res["numbers"]["black_offset"] - 1.0) < 1e-9, res["numbers"]["black_offset"])
res, _ = edit_once(s_l, n_l, {"op": "join"}, {"white_height": 1.0}, linked=False)
check("a typed (unlinked) black offset stays where it was",
      abs(res["numbers"]["black_offset"] - 2.0) < 1e-9, res["numbers"]["black_offset"])
res, _ = edit_once(s_ck, n_ck, {"mode": "both", "op": "new"},
                   {"white_height": 1.0, "black_offset": 0.3})
check("an edit that says where the black starts is obeyed",
      abs(res["numbers"]["black_offset"] - 0.3) < 1e-9, res["numbers"]["black_offset"])
check("keep_black_step: same mode, not linked -> unchanged",
      F.keep_black_step("black", {"black_offset": 0.0, "white_offset": 0, "white_height": 2},
                        "black", {"black_offset": 0.0, "white_offset": 0, "white_height": 2})
      == {"black_offset": 0.0, "white_offset": 0, "white_height": 2})

print("== A1: paint() makes every piece of a tile white (and only its pieces)")


class Appearance:
    def __init__(self, name):
        self.name = name


class Appearances:
    def __init__(self, names):
        self.items = {n: Appearance(n) for n in names}

    def itemByName(self, name):
        return self.items.get(name)


class B:
    """A body: its name, appearance, faces; its component."""
    def __init__(self, name, comp, appearance=None, faces=4):
        self.name, self.parentComponent, self.appearance = name, comp, appearance
        self.faces = [types.SimpleNamespace(appearance=appearance) for _ in range(faces)]


class Bodies(list):
    @property
    def count(self):
        return len(self)

    def item(self, i):
        return self[i]

    def itemByName(self, name):
        return next((b for b in self if b.name == name), None)


steel = Appearance("Steel - Satin")
comp = types.SimpleNamespace()
names = ("tag tile ck", "tag tile ck (1)", "tag tile ck (2)", "tag tile ck2", "tag tile ck2 (1)",
         "tag tile ck (x)", "aruco cells ck", "Body1")
comp.bRepBodies = Bodies(B(nm, comp, steel) for nm in names)
bf_tile = types.SimpleNamespace(bodies=Bodies([comp.bRepBodies.itemByName("tag tile ck")]))
bf_pat = types.SimpleNamespace(bodies=Bodies([comp.bRepBodies.itemByName("aruco cells ck")]))
des = types.SimpleNamespace(appearances=Appearances([T.WHITE, T.BLACK]))
s = F.default_settings()
s.update(mode="both", op="new", has_body=True, key="ck")
done = F.paint(des, s, bf_tile, bf_pat)
got = {b.name: b.appearance.name for b in comp.bRepBodies}
check("the tile and both pieces Fusion split off it are white",
      all(got[nm] == T.WHITE for nm in ("tag tile ck", "tag tile ck (1)", "tag tile ck (2)")),
      got)
check("another tag's tile ('ck2') and a name that is no piece are left alone",
      got["tag tile ck2"] == "Steel - Satin" and got["tag tile ck2 (1)"] == "Steel - Satin"
      and got["tag tile ck (x)"] == "Steel - Satin" and got["Body1"] == "Steel - Satin", got)
check("the pattern black", got["aruco cells ck"] == T.BLACK, got)
check("paint() says it painted the pieces", done.get("tile piece") == 2 and done.get("tile") == 1,
      done)
check("idempotent: a second paint() paints nothing", F.paint(des, s, bf_tile, bf_pat) == {})
check("is_piece_of", F.is_piece_of("tag tile a (12)", "tag tile a")
      and not F.is_piece_of("tag tile ab (1)", "tag tile a")
      and not F.is_piece_of("tag tile a (1b)", "tag tile a")
      and F.is_piece_of("tag tile a", "tag tile a"))

# ===================================================================== A2

print("== A2: a square plate's x, turned 180 (chamfer_test 'edit')")

O = [0.0, 0.0, 0.5]


def face_eval(face_x, x_base, turn, w=6.0, h=6.0, x_ref=None):
    """evaluate() on a stand-in planar face whose face_frame gives `face_x`."""
    z = [0.0, 0.0, 1.0]
    x = list(face_x)
    y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]]
    T.face_frame = lambda f, rot=0.0: (list(O), list(x), list(y), z, w, h)

    def loops(target, o, xx, yy):
        # the face's outline in the frame evaluate settled on
        corners = [(sx * 3.0, sy * 3.0) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        pts = []
        for cx, cy in corners:
            world = [cx * (1.0 if i == 0 else 0.0) + cy * (1.0 if i == 1 else 0.0) + O[i]
                     for i in range(3)]
            d = [world[i] - o[i] for i in range(3)]
            pts.append((dot(d, xx) / 100.0, dot(d, yy) / 100.0))
        return [pts]
    F._face_loops_m = loops
    s = F.default_settings()
    s.update(mode="both", op="new", layout="single", has_body=True, source="face",
             x_base=x_base, x_ref=x_ref, turn=turn, origin_v=OR.ORIGIN_RULE)
    n = F.default_numbers("both")
    n.update(size=30.0)
    return F.evaluate(Face(), s, n)


# what Fusion measured: face_frame's x on a square _box plate is -y
ev0 = face_eval([0.0, -1.0, 0.0], [0.0, -1.0, 0.0], 0)
ev180 = face_eval([0.0, -1.0, 0.0], [0.0, -1.0, 0.0], 180, x_ref=ev0["x_fit"])
x0, x1 = ev0["tag"][1], ev180["tag"][1]
check("un-turned: the record's x is face_frame's (-y on Fusion's square plate)",
      max(abs(a - b) for a, b in zip(x0, [0.0, -1.0, 0.0])) < 1e-12, x0)
check("turned 180: +y -- exactly the [1.2e-16, 1.0, 0.0] Fusion wrote (the test was wrong)",
      max(abs(a - b) for a, b in zip(x1, [0.0, 1.0, 0.0])) < 1e-12, x1)
check("turned 180 = the un-turned x reversed (what chamfer_test checks now)",
      dot(x0, x1) < -0.999999 and dot(ev0["tag"][2], ev180["tag"][2]) < -0.999999)

print("== A2: keep_x -- the creation's x wins")
z = [0.0, 0.0, 1.0]
for face_x, label in (([1.0, 0.0, 0.0], "the same edge"), ([-1.0, 0.0, 0.0], "reversed"),
                      ([0.0, 1.0, 0.0], "a perpendicular edge"),
                      ([0.0, -1.0, 0.0], "the other perpendicular")):
    y = [-face_x[1], face_x[0], 0.0]
    x, yy, w, h = F.keep_x(face_x, y, z, 6.0, 6.0, [1.0, 0.0, 0.0])
    check(f"square face, face_frame picks {label}: x stays +x, right-handed",
          max(abs(a - b) for a, b in zip(x, [1.0, 0.0, 0.0])) < 1e-12
          and max(abs(a - b) for a, b in zip(yy, [0.0, 1.0, 0.0])) < 1e-12, (x, yy))
x, yy, w, h = F.keep_x([0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], z, 6.0, 4.0, [1.0, 0.0, 0.0])
check("a rectangle whose longest edge changed direction: as before (the fit follows it)",
      x == [0.0, 1.0, 0.0] and (w, h) == (6.0, 4.0))
x, yy, w, h = F.keep_x([-1.0, 0.0, 0.0], [0.0, -1.0, 0.0], z, 6.0, 4.0, [1.0, 0.0, 0.0])
check("a rectangle whose longest edge reversed: flipped back (2026-10-08's fix)",
      x == [1.0, 0.0, 0.0] and yy == [0.0, 1.0, 0.0])
ev = face_eval([0.0, 1.0, 0.0], [1.0, 0.0, 0.0], 0)
check("evaluate(): a square face whose first edge turned 90 keeps the tag's x",
      max(abs(a - b) for a, b in zip(ev["tag"][1], [1.0, 0.0, 0.0])) < 1e-12, ev["tag"][1])

# ===================================================================== A3

print("== A3: a copy's variant part never stands in for its template")


class Attrs:
    def __init__(self, data):
        self.data = data

    def itemByName(self, group, name):
        v = self.data.get(name)
        return types.SimpleNamespace(value=v) if v is not None else None


def component(name, records, variant=False):
    c = types.SimpleNamespace(name=name)
    data = {T.TAGS_ATTR: json.dumps(records)}
    if variant:
        data[T.VARIANT_ATTR] = json.dumps({"slot": "Part N:2|vn", "template": "Part N"})
    c.attributes = Attrs(data)
    return c


spec = M.TagSpec(marker_length_m=0.03, marker_id=10)
frame = dict(origin=[1.0, 2.0, 0.5], x_axis=[1.0, 0.0, 0.0], y_axis=[0.0, 1.0, 0.0],
             z_axis=[0.0, 0.0, 1.0])
tmpl_rec = dict(M.TagDef(key="vn", component="Part N", spec=spec, **frame).to_dict(),
                fusion={"custom_feature": "TOKEN-CF", "joint_origin_name": "aruco vn",
                        "joint_origin_anchor": "face centre"})
var_rec = M.TagDef(key="vn", component="Part N · 4X4_50 #30",
                   spec=M.TagSpec(marker_length_m=0.03, marker_id=30), **frame,
                   variant_of={"template": "Part N", "slot": "Part N:2|vn"}).to_dict()
variant = component("Part N · 4X4_50 #30", [var_rec], variant=True)
template = component("Part N", [tmpl_rec])
# Fusion's order is not creation order: the variant part first (as in the run)
des3 = types.SimpleNamespace(allComponents=[variant, template])
check("the old lookup (first record with the key) gets the variant's: no node",
      next(r for c in des3.allComponents for r in T.read_tag_records(c)
           if r["key"] == "vn").get("fusion") is None)
c, r = T.find_record(des3, "vn")
check("find_record: the template's record", c is template and r == tmpl_rec, (c and c.name))
check("template_records: none for a variant part, the template's for its own",
      T.template_records(variant) == [] and T.template_records(template) == [tmpl_rec])
check("a variant part's record is one", T.is_variant_record(var_rec)
      and not T.is_variant_record(tmpl_rec))
check("settings_of(None) is None (it raised AttributeError)", F.settings_of(None) is None)
check("numbers_of(None): the defaults", F.numbers_of(None) == F.default_numbers("both"))
broken = types.SimpleNamespace(customNamedValues=None)
check("settings_of(a node without its named values) is None", F.settings_of(broken) is None)

try:
    T._build_variant(des3, template, component("Part X", []),
                     M.TagDef(key="zz", component="Part X", spec=spec, **frame), spec, "s",
                     None, None)
    check("_build_variant without the template's record: a clear error", False)
except ValueError as exc:
    check("_build_variant without the template's record: a clear error", "no tag 'zz'" in str(exc),
          exc)

# the Joint Origin check skips the variant part (it made worst = inf)
import aruco_live_test as LT  # noqa: E402

tag = M.TagDef.from_dict(tmpl_rec, lambda m: None)
o, x, y, zz = T.optical_frame_cm(tag)
jo = types.SimpleNamespace(
    transform=types.SimpleNamespace(getAsCoordinateSystem=lambda: (
        _Vec(*o), _Vec(*x), _Vec(*y), _Vec(*zz))),
    timelineObject=types.SimpleNamespace(healthState=0, index=3), name="aruco vn")
template.jointOrigins = types.SimpleNamespace(itemByName=lambda n: jo if n == "aruco vn" else None)
variant.jointOrigins = types.SimpleNamespace(itemByName=lambda n: None)
root = template
des3.rootComponent = root
des3.findEntityByToken = lambda tok: []
des3.rootComponent.allOccurrencesByComponent = lambda comp: types.SimpleNamespace(count=0)
res = LT._jo_check(des3, fresh=False)
check("_jo_check: the template's Joint Origin checked, the variant part skipped (not inf)",
      res["worst_mm"] < 1e-9 and res["ok"] and res["variants"] == 1
      and [t["key"] for t in res["tags"]] == ["vn"], res)

print()
print(f"{PASS} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
