"""
apps/workflow/config_views.py

Workflow Config (approval-setup) API views.

Endpoints:
  GET/POST     /api/workflow/config/flows/
  GET/PATCH/DELETE /api/workflow/config/flows/{id}/

  GET/POST     /api/workflow/config/steps/
  GET/PATCH/DELETE /api/workflow/config/steps/{id}/

  GET/POST     /api/workflow/config/rules/
  GET/PATCH/DELETE /api/workflow/config/rules/{id}/

  GET/POST     /api/workflow/config/assignments/
  GET/PATCH/DELETE /api/workflow/config/assignments/{id}/

  GET          /api/workflow/config/preview/
"""

from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from apps.access.capabilities import WORKFLOW_CONFIG_READ, WORKFLOW_CONFIG_MANAGE
from apps.access.viewsets import ReadAfterWriteMixin
from apps.access.permissions import HasCapability, HasAnyCapability
from apps.access.viewsets import ActionCapabilityMixin

from .config_serializers import (
    ApprovalFlowSerializer, ApprovalFlowWriteSerializer,
    ApprovalStepSerializer, ApprovalStepWriteSerializer,
    FlowRuleSerializer, FlowRuleWriteSerializer,
    AssignmentConfigSerializer, AssignmentConfigWriteSerializer,
)
from .exceptions import WorkflowConfigurationError
from .models import (
    WorkflowTemplate, WorkflowStepTemplate,
    WorkflowTemplateMapping, StepAssignmentConfig,
)


_ReadAfterWriteMixin = ReadAfterWriteMixin  # local alias kept for internal use


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _filter_for_org(qs, user):
    """Restrict config queryset to user's org. Superusers see all."""
    if user.is_superuser:
        return qs
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return qs.none()
    return qs.filter(org_id=org_id)


def _filter_steps_for_org(qs, user):
    """Restrict WorkflowStepTemplate queryset via template__org."""
    if user.is_superuser:
        return qs
    org_id = getattr(user, 'org_id', None)
    if not org_id:
        return qs.none()
    return qs.filter(template__org_id=org_id)


def _resolve_org(user):
    """Return the user's org object, or None."""
    return getattr(user, 'org', None)


def _require_org(user):
    """Return user's org or raise 400."""
    org = _resolve_org(user)
    if org is None:
        raise ValidationError({'detail': 'Your account has no organization set.'})
    return org


# ─── Approval Flows (WorkflowTemplate) ───────────────────────────────────────

class ApprovalFlowViewSet(_ReadAfterWriteMixin, ActionCapabilityMixin, ModelViewSet):
    """
    CRUD for WorkflowTemplate (approval flows).
    DELETE soft-deactivates rather than hard-deleting.
    """
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['trigger_type', 'is_active']
    search_fields = ['name', 'code', 'description']
    read_serializer_class = ApprovalFlowSerializer

    action_required_capabilities = {
        'list':           WORKFLOW_CONFIG_READ,
        'retrieve':       WORKFLOW_CONFIG_READ,
        'create':         WORKFLOW_CONFIG_MANAGE,
        'update':         WORKFLOW_CONFIG_MANAGE,
        'partial_update': WORKFLOW_CONFIG_MANAGE,
        'destroy':        WORKFLOW_CONFIG_MANAGE,
    }

    def get_queryset(self):
        qs = WorkflowTemplate.objects.select_related('org').order_by('org', 'name')
        return _filter_for_org(qs, self.request.user)

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ApprovalFlowWriteSerializer
        return ApprovalFlowSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['org'] = _resolve_org(self.request.user)
        return ctx

    def perform_create(self, serializer):
        org = _require_org(self.request.user)
        serializer.save(org=org)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Approval Steps (WorkflowStepTemplate) ───────────────────────────────────

