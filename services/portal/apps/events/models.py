"""Log de eventos do pipeline — a trilha operacional do sistema.

Duas tabelas com papéis distintos:

- `PipelineEvent` é **append-only** e é a verdade histórica: cada etapa de cada
  serviço grava aqui o que fez, quando, em qual máquina e com que resultado.
  Nada neste modelo é atualizado ou apagado depois de escrito.
- `PipelineRun` é uma **projeção** derivada do log, uma linha por captura, para
  que as telas não precisem varrer milhares de eventos. Pode ser apagada
  inteira e reconstruída com `manage.py reconstruir_projecoes` — é isso que
  torna verificável a afirmação de que o log é a fonte da verdade.

Distinção importante e deliberada: `PipelineEvent` **não substitui** o
`AuditLog` de `apps.artifacts`. O `AuditLog` é a trilha de compliance (quem
acessou o quê, exigida por `docs/seguranca/classificacao.md`); este é o diário
operacional (o que o sistema fez). Uma decisão do motor de política escreve nos
dois. Ver `docs/arquitetura/decisoes/005-barramento-de-eventos-e-observabilidade.md`.
"""

import uuid

from django.db import models
from django.db.models import Index

from apps.accounts.models import Organization, User


class Source(models.TextChoices):
    """Quem emitiu o evento."""

    EXTENSAO = "extensao", "Extensão do navegador"
    ORCHESTRATOR = "orchestrator", "Orquestrador"
    PORTAL = "portal", "Portal (web)"
    WORKER = "worker", "Worker Celery"
    BEAT = "beat", "Celery Beat"
    MCP = "mcp", "MCP"


class Status(models.TextChoices):
    """Resultado da etapa.

    `VAZIO` é o valor central deste modelo: separa "rodou e não produziu nada"
    de "falhou". É exatamente a distinção que faltava para diagnosticar por que
    o dom2parser não gerava dados em certas páginas.
    """

    INICIADO = "iniciado", "Iniciado"
    OK = "ok", "Concluído"
    VAZIO = "vazio", "Sem resultado"
    IGNORADO = "ignorado", "Ignorado"
    RETENTANDO = "retentando", "Retentando"
    FALHOU = "falhou", "Falhou"


#: Status que representam um fim de etapa sem sucesso pleno.
STATUS_PROBLEMA = (Status.FALHOU, Status.RETENTANDO)


class PipelineEventoImutavelError(RuntimeError):
    """Tentativa de alterar um evento já gravado."""


