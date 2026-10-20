import dataclasses
import shutil

import pytest

from knowledge_service import config
from knowledge_service.search import index as index_module
from knowledge_service.search.index import TenantIndex, build_index
from knowledge_service.policy import rules_store as rules_module

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


@pytest.fixture(scope="session")
def telecom_db(tmp_path_factory):
    """A tenth-size telecom.db from the tenant's own generator (~2 s), so tests never need the 214 MB file."""
    import subprocess
    import sys

    path = tmp_path_factory.mktemp("telecom") / "telecom.db"
    script = config.settings.tenant_dir("telco_001") / "scripts" / "generate.py"
    subprocess.run([sys.executable, str(script), "--small", "--out", str(path)], check=True, capture_output=True)
    return path


@pytest.fixture(scope="session")
def built_db(tmp_path_factory, telecom_db):
    """Keyword-only SQLite build of every tenant in a temp dir (no Ollama needed)."""
    from knowledge_service.db import loader

    path = tmp_path_factory.mktemp("db") / "knowledge.sqlite"
    loader.build(embedder=None, db_path=path, telecom_dbs={"telco_001": telecom_db})
    return path
