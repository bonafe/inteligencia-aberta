"""Motor de regras de replicação (ADR 011): o motor puro, o modelo, a política e o comando."""
import io
import random
import uuid
from datetime import datetime, timedelta, timezone as tz
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import Organization, User
from apps.artifacts.models import Artifact, AuditLog
from apps.events.models import PipelineEvent
from apps.federacao import politica, regras
from apps.federacao.admin import RegraReplicacaoAdmin
from apps.federacao.models import RegraReplicacao
from apps.federacao.regras import Contexto, Decisao, RegraDados, avaliar

AGORA = datetime(2026, 10, 4, 12, 0, tzinfo=tz.utc)
FUTURO = AGORA + timedelta(days=7)
PASSADO = AGORA - timedelta(days=1)
OBJ = f"urn:uuid:{uuid.uuid4()}"
ESP = f"urn:uuid:{uuid.uuid4()}"

PADROES = [
    RegraDados("p1", "permitir", "enviar", par_tipo="proprio", padrao=True),
    RegraDados("p2", "permitir", "enviar", par_tipo="terceiro", nivel="publico", padrao=True),
    RegraDados("p3", "permitir", "receber", par_tipo="proprio", padrao=True),
    RegraDados("p4", "permitir", "receber", par_tipo="terceiro", padrao=True),
]


def ctx(nivel="publico", par_tipo="terceiro", sentido="enviar", **kw):
    return Contexto(sentido=sentido, par_tipo=par_tipo, nivel=nivel, **kw)


def decidir(regras_, c):
    return avaliar(regras_, c, AGORA)


def concessao(**kw):
    base = dict(id="c1", efeito="permitir", sentido="enviar", par_ref="maria", objeto_urn=OBJ, valida_ate=FUTURO)
    base.update(kw)
    return RegraDados(**base)


# ── pisos ───────────────────────────────────────────────────────────────────

def test_niveis_do_motor_sao_os_do_sistema():
    assert regras.NIVEIS == tuple(v for v, _ in Artifact.ClassificationLevel.choices)


def test_interno_nunca_sai_para_terceiro_nem_com_regra_que_permita():
    todas = PADROES + [RegraDados("u1", "permitir", "enviar", par_tipo="terceiro")]
    d = decidir(todas, ctx("interno", objeto_urn=OBJ, par_ref="maria"))
    assert not d.permitido and d.origem == "piso" and "interno" in d.motivo


def test_interno_nem_com_concessao():
    d = decidir(PADROES + [concessao()], ctx("interno", par_ref="maria", objeto_urn=OBJ))
    assert not d.permitido and d.origem == "piso"


@pytest.mark.parametrize("nivel", ["restrito", "confidencial"])
def test_restrito_e_confidencial_so_saem_para_terceiro_por_concessao(nivel):
    c = ctx(nivel, par_ref="maria", objeto_urn=OBJ)
    assert not decidir(PADROES, c).permitido
    # uma regra permissiva comum NÃO basta
    comum = RegraDados("u1", "permitir", "enviar", par_tipo="terceiro", nivel=nivel)
    d = decidir(PADROES + [comum], c)
    assert not d.permitido and d.origem == "piso"
    # a concessão basta
    d = decidir(PADROES + [concessao()], c)
    assert d.permitido and d.regra_id == "c1" and not d.regra_padrao


@pytest.mark.parametrize("mudanca", [
    dict(valida_ate=PASSADO),                     # expirada
    dict(valida_ate=None),                        # sem validade não é concessão
    dict(ativa=False),                            # revogada
    dict(objeto_urn=f"urn:uuid:{uuid.uuid4()}"),  # de outro objeto
    dict(par_ref="outra-pessoa"),                 # de outro par
    dict(par_ref=None),                           # sem par
    dict(efeito="negar"),
])
def test_concessao_invalida_nao_abre_o_piso(mudanca):
    d = decidir(PADROES + [concessao(**mudanca)], ctx("confidencial", par_ref="maria", objeto_urn=OBJ))
    assert not d.permitido


def test_concessao_vale_so_enquanto_vigente():
    c = ctx("restrito", par_ref="maria", objeto_urn=OBJ)
    assert avaliar([concessao()], c, FUTURO - timedelta(seconds=1)).permitido
    assert not avaliar([concessao()], c, FUTURO).permitido


