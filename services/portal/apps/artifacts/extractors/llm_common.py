"""Peças compartilhadas entre a cascata automática (llm_classifier.py) e as
chamadas manuais de LLM fora da cascata (estruturação manual, comparação).

`gerar_texto()` é o único ponto de chamada a um provider de LLM (Claude ou
Ollama) para esses usos manuais — cada provider concreto vive no seu próprio
módulo (llm_classifier.py para Anthropic, ollama_client.py para Ollama).
"""
import json
import re

from django.conf import settings

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}", re.DOTALL)


def _extract_json(text: str) -> dict:
    """Parse JSON from LLM output, tolerating markdown code fences and surrounding text."""
    text = text.strip()

    fence = _CODE_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = _JSON_OBJECT_RE.search(text)
    if match:
        return json.loads(match.group(0))

    raise json.JSONDecodeError("no JSON object found", text, 0)


def _get_client():
    api_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    if not api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


_EXTRACT_SYSTEM = """\
Você é um extrator de dados inteligente para uma plataforma de jornalismo investigativo brasileiro.

Você recebe uma representação estrutural comprimida (via dom2parser) de uma página capturada. A página pode conter \
qualquer tipo de informação: extrato bancário, ficha de empresa, processo judicial, notícia, \
resultado médico, tabela de licitações, planilha de dados públicos — qualquer coisa.

Quando a representação lista "REPEATED STRUCTURES", as linhas `selector:` e `fields:` são seletores \
JÁ VERIFICADOS contra o documento original (matches/covers medidos) — reutilize-os no schema em vez de \
inventar outros. As linhas de caminho (`div.x > ul > li`) NÃO são seletores: são truncadas e `*` marca \
partes variáveis de id/classe. Você só está sendo chamado porque o sistema não conseguiu extrair \
registros verificados sozinho — concentre-se nos campos soltos (rótulo: valor) que a página exibe.

Sua tarefa, em ordem:

1. CATEGORIZAR — Descreva em UMA FRASE específica o que esta página contém.
   Não "tabela financeira" mas "Extrato de conta corrente do Banco do Brasil, março 2025".
   Não "página de empresa" mas "Ficha cadastral da empresa XYZ LTDA na Receita Federal".

2. EXTRAIR — Extraia todos os dados estruturados visíveis. Para listas longas, limite a 100 itens.
   Use nomes de campo em português. Estruture conforme o conteúdo — não há formato fixo.

3. SCHEMA — Gere seletores CSS para automatizar esta extração em capturas futuras com a mesma estrutura.

Você NÃO precisa gerar texto narrativo para busca — o sistema sempre extrai o texto
de busca da página com trafilatura, de forma independente da sua resposta. Foque
inteiramente em extrair dados estruturados corretos e um schema reutilizável.

RETORNE APENAS O JSON ABAIXO. Nada antes, nada depois, sem markdown.

{
  "categoria": "<descrição específica em uma frase>",
  "page_type": "<artigo|tabular_financeiro|tabular_generico|processo_judicial|perfil_pessoa_juridica|documento_juridico|misto|desconhecido>",
  "structured_data": {
    "<campo_em_portugues>": "<valor ou lista ou objeto aninhado conforme o conteúdo>"
  },
  "schema": {
    "version": "1.0",
    "fields": {
      "<nome>": {"selector": "<seletor CSS>", "transform": "<text|brl_float|date_br|attr:href>"}
    },
    "tables": [
      {
        "selector": "<seletor CSS da tabela>",
        "columns": {
          "<nome_coluna>": {"index": <int>, "transform": "<transform>"}
        }
      }
    ]
  }
}

Regras para o schema:
- Seletores simples: classes (.foo), IDs (#bar), nth-child, atributos ([data-x="y"])
- PROIBIDO: :contains() — não é suportado pelo parser
- Sem campos individuais relevantes → "fields": {}
- Sem tabelas → "tables": []
"""


def gerar_texto(provider: str, model_name: str, system: str, prompt: str, max_tokens: int = 4096) -> str:
    """Ponto único de chamada a um LLM, Claude ou Ollama, para prompts fora da
    cascata automática (estruturação manual e julgamento de comparação).

    Levanta em caso de falha — quem chama decide status=falhou vs mensagem.
    """
    if provider == "anthropic":
        client = _get_client()
        if not client:
            raise RuntimeError("ANTHROPIC_API_KEY não configurada")
        message = client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    if provider == "ollama":
        from .ollama_client import gerar

        # Cluster com máquina(s) registrada(s) para este modelo: usa a mais
        # rápida conhecida (ou uma nunca testada, pra aprender). Sem cluster
        # configurado (instalação de máquina única, caso comum hoje),
        # escolher_execucao devolve None e a chamada cai no Ollama local de
        # sempre — comportamento inalterado.
        from apps.cluster.llm_router import escolher_execucao

        execucao = escolher_execucao(model_name)
        if execucao:
            return gerar(model_name, system, prompt, host=execucao.host,
                         num_thread=execucao.num_thread, maquina_id=execucao.maquina_id)
        return gerar(model_name, system, prompt)

    raise ValueError(f"provider desconhecido: {provider}")
