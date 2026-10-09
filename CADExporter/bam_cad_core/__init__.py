"""bam_cad_core, ArUco subset: the pure core of the ArUco Tags add-in.

The modules beside this file never import a CAD API. They come from the CAD
Exporter repo, where the full package also holds the URDF pipeline
(cad_description, link_tree, urdf_writer, ...). That package's __init__
imports the pipeline; this one imports nothing, so the subset loads on its own.

Import the modules directly:

    from bam_cad_core import markers            # tests and tools
    aruco_core.module("markers")                # inside Fusion (ArucoMarkers/)

The module list is CORE_MODULES in tools/sync_from_cad_exporter.py.
"""
