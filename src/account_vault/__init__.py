"""Ultrafast System-One browsing agent + encrypted account vault.

The perceive -> decide -> act loop is ported from browser-use/jev-ultrafast
(MIT); see docs/jev-ultrafast-port.md for what was kept, what was changed,
and why. See docs/adr-001-model-choice.md for the decide-model decision.
"""

from .agent import Agent
from .browser import Browser, CdpBrowser, CoveredTarget, StalePage
from .static_dom import StaticBrowser

__version__ = "0.1.0"

__all__ = ["Agent", "Browser", "CdpBrowser", "CoveredTarget", "StalePage", "StaticBrowser", "__version__"]
