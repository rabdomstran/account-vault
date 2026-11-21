"""Environment settings for the decide and text-helper backends.

DEVIATION from jev-ultrafast: upstream reads TYPESAFE_* / TEXT_MODEL_* via
bare os.environ lookups scattered through model.py and a hand-rolled .env
parser in demo.py. The hand-rolled parser keeps quotes, inline comments and
``export `` prefixes in values, so a correctly quoted key is sent with literal
quotes and fails as a misleading provider 401 (upstream issue #217). The code
default for TEXT_MODEL_BASE_URL also disagrees with .env.example
(DeepSeek vs OpenRouter; upstream issue #36), and a missing key surfaces as a
raw KeyError or a mid-run crash instead of a startup error (upstream issues
#36 and #133).

Here all settings live in one place, .env is loaded with python-dotenv
(already a project dependency), code defaults match .env.example / ADR-001,
and validate() fails fast with a named ConfigError before any browser
mutation can happen.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


class ConfigError(ValueError):
    """Required backend configuration is missing or inconsistent."""


@dataclass
class Settings:
    # Decide backend (ADR-001): local d1-2B by default, hosted Jev by env var.
    # DEVIATION: upstream hardcodes https://api.typesafe.ai/v1/systemone, so
    # the same model behind another gateway (which may also use a different
    # path, e.g. /v1/evaluate) cannot be used at all (upstream issues #241,
    # #25, #125). Base URL and path are therefore both configurable.
    systemone_base_url: str = "http://127.0.0.1:8000"
    systemone_path: str = "/v1/systemone"
    systemone_model: str = "d1-2B"
    systemone_api_key: str = ""
    # TYPE_TEXT helper. Defaults match .env.example (upstream issue #36).
    text_model_base_url: str = "https://openrouter.ai/api/v1"
    text_model: str = "inception/mercury-2.5"
    text_model_api_key: str = ""
    text_model_reasoning: str = "none"

    @property
    def systemone_url(self):
        return self.systemone_base_url.rstrip("/") + "/" + self.systemone_path.lstrip("/")

    @property
    def systemone_is_local(self):
        return urlparse(self.systemone_base_url).hostname in {"127.0.0.1", "localhost", "::1"}

    def validate(self, *, need_decide=True, need_text=False):
        """Fail fast at startup instead of mid-run (upstream issue #36)."""
        if need_decide and not self.systemone_is_local and not self.systemone_api_key:
            raise ConfigError(
                "SYSTEMONE_API_KEY is required for the hosted decide backend at "
                f"{self.systemone_base_url}; set it in .env or point SYSTEMONE_BASE_URL at a local server."
            )
        if need_text and not self.text_model_api_key:
            raise ConfigError(
                "TEXT_MODEL_API_KEY is required because this page has editable fields (TYPE_TEXT); "
                "no text is hardcoded or guessed by the executor. Set it in .env or inject a text provider."
            )


def load_settings(env_file=None):
    """Load .env (if present) with python-dotenv, then read the environment.

    Real environment variables win over .env values, matching upstream's
    setdefault behaviour without its parsing bugs (upstream issue #217).
    """
    load_dotenv(Path(env_file) if env_file else Path.cwd() / ".env", override=False)
    return Settings(
        systemone_base_url=os.environ.get("SYSTEMONE_BASE_URL", "http://127.0.0.1:8000"),
        systemone_path=os.environ.get("SYSTEMONE_PATH", "/v1/systemone"),
        systemone_model=os.environ.get("SYSTEMONE_MODEL", "d1-2B"),
        systemone_api_key=os.environ.get("SYSTEMONE_API_KEY", ""),
        text_model_base_url=os.environ.get("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1"),
        text_model=os.environ.get("TEXT_MODEL", "inception/mercury-2.5"),
        text_model_api_key=os.environ.get("TEXT_MODEL_API_KEY", ""),
        text_model_reasoning=os.environ.get("TEXT_MODEL_REASONING", "none"),
    )
