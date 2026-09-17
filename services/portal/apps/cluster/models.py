"""Registro de máquinas do cluster, seu status de recursos e o log de saída
usado para replicar dados entre máquinas.

Ver docs/operacao/escala-multimaquina.md para o desenho completo. Resumo:

- `Maquina` é a identidade de um nó que entrou no cluster (fase 1: sempre da
  mesma organização do dono, sem restrição entre organizações — isso é
  trabalho de uma fase futura, ver `organizacao` abaixo).
- `MaquinaStatus` é uma projeção (como `PipelineRun` em `apps.events`),
  atualizada a partir de eventos `maquina.heartbeat` — nunca escrita
  diretamente fora de `apps.cluster.projecao.aplicar_heartbeat`.
- `EventoReplicacao` é o log de saída para outras máquinas puxarem mudanças
  de `Artifact`/`DocumentText`/`DocumentFragment`. Não reaproveita
  `PipelineEvent`: aquele é trilha operacional por nó (não dado a replicar) e
  tem teto de 8 KB por payload — incompatível com o conteúdo que precisa
  viajar aqui. O padrão (log ordenado, append-only, projetável) é o mesmo;
  a tabela é outra.
"""

import uuid

from django.db import models
from django.db.models import Index

from apps.accounts.models import Organization, User


