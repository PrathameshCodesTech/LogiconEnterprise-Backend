from django.contrib import admin

from .models import ClientOnboardingRequest


@admin.register(ClientOnboardingRequest)
class ClientOnboardingRequestAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'client', 'onboarding_type', 'status',
        'requested_by', 'created_at',
    ]
    list_filter = ['status', 'onboarding_type', 'org']
    search_fields = ['client__name', 'summary', 'requested_by__username']
    readonly_fields = ['submitted_at', 'approved_at', 'rejected_at', 'created_at', 'updated_at']
    raw_id_fields = ['org', 'client', 'requested_by']
