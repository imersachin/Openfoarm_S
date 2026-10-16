"""Field groups the machine sections share (presentation only).

The fields write to the draft only when the user changes a value: showing a
section never edits the draft. A machine configuration keeps physics and
patches in name-keyed maps and lists, where an empty or None value written
just by drawing a field would make the draft unreadable (or look unsaved).
A `default` is what an absent value means (the model's default): it is shown
and is not written unless changed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import streamlit as st

from app.components.widgets import get_path, set_path, widget_key
from core.config.models import LengthUnit
from machines.config import PatchType
from vawt.config import Axis

UNIT_LABELS = {
    "m": "metres (m)", "cm": "centimetres (cm)", "mm": "millimetres (mm)",
    "um": "micrometres (µm)", "in": "inches (in)", "ft": "feet (ft)",
}
AXES = [a.value for a in Axis]
PATCH_TYPES = [t.value for t in PatchType]
PATCH_TYPE_LABELS = {
    "INLET": "Inlet", "OUTLET": "Outlet", "WALL": "Wall", "ROTATING_WALL": "Rotating wall",
    "SLIP": "Symmetry or slip",
}


def _write(draft: dict[str, Any], path: str, shown: Any, value: Any) -> None:
    """Write `value` only if it differs from what the draft shows."""
    if value != shown:
        set_path(draft, path, value)


def float_field(draft: dict[str, Any], path: str, label: str, *,
                min_value: float | None = None, max_value: float | None = None,
                default: float | None = None, help: str | None = None) -> None:
    current = get_path(draft, path)
    shown = float(current) if current is not None else default
    value = st.number_input(label, value=shown, min_value=min_value, max_value=max_value,
                            format="%g", key=widget_key(path), help=help)
    _write(draft, path, shown, value)


def int_field(draft: dict[str, Any], path: str, label: str, *,
              min_value: int | None = None, max_value: int | None = None,
              default: int | None = None, help: str | None = None) -> None:
    current = get_path(draft, path)
    shown = int(current) if current is not None else default
    value = st.number_input(label, value=shown, min_value=min_value, max_value=max_value,
                            step=1, key=widget_key(path), help=help)
    _write(draft, path, shown, value)


def text_field(draft: dict[str, Any], path: str, label: str, *,
               help: str | None = None) -> None:
    key = widget_key(path)
    shown = get_path(draft, path) or ""
    if key not in st.session_state:
        st.session_state[key] = shown
    value = st.text_input(label, key=key, help=help)
    if value != shown:
        set_path(draft, path, value or None)


def bool_field(draft: dict[str, Any], path: str, label: str, *,
               default: bool = False, help: str | None = None) -> None:
    current = get_path(draft, path)
    shown = bool(current) if current is not None else default
    value = st.checkbox(label, value=shown, key=widget_key(path), help=help)
    _write(draft, path, shown, value)


def select_field(draft: dict[str, Any], path: str, label: str, options: Sequence[str],
                 labels: dict[str, str] | None = None, *, placeholder: str = "Select…",
                 default: str | None = None, help: str | None = None) -> None:
    current = get_path(draft, path)
    shown: str | None = str(current) if current is not None else default
    choices = list(options)
    index = choices.index(shown) if shown in choices else None
    value = st.selectbox(
        label, options=choices, index=index,
        format_func=lambda option: (labels or {}).get(option, option),
        placeholder=placeholder, key=widget_key(path), help=help)
    _write(draft, path, shown, value)


def vector_field(draft: dict[str, Any], path: str, label: str, unit: str = "",
                 help: str | None = None) -> None:
    st.markdown(f"**{label}**" + (f" ({unit})" if unit else ""), help=help)
    columns = st.columns(3)
    for column, axis in zip(columns, "xyz", strict=True):
        with column:
            float_field(draft, f"{path}.{axis}", axis)


def stl_fields(draft: dict[str, Any], path: str, *, transform: bool = True) -> None:
    """Units (required, never assumed) and the transform of one STL source."""
    source = get_path(draft, f"{path}.source_path")
    st.caption(f"File: `{source}`" if source else "File: none yet")
    select_field(draft, f"{path}.source_units", "Units of the STL file",
                 [u.value for u in LengthUnit], UNIT_LABELS,
                 placeholder="Choose the units (never assumed)")
    if transform:
        float_field(draft, f"{path}.scale", "Scale", min_value=0.0, default=1.0)
        vector_field(draft, f"{path}.rotation_deg", "Rotation", "degrees, X then Y then Z")
        vector_field(draft, f"{path}.translation", "Translation", "m")


def axis_field(draft: dict[str, Any], path: str, label: str, help: str | None = None) -> None:
    select_field(draft, path, label, AXES, {a: f"+{a}" for a in AXES},
                 placeholder="Choose (never assumed)", help=help)


def number_fields(draft: dict[str, Any], prefix: str) -> None:
    """One field per value of a flat settings group, typed by its current value."""
    for name, value in sorted((get_path(draft, prefix) or {}).items()):
        path, label = f"{prefix}.{name}", name.replace("_", " ")
        if isinstance(value, bool):
            bool_field(draft, path, label)
        elif isinstance(value, int):
            int_field(draft, path, label)
        else:
            float_field(draft, path, label)


def new_stl(path: str) -> dict[str, Any]:
    """A fresh StlSource: the file only; the units are left for the user."""
    zero = {"x": 0.0, "y": 0.0, "z": 0.0}
    return {"source_path": path, "scale": 1.0, "rotation_deg": dict(zero),
            "translation": dict(zero)}
