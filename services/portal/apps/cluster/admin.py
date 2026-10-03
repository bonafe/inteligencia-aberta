from django.contrib import admin

from .models import EventoReplicacao, Maquina, MaquinaModeloOllama, MaquinaStatus


@admin.register(Maquina)
class MaquinaAdmin(admin.ModelAdmin):
    list_display = (
        "apelido", "modo", "hospeda_infra_compartilhada", "organizacao", "dono",
        "ollama_endpoint", "ativa", "criada_em",
    )
    list_filter = ("modo", "hospeda_infra_compartilhada", "ativa", "organizacao")
    search_fields = ("apelido", "hostname_declarado")
    readonly_fields = ("id", "token_hash", "criada_em")


@admin.register(MaquinaStatus)
class MaquinaStatusAdmin(admin.ModelAdmin):
    list_display = (
        "maquina", "online", "cpu_percent", "ram_disponivel_mb",
        "disco_disponivel_gb", "ultimo_heartbeat_em",
    )
    readonly_fields = ("maquina", "ultimo_evento_sequence")

    def has_add_permission(self, request):
        return False


@admin.register(MaquinaModeloOllama)
class MaquinaModeloOllamaAdmin(admin.ModelAdmin):
    list_display = (
        "nome_modelo", "maquina", "tokens_por_segundo_medio", "amostras_n",
        "num_thread_observado", "num_ctx_observado", "visto_pela_ultima_vez",
    )
    list_filter = ("nome_modelo",)
    readonly_fields = ("maquina", "nome_modelo", "amostras_n")

    def has_add_permission(self, request):
        return False


@admin.register(EventoReplicacao)
class EventoReplicacaoAdmin(admin.ModelAdmin):
    list_display = ("sequence", "tipo", "organizacao", "objeto_id", "origem_maquina", "ocorrido_em")
    list_filter = ("tipo", "organizacao")
    search_fields = ("objeto_id",)
    readonly_fields = ("id", "sequence", "tipo", "organizacao", "objeto_id", "payload", "origem_maquina", "ocorrido_em")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
