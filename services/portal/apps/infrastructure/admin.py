from django import forms
from django.contrib import admin

from .models import LLMProvider, MCPServer, MCPTool, ImageRegistry, ContainerImage


class LLMProviderForm(forms.ModelForm):
    """A chave nunca volta ao navegador: o campo é só de escrita. Vazio mantém a
    atual; preenchido substitui; a caixa abaixo a remove."""

    nova_api_key = forms.CharField(
        label="Chave de API", required=False, strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text="Cifrada em repouso e nunca exibida depois de salva. Deixe em branco para manter a atual.",
    )
    remover_api_key = forms.BooleanField(label="Remover a chave salva", required=False)

    class Meta:
        model = LLMProvider
        exclude = ("api_key_encrypted",)

    def save(self, commit=True):
        provider = super().save(commit=False)
        if self.cleaned_data.get("remover_api_key"):
            provider.set_api_key("")
        elif self.cleaned_data.get("nova_api_key"):
            provider.set_api_key(self.cleaned_data["nova_api_key"])
        if commit:
            provider.save()
        return provider


@admin.register(LLMProvider)
class LLMProviderAdmin(admin.ModelAdmin):
    form = LLMProviderForm
    list_display = ("name", "organization", "provider_type", "vendor", "model_name", "chave_configurada", "is_active")
    list_filter = ("provider_type", "vendor", "is_active", "organization")
    search_fields = ("name", "model_name")

    @admin.display(boolean=True, description="Chave")
    def chave_configurada(self, obj):
        return obj.tem_api_key


@admin.register(MCPServer)
class MCPServerAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "is_active", "health_status", "last_health_check")
    list_filter = ("is_active", "health_status", "organization")
    search_fields = ("name", "endpoint_url")


@admin.register(MCPTool)
class MCPToolAdmin(admin.ModelAdmin):
    list_display = ("tool_name", "server", "is_enabled", "last_seen")
    list_filter = ("is_enabled", "server")
    search_fields = ("tool_name",)


@admin.register(ImageRegistry)
class ImageRegistryAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "registry_url", "is_active")
    list_filter = ("is_active", "organization")
    search_fields = ("name", "registry_url")


@admin.register(ContainerImage)
class ContainerImageAdmin(admin.ModelAdmin):
    list_display = ("image_name", "tag", "registry", "image_type", "is_active", "pull_status")
    list_filter = ("image_type", "is_active", "pull_status", "registry")
    search_fields = ("image_name", "tag")
