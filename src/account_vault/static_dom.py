"""Offline perceive/act backend for a static HTML file.

DEVIATION (new capability, no upstream equivalent): jev-ultrafast can only
drive a live Chrome over CDP, so its loop cannot run in CI, in unit tests
(upstream tests must mock the browser), or against a local fixture without
a browser daemon. This backend implements the exact same browser contract
— observe() -> page dict with url/title/text/actions/fingerprint/marker/
page_key/guards, fresh(page, action), act(action, page, text) — by parsing
HTML with the standard library and mutating an in-memory DOM.

Semantics intentionally mirror snapshot.js, including this port's coverage
fixes (date/time/range inputs, bare contenteditable, [onclick] rows are all
enumerated; select execution uses option_index). Interactive behaviour is
limited to what static fixtures need: fill, check/radio, select, in-page
anchors, relative-file navigation, and revealing a hidden results region on
submit (see _submit). There is no JavaScript execution; a fixture that
needs script behaviour belongs on the CDP backend.
"""

import html
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

from .browser import StalePage, fingerprint

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


class Element:
    __slots__ = ("tag", "attrs", "children", "parent", "node", "value", "checked", "selected_index", "text_override")

    def __init__(self, tag, attrs, parent, node):
        self.tag = tag
        self.attrs = attrs
        self.children = []
        self.parent = parent
        self.node = node
        self.value = None  # None = fall back to attrs/content
        self.checked = "checked" in attrs
        self.selected_index = None
        self.text_override = None

    def get(self, key, default=None):
        return self.attrs.get(key, default)

    def inner_text(self):
        if self.text_override is not None:
            return self.text_override
        parts = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.inner_text())
        return " ".join(" ".join(parts).split())


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element("#root", {}, None, 0)
        self.stack = [self.root]
        self.next_node = 1
        self.title = ""

    def _add(self, tag, attrs):
        clean = {k: (v if v is not None else "") for k, v in attrs}
        element = Element(tag, clean, self.stack[-1], self.next_node)
        self.next_node += 1
        self.stack[-1].children.append(element)
        return element

    def handle_starttag(self, tag, attrs):
        element = self._add(tag, attrs)
        if tag not in VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self._add(tag, attrs)

    def handle_endtag(self, tag):
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].children.append(data)
        if self.stack[-1].tag == "title":
            self.title += data


def is_static_source(source):
    text = str(source)
    if text.startswith("file://"):
        return True
    return Path(text).exists() and Path(text).is_file()


def _source_path(source):
    text = str(source)
    if text.startswith("file://"):
        return Path(unquote(urlparse(text).path))
    return Path(text)