def test_terceiro_sem_referencia_nao_tem_concessao_possivel():
    assert not decidir(PADROES + [concessao()], ctx("restrito", par_ref=None, objeto_urn=OBJ)).permitido


def test_o_proprio_nao_tem_pisos():
    for nivel in regras.NIVEIS:
        assert decidir(PADROES, ctx(nivel, par_tipo="proprio")).permitido


def test_nivel_desconhecido_vale_como_confidencial():
    d = decidir(PADROES, ctx("secretissimo", par_tipo="terceiro", par_ref="maria", objeto_urn=OBJ))
    assert not d.permitido and d.origem == "piso" and d.nivel_efetivo == "confidencial"
    d = decidir(PADROES, ctx("secretissimo", par_tipo="proprio"))
    assert d.permitido and d.nivel_efetivo == "confidencial"
    assert regras.nivel_efetivo("publico") == "publico"


def test_os_pisos_so_valem_ao_enviar():
    for nivel in ("interno", "restrito", "confidencial"):
        assert decidir(PADROES, ctx(nivel, sentido="receber")).permitido


# ── padrões ─────────────────────────────────────────────────────────────────

def test_padroes_terceiro_recebe_so_publico_e_proprio_recebe_tudo():
    assert decidir(PADROES, ctx("publico")).permitido
    assert decidir(PADROES, ctx("publico")).regra_padrao is True
    assert decidir(PADROES, ctx("publico", par_tipo="proprio")).permitido


def test_sem_nenhuma_regra_nega_por_padrao():
    d = decidir([], ctx("publico", par_tipo="proprio"))
    assert not d.permitido and d.origem == "padrao-negar" and d.regra_id is None


# ── precedência: negar vence ────────────────────────────────────────────────

def test_negar_vence_de_permitir_em_qualquer_ordem():
    negar = RegraDados("n1", "negar", "enviar", par_ref="notebook-trabalho", nivel="confidencial")
    c = ctx("confidencial", par_tipo="proprio", par_ref="notebook-trabalho")
    todas = PADROES + [negar]
    for _ in range(20):
        random.shuffle(todas)
        d = decidir(todas, c)
        assert not d.permitido and d.regra_id == "n1" and d.origem == "regra"


def test_cenario_dos_dois_notebooks():
    """O caso que motivou o motor: um notebook recebe confidencial e o outro não."""
    negar_trabalho = RegraDados("n1", "negar", "enviar", par_ref="notebook-trabalho", nivel="confidencial")
    todas = PADROES + [negar_trabalho]
    viagem = decidir(todas, ctx("confidencial", par_tipo="proprio", par_ref="notebook-viagem"))
    trabalho = decidir(todas, ctx("confidencial", par_tipo="proprio", par_ref="notebook-trabalho"))
    assert viagem.permitido and not trabalho.permitido
    # e o resto continua passando para o notebook de trabalho
    assert decidir(todas, ctx("restrito", par_tipo="proprio", par_ref="notebook-trabalho")).permitido


def test_o_receptor_se_protege_com_regra_de_receber():
    negar = RegraDados("n1", "negar", "receber", nivel="confidencial")
    assert not decidir(PADROES + [negar], ctx("confidencial", par_tipo="proprio", sentido="receber")).permitido
    assert decidir(PADROES + [negar], ctx("restrito", par_tipo="proprio", sentido="receber")).permitido
    # a regra de receber não afeta o envio
    assert decidir(PADROES + [negar], ctx("confidencial", par_tipo="proprio")).permitido


def test_regra_de_negar_inativa_ou_vencida_nao_conta():
    c = ctx("publico", par_tipo="proprio")
    assert decidir(PADROES + [RegraDados("n1", "negar", "enviar", ativa=False)], c).permitido
    assert decidir(PADROES + [RegraDados("n1", "negar", "enviar", valida_ate=PASSADO)], c).permitido
    assert not decidir(PADROES + [RegraDados("n1", "negar", "enviar", valida_ate=FUTURO)], c).permitido


