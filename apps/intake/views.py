"""
apps/intake/views.py

Authenticated management endpoints for intake models + QR code generation.

Capability map:
  Campaigns       campaign.read / campaign.create / campaign.update / campaign.delete
  Form Fields     campaign.read / campaign.update (fields are part of campaign config)
  Submissions     submission.read / submission.update (status patch only)
  QR Code         requires IsAuthenticated (no capability gate — any internal user)
"""

import io

from django.conf import settings
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permissions import HasCapability
from apps.access.querysets import (
    filter_campaign_job_roles_for_user,
    filter_campaigns_for_user,
    filter_form_fields_for_user,
    filter_form_sections_for_user,
    filter_form_templates_for_user,
    filter_submissions_for_user,
    filter_template_fields_for_user,
)
from apps.access.viewsets import ScopedModelViewSet

from .models import CampaignJobRole, FormField, FormSection, FormTemplate, FormTemplateField, IntakeSubmission, QRCampaign
from .serializers import (
    CampaignJobRoleSerializer,
    FormFieldSerializer,
    FormSectionSerializer,
    FormTemplateFieldSerializer,
    FormTemplateSerializer,
    FormTemplateWriteSerializer,
    IntakeSubmissionDetailSerializer,
    IntakeSubmissionListSerializer,
    QRCampaignSerializer,
    QRCampaignWriteSerializer,
    SubmissionStatusUpdateSerializer,
)


# ─── Campaign ViewSet ─────────────────────────────────────────────────────────

