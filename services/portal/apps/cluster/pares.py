"""Pares: cadastro, enrolamento por convite e conferência — ADR 011, decisão 4.

Um **par** é outra instância do Inteligência Aberta (uma linha de `Maquina` com
`eh_local=False`). O enrolamento **não confia no primeiro contato**:

1. o administrador de A cria um **convite** (token de uso único + DID + endereço de A);
2. o administrador de B cola o código, **vê a impressão digital de A** e aceita; B chama
   A com uma requisição **assinada pela chave de B** (`apps.federacao.canal`);
3. cada lado fica com o outro como par **`pendente`**;
4. só quando o administrador, **dos dois lados**, confere a impressão digital por fora
   do canal e confirma, o par vira `confirmado`. É o único caminho para isso.

**O tipo é do lado de quem o atribui.** "Próprio" é um rótulo do administrador, sem
prova de mesmo dono — e dá poder sobre os modelos do Ollama. Por isso marcá-lo como
próprio exige confirmação explícita, e toda mudança de tipo é auditada. Revogar corta o
canal; não recolhe nada que já tenha sido copiado.

Toda ação que muda um par exige dono ou administrador da organização
(`apps.accounts.permissoes`) e deixa `AuditLog` — **sem** token nem conteúdo.
"""

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import socket
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.permissoes import exige_admin
from apps.artifacts.models import AuditLog
from apps.federacao import canal
from apps.federacao.chaves import chave_ativa, garantir_chave_ativa
from apps.federacao.did import publica_de_did_key
from apps.federacao.endpoints import validar_endpoint

from .models import ConviteEnrolamento, Maquina

logger = logging.getLogger(__name__)

VALIDADE_CONVITE_H = 24
CAMINHO_ACEITAR = "/federacao/convite/aceitar/"
PREFIXO_CODIGO = "ia1."
LIMITE_RESPOSTA = 65536


class ErroEnrolamento(ValueError):
    """Algo impede o enrolamento; a mensagem é segura para mostrar ao administrador."""


class ConviteRecusado(Exception):
    """O lado que recebe o convite o recusou. `codigo` é só para o log local."""

    def __init__(self, codigo: str):
        super().__init__(codigo)
        self.codigo = codigo


def nome_da_instancia() -> str:
    return os.environ.get("INSTANCIA_NOME") or socket.gethostname()


def _auditar(operacao: str, organizacao, usuario, motivo: str = "", **meta) -> None:
    try:
        AuditLog.objects.create(
            organization=organizacao, user=usuario if getattr(usuario, "pk", None) else None,
            operation=operacao, outcome="permitido", reason=motivo, metadata=meta,
        )
    except Exception:
        logger.exception("falha ao gravar auditoria de %s", operacao)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _endpoint_local() -> str:
    bruto = getattr(settings, "FEDERACAO_ENDPOINT_ANUNCIADO", "")
    if not bruto:
        raise ErroEnrolamento("defina FEDERACAO_ENDPOINT_ANUNCIADO (o endereço pelo qual os pares alcançam esta instância)")
    try:
        return validar_endpoint(bruto)
    except ValueError as exc:
        raise ErroEnrolamento(f"FEDERACAO_ENDPOINT_ANUNCIADO inválido: {exc}") from None


def _limpar_nome(texto) -> str:
    nome = re.sub(r"[\x00-\x1f\x7f]", "", str(texto or ""))
    return " ".join(nome.split())[:120]


def _apelido_livre(organizacao, base: str) -> str:
    base = _limpar_nome(base) or "instância"
    apelido, n = base[:110], 2
    while Maquina.objects.filter(organizacao=organizacao, apelido=apelido).exists():
        apelido, n = f"{base[:110]} ({n})", n + 1
    return apelido