@pytest.mark.parametrize("condicao,casa_com,nao_casa", [
    ("par_ref", dict(par_ref="a"), dict(par_ref="b")),
    ("par_tipo", dict(par_tipo="proprio"), dict(par_tipo="terceiro")),
    ("nivel", dict(nivel="publico"), dict(nivel="restrito")),
    ("tipo_objeto", dict(tipo_objeto="documento"), dict(tipo_objeto="empresa")),
    ("espaco_urn", dict(espaco_urn=ESP), dict(espaco_urn=f"urn:uuid:{uuid.uuid4()}")),
    ("objeto_urn", dict(objeto_urn=OBJ), dict(objeto_urn=f"urn:uuid:{uuid.uuid4()}")),
])
def test_cada_condicao_preenchida_precisa_bater(condicao, casa_com, nao_casa):
    # Nível público: assim trocar o tipo do par não esbarra nos pisos (que são outro teste).
    base = dict(par_tipo="proprio", par_ref="a", nivel="publico", tipo_objeto="documento",
                espaco_urn=ESP, objeto_urn=OBJ)
    negar = RegraDados("n1", "negar", "enviar", **{condicao: casa_com[condicao]})
    assert not decidir(PADROES + [negar], ctx(**base)).permitido
    assert decidir(PADROES + [negar], ctx(**{**base, **nao_casa})).permitido


def test_negar_por_espaco():
    negar = RegraDados("n1", "negar", "enviar", espaco_urn=ESP)
    assert not decidir(PADROES + [negar], ctx("publico", par_tipo="proprio", espaco_urn=ESP)).permitido
    assert decidir(PADROES + [negar], ctx("publico", par_tipo="proprio", espaco_urn=None)).permitido


def test_a_decisao_lista_as_regras_que_casaram():
    negar = RegraDados("n1", "negar", "enviar", par_tipo="proprio")
    d = decidir(PADROES + [negar], ctx("publico", par_tipo="proprio"))
    assert set(d.casaram) == {"p1", "n1"} and isinstance(d, Decisao)


def test_o_motor_e_deterministico_e_nao_muda_a_entrada():
    todas = PADROES + [RegraDados("n1", "negar", "enviar", nivel="publico", par_tipo="terceiro")]
    copia = list(todas)
    c = ctx("publico")
    assert decidir(todas, c) == decidir(list(reversed(todas)), c) and todas == copia


# ── modelo ──────────────────────────────────────────────────────────────────


@pytest.fixture
def org(db):
    dono = User.objects.create_user(username="dono", password="x")
    return Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)


@pytest.fixture
def outra_org(db):
    dono = User.objects.create_user(username="outro", password="x")
    return Organization.objects.create(name="P", slug="p", org_type="individual", owner=dono)


def _regra(org, **kw):
    base = dict(organizacao=org, efeito="negar", sentido="enviar")
    base.update(kw)
    return RegraReplicacao.objects.create(**base)


@pytest.mark.django_db
class TestModelo:
    def test_nivel_invalido_e_recusado(self, org):
        with pytest.raises(ValidationError):
            _regra(org, nivel="ultrassecreto")

    def test_urns_sao_normalizadas_e_invalidas_recusadas(self, org):
        r = _regra(org, espaco_urn=ESP.upper().replace("URN:UUID:", "URN:UUID:"))
        assert r.espaco_urn == ESP
        with pytest.raises(ValidationError):
            _regra(org, espaco_urn="lixo")

    def test_par_ref_e_aparado_e_vazio_vira_qualquer(self, org):
        assert _regra(org, par_ref="  notebook  ").par_ref == "notebook"
        assert _regra(org, par_ref="   ", nivel="publico").par_ref is None

    def test_regra_com_objeto_exige_validade_e_concessao_exige_par(self, org):
        with pytest.raises(ValidationError):
            _regra(org, objeto_urn=OBJ)
        with pytest.raises(ValidationError):
            _regra(org, objeto_urn=OBJ, valida_ate=FUTURO, efeito="permitir")           # sem par_ref
        _regra(org, objeto_urn=OBJ, valida_ate=FUTURO, efeito="permitir", par_ref="maria")
        _regra(org, objeto_urn=OBJ, valida_ate=FUTURO, efeito="negar")                  # negar não precisa de par

    def test_o_banco_tambem_recusa_objeto_sem_validade(self, org):
        with pytest.raises(IntegrityError), transaction.atomic():
            RegraReplicacao.objects.bulk_create([
                RegraReplicacao(organizacao=org, efeito="negar", sentido="enviar", objeto_urn=OBJ)])

    def test_como_dados_reflete_a_linha(self, org):
        r = _regra(org, par_ref="x", nivel="restrito", valida_ate=FUTURO)
        d = r.como_dados()
        assert (d.id, d.efeito, d.par_ref, d.nivel, d.valida_ate, d.ativa) == (str(r.id), "negar", "x", "restrito", FUTURO, True)

    def test_admin_nao_apaga_regra_padrao(self, org):
        politica.garantir_regras_padrao(org)
        padrao = RegraReplicacao.objects.filter(padrao=True).first()
        comum = _regra(org)
        admin_ = RegraReplicacaoAdmin(RegraReplicacao, admin_site=None)
        assert admin_.has_delete_permission(None, padrao) is False
        assert admin_.has_delete_permission(None, comum) is True
        assert admin_.has_delete_permission(None) is True