class ApprovalStepViewSet(_ReadAfterWriteMixin, ActionCapabilityMixin, ModelViewSet):
    """
    CRUD for WorkflowStepTemplate.
    DELETE is hard if no workflow step instances reference the step, else 400.
    """
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['template', 'assignment_mode', 'actor_type']
    search_fields = ['name', 'code']
    read_serializer_class = ApprovalStepSerializer

    action_required_capabilities = {
        'list':           WORKFLOW_CONFIG_READ,
        'retrieve':       WORKFLOW_CONFIG_READ,
        'create':         WORKFLOW_CONFIG_MANAGE,
        'update':         WORKFLOW_CONFIG_MANAGE,
        'partial_update': WORKFLOW_CONFIG_MANAGE,
        'destroy':        WORKFLOW_CONFIG_MANAGE,
    }

    def get_queryset(self):
        qs = WorkflowStepTemplate.objects.select_related(
            'template', 'template__org',
        ).order_by('template', 'order')
        return _filter_steps_for_org(qs, self.request.user)

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ApprovalStepWriteSerializer
        return ApprovalStepSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['org'] = _resolve_org(self.request.user)
        return ctx

    def perform_create(self, serializer):
        template = serializer.validated_data.get('template')
        if template and not self.request.user.is_superuser:
            org = _resolve_org(self.request.user)
            if org and template.org_id != org.pk:
                raise PermissionDenied(
                    'You cannot create steps for a template in another organization.'
                )
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        from .models import WorkflowStepInstance
        instance = self.get_object()
        if WorkflowStepInstance.objects.filter(step_template=instance).exists():
            return Response(
                {
                    'detail': (
                        'This step cannot be deleted because it is referenced by '
                        'existing workflow step instances.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Flow Rules (WorkflowTemplateMapping) ────────────────────────────────────

class FlowRuleViewSet(_ReadAfterWriteMixin, ActionCapabilityMixin, ModelViewSet):
    """
    CRUD for WorkflowTemplateMapping (flow routing rules).
    DELETE soft-deactivates.
    """
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['trigger_type', 'template', 'client', 'site', 'is_active']
    read_serializer_class = FlowRuleSerializer

    action_required_capabilities = {
        'list':           WORKFLOW_CONFIG_READ,
        'retrieve':       WORKFLOW_CONFIG_READ,
        'create':         WORKFLOW_CONFIG_MANAGE,
        'update':         WORKFLOW_CONFIG_MANAGE,
        'partial_update': WORKFLOW_CONFIG_MANAGE,
        'destroy':        WORKFLOW_CONFIG_MANAGE,
    }

    def get_queryset(self):
        qs = WorkflowTemplateMapping.objects.select_related(
            'org', 'template', 'client', 'site',
        ).order_by('org', 'trigger_type', '-created_at')
        return _filter_for_org(qs, self.request.user)

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return FlowRuleWriteSerializer
        return FlowRuleSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['org'] = _resolve_org(self.request.user)
        return ctx

    def perform_create(self, serializer):
        org = _require_org(self.request.user)
        serializer.save(org=org)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Assignment Configs (StepAssignmentConfig) ────────────────────────────────

class AssignmentConfigViewSet(_ReadAfterWriteMixin, ActionCapabilityMixin, ModelViewSet):
    """
    CRUD for StepAssignmentConfig (responsible people).
    DELETE soft-deactivates, allowing replacement.
    """
    permission_classes = [IsAuthenticated, HasCapability]
    read_serializer_class = AssignmentConfigSerializer
    filterset_fields = [
        'trigger_type', 'step_code', 'client', 'site',
        'department', 'named_user', 'is_active',
    ]
    search_fields = ['step_code', 'named_user__username', 'department__name']

    action_required_capabilities = {
        'list':           WORKFLOW_CONFIG_READ,
        'retrieve':       WORKFLOW_CONFIG_READ,
        'create':         WORKFLOW_CONFIG_MANAGE,
        'update':         WORKFLOW_CONFIG_MANAGE,
        'partial_update': WORKFLOW_CONFIG_MANAGE,
        'destroy':        WORKFLOW_CONFIG_MANAGE,
    }

    def get_queryset(self):
        qs = StepAssignmentConfig.objects.select_related(
            'org', 'client', 'site', 'department', 'named_user',
            'eligible_role', 'eligible_scope',
        ).order_by('org', 'trigger_type', 'step_code', '-created_at')
        return _filter_for_org(qs, self.request.user)

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return AssignmentConfigWriteSerializer
        return AssignmentConfigSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['org'] = _resolve_org(self.request.user)
        return ctx

    def perform_create(self, serializer):
        org = _require_org(self.request.user)
        serializer.save(org=org)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Config Preview ───────────────────────────────────────────────────────────

class ConfigPreviewView(APIView):
    """
    GET /api/workflow/config/preview/

    Dry-run preview showing which template/steps/assignments would apply
    for a given request_type (trigger_type) + optional client/site context.
    Never creates workflows. Always returns 200 — check `ok` field.

    Query params:
      request_type  (required) — trigger_type alias: 'mrf' | 'client_onboarding'
      client        (optional) — Client PK
      site          (optional) — SiteProfile PK
    """
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = WORKFLOW_CONFIG_READ

    def get(self, request):
        from .models import WorkflowTemplateMapping
        from .services import resolve_workflow_template, resolve_step_assignment
        from apps.sites.models import Client, SiteProfile

        request_type = request.query_params.get('request_type', '').strip()
        if not request_type:
            return Response(
                {'detail': '"request_type" query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        client_id = request.query_params.get('client', '').strip()
        site_id = request.query_params.get('site', '').strip()

        # Resolve site/client objects
        site = None
        client = None

        if site_id:
            try:
                site = SiteProfile.objects.select_related('client', 'org').get(pk=site_id)
            except (SiteProfile.DoesNotExist, ValueError):
                return Response(
                    {'detail': f'Site with id={site_id} not found.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if client_id:
            try:
                client = Client.objects.select_related('org').get(pk=client_id)
            except (Client.DoesNotExist, ValueError):
                return Response(
                    {'detail': f'Client with id={client_id} not found.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Derive org from site → client → user
        if site:
            org = site.org
            if client is None:
                client = site.client
        elif client:
            org = client.org
        else:
            org = _resolve_org(request.user)
            if org is None:
                return Response(
                    {'detail': 'Your account has no organization set. '
                               'Provide client or site, or set an org on your account.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Scope check: non-superusers must be in the same org
        if not request.user.is_superuser:
            user_org = _resolve_org(request.user)
            if user_org is None or user_org.pk != org.pk:
                raise PermissionDenied('You do not have access to this organization.')

        today = timezone.now().date()
        errors = []
        warnings = []
        template_info = None
        selected_rule_level = None
        steps_info = []
        resolved_template = None

        # Resolve template
        try:
            resolved_template = resolve_workflow_template(
                request_type, org, client=client, site=site,
            )
            template_info = {
                'id': resolved_template.pk,
                'name': resolved_template.name,
                'code': resolved_template.code,
            }
            selected_rule_level = _detect_mapping_level(
                org, request_type, client=client, site=site,
            )
        except WorkflowConfigurationError as exc:
            errors.append(str(exc))

        # Resolve step assignments
        if resolved_template:
            steps = list(resolved_template.steps.order_by('order'))
            if not steps:
                warnings.append(
                    f'Template "{resolved_template.code}" has no steps configured.'
                )
            for step in steps:
                step_info = {
                    'order': step.order,
                    'code': step.code,
                    'name': step.name,
                    'department': None,
                    'responsible_person': None,
                    'assignment_level': None,
                    'assignment_ok': False,
                }
                try:
                    config = resolve_step_assignment(
                        trigger_type=request_type,
                        org=org,
                        step_code=step.code,
                        client=client,
                        site=site,
                        on_date=today,
                    )
                    step_info['assignment_ok'] = True
                    step_info['responsible_person'] = (
                        config.named_user.username if config.named_user else None
                    )
                    step_info['department'] = (
                        config.department.name if config.department else None
                    )
                    if config.site_id:
                        step_info['assignment_level'] = 'site'
                    elif config.client_id:
                        step_info['assignment_level'] = 'client'
                    else:
                        step_info['assignment_level'] = 'company'
                except WorkflowConfigurationError as exc:
                    errors.append(str(exc))
                steps_info.append(step_info)

        return Response({
            'ok': len(errors) == 0,
            'request_type': request_type,
            'selected_flow': template_info,
            'selected_rule_level': selected_rule_level,
            'steps': steps_info,
            'errors': errors,
            'warnings': warnings,
        })


def _detect_mapping_level(org, trigger_type, client=None, site=None):
    """Determine which mapping level resolved the template."""
    if site is not None and WorkflowTemplateMapping.objects.filter(
        org=org, trigger_type=trigger_type, site=site, is_active=True,
    ).exists():
        return 'site'
    if client is not None and WorkflowTemplateMapping.objects.filter(
        org=org, trigger_type=trigger_type, client=client, site__isnull=True, is_active=True,
    ).exists():
        return 'client'
    return 'company'
