"""
apps/workflow/views.py

MRF + Client Onboarding workflow API views.
"""

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from apps.access.permissions import HasCapability, HasAnyCapability

from .exceptions import WorkflowConfigurationError
from .serializers import (
    WorkflowInstanceSerializer,
    WorkflowStepInstanceSerializer,
    ActOnStepSerializer,
    ReassignStepSerializer,
)


# ─── Scope-aware helpers ──────────────────────────────────────────────────────

def _mrf_qs_for_user(user):
    """Return MRF queryset scoped to what this user can access."""
    from apps.mrf.models import ManpowerRequest
    from apps.access.querysets import filter_mrfs_for_user
    return filter_mrfs_for_user(ManpowerRequest.objects.all(), user)


def _get_mrf_or_404(user, mrf_id):
    """Fetch a single MRF the user has scope access to, or 404."""
    return get_object_or_404(_mrf_qs_for_user(user), pk=mrf_id)


def _onboarding_qs_for_user(user):
    """Return ClientOnboardingRequest queryset scoped to what this user can access."""
    from apps.onboarding.models import ClientOnboardingRequest
    from apps.access.querysets import filter_onboarding_requests_for_user
    return filter_onboarding_requests_for_user(ClientOnboardingRequest.objects.all(), user)


def _get_onboarding_or_404(user, onboarding_id):
    """Fetch a single onboarding request the user has scope access to, or 404."""
    return get_object_or_404(_onboarding_qs_for_user(user), pk=onboarding_id)


def _workflow_instance_qs_for_user(user):
    """
    Return WorkflowInstance queryset filtered by scope.
    Covers both MRF-linked and onboarding-linked instances.
    Superusers see all.
    """
    from .models import WorkflowInstance
    from apps.access.querysets import filter_mrfs_for_user, filter_onboarding_requests_for_user
    from apps.mrf.models import ManpowerRequest
    from apps.onboarding.models import ClientOnboardingRequest

    qs = WorkflowInstance.objects.select_related(
        'org', 'mrf', 'client_onboarding_request', 'template', 'initiated_by',
    ).prefetch_related(
        'steps__step_template',
        'steps__assigned_user',
        'steps__assigned_department',
        'steps__acted_by',
        'audit_trail__actor',
        'audit_trail__reassign_from',
        'audit_trail__reassign_to',
    )

    if user.is_superuser:
        return qs

    accessible_mrf_ids = (
        filter_mrfs_for_user(ManpowerRequest.objects.only('id'), user)
        .values_list('id', flat=True)
    )
    accessible_onboarding_ids = (
        filter_onboarding_requests_for_user(ClientOnboardingRequest.objects.only('id'), user)
        .values_list('id', flat=True)
    )
    return qs.filter(
        Q(mrf_id__in=accessible_mrf_ids) |
        Q(client_onboarding_request_id__in=accessible_onboarding_ids)
    )


# ─── MRF Workflow Views ───────────────────────────────────────────────────────