class Maquina(models.Model):
    class Modo(models.TextChoices):
        COMPUTE = "compute", "Nó de processamento (banco compartilhado)"
        REPLICA = "replica", "Réplica (stack própria, sincroniza por eventos)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Presente desde já mesmo a fase 1 não usando isto pra travar nada: é o
    # que vai permitir, numa fase futura, decidir o que pode ser replicado
    # pra uma máquina com base na organização dona dela — sem essa coluna,
    # aquele trabalho exigiria migração e retrofit em todo o histórico.
    organizacao = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="maquinas")
    dono = models.ForeignKey(User, on_delete=models.PROTECT, related_name="maquinas")
    apelido = models.CharField(max_length=120)
    hostname_declarado = models.CharField(max_length=120, blank=True)
    modo = models.CharField(max_length=20, choices=Modo.choices)
    # Preenchido só nas máquinas que rodam Ollama local (nativo no host,
    # fora do Docker — mesma ressalva de OLLAMA_HOST em settings/base.py).
    # Vazio = esta máquina não participa do roteamento de LLM (llm_router.py).
    ollama_endpoint = models.CharField(max_length=255, blank=True)
    # Capacidade, não hierarquia (ADR-006): esta máquina roda o Postgres/
    # Redis/MinIO/Qdrant que as demais compartilham. Nenhum outro código
    # trata uma `Maquina` com isto em `True` como "mais importante" — o
    # roteador de LLM, por exemplo, ignora este campo por completo.
    hospeda_infra_compartilhada = models.BooleanField(default=False)
    # sha256 do token de autenticação — o token em claro só existe no momento
    # em que `registrar_maquina` o imprime; depois disso é irrecuperável.
    token_hash = models.CharField(max_length=128)
    ativa = models.BooleanField(default=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    ultima_rotacao_token_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "cluster_maquina"
        constraints = [
            models.UniqueConstraint(fields=["organizacao", "apelido"], name="uniq_maquina_organizacao_apelido"),
        ]

    def __str__(self):
        return f"{self.apelido} ({self.get_modo_display()})"


class MaquinaStatus(models.Model):
    """Snapshot atual de recursos de uma máquina — projeção de `maquina.heartbeat`.

    Diferente de `PipelineRun`: aqui um heartbeat perdido só significa uma
    janela de dado desatualizado (não um histórico permanente), por isso a
    atualização roda fora da transação de `emit()` sem quebrar a garantia de
    reconstrutibilidade (`manage.py reconstruir_status_maquinas` reprocessa o
    log inteiro e chega no mesmo estado).
    """

    maquina = models.OneToOneField(Maquina, on_delete=models.CASCADE, related_name="status")
    cpu_percent = models.FloatField(null=True, blank=True)
    cpu_count = models.IntegerField(null=True, blank=True)
    ram_total_mb = models.IntegerField(null=True, blank=True)
    ram_disponivel_mb = models.IntegerField(null=True, blank=True)
    disco_disponivel_gb = models.FloatField(null=True, blank=True)
    filas = models.JSONField(default=list, blank=True)
    ultimo_heartbeat_em = models.DateTimeField(null=True, blank=True)
    # Marca d'água do último evento de heartbeat aplicado — mesmo papel de
    # `PipelineRun.ultimo_evento_sequence`, mas por máquina.
    ultimo_evento_sequence = models.BigIntegerField(default=0)

    class Meta:
        db_table = "cluster_maquina_status"

    def __str__(self):
        return f"status de {self.maquina.apelido}"

    @property
    def online(self) -> bool:
        """Derivado da recência do heartbeat, não guardado em coluna própria
        — evita um segundo lugar de verdade sobre "quando" que precisaria
        ficar sincronizado com `ultimo_heartbeat_em`."""
        from django.utils import timezone

        from .tasks import HEARTBEAT_INTERVALO_S, HEARTBEAT_TOLERANCIA

        if not self.ultimo_heartbeat_em:
            return False
        limite = HEARTBEAT_INTERVALO_S * HEARTBEAT_TOLERANCIA
        return (timezone.now() - self.ultimo_heartbeat_em).total_seconds() <= limite


class MaquinaModeloOllama(models.Model):
    """Capacidade observada de uma máquina para um modelo Ollama específico.

    `tokens_por_segundo_medio`/`amostras_n` vêm de telemetria de chamadas
    reais (`ollama_client.gerar`/`gerar_chat` → evento `llm.chamada_ollama` →
    `apps.cluster.projecao.aplicar_metrica_llm`), não de benchmark sintético
    — Ollama já devolve `eval_count`/`eval_duration` em toda resposta.
    `visto_pela_ultima_vez` vem do heartbeat (`aplicar_heartbeat`), que lista
    os modelos instalados na máquina a cada 30s.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maquina = models.ForeignKey(Maquina, on_delete=models.CASCADE, related_name="modelos_ollama")
    nome_modelo = models.CharField(max_length=200)
    tokens_por_segundo_medio = models.FloatField(null=True, blank=True)
    amostras_n = models.IntegerField(default=0)
    num_thread_observado = models.IntegerField(null=True, blank=True)
    # Contexto usado na última chamada real — não é "o máximo que o modelo
    # suporta", é o que de fato foi pedido (OLLAMA_NUM_CTX no momento da
    # chamada). Continua útil pra saber sob que condição a velocidade acima
    # foi medida: tokens/s muda bastante conforme o contexto usado.
    num_ctx_observado = models.IntegerField(null=True, blank=True)
    visto_pela_ultima_vez = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "cluster_maquina_modelo_ollama"
        constraints = [
            models.UniqueConstraint(fields=["maquina", "nome_modelo"], name="uniq_maquina_modelo_ollama"),
        ]

    def __str__(self):
        return f"{self.nome_modelo} em {self.maquina.apelido}"


class EventoReplicacao(models.Model):
    """Uma mudança em `Artifact`/`DocumentText`/`DocumentFragment` disponível
    para outras máquinas puxarem. Escrita sempre incondicional (via signal em
    `apps.artifacts.signals_replicacao`) — o filtro de "o que pode sair daqui"
    fica isolado em `apps.cluster.replicacao.eventos_para_peer`, o único lugar
    que uma fase futura precisa tocar para restringir por organização/
    classificação."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sequence = models.BigIntegerField(unique=True, editable=False)
    tipo = models.CharField(max_length=60)  # "artifact.upsert", "document_text.upsert", ...
    organizacao = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="eventos_replicacao")
    objeto_id = models.UUIDField()
    payload = models.JSONField()
    origem_maquina = models.ForeignKey(
        Maquina, null=True, blank=True, on_delete=models.SET_NULL, related_name="eventos_emitidos",
    )
    ocorrido_em = models.DateTimeField()

    class Meta:
        db_table = "cluster_evento_replicacao"
        indexes = [
            Index(fields=["-sequence"]),
            Index(fields=["organizacao", "-sequence"]),
        ]

    def __str__(self):
        return f"{self.tipo} {self.objeto_id} (seq {self.sequence})"
