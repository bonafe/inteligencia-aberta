# Motor de Políticas — código determinístico, nunca usa LLM para tomar decisões.
# Recebe operação + classificação do dado e retorna PERMITIDO ou BLOQUEADO.

RULES: dict[str, dict] = {
    "publico": {
        "allow_external_llm": True,
        "allow_external_embedding": True,
        "require_audit": False,
    },
    "interno": {
        "allow_external_llm": True,
        "allow_external_embedding": True,
        "require_audit": True,
    },
    "restrito": {
        "allow_external_llm": False,
        "allow_external_embedding": False,
        "require_audit": True,
    },
    "confidencial": {
        "allow_external_llm": False,
        "allow_external_embedding": False,
        "require_audit": True,
        "audit_every_access": True,
    },
}


def check(operation: str, classification: str, tenant_id: str, requesting_tenant: str) -> dict:
    if tenant_id != requesting_tenant:
        return {"decision": "BLOQUEADO", "reason": "tenant_mismatch"}

    rules = RULES.get(classification)
    if rules is None:
        return {"decision": "BLOQUEADO", "reason": "classificacao_desconhecida"}

    if operation == "chamar_llm_externo" and not rules["allow_external_llm"]:
        return {"decision": "BLOQUEADO", "reason": f"llm_externo_proibido_para_{classification}"}

    if operation == "indexar_embedding" and not rules["allow_external_embedding"]:
        return {"decision": "BLOQUEADO", "reason": f"embedding_externo_proibido_para_{classification}"}

    return {
        "decision": "PERMITIDO",
        "reason": None,
        "requires_audit": rules["require_audit"],
    }