class StartMRFWorkflowView(APIView):
    """POST /api/workflow/mrf/{mrf_id}/start/"""
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'workflow.start_workflow'

    def post(self, request, mrf_id):
        from .services import start_mrf_workflow

        mrf = _get_mrf_or_404(request.user, mrf_id)

        try:
            instance = start_mrf_workflow(mrf, actor=request.user)
        except WorkflowConfigurationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = WorkflowInstanceSerializer(instance, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MRFWorkflowConfigCheckView(APIView):
    """
    GET /api/workflow/mrf/{mrf_id}/config-check/

    Dry-run config check without creating a workflow.
    Returns 200 always — check `ok` field for pass/fail.
    """
    permission_classes = [IsAuthenticated, HasAnyCapability]
    required_capabilities = ['workflow.read', 'workflow.start_workflow']

    def get(self, request, mrf_id):
        from .models import WorkflowTemplateMapping
        from .services import resolve_workflow_template, resolve_step_assignment

        mrf = _get_mrf_or_404(request.user, mrf_id)

        site = mrf.site
        client = getattr(site, 'client', None)
        org = mrf.org
        today = timezone.now().date()

        errors = []
        warnings = []
        template_info = None
        mapping_level = None
        steps_info = []
        resolved_template = None

        try:
            resolved_template = resolve_workflow_template('mrf', org, client=client, site=site)
            template_info = {
                'id': resolved_template.pk,
                'name': resolved_template.name,
                'code': resolved_template.code,
            }
            if site is not None and WorkflowTemplateMapping.objects.filter(
                org=org, trigger_type='mrf', site=site, is_active=True,
            ).exists():
                mapping_level = 'site'
            elif client is not None and WorkflowTemplateMapping.objects.filter(
                org=org, trigger_type='mrf', client=client, site__isnull=True, is_active=True,
            ).exists():
                mapping_level = 'client'
            else:
                mapping_level = 'org'
        except WorkflowConfigurationError as exc:
            errors.append(str(exc))

        if resolved_template is not None:
            steps = list(resolved_template.steps.order_by('order'))
            if not steps:
                warnings.append(f'Template "{resolved_template.code}" has no steps configured.')
            for step in steps:
                step_info = {
                    'step_code': step.code,
                    'step_name': step.name,
                    'assignment_ok': False,
                    'assignment_level': None,
                    'department': None,
                    'assigned_user': None,
                }
                try:
                    config = resolve_step_assignment(
                        trigger_type='mrf',
                        org=org,
                        step_code=step.code,
                        client=client,
                        site=site,
                        on_date=today,
                    )
                    step_info['assignment_ok'] = True
                    step_info['assigned_user'] = (
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
                        step_info['assignment_level'] = 'org'
                except WorkflowConfigurationError as exc:
                    errors.append(str(exc))
                steps_info.append(step_info)

        return Response({
            'ok': len(errors) == 0,
            'mrf': mrf.pk,
            'template': template_info,
            'mapping_level': mapping_level,
            'steps': steps_info,
            'errors': errors,
            'warnings': warnings,
        })


# ─── Client Onboarding Workflow Views ─────────────────────────────────────────

class StartClientOnboardingWorkflowView(APIView):
    """POST /api/workflow/client-onboarding/{id}/start/"""
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'workflow.start_workflow'

    def post(self, request, onboarding_id):
        from .services import start_client_onboarding_workflow

        onboarding_request = _get_onboarding_or_404(request.user, onboarding_id)

        try:
            instance = start_client_onboarding_workflow(
                onboarding_request, actor=request.user,
            )
        except WorkflowConfigurationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = WorkflowInstanceSerializer(instance, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ClientOnboardingWorkflowConfigCheckView(APIView):
    """
    GET /api/workflow/client-onboarding/{id}/config-check/

    Dry-run config check for the client_onboarding workflow.
    Returns 200 always — check `ok` field for pass/fail.
    """
    permission_classes = [IsAuthenticated, HasAnyCapability]
    required_capabilities = ['workflow.read', 'workflow.start_workflow']

    def get(self, request, onboarding_id):
        from .models import WorkflowTemplateMapping
        from .services import resolve_workflow_template, resolve_step_assignment

        onboarding_request = _get_onboarding_or_404(request.user, onboarding_id)

        org = onboarding_request.org
        client = onboarding_request.client
        today = timezone.now().date()

        errors = []
        warnings = []
        template_info = None
        mapping_level = None
        steps_info = []
        resolved_template = None

        try:
            resolved_template = resolve_workflow_template('client_onboarding', org, client=client)
            template_info = {
                'id': resolved_template.pk,
                'name': resolved_template.name,
                'code': resolved_template.code,
            }
            if WorkflowTemplateMapping.objects.filter(
                org=org, trigger_type='client_onboarding', client=client,
                site__isnull=True, is_active=True,
            ).exists():
                mapping_level = 'client'
            else:
                mapping_level = 'org'
        except WorkflowConfigurationError as exc:
            errors.append(str(exc))

        if resolved_template is not None:
            steps = list(resolved_template.steps.order_by('order'))
            if not steps:
                warnings.append(f'Template "{resolved_template.code}" has no steps configured.')
            for step in steps:
                step_info = {
                    'step_code': step.code,
                    'step_name': step.name,
                    'assignment_ok': False,
                    'assignment_level': None,
                    'department': None,
                    'assigned_user': None,
                }
                try:
                    config = resolve_step_assignment(
                        trigger_type='client_onboarding',
                        org=org,
                        step_code=step.code,
                        client=client,
                        on_date=today,
                    )
                    step_info['assignment_ok'] = True
                    step_info['assigned_user'] = (
                        config.named_user.username if config.named_user else None
                    )
                    step_info['department'] = (
                        config.department.name if config.department else None
                    )
                    if config.client_id:
                        step_info['assignment_level'] = 'client'
                    else:
                        step_info['assignment_level'] = 'org'
                except WorkflowConfigurationError as exc:
                    errors.append(str(exc))
                steps_info.append(step_info)

        return Response({
            'ok': len(errors) == 0,
            'client_onboarding_request': onboarding_request.pk,
            'template': template_info,
            'mapping_level': mapping_level,
            'steps': steps_info,
            'errors': errors,
            'warnings': warnings,
        })


# ─── Shared step action views ─────────────────────────────────────────────────

class WorkflowInstanceDetailView(APIView):
    """GET /api/workflow/instances/{id}/"""
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'workflow.read'

    def get(self, request, instance_id):
        qs = _workflow_instance_qs_for_user(request.user)
        instance = get_object_or_404(qs, pk=instance_id)
        serializer = WorkflowInstanceSerializer(instance, context={'request': request})
        return Response(serializer.data)


class ActOnStepView(APIView):
    """
    POST /api/workflow/instances/{instance_id}/steps/{step_id}/act/
    Actor must be the assigned user OR a superuser.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, instance_id, step_id):
        from .models import WorkflowStepInstance
        from .services import act_on_step

        instance = get_object_or_404(
            _workflow_instance_qs_for_user(request.user), pk=instance_id,
        )

        step_instance = get_object_or_404(
            WorkflowStepInstance, pk=step_id, workflow=instance,
        )

        if not request.user.is_superuser and step_instance.assigned_user_id != request.user.pk:
            return Response(
                {'detail': 'You are not assigned to this step.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ActOnStepSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            act_on_step(
                step_instance,
                actor=request.user,
                action=serializer.validated_data['action'],
                comment=serializer.validated_data.get('comment', ''),
            )
        except WorkflowConfigurationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        step_instance.refresh_from_db()
        return Response(WorkflowStepInstanceSerializer(step_instance, context={'request': request}).data)


class ReassignStepView(APIView):
    """
    POST /api/workflow/instances/{instance_id}/steps/{step_id}/reassign/
    Requires workflow.reassign capability OR superuser.
    """
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'workflow.reassign'

    def post(self, request, instance_id, step_id):
        from .models import WorkflowStepInstance
        from .services import reassign_step
        from apps.accounts.models import User

        instance = get_object_or_404(
            _workflow_instance_qs_for_user(request.user), pk=instance_id,
        )

        step_instance = get_object_or_404(
            WorkflowStepInstance, pk=step_id, workflow=instance,
        )

        serializer = ReassignStepSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_user_id = serializer.validated_data['new_user']
        try:
            new_user = User.objects.get(pk=new_user_id, is_active=True)
        except User.DoesNotExist:
            return Response(
                {'detail': 'User not found or inactive.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            reassign_step(
                step_instance,
                actor=request.user,
                new_user=new_user,
                comment=serializer.validated_data.get('comment', ''),
            )
        except WorkflowConfigurationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        step_instance.refresh_from_db()
        return Response(WorkflowStepInstanceSerializer(step_instance, context={'request': request}).data)
