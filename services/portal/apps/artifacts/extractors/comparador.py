"""Comparação entre seções de dado estruturado, julgada por um LLM-juiz."""
import json

from .llm_common import gerar_texto, _extract_json

_JUIZ_SYSTEM = """\
Você é um avaliador técnico que compara extrações estruturadas da mesma página \
web, produzidas por métodos ou modelos diferentes (parsers determinísticos, \
modelos de linguagem distintos). Receberá uma lista de seções, cada uma com um \
rótulo e os dados extraídos por aquele método.

Avalie: completude (quantos campos relevantes cada seção capturou), precisão \
aparente (valores plausíveis vs. claramente errados/truncados) e discrepâncias \
pontuais entre seções para o mesmo campo semântico.

RETORNE APENAS O JSON ABAIXO. Nada antes, nada depois, sem markdown.

{
  "resumo": "<duas ou três frases resumindo a comparação>",
  "mais_completo": "<rótulo da seção mais completa/precisa, ou null se empatado>",
  "completude": {"<rótulo>": <float 0.0-1.0>},
  "discrepancias": [
    {"campo": "<nome do campo>", "observacao": "<o que diverge entre as seções>"}
  ]
}
"""


def julgar_comparacao(
    provider: str, model_name: str, secoes: list[dict], *, subject_id=None, tenant_id=None,
) -> dict:
    """secoes: [{"label": str, "dados": Any}, ...]. Retorna o veredito (dict),
    com "maquina_id" incluído (ver gerar_texto).

    Levanta em erro de chamada/parse — quem chama decide como registrar a falha.
    """
    from apps.events.models import Finalidade

    partes = []
    for secao in secoes:
        dados_json = json.dumps(secao["dados"], ensure_ascii=False, indent=2, default=str)
        partes.append(f"### {secao['label']}\n{dados_json}")
    prompt = "Seções a comparar:\n\n" + "\n\n".join(partes)

    texto, maquina_id = gerar_texto(
        provider, model_name, _JUIZ_SYSTEM, prompt, max_tokens=2048,
        finalidade=Finalidade.COMPARACAO_JUIZ, subject_id=subject_id, tenant_id=tenant_id,
    )
    veredito = _extract_json(texto)
    veredito["maquina_id"] = maquina_id
    return veredito