class StaticBrowser:
    """Browser-contract backend over one static HTML document (see module doc)."""

    def __init__(self, source):
        self.path = _source_path(source).resolve()
        self.url = self.path.as_uri()
        self.last_navigation = None
        self._load(self.path.read_text(encoding="utf-8"))

    @classmethod
    def from_html(cls, markup, url="https://example.test/"):
        browser = cls.__new__(cls)
        browser.path = None
        browser.url = url
        browser.last_navigation = None
        browser._load(markup)
        return browser

    def _load(self, markup):
        parser = _Parser()
        parser.feed(markup)
        self.root = parser.root
        self.title = parser.title.strip()
        self.by_node = {}
        self.by_id = {}
        self.labels_for = {}
        for element in self._walk(self.root):
            self.by_node[element.node] = element
            if element.get("id"):
                self.by_id.setdefault(element.get("id"), element)
            if element.tag == "label" and element.get("for"):
                self.labels_for.setdefault(element.get("for"), element)
        # Initialise select state from `selected` attributes (default: first option).
        for element in self._walk(self.root):
            if element.tag == "select":
                options = self._options(element)
                element.selected_index = next(
                    (i for i, option in enumerate(options) if "selected" in option.attrs),
                    0 if options else -1,
                )

    def _walk(self, element):
        yield element
        for child in element.children:
            if not isinstance(child, str):
                yield from self._walk(child)

    def _options(self, select):
        return [e for e in self._walk(select) if e.tag == "option"]

    # -- perception helpers (mirror snapshot.js) ---------------------------

    def _visible(self, element):
        current = element
        while current is not None and current.tag != "#root":
            if "hidden" in current.attrs or current.get("aria-hidden") == "true" or "inert" in current.attrs:
                return False
            style = re.sub(r"\s+", "", current.get("style", "").lower())
            if "display:none" in style or "visibility:hidden" in style:
                return False
            current = current.parent
        return True

    def _disabled(self, element):
        if "disabled" in element.attrs:
            return True
        current = element.parent
        while current is not None:
            if current.get("aria-disabled") == "true":
                return True
            current = current.parent
        return False

    def _name(self, element):
        labelledby = element.get("aria-labelledby", "")
        if labelledby:
            text = " ".join(self.by_id[i].inner_text() for i in labelledby.split() if i in self.by_id).strip()
            if text:
                return text
        if element.get("aria-label"):
            return element.get("aria-label")
        if element.get("id") and element.get("id") in self.labels_for:
            text = self.labels_for[element.get("id")].inner_text()
            if text:
                return text
        current = element.parent
        while current is not None:
            if current.tag == "label":
                text = current.inner_text()
                if text:
                    return text
                break
            current = current.parent
        if element.tag == "input" and element.get("type", "") in {"button", "submit", "reset"} and element.get("value"):
            return element.get("value")
        if element.get("alt"):
            return element.get("alt")
        if element.tag != "input":
            text = element.inner_text()
            if text:
                return text
        return element.get("title") or element.get("placeholder") or ""

    def _role(self, element):
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
        return None

    def _value(self, element):
        if element.value is not None:
            return element.value
        if element.tag == "textarea":
            return element.inner_text()
        if "contenteditable" in element.attrs:
            return element.inner_text()
        if element.tag == "select":
            options = self._options(element)
            if options and 0 <= (element.selected_index or 0) < len(options):
                return options[element.selected_index].get("value", options[element.selected_index].inner_text())
            return ""
        return element.get("value", "")

    def _scope_text(self, element):
        current = element.parent
        while current is not None and current.tag != "#root":
            scoped = current.tag in {"form", "fieldset", "dialog", "article", "li", "tr"}
            if scoped or current.get("role") in {"dialog", "row"}:
                return current.inner_text()[:600]
            current = current.parent
        return ""

    def _guard(self, element):
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

    def _build(self):
        actions = []
        for element in self._walk(self.root):
            if element.tag in {"#root", "script", "style", "head", "title", "meta", "link"}:
                continue
            input_type = element.get("type", "").lower() if element.tag == "input" else ""
            if input_type in SAFE_EXCLUDE or not self._visible(element) or self._disabled(element):
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
            base = {"node": element.node, "role": role, "label": self._name(element) or role}
            if element.tag == "input" and input_type:
                base["input_type"] = input_type
            if element.get("id"):
                base["dom_id"] = element.get("id")
            if element.get("name"):
                base["name"] = element.get("name")
            if element.get("placeholder"):
                base["placeholder"] = element.get("placeholder")
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
                valid = options and element.selected_index >= 0
                current = options[element.selected_index].inner_text() if valid else ""
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
        words, length = [], 0
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
        guards = {}
        for action in actions:
            if str(action["node"]) not in guards:
                guards[str(action["node"])] = self._guard(self.by_node[action["node"]])
        semantics = [dict(action) for action in actions]
        marker = [self.url, self.title, text, semantics, page_key[1]]
        omitted = max(0, len(actions) - 250)
        actions = actions[:250]
        for index, action in enumerate(actions):
            action["id"] = f"e{index + 1}"
        actions.append({"id": "wait", "kind": "wait", "label": "Wait for the page to update"})
        page = {
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
        return page

    # -- browser contract ---------------------------------------------------

    def observe(self, screenshot=False):
        # Static pages have no pixels; screenshots are simply absent. Agent
        # recording checks for the key before writing (live backend always
        # provides one when requested).
        return self._build()

    def fresh(self, page, action=None):
        current = self._build()
        if action is not None and action.get("kind") in {"click", "select"}:
            node = action.get("node")
            if type(node) is not int:
                return False
            now = [current["page_key"], current["guards"].get(str(node))]
            return now == [page["page_key"], page["guards"].get(str(node))]
        return current["marker"] == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        kind = action["kind"]
        if kind == "wait":
            return {"executed": action["id"]}
        if kind == "scroll":
            return {"executed": action["id"]}
        element = self.by_node.get(action.get("node"))
        if element is None or not self._visible(element) or self._disabled(element):
            if kind == "select":
                raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
            raise StalePage("Target changed or is covered. Observe again.")
        if kind == "fill":
            if "readonly" in element.attrs or element.get("aria-readonly") == "true":
                raise StalePage("Target changed or is covered. Observe again.")
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

    def _click(self, element):
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
        if element.tag == "a" and element.get("href"):
            href = element.get("href")
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

    def _submit(self, element):
        targets = []
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
        values = {}
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

    def close(self):
        return None