def garantir_maquina_local(organizacao) -> Maquina:
    """A `Maquina` desta própria instância na organização (cria se faltar; idempotente).

    Substitui o antigo `registrar_maquina` contra si mesma. Marca `eh_local`, `confirmado`
    e `próprio`, grava o `did` da chave da instância e mantém os endereços anunciados em
    dia com as configurações. Uma linha já existente com o mesmo apelido é adotada.
    """
    chave, _ = garantir_chave_ativa()
    apelido = os.environ.get("CLUSTER_LOCAL_APELIDO") or nome_da_instancia()
    maquina = (
        Maquina.objects.filter(organizacao=organizacao, eh_local=True).first()
        or Maquina.objects.filter(organizacao=organizacao, apelido=apelido).first()
    )
    if maquina is None:
        maquina = Maquina(organizacao=organizacao, dono=organizacao.owner, apelido=apelido, hostname_declarado=apelido)
    maquina.eh_local = True
    maquina.did = chave.did
    maquina.tipo = Maquina.Tipo.PROPRIO
    maquina.estado = Maquina.Estado.CONFIRMADO
    maquina.endpoint_controle = getattr(settings, "FEDERACAO_ENDPOINT_ANUNCIADO", "")
    maquina.ollama_endpoint = getattr(settings, "OLLAMA_ENDPOINT_ANUNCIADO", "") or maquina.ollama_endpoint
    maquina.gateway_endpoint = getattr(settings, "LLM_GATEWAY_ENDPOINT_ANUNCIADO", "") or maquina.gateway_endpoint
    maquina.save()
    return maquina


# ── convite ─────────────────────────────────────────────────────────────────

def _codificar(dados: dict) -> str:
    bruto = json.dumps(dados, sort_keys=True, separators=(",", ":")).encode()
    return PREFIXO_CODIGO + base64.urlsafe_b64encode(bruto).rstrip(b"=").decode()


def decodificar_codigo(codigo: str) -> dict:
    """Valida e abre um código de convite. `ErroEnrolamento` se não for um código bom."""
    invalido = ErroEnrolamento("código de convite inválido")
    texto = (codigo or "").strip()
    if not texto.startswith(PREFIXO_CODIGO) or len(texto) > 4096:
        raise invalido
    try:
        corpo = texto[len(PREFIXO_CODIGO):]
        dados = json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))
        token, did, endpoint, nome = dados["token"], dados["did"], dados["endpoint"], _limpar_nome(dados["nome"])
        if dados.get("v") != 1 or not isinstance(token, str) or not 16 <= len(token) <= 100 or not nome:
            raise invalido
        publica_de_did_key(did)
        endpoint = validar_endpoint(endpoint)
    except ErroEnrolamento:
        raise
    except (ValueError, KeyError, TypeError):
        raise invalido from None
    return {"token": token, "did": did, "endpoint": endpoint, "nome": nome}


def criar_convite(organizacao, usuario, *, tipo: str = Maquina.Tipo.TERCEIRO, confirmou_proprio: bool = False):
    """Cria o convite e devolve `(convite, codigo)`. O código **só aparece agora**."""
    exige_admin(usuario, organizacao)
    if tipo not in Maquina.Tipo.values:
        raise ErroEnrolamento("tipo de par inválido")
    if tipo == Maquina.Tipo.PROPRIO and not confirmou_proprio:
        raise ErroEnrolamento("marcar o par como próprio exige confirmação explícita")
    endpoint = _endpoint_local()
    chave, _ = garantir_chave_ativa()
    token = secrets.token_urlsafe(24)
    convite = ConviteEnrolamento.objects.create(
        organizacao=organizacao, criado_por=usuario, token_hash=_hash(token), tipo=tipo,
        expira_em=timezone.now() + timedelta(hours=VALIDADE_CONVITE_H),
    )
    _auditar("par.convite_criado", organizacao, usuario, convite_id=str(convite.id), tipo=tipo)
    codigo = _codificar({"v": 1, "token": token, "did": chave.did, "endpoint": endpoint, "nome": nome_da_instancia()})
    return convite, codigo