class QRCampaignViewSet(ScopedModelViewSet):
    """
    CRUD for QR campaigns.

    DELETE soft-deactivates (sets is_active=False).
    Token is auto-generated on create; never writable.
    """
    queryset = QRCampaign.objects.select_related('org', 'site').prefetch_related(
        'campaign_job_roles__job_role',
    ).order_by('-created_at')
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_campaigns_for_user
    filterset_fields = ['org', 'site', 'is_active']
    search_fields = ['name', 'title', 'code', 'token']

    action_required_capabilities = {
        'list':           'campaign.read',
        'retrieve':       'campaign.read',
        'create':         'campaign.create',
        'update':         'campaign.update',
        'partial_update': 'campaign.update',
        'destroy':        'campaign.delete',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return QRCampaignWriteSerializer
        return QRCampaignSerializer

    def perform_create(self, serializer):
        actor = self.request.user
        org = actor.org if not actor.is_superuser else serializer.validated_data.get('org', actor.org)
        if not org:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Your account has no organization set.'})
        serializer.save(org=org)

    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active'])

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        out = QRCampaignSerializer(serializer.instance, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        out = QRCampaignSerializer(serializer.instance, context={'request': request})
        return Response(out.data)


# ─── Form Field ViewSet ───────────────────────────────────────────────────────

class FormFieldViewSet(ScopedModelViewSet):
    """
    CRUD for campaign form fields.

    campaign.read  → list/retrieve
    campaign.update → create/update/delete
    """
    queryset = FormField.objects.select_related('campaign', 'role').order_by('sort_order', 'id')
    serializer_class = FormFieldSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_form_fields_for_user
    filterset_fields = ['campaign', 'role', 'is_active', 'field_type']
    search_fields = ['label', 'field_key']

    action_required_capabilities = {
        'list':           'campaign.read',
        'retrieve':       'campaign.read',
        'create':         'campaign.update',
        'update':         'campaign.update',
        'partial_update': 'campaign.update',
        'destroy':        'campaign.update',
    }

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active'])


# ─── Campaign Job Role ViewSet ────────────────────────────────────────────────

class CampaignJobRoleViewSet(ScopedModelViewSet):
    """
    CRUD for campaign job roles.

    DELETE soft-deactivates (sets is_active=False).
    campaign.read  → list/retrieve
    campaign.update → create/update/delete
    """
    queryset = CampaignJobRole.objects.select_related(
        'campaign', 'job_role',
    ).order_by('id')
    serializer_class = CampaignJobRoleSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_campaign_job_roles_for_user
    filterset_fields = ['campaign', 'job_role', 'is_active']
    search_fields = ['campaign__name', 'campaign__title', 'job_role__name', 'job_role__code']

    action_required_capabilities = {
        'list':           'campaign.read',
        'retrieve':       'campaign.read',
        'create':         'campaign.update',
        'update':         'campaign.update',
        'partial_update': 'campaign.update',
        'destroy':        'campaign.update',
    }

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active'])


# ─── Submission ViewSet ───────────────────────────────────────────────────────

class IntakeSubmissionViewSet(ScopedModelViewSet):
    """
    List/retrieve/status-patch for submissions.

    create/destroy are disabled (submissions created via public API only).
    PATCH supports status update only.
    """
    queryset = IntakeSubmission.objects.select_related(
        'campaign', 'site', 'candidate', 'job_role',
    ).prefetch_related('answers__field', 'documents').order_by('-submitted_at')
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_submissions_for_user
    filterset_fields = ['campaign', 'site', 'candidate', 'status', 'job_role',
                        'language', 'is_possible_duplicate']
    search_fields = ['full_name', 'mobile_number_normalized', 'first_name', 'last_name']
    http_method_names = ['get', 'patch', 'head', 'options']

    action_required_capabilities = {
        'list':           'submission.read',
        'retrieve':       'submission.read',
        'partial_update': 'submission.update',
    }

    def get_serializer_class(self):
        if self.action == 'partial_update':
            return SubmissionStatusUpdateSerializer
        if self.action == 'retrieve':
            return IntakeSubmissionDetailSerializer
        return IntakeSubmissionListSerializer

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = SubmissionStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_status = serializer.validated_data['status']
        instance.status = new_status
        instance.save(update_fields=['status', 'updated_at'])

        out = IntakeSubmissionDetailSerializer(instance, context={'request': request})
        return Response(out.data)


# ─── Form Template ViewSets ───────────────────────────────────────────────────

class FormTemplateViewSet(ScopedModelViewSet):
    """
    CRUD for reusable form templates.

    campaign.read  → list/retrieve
    campaign.create → create
    campaign.update → update/partial_update
    campaign.delete → destroy (soft-deactivates)
    """
    queryset = FormTemplate.objects.select_related('org').prefetch_related(
        'sections__template_fields',
    ).order_by('-created_at')
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_form_templates_for_user
    filterset_fields = ['org', 'is_active']
    search_fields = ['name']

    action_required_capabilities = {
        'list':           'campaign.read',
        'retrieve':       'campaign.read',
        'create':         'campaign.create',
        'update':         'campaign.update',
        'partial_update': 'campaign.update',
        'destroy':        'campaign.delete',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return FormTemplateWriteSerializer
        return FormTemplateSerializer

    def perform_create(self, serializer):
        actor = self.request.user
        org = actor.org if not actor.is_superuser else serializer.validated_data.get('org', actor.org)
        if not org:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Your account has no organization set.'})
        serializer.save(org=org)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        out = FormTemplateSerializer(serializer.instance, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        out = FormTemplateSerializer(serializer.instance, context={'request': request})
        return Response(out.data)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active'])


class FormSectionViewSet(ScopedModelViewSet):
    """
    CRUD for form template sections.

    campaign.read  → list/retrieve
    campaign.update → create/update/delete
    DELETE soft-deactivates (sets is_active=False).
    """
    queryset = FormSection.objects.select_related('template').order_by('sort_order', 'id')
    serializer_class = FormSectionSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_form_sections_for_user
    filterset_fields = ['template', 'is_active']
    search_fields = ['name']

    action_required_capabilities = {
        'list':           'campaign.read',
        'retrieve':       'campaign.read',
        'create':         'campaign.update',
        'update':         'campaign.update',
        'partial_update': 'campaign.update',
        'destroy':        'campaign.update',
    }

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active'])


class FormTemplateFieldViewSet(ScopedModelViewSet):
    """
    CRUD for form template fields.

    campaign.read  → list/retrieve
    campaign.update → create/update/delete (soft-deactivates on destroy)
    """
    queryset = FormTemplateField.objects.select_related(
        'section__template', 'role',
    ).order_by('sort_order', 'id')
    serializer_class = FormTemplateFieldSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_template_fields_for_user
    filterset_fields = ['section', 'role', 'is_active', 'field_type']
    search_fields = ['label', 'field_key']

    action_required_capabilities = {
        'list':           'campaign.read',
        'retrieve':       'campaign.read',
        'create':         'campaign.update',
        'update':         'campaign.update',
        'partial_update': 'campaign.update',
        'destroy':        'campaign.update',
    }

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active'])


# ─── QR Code View ─────────────────────────────────────────────────────────────

class CampaignQRCodeView(APIView):
    """GET /api/intake/campaigns/<pk>/qrcode/ — returns PNG QR code image."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            import qrcode
        except ImportError:
            return Response(
                {'detail': 'qrcode package is not installed.'},
                status=503,
            )

        try:
            campaign = QRCampaign.objects.get(pk=pk)
        except QRCampaign.DoesNotExist:
            raise NotFound("Campaign not found.")

        frontend_url = (
            getattr(settings, 'FRONTEND_BASE_URL', '') or 'http://localhost:5173'
        ).strip().rstrip('/')
        apply_url = f"{frontend_url}/apply/{campaign.token}"

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(apply_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)

        filename = f"qr_{campaign.token[:12]}.png"
        response = HttpResponse(buf.getvalue(), content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['X-Campaign-Title'] = campaign.title or campaign.name
        response['X-Apply-URL'] = apply_url
        return response
