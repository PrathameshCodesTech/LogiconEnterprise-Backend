from django.contrib import admin
from .models import HiringApplication, Interview, InterviewFeedback, Offer


class InterviewInline(admin.TabularInline):
    model = Interview
    extra = 0
    fields = ['round_type', 'round_number', 'mode', 'status', 'scheduled_at', 'interviewer']
    raw_id_fields = ['scheduled_by', 'interviewer']


@admin.register(HiringApplication)
class HiringApplicationAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'candidate', 'job_role', 'site', 'status',
        'client_visible', 'client_decision', 'match_score', 'created_at',
    ]
    search_fields = [
        'candidate__first_name', 'candidate__last_name',
        'candidate__phone', 'job_role__name', 'site__name',
    ]
    list_filter = ['status', 'client_visible', 'client_decision', 'org', 'job_role']
    readonly_fields = ['created_at', 'updated_at', 'shortlisted_at', 'client_decision_at']
    raw_id_fields = [
        'org', 'candidate', 'mrf', 'mrf_line_item', 'site', 'job_role',
        'source_intake_submission', 'shortlisted_by',
        'client_decision_by',
    ]
    inlines = [InterviewInline]


class InterviewFeedbackInline(admin.TabularInline):
    model = InterviewFeedback
    extra = 0
    fields = ['given_by', 'rating', 'recommendation', 'feedback']
    raw_id_fields = ['given_by']


@admin.register(Interview)
class InterviewAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'hiring_application', 'round_type', 'round_number',
        'mode', 'status', 'scheduled_at', 'interviewer',
    ]
    search_fields = [
        'hiring_application__candidate__first_name',
        'hiring_application__candidate__last_name',
    ]
    list_filter = ['round_type', 'status', 'mode']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['hiring_application', 'scheduled_by', 'interviewer']
    inlines = [InterviewFeedbackInline]


@admin.register(InterviewFeedback)
class InterviewFeedbackAdmin(admin.ModelAdmin):
    list_display = ['id', 'interview', 'given_by', 'rating', 'recommendation', 'created_at']
    search_fields = ['given_by__username', 'given_by__first_name']
    list_filter = ['recommendation']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['interview', 'given_by']


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'hiring_application', 'offered_ctc', 'status',
        'joining_date', 'released_by', 'released_at',
    ]
    search_fields = [
        'hiring_application__candidate__first_name',
        'hiring_application__candidate__last_name',
    ]
    list_filter = ['status']
    readonly_fields = ['created_at', 'updated_at', 'released_at', 'accepted_at', 'declined_at']
    raw_id_fields = ['hiring_application', 'released_by']