def _enviar(url: str, corpo: bytes, headers: dict):
    """POST ao par: sem seguir redirects, com timeout e limite de tamanho da resposta."""
    try:
        resposta = requests.post(url, data=corpo, headers=headers, timeout=10, allow_redirects=False, stream=True)
        conteudo = resposta.raw.read(LIMITE_RESPOSTA + 1, decode_content=True)
    except requests.RequestException:
        raise ErroEnrolamento("não consegui falar com a outra instância (verifique o endereço e a rede)") from None
    if len(conteudo) > LIMITE_RESPOSTA:
        raise ErroEnrolamento("a resposta da outra instância é grande demais")
    return resposta.status_code, conteudo, resposta.headers


def aceitar_convite(organizacao, usuario, codigo: str, tipo: str, *, confirmou_proprio: bool = False, enviar=None):
    """Aceita o convite da outra instância (lado B). Devolve o par criado, ainda `pendente`.

    Chama a outra instância com uma requisição assinada pela **nossa** chave e confere que
    a resposta veio assinada pelo DID que estava no código. Nada é criado se algo falhar.
    """
    exige_admin(usuario, organizacao)
    if tipo not in Maquina.Tipo.values:
        raise ErroEnrolamento("tipo de par inválido")
    if tipo == Maquina.Tipo.PROPRIO and not confirmou_proprio:
        raise ErroEnrolamento("marcar o par como próprio exige confirmação explícita")
    dados = decodificar_codigo(codigo)
    chave, _ = garantir_chave_ativa()
    if dados["did"] == chave.did:
        raise ErroEnrolamento("este convite é desta própria instância")
    if Maquina.objects.filter(organizacao=organizacao, did=dados["did"]).exists():
        raise ErroEnrolamento("esta instância já está cadastrada como par")
    endpoint_local = _endpoint_local()

    corpo = json.dumps({"token": dados["token"], "did": chave.did, "endpoint": endpoint_local,
                        "nome": nome_da_instancia()}, separators=(",", ":")).encode()
    headers = canal.assinar_requisicao("POST", CAMINHO_ACEITAR, corpo, dados["did"], chave=chave)
    status, conteudo, cabecalhos = (enviar or _enviar)(
        dados["endpoint"] + CAMINHO_ACEITAR, corpo, {**headers, "Content-Type": "application/json"})
    if status != 200:
        raise ErroEnrolamento(f"a outra instância recusou o convite (HTTP {status}); ele pode ter expirado ou já ter sido usado")
    try:
        canal.verificar_resposta(conteudo, headers[canal.H_NONCE], dados["did"],
                                 cabecalhos.get(canal.H_ASSINATURA_RESPOSTA, ""))
        resposta = json.loads(conteudo)
    except (canal.AssinaturaInvalida, ValueError):
        raise ErroEnrolamento("a resposta da outra instância não confere com o convite") from None
    if not isinstance(resposta, dict) or resposta.get("did") != dados["did"]:
        raise ErroEnrolamento("a outra instância se identificou com um DID diferente do convite")

    par = Maquina.objects.create(
        organizacao=organizacao, dono=usuario, apelido=_apelido_livre(organizacao, dados["nome"]),
        did=dados["did"], tipo=tipo, estado=Maquina.Estado.PENDENTE, endpoint_controle=dados["endpoint"],
    )
    _auditar("par.convite_aceito", organizacao, usuario, par_id=str(par.id), did=par.did, tipo=tipo)
    return par


