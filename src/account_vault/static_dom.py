"""Offline perceive/act backend for a static HTML file.

DEVIATION (new capability, no upstream equivalent): jev-ultrafast can only
drive a live Chrome over CDP, so its loop cannot run in CI, in unit tests
(upstream tests must mock the browser), or against a local fixture without
a browser daemon. This backend implements the exact same browser contract
— observe() -> page dict with url/title/text/actions/fingerprint/marker/
page_key/guards, fresh(page, action), act(action, page, text) — by parsing
HTML with the standard library and mutating an in-memory DOM.

Semantics intentionally mirror snapshot.js, including this port's coverage
fixes (date/time/range inputs, bare contenteditable, [onclick] rows and
the #23 second pass -- pointer rows, icon-only controls, concealed-input
label proxies -- are all enumerated; select execution uses option_index). Interactive behaviour is
limited to what static fixtures need: fill, check/radio, select, in-page
anchors, relative-file navigation, and revealing a hidden results region on
submit (see _submit). There is no JavaScript execution; a fixture that
needs script behaviour belongs on the CDP backend.
"""

import datetime
import html
import math
import re
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, overload
from urllib.parse import unquote, urlparse

from .browser import NATIVE_FILL_TYPES, StalePage, fingerprint

VOID = {"input", "br", "img", "hr", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
ROLES = {
    "button",
    "link",
    "checkbox",
    "radio",
    "switch",
    "tab",
    "menuitem",
    "menuitemradio",
    "option",
    "gridcell",
    "combobox",
    "textbox",
    "searchbox",
    "spinbutton",
    "slider",
}
SAFE_EXCLUDE = {"password", "file", "hidden"}

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_MONTH_RE = re.compile(r"(\d{4})-(\d{2})\Z")
_WEEK_RE = re.compile(r"(\d{4})-W(\d{2})\Z")
_TIME_RE = re.compile(r"\d{2}:\d{2}(:\d{2}(\.\d{1,3})?)?\Z")
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}\Z")


def valid_native_value(input_type: str, text: str) -> bool:
    """Mirror the browser's value sanitisation for NATIVE_FILL_TYPES.

    The CDP backend lets Chrome judge (an invalid date/time string
    sanitises to empty); offline there is no browser, so the same
    shapes are checked here and a bad value is rejected identically
    instead of being stored as if the fill worked (upstream #227).
    Range bounds/step are the browser's business on the live path and
    are not modelled here beyond finiteness, matching the JS guard.
    """
    try:
        if input_type == "date":
            if not _DATE_RE.match(text):
                return False
            datetime.date.fromisoformat(text)
            return True
        if input_type == "month":
            match = _MONTH_RE.match(text)
            return match is not None and 1 <= int(match.group(2)) <= 12
        if input_type == "week":
            match = _WEEK_RE.match(text)
            if match is None:
                return False
            datetime.date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
            return True
        if input_type == "time":
            if not _TIME_RE.match(text):
                return False
            datetime.time.fromisoformat(text)
            return True
        if input_type == "datetime-local":
            date_part, separator, time_part = text.partition("T")
            if not separator:
                date_part, separator, time_part = text.partition(" ")
            return bool(separator) and valid_native_value("date", date_part) and valid_native_value("time", time_part)
        if input_type == "color":
            return _COLOR_RE.match(text) is not None
        if input_type == "range":
            return bool(text.strip()) and math.isfinite(float(text))
    except ValueError:
        return False
    return True


