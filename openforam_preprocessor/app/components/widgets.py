"""Presentation helpers: form fields bound to the draft configuration dict.

Each field reads its initial value from the draft and writes the widget
value back, so the draft always mirrors the form. Keys include a
generation counter: bumping it (project switch, discard) recreates widgets
from the draft.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import streamlit as st

from core.issues import Issue, IssueSeverity

GENERATION = "draft_generation"


def _key(path: str) -> str:
    return f"cfg{st.session_state.get(GENERATION, 0)}:{path}"


def get_path(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def set_path(data: dict[str, Any], path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    target = data
    for part in parents:
        target = target.setdefault(part, {})
    target[leaf] = value


def float_field(draft: dict[str, Any], path: str, label: str, *,
                min_value: float | None = None, max_value: float | None = None,
                help: str | None = None) -> None:
    current = get_path(draft, path)
    value = st.number_input(
        label,
        value=None if current is None else float(current),
        min_value=min_value, max_value=max_value, format="%g",
        key=_key(path), help=help,
    )
    set_path(draft, path, value)


def int_field(draft: dict[str, Any], path: str, label: str, *,
              min_value: int | None = None, max_value: int | None = None,
              help: str | None = None) -> None:
    current = get_path(draft, path)
    value = st.number_input(
        label,
        value=None if current is None else int(current),
        min_value=min_value, max_value=max_value, step=1,
        key=_key(path), help=help,
    )
    set_path(draft, path, value)


def text_field(draft: dict[str, Any], path: str, label: str, *,
               help: str | None = None) -> None:
    key = _key(path)
    if key not in st.session_state:
        st.session_state[key] = get_path(draft, path) or ""
    set_path(draft, path, st.text_input(label, key=key, help=help))


def bool_field(draft: dict[str, Any], path: str, label: str, *,
               help: str | None = None) -> None:
    value = st.checkbox(label, value=bool(get_path(draft, path)), key=_key(path), help=help)
    set_path(draft, path, value)


def select_field(draft: dict[str, Any], path: str, label: str, options: Sequence[str],
                 labels: dict[str, str] | None = None, *, placeholder: str = "Select…",
                 help: str | None = None) -> None:
    current = get_path(draft, path)
    value = st.selectbox(
        label,
        options=list(options),
        index=list(options).index(current) if current in options else None,
        format_func=lambda option: (labels or {}).get(option, option),
        placeholder=placeholder,
        key=_key(path),
        help=help,
    )
    set_path(draft, path, value)


def vector_field(draft: dict[str, Any], path: str, label: str, unit: str = "",
                 help: str | None = None) -> None:
    st.markdown(f"**{label}**" + (f" ({unit})" if unit else ""), help=help)
    columns = st.columns(3)
    for column, axis in zip(columns, "xyz", strict=True):
        with column:
            float_field(draft, f"{path}.{axis}", axis)


def set_text_value(path: str, value: str) -> None:
    """Update a text field before it is rendered in this run."""
    st.session_state[_key(path)] = value


_RENDER = {
    IssueSeverity.BLOCKING: st.error,
    IssueSeverity.ERROR: st.error,
    IssueSeverity.WARNING: st.warning,
    IssueSeverity.INFO: st.info,
}


def render_issues(issues: Iterable[Issue]) -> None:
    for issue in issues:
        text = f"**{issue.severity.value}** · {issue.stage.value} · {issue.message}"
        if issue.suggested_action:
            text += f"\n\n*Next step:* {issue.suggested_action}"
        if issue.log_reference:
            text += f"\n\n*Log:* `{issue.log_reference}`"
        _RENDER[issue.severity](text)
        if issue.explanation or issue.details:
            with st.expander(f"Details: {issue.code}"):
                if issue.explanation:
                    st.write(issue.explanation)
                if issue.details:
                    st.json(issue.details)
