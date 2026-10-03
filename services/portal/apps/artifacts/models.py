import uuid
from django.db import models
from django.db.models import Index
from apps.accounts.models import User, Organization


class Artifact(models.Model):
    class Type(models.TextChoices):
        PERSON = "pessoa", "Pessoa Física"
        COMPANY = "empresa", "Pessoa Jurídica"
        DOCUMENT = "documento", "Documento"
        PROCESS = "processo", "Processo"
        ADDRESS = "endereco", "Endereço"
        EVENT = "evento", "Evento"
    class ClassificationLevel(models.TextChoices):
        PUBLIC = "publico", "Público"
        INTERNAL = "interno", "Interno"
        RESTRICTED = "restrito", "Restrito"
        CONFIDENTIAL = "confidencial", "Confidencial"

    class InfoType(models.TextChoices):
        FACT = "fato", "Fato"
        OPINION = "opiniao", "Opinião"
        INFERENCE = "inferencia", "Inferência"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artifact_type = models.CharField(max_length=20, choices=Type.choices)
    content = models.JSONField()

    classification_level = models.CharField(
        max_length=20,
        choices=ClassificationLevel.choices,
        default=ClassificationLevel.RESTRICTED,
    )
    tenant = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="artifacts")
    allow_external_llm = models.BooleanField(default=False)
    classified_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL, related_name="classified_artifacts"
    )
    classified_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    info_type = models.CharField(max_length=20, choices=InfoType.choices)
    sources = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "artifacts_artifact"

    def __str__(self):
        return f"{self.get_artifact_type_display()} / {self.id}"


class ArtifactLineage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent = models.ForeignKey(Artifact, on_delete=models.PROTECT, related_name="children_lineage")
    child = models.ForeignKey(Artifact, on_delete=models.PROTECT, related_name="parent_lineage")
    transformation = models.CharField(max_length=50)
    processor = models.CharField(max_length=100)
    parameters = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "artifacts_lineage"

    def __str__(self):
        return f"{self.transformation}: {self.parent_id} → {self.child_id}"


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artifact = models.ForeignKey(Artifact, null=True, on_delete=models.SET_NULL, related_name="audit_logs")
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="audit_logs")
    organization = models.ForeignKey(
        Organization, null=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    operation = models.CharField(max_length=100)
    outcome = models.CharField(max_length=20)  # "permitido" | "bloqueado"
    reason = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        db_table = "audit_log"
        default_permissions = ()
        permissions = [("add_auditlog", "Can add audit log")]

    def __str__(self):
        return f"{self.operation} / {self.outcome} / {self.timestamp}"


class Sharing(models.Model):
    class RecipientType(models.TextChoices):
        USER = "usuario", "Usuário"
        ORGANIZATION = "organizacao", "Organização"

    class Status(models.TextChoices):
        ACTIVE = "ativo", "Ativo"
        EXPIRED = "expirado", "Expirado"
        REVOKED = "revogado", "Revogado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artifact = models.ForeignKey(Artifact, on_delete=models.CASCADE, related_name="sharings")
    shared_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="sent_sharings")
    recipient_type = models.CharField(max_length=20, choices=RecipientType.choices)
    recipient_id = models.UUIDField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="revoked_sharings"
    )

    class Meta:
        db_table = "artifacts_sharing"

    def __str__(self):
        return f"{self.artifact} → {self.recipient_type}:{self.recipient_id}"


