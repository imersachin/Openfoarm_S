"""Short notes under the file uploaders: which file goes where (owner's text).

Shown as st.caption (muted text, readable in the light and dark themes). Paths
are in code spans so Markdown keeps their backslashes.
"""

from __future__ import annotations

# Machines app, the Imported mesh uploader.
MSH_UPLOAD_NOTE = (
    "Which file? Use .stl for geometry only (rotor, runner, casing): the app builds the "
    "mesh for you. Choose Build from STL. Use .msh (Fluent or Gmsh) when you already have "
    "a finished mesh: the app converts it to OpenFOAM, checks it, and sets up the case. "
    "Choose Import .msh. Files over 200 MB: don't upload. Copy them into WSL (for example "
    r"`\\wsl$\Ubuntu-24.04\root\meshes`) and enter the path below."
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