class PipelineEvent(models.Model):
    """Um fato que aconteceu em algum ponto do sistema. Nunca muda."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Ordem total. Preenchida por `nextval('pipeline_event_seq')` lido
    # explicitamente antes do INSERT (ver emit.py) — o emissor precisa do número
    # em mãos para publicar no WebSocket.
    sequence = models.BigIntegerField(unique=True, editable=False)

    # A unidade de trabalho ponta a ponta: uma captura, do clique na extensão ao
    # ponto gravado no Qdrant. Atravessa os três serviços.
    correlation_id = models.UUIDField(db_index=True)
    # O evento que provocou este, quando aplicável.
    causation_id = models.UUIDField(null=True, blank=True)

    tenant = models.ForeignKey(
        Organization, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="pipeline_events",
    )
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="pipeline_events",
    )

    source = models.CharField(max_length=20, choices=Source.choices)
    # Namespaced com ponto: "extracao.dom2parser", "captura.armazenada".
    stage = models.CharField(max_length=80)
    status = models.CharField(max_length=20, choices=Status.choices)

    # A que objeto o evento se refere: "artifact", "document_text", "fragment",
    # "url_pattern", "investigacao", "worker".
    subject_type = models.CharField(max_length=40, blank=True)
    subject_id = models.UUIDField(null=True, blank=True, db_index=True)

    # Frase curta em português, pronta para aparecer na interface sem tradução.
    message = models.TextField(blank=True)
    # Detalhes estruturados. Teto de 8 KB, imposto em emit.py. Nunca carrega
    # conteúdo capturado (HTML, MHTML, texto extraído) nem segredo.
    payload = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    duration_ms = models.IntegerField(null=True, blank=True)

    # Identidade de execução — é o que torna o log legível quando há mais de um
    # worker. Sem isso, em cluster, não dá para saber qual nó fez o quê.
    hostname = models.CharField(max_length=120, blank=True)
    process_id = models.IntegerField(null=True, blank=True)

    celery_task_id = models.CharField(max_length=64, blank=True, db_index=True)
    celery_task_name = models.CharField(max_length=200, blank=True)

    # Carimbado na origem (pode ser outro serviço, outra máquina).
    occurred_at = models.DateTimeField(db_index=True)
    # Carimbado no portal ao gravar. A diferença revela atraso de ingestão.
    recorded_at = models.DateTimeField(auto_now_add=True)

    # Eventos de infraestrutura (worker subindo, varredura do catch-up) não
    # pertencem a captura nenhuma. A marca vive no próprio evento, e não na
    # lógica de quem emite, para que a reconstrução da projeção chegue ao mesmo
    # resultado do caminho incremental — que é a propriedade central do modelo.
    avulso = models.BooleanField(default=False)

    schema_version = models.SmallIntegerField(default=1)

    class Meta:
        db_table = "pipeline_event"
        ordering = ["-sequence"]
        # Append-only, mesmo padrão do AuditLog: sem change, sem delete.
        default_permissions = ()
        permissions = [("add_pipelineevent", "Pode registrar evento de pipeline")]
        indexes = [
            Index(fields=["correlation_id", "sequence"], name="pipeline_evt_corr_seq_idx"),
            Index(fields=["tenant", "-occurred_at"], name="pipeline_evt_tenant_dt_idx"),
            Index(fields=["stage", "status"], name="pipeline_evt_stage_status_idx"),
            Index(fields=["-sequence"], name="pipeline_evt_seq_desc_idx"),
        ]

    def __str__(self):
        return f"[{self.sequence}] {self.stage} / {self.status}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PipelineEventoImutavelError(
                "PipelineEvent é append-only: um evento gravado não pode ser alterado."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PipelineEventoImutavelError("PipelineEvent é append-only: não pode ser apagado.")

    @property
    def com_problema(self) -> bool:
        return self.status in STATUS_PROBLEMA

    def para_websocket(self) -> dict:
        """Forma serializável enviada ao painel."""
        return {
            "id": str(self.id),
            "sequence": self.sequence,
            "correlation_id": str(self.correlation_id),
            "source": self.source,
            "stage": self.stage,
            "status": self.status,
            "subject_type": self.subject_type,
            "subject_id": str(self.subject_id) if self.subject_id else None,
            "message": self.message,
            "payload": self.payload,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "hostname": self.hostname,
            "process_id": self.process_id,
            "celery_task_name": self.celery_task_name,
            "occurred_at": self.occurred_at.isoformat(),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
        }


class PipelineRun(models.Model):
    """Projeção: o estado consolidado de uma captura.

    Derivada inteiramente de `PipelineEvent` por `projecao.aplicar_evento`.
    Apagar esta tabela não perde informação — `manage.py reconstruir_projecoes`
    a regenera a partir do log.
    """

    class Status(models.TextChoices):
        EM_ANDAMENTO = "em_andamento", "Em andamento"
        CONCLUIDO = "concluido", "Concluído"
        PARCIAL = "parcial", "Concluído com ressalvas"
        FALHOU = "falhou", "Falhou"
        IGNORADO = "ignorado", "Ignorado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    correlation_id = models.UUIDField(unique=True)

    tenant = models.ForeignKey(
        Organization, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="pipeline_runs",
    )
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="pipeline_runs",
    )
    artifact = models.ForeignKey(
        "artifacts.Artifact", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="pipeline_runs",
    )

    url = models.CharField(max_length=2048, blank=True)
    titulo = models.CharField(max_length=500, blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.EM_ANDAMENTO
    )
    # {"extracao.dom2parser": {"status": "vazio", "em": "...", "ms": 812,
    #                          "msg": "...", "n": 0}}
    etapas = models.JSONField(default=dict, blank=True)

    iniciado_em = models.DateTimeField()
    atualizado_em = models.DateTimeField()
    concluido_em = models.DateTimeField(null=True, blank=True)
    duracao_ms = models.IntegerField(null=True, blank=True)

    total_eventos = models.IntegerField(default=0)
    total_falhas = models.IntegerField(default=0)
    # Cursor da projeção: até onde o log já foi aplicado nesta linha.
    ultimo_evento_sequence = models.BigIntegerField(default=0)

    class Meta:
        db_table = "pipeline_run"
        ordering = ["-iniciado_em"]
        indexes = [
            Index(fields=["tenant", "-iniciado_em"], name="pipeline_run_tenant_dt_idx"),
            Index(fields=["status"], name="pipeline_run_status_idx"),
        ]

    def __str__(self):
        return f"{self.url or self.correlation_id} ({self.get_status_display()})"


class Finalidade(models.TextChoices):
    """Por que o sistema chamou um LLM — a mesma dupla provider/modelo serve
    finalidades com custo e criticidade muito diferentes; sem isso, "custo
    médio por chamada" mistura classificação barata com extração cara."""

    CLASSIFICACAO_AUTOMATICA = "classificacao_automatica", "Classificação automática (page_type)"
    EXTRACAO_AUTOMATICA = "extracao_automatica", "Extração automática (schema+dados)"
    ESTRUTURACAO_MANUAL = "estruturacao_manual", "Estruturação manual"
    COMPARACAO_JUIZ = "comparacao_juiz", "Comparação (juiz)"
    GATEWAY_EXTERNO = "gateway_externo", "Gateway OpenAI-compatível"


