"""The pure core (CADExporter/bam_cad_core), loaded under a private name.

Fusion runs every add-in in ONE CPython. The CAD Exporter add-in already
imported `bam_cad_core` from whichever tree its junction points at, and it
purges every `bam_` module from sys.modules on each press. Importing our
tree's copy under the same name would either get theirs (wrong code) or be
purged mid-command. So this file loads the package from THIS repo under the
name `arucocore`; its relative imports then resolve inside that alias and the
two copies never meet.
"""
from __future__ import annotations

import importlib.util
import os
import sys

ALIAS = "arucocore"
HERE = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.normpath(os.path.join(HERE, "..", "CADExporter", "bam_cad_core"))


def load(fresh: bool = False):
    """The aliased package; `fresh=True` drops and re-executes every module."""
    if fresh:
        for name in [n for n in sys.modules if n == ALIAS or n.startswith(ALIAS + ".")]:
            del sys.modules[name]
    pkg = sys.modules.get(ALIAS)
    if pkg is not None:
        return pkg
    spec = importlib.util.spec_from_file_location(
        ALIAS, os.path.join(CORE_DIR, "__init__.py"),
        submodule_search_locations=[CORE_DIR])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[ALIAS] = pkg
    try:
        spec.loader.exec_module(pkg)
    except Exception:
        del sys.modules[ALIAS]
        raise
    return pkg


def module(name: str, fresh: bool = False):
    """`module("markers")` -> arucocore.markers."""
    load(fresh)
    return importlib.import_module(f"{ALIAS}.{name}")


# Importing (or re-importing) this file drops any cached copy of the core, so
# a dev-bridge reload of `aruco_core` is also a reload of the core itself.
for _name in [n for n in sys.modules if n == ALIAS or n.startswith(ALIAS + ".")]:
    del sys.modules[_name]
