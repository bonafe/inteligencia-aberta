"""Registro de máquinas (e pares) do cluster e seu status de recursos.

Ver docs/operacao/escala-multimaquina.md para o desenho completo. Resumo:

- `Maquina` é a identidade de um nó que entrou no cluster (fase 1: sempre da
  mesma organização do dono, sem restrição entre organizações — isso é
  trabalho de uma fase futura, ver `organizacao` abaixo).
- `MaquinaStatus` é uma projeção (como `PipelineRun` em `apps.events`),
  atualizada a partir de eventos `maquina.heartbeat` — nunca escrita
  diretamente fora de `apps.cluster.projecao.aplicar_heartbeat`.
"""

import uuid

from django.db import models
from django.db.models import Index, Q

from apps.accounts.models import Organization, User


class Maquina(models.Model):
    """Uma instância do Inteligência Aberta (esta, `eh_local`, ou um **par** remoto).

    Fusão de `Maquina` e `Par` (ADR 011, decisão 4): o par remoto é uma linha daqui,
    com o `did:key` da instância dele, o `tipo` que **este lado** lhe atribui e o
    `estado` do enrolamento. O roteador de LLM e o canal de controle só enxergam
    pares **confirmados**; um par só vira `confirmado` depois que o administrador
    confere a impressão digital (`apps.cluster.pares.confirmar`).
    """

    class Tipo(models.TextChoices):
        PROPRIO = "proprio", "Próprio (mesmo dono)"
        TERCEIRO = "terceiro", "Terceiro"

    class Estado(models.TextChoices):
        PENDENTE = "pendente", "Pendente de conferência"
        CONFIRMADO = "confirmado", "Confirmado"
        REVOGADO = "revogado", "Revogado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Presente desde já mesmo a fase 1 não usando isto pra travar nada: é o
    # que vai permitir, numa fase futura, decidir o que pode ser replicado
    # pra uma máquina com base na organização dona dela — sem essa coluna,
    # aquele trabalho exigiria migração e retrofit em todo o histórico.
    organizacao = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="maquinas")
    dono = models.ForeignKey(User, on_delete=models.PROTECT, related_name="maquinas")
    apelido = models.CharField(max_length=120)
    hostname_declarado = models.CharField(max_length=120, blank=True)
    # Preenchido só nas máquinas que rodam Ollama local (nativo no host,
    # fora do Docker — mesma ressalva de OLLAMA_HOST em settings/base.py).
    # Vazio = esta máquina não participa do roteamento de LLM (llm_router.py).
    ollama_endpoint = models.CharField(max_length=255, blank=True)
    # Base do gateway autenticado desta máquina (`POST <base>/v1/chat/completions`,
    # apps/cluster/gateway.py). É por ele — e não pelo `ollama_endpoint`, que
    # não tem autenticação — que os peers devem chamar o LLM desta máquina
    # (ADR 009). Vazio = sem gateway; o token (`LLM_GATEWAY_TOKEN`) é do
    # cluster, não da máquina, e não é guardado aqui.
    gateway_endpoint = models.CharField(max_length=255, blank=True)
    ativa = models.BooleanField(default=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    ultima_rotacao_token_em = models.DateTimeField(null=True, blank=True)

    # ── Par (ADR 011) ──
    #: Esta linha é a própria instância (no máximo uma por organização).
    eh_local = models.BooleanField(default=False)
    #: `did:key` da instância; nulo em máquinas cadastradas antes do enrolamento.
    did = models.CharField(max_length=100, null=True, blank=True)
    #: O que **este lado** diz que o par é. "Próprio" é um rótulo do administrador,
    #: sem prova criptográfica de mesmo dono — e dá poder sobre os modelos do Ollama.
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.TERCEIRO)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDENTE)
    impressao_digital_conferida_em = models.DateTimeField(null=True, blank=True)
    conferida_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    #: Base do canal assinado do par (ex.: `http://100.x.y.z:8000`).
    endpoint_controle = models.CharField(max_length=255, blank=True)
    capacidades_json = models.JSONField(default=dict, blank=True)
    #: Último pull **bem-sucedido** (frescor do que sabemos do par).
    ultimo_pull_em = models.DateTimeField(null=True, blank=True)
    ultimo_pull_erro = models.TextField(blank=True)
    #: Controle do backoff: falhas seguidas e quando foi a última tentativa (certa ou errada).
    pull_falhas = models.PositiveIntegerField(default=0)
    ultima_tentativa_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "cluster_maquina"
        constraints = [
            models.UniqueConstraint(fields=["organizacao", "apelido"], name="uniq_maquina_organizacao_apelido"),
            models.UniqueConstraint(
                fields=["organizacao", "did"], condition=Q(did__isnull=False), name="uniq_maquina_organizacao_did",
            ),
            models.UniqueConstraint(
                fields=["organizacao"], condition=Q(eh_local=True), name="uniq_maquina_local_por_organizacao",
            ),
        ]

    def __str__(self):
        return self.apelido

    @property
    def esta_online(self) -> bool:
        """A própria instância está sempre de pé; um par, enquanto o último pull é recente.

        Para um par, `MaquinaStatus.ultimo_heartbeat_em` é o instante do último **pull** bem-sucedido.
        """
        if self.eh_local:
            return True
        status = getattr(self, "status", None)
        return bool(status and status.online)


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
    # Ollama: `None` = ainda não se sabe; `False` = a máquina está de pé mas o Ollama não responde.
    ollama_disponivel = models.BooleanField(null=True, blank=True)
    ollama_versao = models.CharField(max_length=40, blank=True)
    #: Espaço livre onde o Ollama guarda os modelos. A API do Ollama não informa isso: só existe se o volume
    #: for montado no portal (`OLLAMA_DATA_DIR_NA_PORTAL`); `None` = desconhecido (não bloqueia nada).
    disco_ollama_livre_gb = models.FloatField(null=True, blank=True)
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
    # Inventário (do `/api/tags` e do `/api/ps` do Ollama — ver `projecao.aplicar_inventario`).
    tamanho_bytes = models.BigIntegerField(null=True, blank=True)
    digest = models.CharField(max_length=100, blank=True)
    familia = models.CharField(max_length=60, blank=True)
    parametros = models.CharField(max_length=20, blank=True)
    quantizacao = models.CharField(max_length=30, blank=True)
    carregado = models.BooleanField(default=False)

    class Meta:
        db_table = "cluster_maquina_modelo_ollama"
        constraints = [
            models.UniqueConstraint(fields=["maquina", "nome_modelo"], name="uniq_maquina_modelo_ollama"),
        ]

    def __str__(self):
        return f"{self.nome_modelo} em {self.maquina.apelido}"


