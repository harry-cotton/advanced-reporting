"""Common interface for any campaign-data source (CSV/synthetic today, APIs later).

`DataSource` is the contract for automated extraction. A concrete source implements
`fetch(start, end)` and returns the canonical long schema (see ``ingestion/schema.py``),
so the transform / modeling / reporting layers never know which platform the data came
from. The base class also provides the cross-cutting hooks every real connector needs:
credential loading from ``.env`` (never hardcoded) and basic retry/rate-limit handling.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
import os
import time

import pandas as pd

from ..utils import load_env_file


class MissingCredentialsError(RuntimeError):
    """Raised when a source is missing required credentials (env vars / .env)."""


class DataSource(ABC):
    name: str = "base"
    source: str = "default"   # key into config/mappings.yaml `sources:` column maps

    @abstractmethod
    def fetch(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """Return a dataframe in the canonical long campaign schema.

        Parameters
        ----------
        start, end:
            Inclusive ISO date strings (``"YYYY-MM-DD"``) bounding an incremental pull,
            or ``None`` for the full available history. Real connectors translate these
            into the platform's date-range query; the synthetic/CSV sources filter on the
            ``date`` column.

        See ``ingestion/schema.py`` (``CANONICAL_COLUMNS``) for the exact contract:
        daily grain, one row per date x channel x campaign x geo.
        """
        raise NotImplementedError

    # --- hooks shared by every source (used by real connectors) -----------------------

    def require_credentials(self, *env_keys: str) -> dict[str, str]:
        """Load ``.env`` then read each required credential from the environment.

        Returns ``{key: value}`` for the requested keys, or raises
        ``MissingCredentialsError`` listing every key that is unset. Credentials are read
        from the environment only -- never hardcoded or committed.
        """
        load_env_file()
        found, missing = {}, []
        for key in env_keys:
            val = os.getenv(key)
            if val:
                found[key] = val
            else:
                missing.append(key)
        if missing:
            raise MissingCredentialsError(
                f"{self.name}: missing credentials {sorted(missing)}. "
                "Set them in .env (gitignored) or the environment."
            )
        return found

    def with_retries(self, fn, *, retries: int = 3, base_delay: float = 0.5,
                     exceptions: tuple[type[BaseException], ...] = (Exception,)):
        """Call ``fn()`` with basic exponential-backoff retry/rate-limit handling.

        Retries on ``exceptions`` up to ``retries`` times, sleeping
        ``base_delay * 2**attempt`` seconds between attempts, then re-raises the last
        error. Dependency-free; real connectors wrap their HTTP/API call in this.
        """
        last = None
        for attempt in range(retries):
            try:
                return fn()
            except exceptions as e:  # noqa: PERF203 - retry loop is intentional
                last = e
                if attempt < retries - 1:
                    time.sleep(base_delay * (2 ** attempt))
        raise last
