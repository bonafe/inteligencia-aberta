"""Estruturação manual: extração de dados estruturados por LLM disparada sob
demanda pelo visualizador (não roda automaticamente no pipeline de captura —
ver apps.artifacts.tasks.extract_text_from_mhtml), capaz de falar com
qualquer provider (Claude ou Ollama) via gerar_texto().

O LLM recebe a página inteira, não a saída do dom2parser: a representação
comprimida só traz amostras das linhas repetidas (e deixa de fora campos
soltos como vencimento e totais), e o modelo devolvia a amostra como se fosse
o documento. A entrada preferida é o HTML enxuto (`html_enxuto.py`), que
mantém a estrutura das tabelas; o texto visível é o plano B.

Puramente funcional — nunca grava nada; quem chama decide onde persistir.
"""
from .llm_common import _extract_json, gerar_texto, prompt_extracao

#: Estimativa conservadora para português com muitos números (fatura, extrato).
CHARS_POR_TOKEN = 3
#: Reserva de contexto para a resposta: a página inteira em JSON pode ser longa.
TOKENS_RESPOSTA = 8192
NUM_CTX_MINIMO = 8192
NUM_CTX_MAXIMO = 65536
MAX_TOKENS_RESPOSTA = 16000


def num_ctx_para(system: str, prompt: str) -> int:
    """Janela de contexto que cabe a entrada inteira mais a resposta, em múltiplos
    de 4096. Levanta se nem o teto couber — melhor falhar dizendo do que o Ollama
    cortar a página sem avisar."""
    tokens = (len(system) + len(prompt)) // CHARS_POR_TOKEN + TOKENS_RESPOSTA
    num_ctx = max(NUM_CTX_MINIMO, -(-tokens // 4096) * 4096)
    if num_ctx > NUM_CTX_MAXIMO:
        raise ValueError(
            f"página grande demais para estruturar de uma vez: ~{tokens} tokens, teto {NUM_CTX_MAXIMO}"
        )
    return num_ctx


_ROTULO = {"html": "HTML da página", "texto": "Texto da página"}


def estruturar_manual(
    provider: str, model_name: str, conteudo: str, url: str, page_type_hint: str = "",
    *, formato: str = "html", titulo: str = "", subject_id=None, tenant_id=None, maquina_id=None,
) -> dict:
    """`conteudo` é a página inteira no `formato` dado ("html" enxuto ou "texto").
    Retorna {categoria, page_type, structured_data, maquina_id}. Levanta em
    erro de chamada (rede indisponível, parse impossível) — quem chama decide
    vazio vs falhou."""
    from apps.events.models import Finalidade

    if not conteudo:
        raise ValueError("a captura não tem conteúdo para enviar ao LLM")

    system = prompt_extracao(formato)
    hint_line = f"Dica da análise estrutural: {page_type_hint}\n" if page_type_hint else ""
    titulo_line = f"Título: {titulo}\n" if titulo else ""
    user_prompt = f"{hint_line}URL: {url}\n{titulo_line}\n{_ROTULO[formato]}:\n{conteudo}"

    texto, maquina_usada = gerar_texto(
        provider, model_name, system, user_prompt, max_tokens=MAX_TOKENS_RESPOSTA,
        num_ctx=num_ctx_para(system, user_prompt),
        finalidade=Finalidade.ESTRUTURACAO_MANUAL, subject_id=subject_id, tenant_id=tenant_id, maquina_id=maquina_id,
    )
    data = _extract_json(texto)

    return {
        "categoria": data.get("categoria", ""),
        "page_type": data.get("page_type", page_type_hint or "desconhecido"),
        "structured_data": data.get("structured_data") or None,
        "maquina_id": maquina_usada,
    }