class ConviteEnrolamento(models.Model):
    """Convite para outra instância virar par desta — ADR 011.

    O token só existe em claro no código de convite mostrado **uma vez** ao
    administrador; aqui fica o hash. Uso único (`usado_em`) e curto (`expira_em`).
    O `tipo` é o que **este lado** vai atribuir ao par que aceitar.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organizacao = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="convites_enrolamento")
    criado_por = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    token_hash = models.CharField(max_length=64, unique=True)
    tipo = models.CharField(max_length=10, choices=Maquina.Tipo.choices, default=Maquina.Tipo.TERCEIRO)
    expira_em = models.DateTimeField()
    usado_em = models.DateTimeField(null=True, blank=True)
    usado_por_did = models.CharField(max_length=100, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cluster_convite_enrolamento"

    def __str__(self):
        return f"convite {self.id} ({self.tipo})"


class OperacaoModeloOllama(models.Model):
    """Instalar (`pull`) ou remover (`delete`) um modelo do Ollama de uma máquina — Marco D2.

    Para a máquina **local** é a operação de verdade: uma task Celery a executa e vai atualizando
    `bytes_*` e `fase`. Para um **par** é um **espelho**: a operação existe no par (`operacao_remota_id`) e o
    pull periódico traz o progresso para cá. No máximo **uma operação ativa por (máquina, modelo)** — duas
    instalações do mesmo modelo, ou instalar durante remover, são recusadas (409) pelo próprio banco.
    """

    class Tipo(models.TextChoices):
        PULL = "pull", "Instalar"
        DELETE = "delete", "Remover"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        EXECUTANDO = "executando", "Executando"
        CONCLUIDA = "concluida", "Concluída"
        FALHOU = "falhou", "Falhou"
        CANCELADA = "cancelada", "Cancelada"

    ATIVOS = (Status.PENDENTE, Status.EXECUTANDO)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maquina = models.ForeignKey(Maquina, on_delete=models.CASCADE, related_name="operacoes_modelo")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    modelo = models.CharField(max_length=200)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDENTE)
    bytes_total = models.BigIntegerField(null=True, blank=True)
    bytes_concluidos = models.BigIntegerField(default=0)
    fase = models.CharField(max_length=120, blank=True)
    erro = models.TextField(blank=True)
    celery_task_id = models.CharField(max_length=155, blank=True)
    solicitado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    #: Quando o comando veio de um par (e não de um usuário daqui): quem mandou e o ator que ele afirmou.
    origem_par = models.ForeignKey(Maquina, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ator_afirmado = models.JSONField(default=dict, blank=True)
    operacao_remota_id = models.UUIDField(null=True, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    iniciada_em = models.DateTimeField(null=True, blank=True)
    finalizada_em = models.DateTimeField(null=True, blank=True)
    ultimo_progresso_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "cluster_operacao_modelo_ollama"
        ordering = ["-criada_em"]
        indexes = [Index(fields=["maquina", "-criada_em"])]
        constraints = [
            models.UniqueConstraint(
                fields=["maquina", "modelo"], condition=Q(status__in=["pendente", "executando"]),
                name="uma_operacao_ativa_por_maquina_modelo",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.modelo} em {self.maquina.apelido} ({self.status})"

    @property
    def ativa(self) -> bool:
        return self.status in self.ATIVOS

    @property
    def percentual(self) -> int | None:
        if self.tipo != self.Tipo.PULL or not self.bytes_total:
            return None
        return min(100, int(self.bytes_concluidos * 100 / self.bytes_total))
