"""permite_llm_externo espelha as regras de classificação do policy_engine.

Sem banco: é uma função pura, testada isolada dos models.
"""
import pytest

from apps.artifacts.models import Artifact
from apps.artifacts.policy import permite_llm_externo


@pytest.mark.parametrize("nivel,esperado", [
    (Artifact.ClassificationLevel.PUBLIC, True),
    (Artifact.ClassificationLevel.INTERNAL, True),
    (Artifact.ClassificationLevel.RESTRICTED, False),
    (Artifact.ClassificationLevel.CONFIDENTIAL, False),
])
def test_permite_llm_externo_por_classificacao(nivel, esperado):
    assert permite_llm_externo(nivel) is esperado