# ── política (motor + banco) ────────────────────────────────────────────────

def _artefato(org, nivel="restrito", tipo=Artifact.Type.DOCUMENT):
    return Artifact.objects.create(
        artifact_type=tipo, tenant=org, info_type=Artifact.InfoType.FACT, classification_level=nivel,
        content={"url": "https://exemplo.org/", "mhtml_path": "x"},
    )


@pytest.mark.django_db
class TestPolitica:
    def test_semeia_as_quatro_padrao_uma_vez(self, org):
        assert politica.garantir_regras_padrao(org) == 4
        assert politica.garantir_regras_padrao(org) == 0
        assert RegraReplicacao.objects.filter(organizacao=org, padrao=True, ativa=True).count() == 4

    def test_padrao_desativada_ou_apagada_nao_ressurge_sozinha(self, org):
        politica.garantir_regras_padrao(org)
        regra = RegraReplicacao.objects.get(chave_padrao="enviar-proprio-tudo")
        regra.ativa = False
        regra.save()
        RegraReplicacao.objects.filter(chave_padrao="receber-terceiro-tudo").delete()
        assert politica.garantir_regras_padrao(org) == 0
        assert RegraReplicacao.objects.filter(organizacao=org, padrao=True).count() == 3
        # a semeadura explícita (--semear) recria só a que faltava, sem mexer na desativada
        assert politica.garantir_regras_padrao(org, completo=True) == 1
        assert not RegraReplicacao.objects.get(chave_padrao="enviar-proprio-tudo").ativa

    def test_decidir_semeia_na_primeira_vez(self, org):
        assert not RegraReplicacao.objects.exists()
        d = politica.decidir(org, ctx("publico", par_tipo="proprio"))
        assert d.permitido and RegraReplicacao.objects.filter(organizacao=org).count() == 4

    def test_com_os_padroes_o_proprio_recebe_tudo_e_o_terceiro_so_publico(self, org):
        for nivel in regras.NIVEIS:
            assert politica.decidir(org, ctx(nivel, par_tipo="proprio")).permitido
        assert politica.decidir(org, ctx("publico")).permitido
        assert not politica.decidir(org, ctx("interno")).permitido
        assert not politica.decidir(org, ctx("restrito")).permitido

    def test_desativar_o_padrao_do_proprio_faz_negar(self, org):
        politica.garantir_regras_padrao(org)
        RegraReplicacao.objects.filter(chave_padrao="enviar-proprio-tudo").update(ativa=False)
        assert not politica.decidir(org, ctx("publico", par_tipo="proprio")).permitido

    def test_regra_do_usuario_nega_para_um_par_especifico(self, org):
        _regra(org, par_ref="notebook-trabalho", nivel="confidencial")
        assert not politica.decidir(org, ctx("confidencial", par_tipo="proprio", par_ref="notebook-trabalho")).permitido
        assert politica.decidir(org, ctx("confidencial", par_tipo="proprio", par_ref="notebook-viagem")).permitido

    def test_as_regras_de_outra_organizacao_nao_valem(self, org, outra_org):
        _regra(outra_org, nivel="publico")                      # nega tudo público... na outra organização
        assert politica.decidir(org, ctx("publico", par_tipo="proprio")).permitido
        assert not politica.decidir(outra_org, ctx("publico", par_tipo="proprio")).permitido

    def test_decidir_artefato_usa_o_nivel_o_tipo_e_a_urn_do_artefato(self, org):
        a = _artefato(org, "confidencial")
        _regra(org, tipo_objeto="documento", par_tipo="proprio", nivel="confidencial")
        d = politica.decidir_artefato(a, sentido="enviar", par_tipo="proprio", par_ref="n")
        assert not d.permitido and d.nivel_efetivo == "confidencial"
        e = _artefato(org, "confidencial", tipo=Artifact.Type.COMPANY)
        assert politica.decidir_artefato(e, sentido="enviar", par_tipo="proprio", par_ref="n").permitido

    def test_conceder_e_revogar(self, org):
        a = _artefato(org, "confidencial")
        assert not politica.decidir_artefato(a, sentido="enviar", par_tipo="terceiro", par_ref="maria").permitido
        c = politica.conceder(org, a, par_ref="maria", valida_ate=timezone.now() + timedelta(days=1), criada_por=org.owner)
        assert c.objeto_urn == a.urn and c.efeito == "permitir" and c.criada_por_id == org.owner_id
        assert politica.decidir_artefato(a, sentido="enviar", par_tipo="terceiro", par_ref="maria").permitido
        assert not politica.decidir_artefato(a, sentido="enviar", par_tipo="terceiro", par_ref="outra").permitido
        politica.revogar(c)
        politica.revogar(c)                                     # idempotente
        c.refresh_from_db()
        assert not c.ativa and c.revogada_em is not None
        assert not politica.decidir_artefato(a, sentido="enviar", par_tipo="terceiro", par_ref="maria").permitido

    def test_conceder_valida_a_entrada(self, org, outra_org):
        a = _artefato(org, "confidencial")
        amanha = timezone.now() + timedelta(days=1)
        with pytest.raises(ValueError):
            politica.conceder(org, a, par_ref="m", valida_ate=None)
        with pytest.raises(ValueError):
            politica.conceder(org, a, par_ref="m", valida_ate=timezone.now() - timedelta(seconds=1))
        with pytest.raises(ValueError):
            politica.conceder(org, a, par_ref="  ", valida_ate=amanha)
        with pytest.raises(ValueError):
            politica.conceder(outra_org, a, par_ref="m", valida_ate=amanha)
        assert not RegraReplicacao.objects.filter(objeto_urn=a.urn).exists()

    def test_concessao_nao_serve_para_interno(self, org):
        a = _artefato(org, "interno")
        politica.conceder(org, a, par_ref="maria", valida_ate=timezone.now() + timedelta(days=1))
        assert not politica.decidir_artefato(a, sentido="enviar", par_tipo="terceiro", par_ref="maria").permitido


