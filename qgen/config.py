"""Where the database and the model come from.

Read from the process environment, optionally topped up from ``.env`` files. There is no default
database URL and no default API key: a package that quietly falls back to somebody's development
database is a package that eventually writes to the wrong one.

WHY THE PROVIDER VARIABLES KEEP THEIR ``COACHING_LLM_`` NAMES
-------------------------------------------------------------
The sibling project already configures a Bedrock model under those names. An operator who has
configured a model has configured *the* model, and asking them to name it twice under two
prefixes is how the two drift apart and one of them ends up pointing at a retired inference
profile. Point :envvar:`QGEN_ENV_FILE` at that project's ``backend/.env`` and nothing needs
copying - which also means the key is not duplicated into a second file to be forgotten about.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigurationError

#: The package's own env file, beside this source tree. Git-ignored.
DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _parse_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` pairs from a dotenv-style file. Missing file, no pairs."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        # Quotes are stripped only when they wrap the whole value, so a password containing a
        # quote is not silently truncated.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_environment() -> dict[str, str]:
    """The environment as this package sees it.

    Precedence, strongest first: the real process environment, then :envvar:`QGEN_ENV_FILE` if
    one is named, then the package's own ``.env``. The process environment wins so a one-off
    ``QGEN_DATABASE_URL=... python -m qgen`` cannot be overridden by a file the operator forgot
    was there.

    :envvar:`QGEN_ENV_FILE` is honoured from the ``.env`` file as well as from the environment,
    so the local file can say "and take the model settings from over there" once, rather than
    every command line having to.
    """
    merged: dict[str, str] = {}
    merged.update(_parse_env_file(DEFAULT_ENV_FILE))
    named = (os.environ.get("QGEN_ENV_FILE") or merged.get("QGEN_ENV_FILE") or "").strip()
    if named:
        merged.update(_parse_env_file(Path(named)))
    merged.update(os.environ)
    return merged


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    llm_provider: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_region: str = "us-east-1"
    llm_timeout_seconds: float = 120.0
    #: The course website's own search URL, with ``{query}`` where the question goes. Consulted
    #: only when the database has nothing. Empty means the site is never contacted.
    site_search: str = ""
    #: The company's SQL Server legal reference database. Consulted when the course material has
    #: nothing. Empty host means it is never contacted.
    legal_host: str = ""
    legal_port: str = "1433"
    legal_database: str = ""
    legal_user: str = ""
    legal_password: str = ""

    @property
    def llm_configured(self) -> bool:
        """Whether a model can actually be called.

        Provider, key and model - all three. A half-configured provider is the case that produces
        a confusing 500 halfway through a run instead of a clean 503 before it starts.
        """
        return bool(self.llm_provider) and bool(self.llm_api_key) and bool(self.llm_model)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Settings for this run, or :class:`ConfigurationError` if the database is not named.

    A missing model is *not* an error here. Listing courses needs no model at all, and the code
    path that does need one raises a 503 that says so. Refusing to start would make ``courses``
    unusable on a machine that has no key.
    """
    values = env if env is not None else load_environment()

    database_url = (values.get("QGEN_DATABASE_URL") or "").strip()
    if not database_url:
        raise ConfigurationError(
            "QGEN_DATABASE_URL is not set. Put it in packages/qgen/.env "
            "(see .env.example), or export it.",
            reason="NO_DATABASE_URL",
        )

    timeout_raw = (values.get("COACHING_LLM_TIMEOUT_SECONDS") or "").strip()
    try:
        # The sibling project sets 20 seconds for a chat turn. Generation is not a chat turn -
        # twenty questions is tens of seconds of model output - so its timeout is a floor of two
        # minutes, and the configured value only ever raises it.
        timeout = max(120.0, float(timeout_raw)) if timeout_raw else 120.0
    except ValueError:
        timeout = 120.0

    return Settings(
        database_url=database_url,
        llm_provider=(values.get("COACHING_LLM_PROVIDER") or "").strip().lower(),
        llm_api_key=(values.get("COACHING_LLM_API_KEY") or "").strip(),
        llm_model=(values.get("COACHING_LLM_MODEL") or "").strip(),
        llm_region=(values.get("COACHING_LLM_REGION") or "").strip() or "us-east-1",
        llm_timeout_seconds=timeout,
        site_search=(values.get("QGEN_SITE_SEARCH") or "").strip(),
        legal_host=(values.get("QGEN_MSSQL_HOST") or "").strip(),
        legal_port=(values.get("QGEN_MSSQL_PORT") or "1433").strip(),
        legal_database=(values.get("QGEN_MSSQL_DATABASE") or "").strip(),
        legal_user=(values.get("QGEN_MSSQL_USER") or "").strip(),
        legal_password=(values.get("QGEN_MSSQL_PASSWORD") or "").strip(),
    )
