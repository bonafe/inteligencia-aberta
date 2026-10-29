"""Peças compartilhadas entre a classificação automática de page_type
(llm_classifier.py) e as chamadas manuais de LLM (estruturação manual,
comparação) — nenhuma extração de structured_data roda mais automaticamente
(ver apps.artifacts.tasks.extract_text_from_mhtml, 2026-09-17).

`gerar_texto()` é o único ponto de chamada a um provider de LLM (Claude ou
Ollama) para esses usos manuais — cada provider concreto vive no seu próprio
módulo (llm_classifier.py para Anthropic, ollama_client.py para Ollama).
"""
import json
import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

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


def _chave_anthropic(tenant_id=None) -> str:
    """Chave Anthropic a usar: a da organização (`LLMProvider` externo, ativo,
    vendor anthropic, com chave) se houver; senão a global do `.env`. Nunca
    levanta — uma chave da organização ilegível cai na global, com aviso."""
    if tenant_id:
        try:
            from apps.infrastructure.crypto import ChaveIlegivel
            from apps.infrastructure.models import LLMProvider

            for provider in LLMProvider.objects.filter(
                organization_id=tenant_id, provider_type=LLMProvider.ProviderType.EXTERNAL,
                vendor=LLMProvider.Vendor.ANTHROPIC, is_active=True,
            ).exclude(api_key_encrypted__isnull=True).exclude(api_key_encrypted="").order_by("created_at"):
                try:
                    return provider.get_api_key()
                except ChaveIlegivel:
                    logger.warning("chave do provider %s ilegível — cadastre de novo", provider.id)
        except Exception:
            logger.exception("falha ao buscar a chave Anthropic da organização %s", tenant_id)
    return getattr(settings, "ANTHROPIC_API_KEY", None) or ""


def _get_client(tenant_id=None):
    api_key = _chave_anthropic(tenant_id)
    if not api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


#: Abertura do prompt de extração conforme a entrada (`estruturacao_manual.py`).
_EXTRACT_ENTRADA = {
    "html": (
        "Você recebe o HTML COMPLETO de uma página capturada, já limpo: sem scripts, estilos, "
        "menus nem atributos visuais. Ficaram as tags e as classes/ids que mostram a estrutura — "
        "tabelas, linhas (tr), células (td/th), títulos de seção. Use essa estrutura (cabeçalhos "
        "th, classes como 'titulo' ou 'total', a posição da célula na linha) para saber o que é "
        "cada valor."
    ),
    "texto": (
        "Você recebe o texto visível COMPLETO de uma página capturada, na ordem em que aparece, "
        "uma linha por trecho de texto (em tabelas, cada célula costuma vir numa linha própria, "
        "na ordem das colunas)."
    ),
}

_EXTRACT_SYSTEM = """\
Você é um extrator de dados para uma plataforma de jornalismo investigativo brasileiro.

{entrada} A página pode ser qualquer coisa: fatura de cartão, extrato bancário, ficha de \
empresa, processo judicial, notícia, resultado de exame, tabela de licitações, planilha de \
dados públicos.

Sua tarefa, em ordem:

1. CATEGORIZAR — Descreva em UMA FRASE específica o que esta página contém, usando o que \
   está escrito nela (emissor, tipo de documento, período). Não invente data, ano ou nome \
   que não esteja no texto.

2. EXTRAIR — Extraia TODOS os dados estruturados da página, sem resumir e sem amostrar:
   - campos soltos (rótulo: valor) em um objeto — ex.: números de documento, datas, totais, \
     nomes, situação;
   - listas e tabelas como LISTA DE OBJETOS, um objeto por linha, com as mesmas chaves em \
     todas as linhas — nunca como listas paralelas por coluna;
   - quando uma tabela é dividida em seções (títulos entre as linhas), registre a seção em \
     cada linha, num campo próprio;
   - linhas de subtotal/total ficam fora da lista de linhas, nos campos soltos;
   - copie valores, datas e textos exatamente como aparecem (não converta, não arredonde);
   - ignore menus, rodapés, avisos e textos de navegação do site.
   Use nomes de campo em português, em snake_case.

RETORNE APENAS O JSON ABAIXO. Nada antes, nada depois, sem markdown.

{
  "categoria": "<descrição específica em uma frase>",
  "page_type": "<artigo|tabular_financeiro|tabular_generico|processo_judicial|perfil_pessoa_juridica|documento_juridico|misto|desconhecido>",
  "structured_data": {
    "<campo_em_portugues>": "<valor, lista de objetos ou objeto aninhado conforme o conteúdo>"
  }
}
"""


def prompt_extracao(formato: str) -> str:
    """System prompt de extração para a entrada `formato` ("html" ou "texto").
    `replace`, não `format`: o JSON de exemplo tem chaves."""
    return _EXTRACT_SYSTEM.replace("{entrada}", _EXTRACT_ENTRADA[formato])