class ChamadaLLM(models.Model):
    """Uma chamada real a um provider de LLM (Anthropic ou Ollama) — uma linha
    por chamada, nunca uma média. Para "qual máquina é mais rápida" já existe
    `cluster.MaquinaModeloOllama` (média corrida); esta tabela existe para a
    pergunta oposta: quanto essa chamada específica custou, quando, em que
    máquina, para quê.

    Projeção de um evento `llm.chamada` (ou, para chamadas antigas, do
    `llm.chamada_ollama` que já existia antes desta tabela), construída por
    `apps.events.llm_telemetria.registrar_chamada_llm` logo após `emit()` —
    fora da transação do evento, mesmo padrão de
    `apps.cluster.projecao.aplicar_heartbeat`/`aplicar_metrica_llm`.
    Reconstruível do zero via `manage.py reconstruir_chamadas_llm`.
    """

    class Provider(models.TextChoices):
        ANTHROPIC = "anthropic", "Claude (externo)"
        OLLAMA = "ollama", "Ollama"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evento = models.OneToOneField(
        PipelineEvent, on_delete=models.CASCADE, related_name="chamada_llm",
    )
    tenant = models.ForeignKey(
        Organization, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="chamadas_llm",
    )

    # Desnormalizado de evento.occurred_at — evita um join só pra ordenar/filtrar
    # por data, que é a consulta mais comum sobre esta tabela.
    ocorreu_em = models.DateTimeField(db_index=True)
    finalidade = models.CharField(max_length=30, choices=Finalidade.choices)
    provider = models.CharField(max_length=20, choices=Provider.choices)
    modelo_solicitado = models.CharField(max_length=255)
    # O que o provider de fato ecoou de volta — pode divergir do solicitado
    # (alias, versão pinned) e é o único valor confiável pra reconciliar custo.
    modelo_resposta = models.CharField(max_length=255, blank=True)

    # Ollama: a máquina EXECUTORA (a que o roteador escolheu). Anthropic: a
    # máquina LOCAL emissora, quando o cluster está configurado — não existe
    # "máquina executora" nossa para uma chamada que roda na nuvem do provider.
    maquina = models.ForeignKey(
        "cluster.Maquina", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="chamadas_llm",
    )

    sucesso = models.BooleanField()
    error_message = models.TextField(blank=True)

    # Só a chamada HTTP/API isolada — nunca a task inteira (que inclui parse de
    # JSON, validação de schema, escrita no banco). EstruturacaoLLM.duration_ms
    # e Comparacao.duration_ms continuam medindo a task; este mede só o LLM.
    duration_ms = models.IntegerField(null=True, blank=True)
    tokens_entrada = models.IntegerField(null=True, blank=True)
    tokens_saida = models.IntegerField(null=True, blank=True)
    # Só ollama: tokens de saída / eval_duration (tempo de geração pura
    # reportado pelo próprio Ollama) — mais preciso que tokens_saida/duration_ms
    # de parede, que inclui rede/fila. Vazio em anthropic (o provider não
    # reporta tempo de geração isolado).
    tokens_por_segundo = models.FloatField(null=True, blank=True)
    chars_enviados = models.IntegerField(null=True, blank=True)
    # "max_tokens" aqui indica resposta truncada — silenciosamente corrompe
    # structured_data/schema se ninguém checar isto.
    stop_reason = models.CharField(max_length=40, blank=True)
    # message.id da Anthropic, pra cruzar com o console/billing deles. Vazio em
    # ollama (não existe log do lado do provider pra cruzar).
    request_id = models.CharField(max_length=120, blank=True)
    # Só ollama — os dois parâmetros de que a velocidade observada depende.
    num_thread = models.IntegerField(null=True, blank=True)
    num_ctx = models.IntegerField(null=True, blank=True)

    # O que motivou a chamada — mesma convenção de PipelineEvent.subject_type/
    # subject_id, deliberadamente sem FK direta: os pontos de chamada do
    # sistema (classificação automática de page_type, estruturação manual,
    # comparação, gateway externo) se linkam todos do mesmo jeito, sem
    # exceção pra nenhum.
    subject_type = models.CharField(max_length=40, blank=True)
    subject_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "events_chamada_llm"
        indexes = [
            Index(fields=["-ocorreu_em"], name="chamada_llm_ocorreu_em_idx"),
            Index(fields=["maquina", "-ocorreu_em"], name="chamada_llm_maquina_dt_idx"),
            Index(fields=["provider", "modelo_resposta", "-ocorreu_em"], name="chamada_llm_modelo_dt_idx"),
            Index(fields=["tenant", "-ocorreu_em"], name="chamada_llm_tenant_dt_idx"),
            Index(fields=["subject_type", "subject_id"], name="chamada_llm_subject_idx"),
        ]

    def __str__(self):
        return f"{self.provider}:{self.modelo_resposta or self.modelo_solicitado} ({self.finalidade})"
