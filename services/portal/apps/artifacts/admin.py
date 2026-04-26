from django.contrib import admin
from .models import Artifact, AuditLog, Sharing


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    list_display = ("id", "artifact_type", "classification_level", "tenant", "info_type", "created_at")
    list_filter = ("artifact_type", "classification_level", "info_type")
    search_fields = ("id",)
    readonly_fields = ("id", "classified_at", "created_at", "updated_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("operation", "outcome", "user", "artifact", "timestamp")
    list_filter = ("outcome", "operation")
    readonly_fields = (
        "id", "artifact", "user", "organization",
        "operation", "outcome", "reason", "timestamp", "metadata",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Sharing)
class SharingAdmin(admin.ModelAdmin):
    list_display = ("artifact", "shared_by", "recipient_type", "status", "created_at", "expires_at")
    list_filter = ("status", "recipient_type")
    readonly_fields = ("id", "created_at")