def receber_convite(corpo: bytes, caminho: str, headers) -> dict:
    """Lado A: processa o `POST` assinado de B. Devolve o corpo da resposta ou levanta `ConviteRecusado`.

    O motivo da recusa **nunca** vai para quem chamou (só `codigo`, para o log local) —
    assim a resposta não diz se o token existe, expirou ou foi usado.
    """
    chave = chave_ativa()
    if chave is None:
        raise ConviteRecusado("sem_chave")
    try:
        did_origem = canal.verificar_requisicao("POST", caminho, headers, corpo, did_local=chave.did)
    except canal.AssinaturaInvalida as exc:
        raise ConviteRecusado(f"assinatura:{exc.codigo}") from None
    try:
        dados = json.loads(corpo)
        token, did, nome = dados["token"], dados["did"], _limpar_nome(dados["nome"])
        endpoint = validar_endpoint(dados["endpoint"])
        if not isinstance(token, str) or did != did_origem or not nome:
            raise ValueError("campos")
    except (ValueError, KeyError, TypeError):
        raise ConviteRecusado("corpo") from None

    agora = timezone.now()
    with transaction.atomic():
        convite = ConviteEnrolamento.objects.select_for_update().filter(token_hash=_hash(token)).first()
        if convite is None or convite.usado_em is not None or convite.expira_em <= agora:
            raise ConviteRecusado("convite")
        organizacao = convite.organizacao
        if Maquina.objects.filter(organizacao=organizacao, did=did_origem).exists():
            raise ConviteRecusado("duplicado")
        par = Maquina.objects.create(
            organizacao=organizacao, dono=convite.criado_por or organizacao.owner,
            apelido=_apelido_livre(organizacao, nome), did=did_origem, tipo=convite.tipo,
            estado=Maquina.Estado.PENDENTE, endpoint_controle=endpoint,
        )
        convite.usado_em, convite.usado_por_did = agora, did_origem
        convite.save(update_fields=["usado_em", "usado_por_did"])
    _auditar("par.convite_recebido", organizacao, convite.criado_por, par_id=str(par.id), did=did_origem, tipo=convite.tipo)
    return {"did": chave.did, "nome": nome_da_instancia()}


# ── ciclo de vida do par ────────────────────────────────────────────────────

def _par_editavel(par: Maquina) -> None:
    if par.eh_local:
        raise ErroEnrolamento("a própria instância não é um par")


def confirmar(par: Maquina, usuario) -> Maquina:
    """O administrador **conferiu a impressão digital por fora do canal**: o par vira `confirmado`."""
    exige_admin(usuario, par.organizacao)
    _par_editavel(par)
    if par.estado == Maquina.Estado.REVOGADO:
        raise ErroEnrolamento("um par revogado não volta; faça um novo enrolamento")
    if par.estado == Maquina.Estado.CONFIRMADO:
        return par
    par.estado = Maquina.Estado.CONFIRMADO
    par.impressao_digital_conferida_em = timezone.now()
    par.conferida_por = usuario
    par.save(update_fields=["estado", "impressao_digital_conferida_em", "conferida_por"])
    _auditar("par.confirmado", par.organizacao, usuario, par_id=str(par.id), did=par.did, tipo=par.tipo)
    return par


def revogar(par: Maquina, usuario) -> Maquina:
    """Corta o canal com o par. Não recolhe o que já foi copiado. Idempotente."""
    exige_admin(usuario, par.organizacao)
    _par_editavel(par)
    if par.estado != Maquina.Estado.REVOGADO:
        par.estado = Maquina.Estado.REVOGADO
        par.save(update_fields=["estado"])
        _auditar("par.revogado", par.organizacao, usuario, par_id=str(par.id), did=par.did)
    return par


def definir_tipo(par: Maquina, usuario, tipo: str, *, confirmou_proprio: bool = False) -> Maquina:
    """Muda o tipo do par. Promover a `próprio` exige confirmação explícita e é auditado."""
    exige_admin(usuario, par.organizacao)
    _par_editavel(par)
    if tipo not in Maquina.Tipo.values:
        raise ErroEnrolamento("tipo de par inválido")
    if par.estado == Maquina.Estado.REVOGADO:
        raise ErroEnrolamento("um par revogado não pode mudar de tipo")
    if tipo == par.tipo:
        return par
    if tipo == Maquina.Tipo.PROPRIO and not confirmou_proprio:
        raise ErroEnrolamento("marcar o par como próprio exige confirmação explícita")
    anterior, par.tipo = par.tipo, tipo
    par.save(update_fields=["tipo"])
    _auditar("par.tipo_alterado", par.organizacao, usuario, par_id=str(par.id), did=par.did, de=anterior, para=tipo)
    return par