# ── registro da decisão (P8) ────────────────────────────────────────────────

@pytest.mark.django_db
class TestRegistro:
    def _registrar(self, org, nivel, par_tipo="proprio", par_ref="n"):
        a = _artefato(org, nivel)
        c = politica.contexto_do_artefato(a, sentido="enviar", par_tipo=par_tipo, par_ref=par_ref)
        d = politica.decidir(org, c)
        politica.registrar_decisao(d, c, organizacao=org, artefato=a)
        return a, d

    def test_toda_decisao_vira_pipeline_event_sem_conteudo(self, org):
        a, d = self._registrar(org, "publico")
        ev = PipelineEvent.objects.get(stage="federacao.decisao")
        assert ev.status == "ok" and str(ev.subject_id) == str(a.id) and ev.tenant_id == org.id
        assert ev.payload["regra_id"] == d.regra_id and ev.payload["permitido"] is True
        assert "url" not in json_dump(ev.payload) and "exemplo.org" not in json_dump(ev.payload)

    def test_negada_vira_status_ignorado_e_o_publico_nao_gera_audit_log(self, org):
        self._registrar(org, "interno", par_tipo="terceiro")
        assert PipelineEvent.objects.get(stage="federacao.decisao").status == "ignorado"
        assert AuditLog.objects.count() == 0

    @pytest.mark.parametrize("nivel,par_tipo,outcome", [
        ("confidencial", "proprio", "permitido"),
        ("restrito", "proprio", "permitido"),
        ("confidencial", "terceiro", "bloqueado"),
        ("restrito", "terceiro", "bloqueado"),
    ])
    def test_restrito_e_confidencial_geram_audit_log(self, org, nivel, par_tipo, outcome):
        a, d = self._registrar(org, nivel, par_tipo=par_tipo, par_ref="maria")
        log = AuditLog.objects.get()
        assert log.outcome == outcome and log.operation == "replicacao.enviar"
        assert log.artifact_id == a.id and log.organization_id == org.id and log.reason == d.motivo
        assert log.metadata["par_ref"] == "maria"

    def test_registrar_nunca_levanta(self, org):
        a = _artefato(org, "confidencial")
        c = politica.contexto_do_artefato(a, sentido="enviar", par_tipo="proprio")
        d = politica.decidir(org, c)
        with mock.patch.object(politica, "emit", side_effect=RuntimeError("x")):
            politica.registrar_decisao(d, c, organizacao=org, artefato=a)

    def test_decidir_sozinho_nao_deixa_rastro(self, org):
        politica.decidir(org, ctx("confidencial", par_tipo="proprio"))
        assert not PipelineEvent.objects.filter(stage="federacao.decisao").exists() and AuditLog.objects.count() == 0


