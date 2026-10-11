"""What produced a trace: schema version, the tenant configuration and the word lists, as short stable hashes.

With these on every trace, a wrong decision can be tied to the exact configuration that was running.
"""

import hashlib
from functools import lru_cache

from team_b.brain.lexicon import DEFAULT_LEXICON_PATH
from team_b.domain.tenant import TenantConfig
from team_b.domain.trace import TRACE_SCHEMA_VERSION


def _short(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


@lru_cache(maxsize=1)
def lexicon_hash() -> str:
    return _short(DEFAULT_LEXICON_PATH.read_bytes())


def tenant_config_hash(tenant: TenantConfig) -> str:
    return _short(tenant.model_dump_json().encode("utf-8"))


def base_versions(tenant: TenantConfig) -> dict[str, str]:
    """The versions every trace records, whatever the turn did."""
    return {
        "schema": TRACE_SCHEMA_VERSION,
        "tenant_config_hash": tenant_config_hash(tenant),
        "lexicon_hash": lexicon_hash(),
    }
