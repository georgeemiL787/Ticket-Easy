import dataclasses
import shutil

import pytest

from team_a import config
from team_a.knowledge import index as index_module
from team_a.knowledge.index import TenantIndex, build_index
from team_a.policy import rules_store as rules_module

TENANT = "shop_001"


@pytest.fixture(scope="session")
def keyword_index(tmp_path_factory) -> TenantIndex:
    """Keyword-only index of the seed corpus in a temp dir (no Ollama needed)."""
    tmp = tmp_path_factory.mktemp("index")
    patched = dataclasses.replace(config.settings, index_dir=tmp)
    mp = pytest.MonkeyPatch()
    mp.setattr(index_module, "settings", patched)
    build_index(TENANT, embedder=None)
    idx = TenantIndex.load(TENANT)
    mp.undo()
    return idx


@pytest.fixture
def rule_store(tmp_path) -> rules_module.RuleStore:
    """A writable copy of the shop_001 rules."""
    path = tmp_path / "rules.json"
    shutil.copy(config.settings.rules_file(TENANT), path)
    return rules_module.RuleStore(TENANT, path=path)
