from .models import Artifact

_CLASSIFICACOES_SEM_LLM_EXTERNO = {
    Artifact.ClassificationLevel.RESTRICTED,
    Artifact.ClassificationLevel.CONFIDENTIAL,
}


def permite_llm_externo(classification_level: str) -> bool:
    """Espelha services/orchestrator/policy_engine.py (RULES[...]['allow_external_llm']).

    Duplicado deliberadamente: o orchestrator é outro serviço FastAPI, o portal
    não o importa. Este espelho é puro (sem I/O) e deve mudar em sincronia com
    policy_engine.py — não modificar policy_engine.py para acomodar isto (ver
    "o que nunca tocar" em CLAUDE.md).
    """
    return classification_level not in _CLASSIFICACOES_SEM_LLM_EXTERNO