def json_dump(obj) -> str:
    import json

    return json.dumps(obj, default=str)


# ── comando ─────────────────────────────────────────────────────────────────

def _cmd(*args):
    saida = io.StringIO()
    call_command("regras_replicacao", *args, stdout=saida)
    return saida.getvalue()


@pytest.mark.django_db
class TestComando:
    def test_semear_cria_as_padrao_em_cada_organizacao(self, org, outra_org):
        assert "8 regra(s) padrão criada(s)" in _cmd("--semear")
        assert "0 regra(s) padrão criada(s)" in _cmd("--semear")

    def test_listar(self, org):
        politica.garantir_regras_padrao(org)
        saida = _cmd("--listar", "--organizacao", org.slug)
        assert saida.count("padrão") == 4 and "permitir" in saida

    def test_decidir_explica_e_nao_registra(self, org):
        saida = _cmd("--decidir", "--organizacao", org.slug, "--par-tipo", "terceiro", "--nivel", "confidencial")
        assert "NEGADO" in saida and "[piso]" in saida
        saida = _cmd("--decidir", "--organizacao", org.slug, "--par-tipo", "proprio", "--nivel", "confidencial")
        assert "PERMITIDO" in saida and "[regra]" in saida and "nível efetivo: confidencial" in saida
        assert not PipelineEvent.objects.filter(stage="federacao.decisao").exists()

    def test_erros_de_uso(self, org):
        with pytest.raises(CommandError):
            _cmd()
        with pytest.raises(CommandError):
            _cmd("--listar")
        with pytest.raises(CommandError):
            _cmd("--listar", "--organizacao", "nao-existe")
        with pytest.raises(CommandError):
            _cmd("--decidir", "--organizacao", org.slug)


# ── concessão em lote por espaço (ADR 018, item 2) ───────────────────────────

def lote(**kw):
    base = dict(id="l1", efeito="permitir", sentido="enviar", par_ref="irmao", espaco_urn=ESP, valida_ate=FUTURO)
    base.update(kw)
    return RegraDados(**base)


@pytest.mark.parametrize("nivel", ["restrito", "confidencial"])
def test_lote_por_espaco_explicito_libera_restrito_e_confidencial(nivel):
    c = ctx(nivel=nivel, par_ref="irmao", espaco_urn=ESP, espaco_explicito=True, objeto_urn=OBJ)
    d = decidir(PADROES + [lote()], c)
    assert d.permitido and d.regra_id == "l1"


