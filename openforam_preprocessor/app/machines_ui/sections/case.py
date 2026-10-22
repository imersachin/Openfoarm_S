"""Case setup results, apart from the mesh: the case-setup stage of the last
run (SUCCESS, SKIPPED or FAILED with its reason), the derived values in the
units entered and as written, and the generated case files. Prepared, not
solved."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.machines_ui import state
from app.machines_ui.sections.common import COURANT_NOTE, zone_html
from app.machines_ui.sections.mesh import stage_view

REASONS = {
    "NO_PHYSICS": "The project has no physics: it is meshed only.",
    "PHYSICS_INVALID": "The physics settings have problems (see the Physics sections).",
    "MESH_NOT_READY": "The mesh did not pass its checks, so no case was set up.",
    "CASE_SETUP_FAILED": "Setting up the case failed (see the message).",
}


def values_view(report: dict[str, Any]) -> None:
    rotation = report.get("rotation") or {}
    if rotation:
        st.markdown(f"**Rotation** of `{rotation.get('zone')}` about axis "
                    f"{rotation.get('axis')} through {rotation.get('origin')}: "
                    f"ω = {rotation.get('omega_rad_s', 0):.6g} rad/s = "
                    f"{rotation.get('rpm', 0):.6g} rpm (signed: positive is right-handed "
                    "about +axis)"
                    + (f", from the tip-speed ratio with tip radius R = "
                       f"{rotation['tip_radius_m']:g} m" if "tip_radius_m" in rotation else ""))
    st.markdown(f"**Fluid:** {report.get('fluid')} · **turbulence:** "
                f"{report.get('turbulence')} · **solver:** {report.get('solver')}")
    inlets = report.get("inlets") or {}
    if inlets:
        st.markdown("**Inlets**")
        for name, area in (report.get("inlet_area_m2") or {}).items():
            st.caption(f"Inlet {name}: area {area:.6g} m² "
                       f"({report.get('inlet_area_note', 'measured from the mesh')})")
        st.json(inlets)
    outlets = report.get("outlets") or {}
    if outlets:
        st.markdown("**Outlets** (Pa and kinematic, as written)")
        st.dataframe([{"patch": name, **value} for name, value in outlets.items()],
                     hide_index=True, width="stretch")
    time = report.get("time") or {}
    if time:
        st.markdown("**Time controls**")
        st.caption(str(time.get("estimates", "")))
        st.caption(COURANT_NOTE)
        st.json({k: v for k, v in time.items() if k != "estimates"})
    solution_view(report)
    st.markdown("**Patches as set up**")
    st.dataframe([{"patch": k, "type": v} for k, v in (report.get("patches") or {}).items()],
                 hide_index=True, width="stretch")


def solution_view(report: dict[str, Any]) -> None:
    """The solution settings and why (owner, after M2a): correctors above 70 deg
    of mesh non-orthogonality; the schemes are limited in every case."""
    found = report.get("non_orthogonality")
    if not isinstance(found, dict):
        st.markdown("**Solution settings:** the tutorial's (nCorrectors 1, no "
                    "non-orthogonal correctors); schemes limited (limited corrected 0.33).")
        return
    zone = found.get("zone")
    st.markdown(f"**Solution settings:** max non-orthogonality {found['max_deg']:.1f} deg"
                + (f" in {zone_html(str(zone))}" if zone else "")
                + " (above 70 deg): 2 non-orthogonal correctors, nCorrectors 2, in "
                "system/fvSolution with the reason; schemes limited (limited corrected "
                "0.33), as in every case.", unsafe_allow_html=True)


def render(ctx: state.Context) -> None:
    st.subheader("Case setup")
    stage = ctx.service.last_stages()["case_setup"]
    stage_view(stage, "Case setup")
    if stage and stage.get("reason") in REASONS:
        st.caption(REASONS[str(stage["reason"])])
    report = ctx.service.reports()["case_setup"]
    if report is None:
        st.info("No case has been set up yet. Set the physics, then run.")
        return
    if report.get("stale"):
        st.warning("The case in case/ no longer matches the project: the last case setup did "
                   "not succeed. It is kept (it may hold solver results) and marked stale.")
    if report.get("note"):
        st.info(str(report["note"]))
    if report.get("status") == "SUCCESS":
        values_view(report)
    elif report.get("previous"):
        with st.expander("The last successful case setup"):
            values_view(report["previous"])
    files = ctx.service.case_files()
    if files:
        name = st.selectbox("Case file", list(files), key="machines_case_file")
        if name:
            st.code(files[name], language=None)