def gerar_texto(
    provider: str, model_name: str, system: str, prompt: str, max_tokens: int = 4096,
    *, finalidade: str, subject_id=None, tenant_id=None, maquina_id: str | None = None,
    num_ctx: int | None = None,
) -> tuple[str, str | None]:
    """Ponto único de chamada a um LLM, Claude ou Ollama, para os usos manuais
    do sistema (estruturação manual e julgamento de comparação) — nenhuma
    extração de structured_data roda automaticamente hoje.

    Devolve (texto, maquina_id) — `maquina_id` é a `Maquina` que o roteador
    escolheu pra atender esta chamada (provider ollama com cluster
    configurado), ou `None` (provider anthropic, ou ollama local sem
    cluster) — é o que preenche `EstruturacaoLLM.maquina`/`Comparacao.maquina`,
    a proveniência que o Mapa Vivo mostra.

    `finalidade`/`subject_id`/`tenant_id` alimentam
    `apps.events.llm_telemetria.registrar_chamada_llm` — obrigatório indicar
    para quê a chamada é (`apps.events.models.Finalidade`), porque o mesmo
    par provider/modelo serve tarefas de custo muito diferente.

    `maquina_id` fixa a máquina do cluster (provider ollama): a pessoa a escolheu na tela, como no
    chat do Agora. Sem ele, o roteador pega a mais rápida. A view já validou o acesso.

    `num_ctx` (provider ollama) é a janela de contexto desta chamada; sem ele vale o
    `OLLAMA_NUM_CTX` do nó que executa. Quem manda a página inteira tem de dizer o tamanho,
    senão o Ollama corta a entrada sem avisar.

    Levanta em caso de falha — quem chama decide status=falhou vs mensagem.
    """
    if provider == "anthropic":
        from time import perf_counter

        from apps.events.llm_telemetria import ResultadoLLM, registrar_chamada_llm

        client = _get_client(tenant_id)
        if not client:
            raise RuntimeError(
                "Chave Anthropic não configurada (nem na organização, em LLMProvider, nem em ANTHROPIC_API_KEY)"
            )

        chars_enviados = len(system) + len(prompt)
        t0 = perf_counter()
        try:
            message = client.messages.create(
                model=model_name,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            registrar_chamada_llm(
                finalidade=finalidade, duration_ms=int((perf_counter() - t0) * 1000),
                subject_id=subject_id, tenant_id=tenant_id,
                resultado=ResultadoLLM(
                    provider="anthropic", modelo_solicitado=model_name, sucesso=False,
                    chars_enviados=chars_enviados, error_message=str(exc),
                ),
            )
            raise

        registrar_chamada_llm(
            finalidade=finalidade, duration_ms=int((perf_counter() - t0) * 1000),
            subject_id=subject_id, tenant_id=tenant_id,
            resultado=ResultadoLLM(
                provider="anthropic", modelo_solicitado=model_name, sucesso=True,
                modelo_resposta=message.model, tokens_entrada=message.usage.input_tokens,
                tokens_saida=message.usage.output_tokens, stop_reason=message.stop_reason or "",
                request_id=message.id, chars_enviados=chars_enviados,
            ),
        )
        return message.content[0].text, None

    if provider == "ollama":
        from .ollama_client import gerar

        # Cluster com máquina(s) registrada(s) para este modelo: usa a mais
        # rápida conhecida (ou uma nunca testada, pra aprender). Sem cluster
        # configurado (instalação de máquina única, caso comum hoje),
        # escolher_execucao devolve None e a chamada cai no Ollama local de
        # sempre — comportamento inalterado.
        from apps.cluster.llm_router import escolher_execucao

        # Telemetria desta chamada HTTP já acontece dentro de gerar()
        # (ollama_client._chamar) — registrar de novo aqui contaria a mesma
        # chamada duas vezes.
        if maquina_id:
            from apps.cluster.llm_router import execucao_para
            from apps.cluster.models import Maquina

            execucao = execucao_para(Maquina.objects.select_related("status").get(id=maquina_id))
        else:
            execucao = escolher_execucao(model_name)
        if execucao:
            # Peer com gateway autenticado (ADR 009): vai por ele, não direto
            # no Ollama dele. `gateway` só entra nos kwargs quando preenchido.
            extra = {"gateway": execucao.gateway} if execucao.gateway else {}
            texto = gerar(model_name, system, prompt, host=execucao.host,
                          num_thread=execucao.num_thread, num_ctx=num_ctx, maquina_id=execucao.maquina_id,
                          finalidade=finalidade, subject_id=subject_id, tenant_id=tenant_id, **extra)
            return texto, execucao.maquina_id
        return gerar(model_name, system, prompt, num_ctx=num_ctx,
                      finalidade=finalidade, subject_id=subject_id, tenant_id=tenant_id), None

    raise ValueError(f"provider desconhecido: {provider}")