class Element:
    __slots__ = ("tag", "attrs", "children", "parent", "node", "value", "checked", "selected_index", "text_override")

    tag: str
    attrs: dict[str, str]
    children: list["Element | str"]
    parent: "Element | None"
    node: int
    value: str | None
    checked: bool
    selected_index: int | None
    text_override: str | None

    def __init__(self, tag: str, attrs: dict[str, str], parent: "Element | None", node: int) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children = []
        self.parent = parent
        self.node = node
        self.value = None  # None = fall back to attrs/content
        self.checked = "checked" in attrs
        self.selected_index = None
        self.text_override = None

    @overload
    def get(self, key: str, default: None = None) -> str | None: ...
    @overload
    def get(self, key: str, default: str) -> str: ...

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.attrs.get(key, default)

    def inner_text(self) -> str:
        if self.text_override is not None:
            return self.text_override
        parts: list[str] = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.inner_text())
        return " ".join(" ".join(parts).split())


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#root", {}, None, 0)
        self.stack: list[Element] = [self.root]
        self.next_node = 1
        self.title = ""

    def _add(self, tag: str, attrs: list[tuple[str, str | None]]) -> Element:
        clean = {k: (v if v is not None else "") for k, v in attrs}
        element = Element(tag, clean, self.stack[-1], self.next_node)
        self.next_node += 1
        self.stack[-1].children.append(element)
        return element

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = self._add(tag, attrs)
        if tag not in VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._add(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.stack[-1].children.append(data)
        # An SVG <title> names its icon (see _icon_name); only the
        # document <title> may become the page title.
        if self.stack[-1].tag == "title" and not any(e.tag == "svg" for e in self.stack[:-1]):
            self.title += data


def is_static_source(source: str | Path) -> bool:
    text = str(source)
    if text.startswith("file://"):
        return True
    return Path(text).exists() and Path(text).is_file()


def _source_path(source: str | Path) -> Path:
    text = str(source)
    if text.startswith("file://"):
        return Path(unquote(urlparse(text).path))
    return Path(text)


class StaticBrowser:
    """Browser-contract backend over one static HTML document (see module doc)."""

    path: Path | None
    url: str
    last_navigation: str | None
    root: Element
    title: str
    by_node: dict[int, Element]
    by_id: dict[str, Element]
    labels_for: dict[str, Element]

    def __init__(self, source: str | Path) -> None:
        self.path = _source_path(source).resolve()
        self.url = self.path.as_uri()
        self.last_navigation = None
        self._load(self.path.read_text(encoding="utf-8"))

    @classmethod
    def from_html(cls, markup: str, url: str = "https://example.test/") -> "StaticBrowser":
        browser = cls.__new__(cls)
        browser.path = None
        browser.url = url
        browser.last_navigation = None
        browser._load(markup)
        return browser

    def _load(self, markup: str) -> None:
        # (marker, page_key, guards) of the last _build(); see fresh().
        self._fresh_cache: tuple[Any, Any, Any] | None = None
        parser = _Parser()
        parser.feed(markup)
        self.root = parser.root
        self.title = parser.title.strip()
        self.by_node = {}
        self.by_id = {}
        self.labels_for = {}
        for element in self._walk(self.root):
            self.by_node[element.node] = element
            element_id = element.get("id")
            if element_id:
                self.by_id.setdefault(element_id, element)
            for_id = element.get("for")
            if element.tag == "label" and for_id:
                self.labels_for.setdefault(for_id, element)
        # Initialise select state from `selected` attributes (default: first option).
        for element in self._walk(self.root):
            if element.tag == "select":
                options = self._options(element)
                element.selected_index = next(
                    (i for i, option in enumerate(options) if "selected" in option.attrs),
                    0 if options else -1,
                )

    def _walk(self, element: Element) -> Iterator[Element]:
        yield element
        for child in element.children:
            if not isinstance(child, str):
                yield from self._walk(child)

    def _options(self, select: Element) -> list[Element]:
        return [e for e in self._walk(select) if e.tag == "option"]

    # -- perception helpers (mirror snapshot.js) ---------------------------

    def _visible(self, element: Element) -> bool:
        current: Element | None = element
        while current is not None and current.tag != "#root":
            if "hidden" in current.attrs or current.get("aria-hidden") == "true" or "inert" in current.attrs:
                return False
            style = re.sub(r"\s+", "", current.get("style", "").lower())
            if "display:none" in style or "visibility:hidden" in style:
                return False
            current = current.parent
        return True

    def _style_map(self, element: Element) -> dict[str, str]:
        # Inline declarations only: offline there is no cascade, so a
        # fixture declares cursor/position/concealment where the live
        # backend would compute it (stylesheets stay a live-only signal).
        result: dict[str, str] = {}
        for declaration in element.get("style", "").split(";"):
            key, separator, value = declaration.partition(":")
            if separator:
                result[key.strip().lower()] = " ".join(value.split()).lower()
        return result

    def _concealed(self, element: Element) -> bool:
        # Mirror of snapshot.js concealed(): no hittable box of its own.
        if not self._visible(element):
            return True
        style = self._style_map(element)
        if style.get("opacity") == "0":
            return True
        return style.get("width") in {"0", "0px"} or style.get("height") in {"0", "0px"}

    def _label_for(self, element: Element) -> Element | None:
        element_id = element.get("id")
        if element_id and element_id in self.labels_for:
            return self.labels_for[element_id]
        current = element.parent
        while current is not None and current.tag != "#root":
            if current.tag == "label":
                return current
            current = current.parent
        return None

    def _icon_name(self, element: Element) -> str:
        # Mirror of snapshot.js iconName(): name an icon-only control
        # from its svg (title, aria-label, id, icon-* class token).
        svg = next((e for e in self._walk(element) if e.tag == "svg"), None)
        if svg is None:
            return ""
        title = next((e for e in self._walk(svg) if e.tag == "title"), None)
        if title is not None and title.inner_text():
            return title.inner_text()[:120]
        aria = (svg.get("aria-label") or "").strip()
        if aria:
            return aria[:120]
        svg_id = svg.get("id")
        if svg_id:
            return svg_id[:120]
        for token in svg.get("class", "").split():
            match = re.search(r"icon-([a-z0-9-]+)$", token, flags=re.I)
            if match:
                return match.group(1)[:120]
        return ""

    def _in_overlay(self, element: Element) -> bool:
        current, depth = element.parent, 0
        while current is not None and current.tag != "#root" and depth < 8:
            if self._style_map(current).get("position") in {"absolute", "fixed"}:
                return True
            current, depth = current.parent, depth + 1
        return False

    def _pointer_list(self, element: Element) -> bool:
        parent = element.parent
        if parent is None:
            return False
        listed = parent.tag in {"ul", "ol"} or parent.get("role") in {"list", "listbox", "menu", "tablist"}
        if not listed:
            return False
        siblings = [
            child
            for child in parent.children
            if not isinstance(child, str)
            and child is not element
            and child.tag == element.tag
            and self._visible(child)
            and self._style_map(child).get("cursor") == "pointer"
        ]
        return len(siblings) >= 2

    def _disabled(self, element: Element) -> bool:
        if "disabled" in element.attrs:
            return True
        current = element.parent
        while current is not None:
            if current.get("aria-disabled") == "true":
                return True
            current = current.parent
        return False

    def _text_without(self, element: Element, skip: Element) -> str:
        # inner_text() with one subtree omitted. snapshot.js name() puts
        # the named element in its seen set, so a <label> wrapping (or
        # pointing at) a field never contributes the field's own text --
        # a textarea's default value, a select's options -- to the name.
        if element is skip:
            return ""
        if element.text_override is not None:
            return element.text_override
        parts = [child if isinstance(child, str) else self._text_without(child, skip) for child in element.children]
        return " ".join(" ".join(parts).split())

    def _name(self, element: Element) -> str:
        labelledby = element.get("aria-labelledby", "")
        if labelledby:
            text = " ".join(self.by_id[i].inner_text() for i in labelledby.split() if i in self.by_id).strip()
            if text:
                return text
        aria_label = element.get("aria-label")
        if aria_label:
            return aria_label
        element_id = element.get("id")
        if element_id and element_id in self.labels_for:
            text = self._text_without(self.labels_for[element_id], element)
            if text:
                return text
        current = element.parent
        while current is not None:
            if current.tag == "label":
                text = self._text_without(current, element)
                if text:
                    return text
                break
            current = current.parent
        value = element.get("value")
        if element.tag == "input" and element.get("type", "") in {"button", "submit", "reset"} and value:
            return value
        alt = element.get("alt")
        if alt:
            return alt
        # Same DEVIATION as snapshot.js (upstream PR #246): a textarea's
        # inner text is its default value, not its name; like INPUT it
        # falls through to title/placeholder instead of its content.
        if element.tag not in {"input", "textarea"}:
            text = element.inner_text()
            if text:
                return text
        return element.get("title") or element.get("placeholder") or ""

    def _role(self, element: Element) -> str | None:
        explicit = element.get("role")
        if explicit in ROLES:
            return explicit
        if element.tag in {"button", "summary"}:
            return "button"
        if element.tag == "a" and "href" in element.attrs:
            return "link"
        if element.tag == "select":
            return "combobox"
        if element.tag == "textarea" or "contenteditable" in element.attrs:
            return "textbox"
        if element.tag == "input":
            kind = element.get("type", "text").lower() or "text"
            if kind in {"checkbox", "radio"}:
                return kind
            if kind in {"button", "submit", "reset", "image"}:
                return "button"
            if kind == "search":
                return "searchbox"
            if kind == "number":
                return "spinbutton"
            if kind in {"text", "email", "url", "tel"}:
                return "textbox"
            # Same DEVIATION as snapshot.js (#215/#227): value-bearing native
            # types are fillable instead of invisible.
            if kind in {"date", "time", "datetime-local", "month", "week", "color"}:
                return "textbox"
            if kind == "range":
                return "slider"
            return None
        # Same DEVIATION as snapshot.js (#23): clickable non-native rows.
        if "onclick" in element.attrs:
            return "button"
        # Same DEVIATION as snapshot.js (#23): a label standing in for
        # its concealed radio/checkbox keeps the input's role in guards.
        if element.tag == "label":
            inner = next(
                (
                    e
                    for e in self._walk(element)
                    if e.tag == "input" and e.get("type", "").lower() in {"radio", "checkbox"}
                ),
                None,
            )
            if inner is None and element.get("for"):
                inner = self.by_id.get(element.get("for") or "")
            if inner is not None and inner.get("type", "").lower() in {"radio", "checkbox"} and self._concealed(inner):
                return inner.get("type", "").lower()
        return None

    def _value(self, element: Element) -> str:
        if element.value is not None:
            return element.value
        if element.tag == "textarea":
            return element.inner_text()
        if "contenteditable" in element.attrs:
            return element.inner_text()
        if element.tag == "select":
            options = self._options(element)
            selected = element.selected_index or 0
            if options and 0 <= selected < len(options):
                return options[selected].get("value", options[selected].inner_text())
            return ""
        return element.get("value", "")

    def _scope_text(self, element: Element) -> str:
        current = element.parent
        while current is not None and current.tag != "#root":
            scoped = current.tag in {"form", "fieldset", "dialog", "article", "li", "tr"}
            if scoped or current.get("role") in {"dialog", "row"}:
                return current.inner_text()[:600]
            current = current.parent
        return ""

    def _guard(self, element: Element) -> list[Any] | None:
        if not self._visible(element):
            return None
        options = self._options(element) if element.tag == "select" else []
        has_value = element.tag in {"input", "textarea", "select"} or "contenteditable" in element.attrs
        scope = self._scope_text(element) or (element.parent.inner_text()[:6000] if element.parent else "")
        return [
            element.node,
            self._role(element),
            self._name(element),
            self._value(element) if has_value else None,
            element.checked if element.tag == "input" else None,
            element.selected_index if element.tag == "select" else None,
            "readonly" in element.attrs,
            self._disabled(element),
            element.get("aria-disabled"),
            element.get("aria-expanded"),
            element.get("aria-checked"),
            element.get("aria-selected"),
            element.get("href"),
            scope,
            len(options),
        ]

    def _build(self) -> dict[str, Any]:
        actions: list[dict[str, Any]] = []
        for element in self._walk(self.root):
            if element.tag in {"#root", "script", "style", "head", "title", "meta", "link"}:
                continue
            input_type = element.get("type", "").lower() if element.tag == "input" else ""
            if input_type in SAFE_EXCLUDE or not self._visible(element) or self._disabled(element):
                continue
            # Concealed radios/checkboxes are re-offered via their label
            # in the second pass below (mirror of snapshot.js, #23).
            if input_type in {"radio", "checkbox"} and self._concealed(element):
                continue
            # Enumerate the same candidate set as snapshot.js's selector.
            candidate = (
                (element.tag == "a" and "href" in element.attrs)
                or element.tag in {"button", "input", "textarea", "select", "summary"}
                or "contenteditable" in element.attrs
                or "onclick" in element.attrs
                or element.get("role") in ROLES
            )
            role = self._role(element) if candidate else None
            if role is None:
                continue
            nested_button = any(
                e.tag == "button" or e.get("role") == "button" for e in self._walk(element) if e is not element
            )
            if role == "gridcell" and nested_button:
                continue
            base: dict[str, Any] = {"node": element.node, "role": role, "label": self._name(element) or role}
            if element.tag == "input" and input_type:
                base["input_type"] = input_type
            for attr, key in (("id", "dom_id"), ("name", "name"), ("placeholder", "placeholder")):
                attr_value = element.get(attr)
                if attr_value:
                    base[key] = attr_value
            scope = self._scope_text(element)
            if scope:
                base["scope"] = scope
            for key in ("checked", "selected", "expanded"):
                if element.get(f"aria-{key}") is not None:
                    base[key] = element.get(f"aria-{key}")
            if input_type in {"checkbox", "radio"}:
                base["checked"] = str(element.checked).lower()
            if element.tag == "select":
                options = self._options(element)
                selected = element.selected_index
                current = options[selected].inner_text() if options and selected is not None and selected >= 0 else ""
                for option_index, option in enumerate(options):
                    if option_index == element.selected_index or "disabled" in option.attrs:
                        continue
                    actions.append(
                        {
                            **base,
                            "kind": "select",
                            "value": option.get("value", option.inner_text()),
                            "option_index": option_index,
                            "current_value": current,
                            "label": f"{base['label']} → {option.inner_text()}",
                        }
                    )
            else:
                editable = (
                    "readonly" not in element.attrs
                    and element.get("aria-readonly") != "true"
                    and (
                        role in {"textbox", "searchbox", "spinbutton", "slider"}
                        or (role == "combobox" and element.tag in {"input", "textarea"})
                    )
                )
                value = self._value(element)
                actions.append({**base, "kind": "fill" if editable else "click", "value": value})
                if editable:
                    actions.append({**base, "kind": "click", "value": value, "label": "Open " + base["label"]})
        # Second pass (mirror of snapshot.js, upstream issue #23 second
        # half): label proxies for concealed radios/checkboxes, then
        # non-onclick clickable rows / icon-only controls. Same gates:
        # click evidence + label + list/overlay context unless wiring or
        # icon speaks for itself, innermost only, capped at 40 extras.
        collected = {action["node"] for action in actions}
        extras = 0
        for element in self._walk(self.root):
            if extras >= 40:
                break
            if element.tag != "input" or element.get("type", "").lower() not in {"radio", "checkbox"}:
                continue
            if self._disabled(element) or not self._concealed(element):
                continue
            label_el = self._label_for(element)
            if label_el is None or not self._visible(label_el) or self._disabled(label_el):
                continue
            if label_el.node in collected:
                continue
            collected.add(label_el.node)
            kind = element.get("type", "").lower()
            actions.append(
                {
                    "node": label_el.node,
                    "role": kind,
                    "label": self._name(label_el) or self._name(element) or kind,
                    "kind": "click",
                    "value": "",
                    "checked": str(element.checked).lower(),
                    "input_type": kind,
                }
            )
            extras += 1

        def _is_control(el: Element) -> bool:
            return (
                el.tag in {"button", "input", "textarea", "select", "summary"}
                or (el.tag == "a" and "href" in el.attrs)
                or "contenteditable" in el.attrs
                or "onclick" in el.attrs
                or el.get("role") in ROLES
            )

        candidates: list[tuple[Element, str, bool, bool, str]] = []
        for element in self._walk(self.root):
            if len(candidates) >= 80:
                break
            if element.tag not in {"li", "div", "span", "td", "dd", "p"}:
                continue
            if not self._visible(element) or self._disabled(element) or _is_control(element):
                continue
            ancestor, inside = element.parent, False
            while ancestor is not None and ancestor.tag != "#root":
                if _is_control(ancestor):
                    inside = True
                    break
                ancestor = ancestor.parent
            if inside or any(_is_control(e) for e in self._walk(element) if e is not element):
                continue
            pointer = self._style_map(element).get("cursor") == "pointer"
            try:
                tab_stop = int(element.get("tabindex") or "-1") >= 0
            except ValueError:
                tab_stop = False
            strong = tab_stop or "data-clickable" in element.attrs or "data-action" in element.attrs
            if not pointer and not strong:
                continue
            icon = self._icon_name(element)
            label_text = self._name(element).strip()[:120] or icon
            if not label_text:
                continue
            listed = self._pointer_list(element)
            if not strong and not icon and not listed and not self._in_overlay(element):
                continue
            candidates.append((element, label_text, listed, strong, icon))
        # Same row-collapse as snapshot.js: a span whose only evidence is
        # inherited from a candidate row does not split the row in two.
        candidate_elements = {el for el, _t, _l, _s, _i in candidates}
        rows = [entry for entry in candidates if entry[3] or entry[4] or entry[0].parent not in candidate_elements]
        candidate_nodes = {el.node for el, _t, _l, _s, _i in rows}
        for element, label_text, listed, _strong, _icon in rows:
            if extras >= 40:
                break
            if element.node in collected:
                continue
            if any(e.node in candidate_nodes for e in self._walk(element) if e is not element):
                continue  # innermost only: a nested candidate speaks for this subtree
            collected.add(element.node)
            extra: dict[str, Any] = {
                "node": element.node,
                "role": "option" if listed else "button",
                "label": label_text,
                "kind": "click",
                "value": "",
            }
            if element.get("id"):
                extra["dom_id"] = element.get("id")
            actions.append(extra)
            extras += 1
        words: list[str] = []
        length = 0
        for element in self._walk(self.root):
            skipped = element.tag in {"script", "style", "noscript", "template", "head", "title"}
            if skipped or not self._visible(element):
                continue
            if element.text_override and length < 6000:
                words.append(" ".join(element.text_override.split()))
                length += len(element.text_override)
                continue
            for child in element.children:
                if isinstance(child, str) and child.strip() and length < 6000:
                    words.append(" ".join(child.split()))
                    length += len(child)
        text = "\n".join(words)[:6000]
        page_key = [
            self.url,
            [
                [e.node, self._value(e), e.checked, e.selected_index, "disabled" in e.attrs, "readonly" in e.attrs]
                for e in self._walk(self.root)
                if e.tag in {"input", "textarea", "select"} and e.get("type", "").lower() not in SAFE_EXCLUDE
            ],
        ]
        guards: dict[str, Any] = {}
        for action in actions:
            if str(action["node"]) not in guards:
                guards[str(action["node"])] = self._guard(self.by_node[action["node"]])
        semantics = [dict(action) for action in actions]
        marker: list[Any] = [self.url, self.title, text, semantics, page_key[1]]
        omitted = max(0, len(actions) - 250)
        actions = actions[:250]
        for index, action in enumerate(actions):
            action["id"] = f"e{index + 1}"
        actions.append({"id": "wait", "kind": "wait", "label": "Wait for the page to update"})
        page: dict[str, Any] = {
            "url": self.url,
            "title": self.title,
            "w": 1120,
            "h": 780,
            "text": text,
            "scroll": {"y": 0, "height": 780},
            "actions": actions,
            "marker": marker,
            "page_key": page_key,
            "guards": guards,
            "omitted_actions": omitted,
        }
        page["fingerprint"] = fingerprint(page)
        # Freshness cache for fresh() below: the marker/guards this
        # build just computed, valid until act() or _load() clears it.
        self._fresh_cache = (marker, page_key, guards)
        return page

    # -- browser contract ---------------------------------------------------

    def observe(self, screenshot: bool = False) -> dict[str, Any]:
        # Static pages have no pixels; screenshots are simply absent. Agent
        # recording checks for the key before writing (live backend always
        # provides one when requested).
        return self._build()

    def fresh(self, page: dict[str, Any], action: dict[str, Any] | None = None) -> bool:
        # DEVIATION (docs/latency-results.md): this used to rebuild the
        # whole element table on every call (3-4 builds per loop step,
        # 93-96% of static step time). The static DOM mutates only
        # through act() and navigation (_load), both of which clear
        # the cache _build() leaves behind, so an unmutated page can
        # answer from that build. observe() still rebuilds always.
        current = self._fresh_cache
        if current is None:
            self._build()
            current = self._fresh_cache
        assert current is not None  # _build always populates it
        marker, page_key, guards = current
        if action is not None and action.get("kind") in {"click", "select"}:
            node = action.get("node")
            if type(node) is not int:
                return False
            now = [page_key, guards.get(str(node))]
            return bool(now == [page["page_key"], page["guards"].get(str(node))])
        return bool(marker == page["marker"])

    def act(self, action: dict[str, Any], page: dict[str, Any], text: str | None = None) -> dict[str, Any]:
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        kind = action["kind"]
        if kind == "wait":
            return {"executed": action["id"]}
        if kind == "scroll":
            return {"executed": action["id"]}
        node = action.get("node")
        element = self.by_node.get(node) if type(node) is int else None
        # From here the branches below may mutate the DOM (or raise
        # trying): drop the freshness cache first so no later fresh()
        # can answer from the pre-act build.
        self._fresh_cache = None
        if element is None or not self._visible(element) or self._disabled(element):
            if kind == "select":
                raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
            raise StalePage("Target changed or is covered. Observe again.")
        if kind == "fill":
            if "readonly" in element.attrs or element.get("aria-readonly") == "true":
                raise StalePage("Target changed or is covered. Observe again.")
            # Same verdict as the CDP native fill (#227): an unusable
            # value fails loudly with the field left untouched, instead
            # of being stored as if the fill had committed.
            input_type = element.get("type", "").lower() if element.tag == "input" else ""
            if input_type in NATIVE_FILL_TYPES:
                if not text or not text.strip():
                    raise RuntimeError(f"Native input fill rejected: no value supplied for {input_type} input")
                if not valid_native_value(input_type, text):
                    raise RuntimeError(f"Native input fill rejected: not a valid {input_type} value: {text}")
            if "contenteditable" in element.attrs or element.tag == "textarea":
                element.text_override = text or ""
            element.value = text or ""
        elif kind == "select":
            # Same DEVIATION as browser.py (#216): exact option_index, never
            # first-value-match.
            options = self._options(element)
            index = action.get("option_index")
            if not isinstance(index, int) or not (0 <= index < len(options)) or "disabled" in options[index].attrs:
                raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
            element.selected_index = index
        elif kind == "click":
            self._click(element)
        return {"executed": action["id"]}

    def _click(self, element: Element) -> None:
        # A label proxy (#23) activates its concealed control, exactly
        # as a real click on the label does in a browser.
        if element.tag == "label":
            inner = next((e for e in self._walk(element) if e.tag == "input"), None)
            if inner is None and element.get("for"):
                inner = self.by_id.get(element.get("for") or "")
            if inner is not None:
                self._click(inner)
                return
        input_type = element.get("type", "").lower() if element.tag == "input" else ""
        if input_type == "checkbox":
            element.checked = not element.checked
            return
        if input_type == "radio":
            name = element.get("name")
            for other in self._walk(self.root):
                if other.tag == "input" and other.get("type", "").lower() == "radio" and other.get("name") == name:
                    other.checked = other is element
            return
        href = element.get("href")
        if element.tag == "a" and href:
            self.last_navigation = href
            if href.startswith("#"):
                return
            if self.path is not None and not urlparse(href).scheme:
                target = (self.path.parent / href.split("#")[0]).resolve()
                if target.exists():
                    self.path = target
                    self.url = target.as_uri()
                    self._load(target.read_text(encoding="utf-8"))
            return
        # Submit-like buttons (and explicit data-reveals controls) reveal the
        # fixture's results region. Upstream has no equivalent: a live page
        # runs its own script. Keeping this tiny and attribute-driven means
        # fixtures declare their behaviour instead of the backend guessing.
        self._submit(element)

    def _submit(self, element: Element) -> None:
        targets: list[Element] = []
        reveals = element.get("data-reveals", "")
        for token in reveals.split(","):
            token = token.strip().lstrip("#")
            if token and token in self.by_id:
                targets.append(self.by_id[token])
        if not targets:
            form = element.parent
            while form is not None and form.tag != "form":
                form = form.parent
            scope = form if form is not None else self.root
            targets = [e for e in self._walk(scope) if "data-results" in e.attrs]
            if not targets and "results" in self.by_id:
                targets = [self.by_id["results"]]
        values: dict[str, str] = {}
        for field in self._walk(self.root):
            if field.tag in {"input", "textarea", "select"} or "contenteditable" in field.attrs:
                key = field.get("name") or field.get("id")
                if key:
                    values[key] = html.escape(str(self._value(field)))
        for target in targets:
            target.attrs.pop("hidden", None)
            style = target.get("style", "")
            target.attrs["style"] = re.sub(r"display\s*:\s*none;?", "", style, flags=re.I)
            template = target.get("data-template")
            if template:
                rendered = template
                for key, value in values.items():
                    rendered = rendered.replace("{" + key + "}", html.unescape(value))
                target.text_override = rendered

    def close(self) -> None:
        return None
