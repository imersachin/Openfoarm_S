"""Short notes under the file uploaders: which file goes where (owner's text).

Shown as st.caption (muted text, readable in the light and dark themes). Paths
are in code spans so Markdown keeps their backslashes.
"""

from __future__ import annotations

# Machines app, the Imported mesh uploader.
MSH_UPLOAD_NOTE = (
    "Which file? Use .stl for geometry only: the app builds the mesh. Use .msh (Fluent or "
    "Gmsh) for a finished mesh: the app converts and checks it. Large files (over 200 MB): "
    "don't upload. Use Browse files on disk below. The app copies the file into WSL for you."
)

# Machines app, every STL uploader.
STL_UPLOAD_NOTE = (
    "STL only here: geometry that the app will mesh. Already have a mesh (.msh)? "
    "Go to Project -> Mesh source -> Import .msh."
)

# VAWT app, the rotor uploader.
VAWT_UPLOAD_NOTE = (
    "This app accepts .stl geometry for a VAWT rotor only. For .msh meshes, HAWT, Francis "
    r"or other machines, use the Machines app: `scripts\run_machines_app.cmd` -> "
    "http://localhost:8502"
)
