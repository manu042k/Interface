"""ST-008/009: Perception Module.

Turns an adapter's `RawSnapshot` into a normalized `SurfaceState` — the single
representation the discovery model and the locator engine both reason from.

Degrades deliberately: when a page has no useful accessibility info (hostile
legacy markup), the AX summary is thin but the DOM outline + screenshot still
carry the state, rather than returning something empty.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from ..models import ActionType, SurfaceState
from .base import Action, RawSnapshot, SurfaceAdapter

_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
_INTERESTING_TAGS = {
    "form", "input", "select", "option", "textarea", "button", "a",
    "table", "tr", "th", "td", "h1", "h2", "h3", "label", "legend", "fieldset",
}


class Perception:
    def __init__(self, *, max_dom_chars: int = 6000, max_ax_lines: int = 120) -> None:
        self._max_dom = max_dom_chars
        self._max_ax = max_ax_lines

    async def observe(
        self,
        adapter: SurfaceAdapter,
        session_handle: str,
        *,
        sink: Any | None = None,
        run_id: str | None = None,
        step: int | None = None,
    ) -> SurfaceState:
        raw = await adapter.snapshot(session_handle)
        ax_summary = _summarize_ax(raw.ax_tree, self._max_ax)
        outline = _dom_outline(raw.html, self._max_dom)
        parts: list[str] = []
        if raw.visible_text:
            parts.append("VISIBLE PAGE TEXT:\n" + raw.visible_text)
        fields = _summarize_fields(raw.form_values)
        if fields:
            parts.append(fields)
        parts.append("DOM OUTLINE:\n" + outline)
        dom_excerpt = "\n\n".join(parts)
        fingerprint = _fingerprint(raw)

        screenshot_ref: str | None = None
        if raw.screenshot_png and sink is not None and run_id is not None:
            screenshot_ref = sink.put_evidence(
                run_id, step, "screenshot", raw.screenshot_png, {"url": raw.url}
            )

        return SurfaceState(
            url=raw.url,
            title=raw.title,
            ax_summary=ax_summary,
            dom_excerpt=dom_excerpt,
            screenshot_ref=screenshot_ref,
            fingerprint=fingerprint,
        )

    async def extract(
        self,
        adapter: SurfaceAdapter,
        session_handle: str,
        target_description: Any,
        expected_shape: str = "string",
    ) -> tuple[Any | None, str | None]:
        """Returns (value, error). error names expected-vs-observed (feeds ST-034)."""
        res = await adapter.execute(
            session_handle,
            Action(
                type=ActionType.EXTRACT,
                target_description=target_description,
                expected_shape=expected_shape,
            ),
        )
        if res.ok:
            return res.extracted, None
        return None, res.error or f"could not extract {target_description!r}"


# ---------------------------------------------------------------------------


def _summarize_fields(values: list[dict[str, Any]]) -> str:
    """A compact 'what the form controls currently hold' block — the serialized
    DOM doesn't reflect typed values, so without this the agent re-types fields."""
    if not values:
        return ""
    lines = ["CURRENT FORM FIELD VALUES (already entered — do not re-type if correct):"]
    for f in values[:25]:
        who = f.get("label") or f.get("name") or f.get("id") or f.get("type") or f["tag"]
        val = f.get("value")
        shown = f'"{val}"' if val else "(empty)"
        if f.get("untouched"):
            shown += "  <-- still the DEFAULT option, not chosen yet"
        lines.append(f"  - {who}: {shown}")
    return "\n".join(lines)


def _summarize_ax(ax_tree: list[dict[str, Any]], max_lines: int) -> str:
    lines: list[str] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        if len(lines) >= max_lines or not isinstance(node, dict):
            return
        role = node.get("role", "")
        name = (node.get("name") or "").strip()
        value = (node.get("value") or "").strip()
        if role and role not in {"WebArea", "generic", "none", "presentation"}:
            bit = f"{'  ' * depth}- {role}"
            if name:
                bit += f' "{name[:80]}"'
            if value:
                bit += f" = {value[:40]!r}"
            lines.append(bit)
            depth += 1
        for child in node.get("children", []) or []:
            walk(child, depth)

    for root in ax_tree:
        walk(root, 0)
    return "\n".join(lines) if lines else "(no accessible structure — legacy markup; rely on DOM outline + screenshot)"


def _dom_outline(html: str, max_chars: int) -> str:
    if not html:
        return ""
    # drop script/style/head noise
    html = re.sub(r"<script\b.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<style\b.*?</style>", "", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)

    out: list[str] = []
    depth = 0
    pos = 0
    for m in _TAG_RE.finditer(html):
        closing, tag = m.group(1), m.group(2).lower()
        text = re.sub(r"\s+", " ", html[pos:m.start()]).strip()
        pos = m.end()
        if text and depth <= 12:
            out.append(f"{'  ' * min(depth, 8)}{text[:160]}")
        if tag not in _INTERESTING_TAGS:
            continue
        if closing:
            depth = max(0, depth - 1)
        else:
            attrs = _keep_attrs(m.group(0))
            out.append(f"{'  ' * min(depth, 8)}<{tag}{attrs}>")
            if tag not in {"input", "option"}:
                depth += 1
        if sum(len(x) for x in out) > max_chars:
            out.append("... (truncated)")
            break
    return "\n".join(out)


def _keep_attrs(tag_html: str) -> str:
    keep = []
    for attr in ("id", "name", "type", "value", "href", "role", "aria-label", "placeholder", "data-testid"):
        m = re.search(rf'{attr}\s*=\s*"([^"]*)"', tag_html)
        if m:
            keep.append(f' {attr}="{m.group(1)[:60]}"')
    return "".join(keep)


def _fingerprint(raw: RawSnapshot) -> str:
    """Structural signature: the tag skeleton + form field names, no text values.
    Stable across content changes, sensitive to layout/markup drift."""
    skeleton = "".join(sorted(t.lower() for _, t in _TAG_RE.findall(raw.html)))
    names = "".join(sorted(re.findall(r'name\s*=\s*"([^"]+)"', raw.html)))
    path = re.sub(r"\d+", "#", raw.url.split("?")[0])
    return hashlib.sha1(f"{path}\n{skeleton}\n{names}".encode()).hexdigest()[:16]