class DocumentText(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.OneToOneField(
        Artifact, on_delete=models.CASCADE, related_name="extracted_text"
    )
    text = models.TextField()
    # Texto bruto de toda a página (nav, rodapé, menus inclusos) — trafilatura descarta
    # esse boilerplate intencionalmente para curar "text"; full_text existe para quando
    # o descarte remove algo que a busca precisava.
    full_text = models.TextField(null=True, blank=True)
    title = models.CharField(max_length=500, blank=True)
    source_url = models.CharField(max_length=2048, blank=True)
    page_type = models.CharField(max_length=50, blank=True)
    detection_confidence = models.FloatField(null=True, blank=True)
    detection_source = models.CharField(max_length=50, blank=True)
    url_pattern_cache = models.ForeignKey(
        "URLPatternCache", null=True, blank=True, on_delete=models.SET_NULL
    )
    # Vencedora entre as estratégias de extração abaixo — decisão adiada de
    # propósito (2026-09-17): nenhum processo atribui este campo por enquanto,
    # ver apps.artifacts.tasks.extract_text_from_mhtml.
    structured_data = models.JSONField(null=True, blank=True)
    dados_estruturados_dom2parser = models.JSONField(null=True, blank=True)
    dados_estruturados_extruct = models.JSONField(null=True, blank=True)
    # Extrator determinístico por page_type (apps/artifacts/extractors/strategies.py) —
    # roda sempre, junto com dom2parser e extruct acima, cada um no seu próprio campo.
    dados_estruturados_deterministico = models.JSONField(null=True, blank=True)
    dom_representation = models.TextField(null=True, blank=True)
    # Tamanho (bytes) de cada representação que o pipeline produziu para esta
    # página — ver apps.artifacts.tamanhos.montar(). Persistido uma vez na
    # extração para alimentar o gráfico de tamanhos da galeria sem recalcular
    # nem consultar o log de eventos a cada request.
    tamanhos = models.JSONField(null=True, blank=True)
    extractor_version = models.CharField(max_length=100, blank=True)
    char_count = models.IntegerField(default=0)
    word_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "artifacts_document_text"

    def __str__(self):
        return f"DocumentText({self.document_id}) — {self.word_count}w"


class DocumentFragment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_text = models.ForeignKey(
        DocumentText, on_delete=models.CASCADE, related_name="fragments"
    )
    text = models.TextField()
    fragment_index = models.IntegerField()
    total_fragments = models.IntegerField()
    qdrant_point_id = models.CharField(max_length=100, blank=True)
    qdrant_collection = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "artifacts_document_fragment"
        ordering = ["fragment_index"]

    def __str__(self):
        return f"Fragment {self.fragment_index + 1}/{self.total_fragments} of {self.document_text_id}"


class URLPatternCache(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="url_pattern_caches"
    )
    domain = models.CharField(max_length=255)
    path_pattern = models.CharField(max_length=1024)
    structure_fingerprint = models.CharField(max_length=32, default="")
    page_type = models.CharField(max_length=50)
    confidence = models.FloatField()
    detection_source = models.CharField(max_length=30, default="structural_analysis")
    # Não usado para extrair structured_data (schema salvo ficava preso aos campos
    # que tinham valor na captura que o gerou — errado para páginas cujo dado muda a
    # cada visita). Mantido pelo divergence_count abaixo; sem escritor ativo, hoje.
    extractor_config = models.JSONField(default=dict)
    hit_count = models.PositiveIntegerField(default=1)
    divergence_count = models.PositiveIntegerField(default=0)
    schema_failure_count = models.PositiveIntegerField(default=0)
    needs_review = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "artifacts_url_pattern_cache"
        unique_together = [("tenant", "domain", "path_pattern", "structure_fingerprint")]
        indexes = [Index(fields=["tenant", "domain"])]

    def __str__(self):
        return f"{self.domain}{self.path_pattern} → {self.page_type}"


class EstruturacaoLLM(models.Model):
    """Uma execução manual de estruturação, disparada no visualizador.

    Estritamente aditiva: nunca escreve em DocumentText.structured_data nem em
    URLPatternCache. Acumula uma linha por execução (um modelo, um disparo) para
    permitir comparar a mesma página estruturada por modelos diferentes.
    """

    class Provider(models.TextChoices):
        ANTHROPIC = "anthropic", "Claude (externo)"
        OLLAMA = "ollama", "Ollama (local)"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"       # criada, na fila do Celery
        EXECUTANDO = "executando", "Executando"  # worker pegou a task, chamando o LLM
        CONCLUIDO = "concluido", "Concluído"
        VAZIO = "vazio", "Vazio"
        FALHOU = "falhou", "Falhou"
        CANCELADO = "cancelado", "Cancelado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_text = models.ForeignKey(
        DocumentText, on_delete=models.CASCADE, related_name="estruturacoes_llm"
    )
    tenant = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="estruturacoes_llm"
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    model_name = models.CharField(max_length=255)
    # Referência por string ("cluster.Maquina"): evita acoplar a ordem de
    # import de apps.artifacts.models a apps.cluster.models — Django resolve
    # a FK depois que todo app já carregou. Só preenchido quando o roteador
    # (apps.cluster.llm_router) escolheu uma máquina de verdade — provider
    # anthropic, ou ollama sem cluster configurado, ficam com null (mesmo
    # significado de sempre: "rodou localmente, sem roteamento").
    maquina = models.ForeignKey(
        "cluster.Maquina", null=True, blank=True, on_delete=models.SET_NULL, related_name="estruturacoes_llm",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    categoria = models.CharField(max_length=500, blank=True)
    structured_data = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    triggered_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL, related_name="estruturacoes_llm"
    )
    # celery_task_id habilita cancelamento real: revoke(terminate=True) manda
    # SIGKILL no processo do worker, a única forma confiável de interromper uma
    # chamada HTTP síncrona e bloqueante ao Ollama/Claude no meio do caminho.
    celery_task_id = models.CharField(max_length=155, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "artifacts_estruturacao_llm"
        indexes = [Index(fields=["document_text", "-created_at"])]

    def __str__(self):
        return f"{self.provider}:{self.model_name} — {self.status} ({self.document_text_id})"


class Comparacao(models.Model):
    """Comparação de 2+ seções de dado estruturado, julgada por um LLM.

    `referencias` identifica o que foi comparado (para navegação/auditoria),
    mas `resultado` inclui um snapshot do conteúdo enviado ao juiz — a
    comparação é autocontida e sobrevive a um reprocessamento que apague o
    DocumentText de origem (extract_text_from_mhtml com forcar=True apaga e
    recria DocumentText, o que apagaria em cascata as EstruturacaoLLM
    referenciadas).
    """

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        EXECUTANDO = "executando", "Executando"
        CONCLUIDO = "concluido", "Concluído"
        FALHOU = "falhou", "Falhou"
        CANCELADO = "cancelado", "Cancelado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artifact = models.ForeignKey(Artifact, on_delete=models.CASCADE, related_name="comparacoes")
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="comparacoes")
    referencias = models.JSONField(default=list)
    modelo_juiz_provider = models.CharField(max_length=20, choices=EstruturacaoLLM.Provider.choices)
    modelo_juiz_model_name = models.CharField(max_length=255)
    # Mesmo campo/mesma razão de EstruturacaoLLM.maquina.
    maquina = models.ForeignKey(
        "cluster.Maquina", null=True, blank=True, on_delete=models.SET_NULL, related_name="comparacoes",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    resultado = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    triggered_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL, related_name="comparacoes"
    )
    celery_task_id = models.CharField(max_length=155, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "artifacts_comparacao"
        indexes = [Index(fields=["artifact", "-created_at"])]

    def __str__(self):
        return f"Comparação({self.artifact_id}) — {self.status} — {len(self.referencias)} seções"
