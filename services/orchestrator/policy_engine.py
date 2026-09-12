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


def registrar_decisao(
    decisao: dict,
    *,
    operation: str,
    classification: str,
    tenant_id: str,
    requesting_tenant: str,
    correlation_id: str,
    user_id: str | None = None,
) -> None:
    """Publica uma decisão já tomada no log de eventos.

    Deliberadamente **fora** de `check()`: o motor de políticas é determinístico
    e não faz I/O — misturar rede ali tornaria a decisão dependente da
    disponibilidade de outro serviço. Aqui só se observa o que já foi decidido.

    Isto **não** substitui o registro de auditoria exigido por
    `docs/seguranca/classificacao.md`: quando `requires_audit` é verdadeiro, o
    `AuditLog` continua sendo a trilha de compliance. Este evento é a trilha
    operacional, que permite ver a decisão na timeline da captura.
    """
    from eventos import emitir

    emitir(
        "politica.decisao",
        "ok" if decisao["decision"] == "PERMITIDO" else "ignorado",
        correlation_id=correlation_id,
        tenant_id=tenant_id,
        user_id=user_id,
        message=f"{operation} sobre dado '{classification}': {decisao['decision']}",
        payload={
            "operacao": operation,
            "classificacao": classification,
            "decisao": decisao["decision"],
            "motivo": decisao.get("reason"),
            "exige_auditoria": decisao.get("requires_audit", False),
            "tenant_dono": str(tenant_id),
            "tenant_solicitante": str(requesting_tenant),
        },
    )
