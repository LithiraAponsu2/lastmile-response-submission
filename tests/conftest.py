from __future__ import annotations

import pytest

from lastmile.config import Settings
from lastmile.eval import load_corpus
from lastmile.llm import FakeLLM
from lastmile.packs import PackRegistry
from lastmile.pipeline import Options, Pipeline
from lastmile.store import Store


@pytest.fixture(scope="session")
def registry() -> PackRegistry:
    return PackRegistry()


@pytest.fixture(scope="session")
def corpus() -> dict[str, dict]:
    return {c["id"]: c for c in load_corpus()}


@pytest.fixture
def store() -> Store:
    return Store()


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def lk(registry):
    return registry.get("lk-landslide")


@pytest.fixture
def make_pipe(registry, store):
    def _make(faults=None, llm=None, **opt):
        return Pipeline(llm or FakeLLM(faults), registry, store, Options(**opt))

    return _make


@pytest.fixture
def clean_run(make_pipe, corpus):
    return make_pipe().run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