def test_lote_nao_vale_no_espaco_padrao():
    c = ctx(nivel="restrito", par_ref="irmao", espaco_urn=ESP, espaco_explicito=False, objeto_urn=OBJ)
    d = decidir(PADROES + [lote()], c)
    assert not d.permitido and d.origem == "piso"


def test_lote_exige_par_nomeado_e_validade():
    c = ctx(nivel="restrito", par_ref="irmao", espaco_urn=ESP, espaco_explicito=True, objeto_urn=OBJ)
    assert not decidir(PADROES + [lote(par_ref=None)], c).permitido
    assert not decidir(PADROES + [lote(valida_ate=None)], c).permitido
    assert not decidir(PADROES + [lote(valida_ate=PASSADO)], c).permitido


def test_lote_nao_vale_para_outro_par_nem_outro_espaco():
    outro_esp = f"urn:uuid:{uuid.uuid4()}"
    c = ctx(nivel="restrito", par_ref="tio", espaco_urn=ESP, espaco_explicito=True, objeto_urn=OBJ)
    assert not decidir(PADROES + [lote()], c).permitido
    c = ctx(nivel="restrito", par_ref="irmao", espaco_urn=outro_esp, espaco_explicito=True, objeto_urn=OBJ)
    assert not decidir(PADROES + [lote()], c).permitido


def test_lote_nao_libera_interno_e_negar_vence():
    c = ctx(nivel="interno", par_ref="irmao", espaco_urn=ESP, espaco_explicito=True, objeto_urn=OBJ)
    assert decidir(PADROES + [lote()], c).origem == "piso"
    c = ctx(nivel="restrito", par_ref="irmao", espaco_urn=ESP, espaco_explicito=True, objeto_urn=OBJ)
    negar_obj = RegraDados("n1", "negar", "enviar", objeto_urn=OBJ)  # "sem a foto da tia"
    d = decidir(PADROES + [lote(), negar_obj], c)
    assert not d.permitido and d.regra_id == "n1"


@pytest.mark.django_db
class TestConcederEspaco:
    @pytest.fixture
    def org(self):
        dono = User.objects.create_user(username="d", password="x")
        return Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)

    def test_cria_a_regra_e_vale_para_o_artefato_do_espaco(self, org):
        from apps.federacao import espacos

        esp = espacos.criar_espaco(organizacao=org, nome="familia")
        art = Artifact.objects.create(
            artifact_type="documento", content={}, tenant=org, classification_level="restrito", info_type="fato",
        )
        espacos.incluir_artefato(esp, art)
        antes = politica.decidir_artefato(art, sentido="enviar", par_tipo="terceiro", par_ref="irmao",
                                          espaco_urn=esp.urn, espaco_explicito=True)
        assert not antes.permitido
        politica.conceder_espaco(org, esp, par_ref="irmao", valida_ate=timezone.now() + timedelta(days=30))
        depois = politica.decidir_artefato(art, sentido="enviar", par_tipo="terceiro", par_ref="irmao",
                                           espaco_urn=esp.urn, espaco_explicito=True)
        assert depois.permitido

    def test_recusa_espaco_padrao_validade_longa_e_par_vazio(self, org):
        from apps.federacao import espacos

        esp = espacos.criar_espaco(organizacao=org, nome="familia")
        futuro = timezone.now() + timedelta(days=30)
        with pytest.raises(ValueError):
            politica.conceder_espaco(org, espacos.espaco_padrao(org), par_ref="x", valida_ate=futuro)
        with pytest.raises(ValueError):
            politica.conceder_espaco(org, esp, par_ref="x", valida_ate=timezone.now() + timedelta(days=400))
        with pytest.raises(ValueError):
            politica.conceder_espaco(org, esp, par_ref=" ", valida_ate=futuro)
        with pytest.raises(ValueError):
            politica.conceder_espaco(org, esp, par_ref="x", valida_ate=timezone.now() - timedelta(days=1))

    def test_revogar_desfaz(self, org):
        from apps.federacao import espacos

        esp = espacos.criar_espaco(organizacao=org, nome="familia")
        regra = politica.conceder_espaco(org, esp, par_ref="irmao", valida_ate=timezone.now() + timedelta(days=30))
        politica.revogar(regra)
        regra.refresh_from_db()
        assert not regra.ativa
