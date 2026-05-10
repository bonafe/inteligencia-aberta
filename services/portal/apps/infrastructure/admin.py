from django.contrib import admin
from .models import LLMProvider, MCPServer, MCPTool, ImageRegistry, ContainerImage


@admin.register(LLMProvider)
class LLMProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "provider_type", "model_name", "is_active")
    list_filter = ("provider_type", "is_active", "organization")
    search_fields = ("name", "model_name")


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
