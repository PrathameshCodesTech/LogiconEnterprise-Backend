"""
apps/workflow/views.py

MRF + Mobilisation workflow API views.
"""

from django.db.models import Q, Prefetch
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from apps.access.permissions import HasCapability, HasAnyCapability

from .exceptions import OnboardingPreflightError, WorkflowConfigurationError
from .serializers import (
    WorkflowInstanceSerializer,
    WorkflowStepInstanceSerializer,
    ActOnStepSerializer,
    ReassignStepSerializer,
    WorkflowMyTaskSerializer,
    serialize_my_workflow_task_detail,
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
    """Return MobilisationSetupRequest queryset scoped to what this user can access."""
    from apps.mobilisation.models import MobilisationSetupRequest
    from apps.access.querysets import filter_mobilisation_requests_for_user
    return filter_mobilisation_requests_for_user(MobilisationSetupRequest.objects.all(), user)


def _get_onboarding_or_404(user, onboarding_id):
    """Fetch a single mobilisation request the user has scope access to, or 404."""
    return get_object_or_404(_onboarding_qs_for_user(user), pk=onboarding_id)


def _proposal_version_qs_for_user(user):
    """Return ProposalVersion queryset scoped to what this user can access."""
    from apps.sales.models import ProposalVersion
    from apps.sales.querysets import filter_proposal_versions_for_user
    return filter_proposal_versions_for_user(ProposalVersion.objects.all(), user)


def _get_proposal_version_or_404(user, proposal_version_id):
    """Fetch a single proposal version the user has scope access to, or 404."""
    return get_object_or_404(_proposal_version_qs_for_user(user), pk=proposal_version_id)


def _workflow_instance_qs_for_user(user):
    """
    Return WorkflowInstance queryset filtered by scope.
    Covers both MRF-linked and mobilisation-linked instances.
    Superusers see all.
    """
    from .models import WorkflowInstance
    from apps.access.querysets import filter_mrfs_for_user, filter_mobilisation_requests_for_user
    from apps.mrf.models import ManpowerRequest
    from apps.mobilisation.models import MobilisationSetupRequest

    qs = WorkflowInstance.objects.select_related(
        'org', 'mrf', 'client_onboarding_request', 'proposal_version', 'template', 'initiated_by',
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
        filter_mobilisation_requests_for_user(MobilisationSetupRequest.objects.only('id'), user)
        .values_list('id', flat=True)
    )
    from apps.sales.models import ProposalVersion
    from apps.sales.querysets import filter_proposal_versions_for_user
    accessible_proposal_ids = (
        filter_proposal_versions_for_user(ProposalVersion.objects.only('id'), user)
        .values_list('id', flat=True)
    )
    return qs.filter(
        Q(mrf_id__in=accessible_mrf_ids) |
        Q(client_onboarding_request_id__in=accessible_onboarding_ids) |
        Q(proposal_version_id__in=accessible_proposal_ids)
    )


def _my_tasks_steps_queryset(user, see_all_active_for_superuser):
    """
    Active steps on active workflows, limited to workflow instances the user can access.
    Non-superusers (and superusers by default): only steps assigned to this user.
    Superuser + see_all_active_for_superuser: all active steps (still scoped workflows for non-superuser path only).
    """
    from .models import WorkflowStepInstance

    qs = (
        WorkflowStepInstance.objects.filter(
            status='active',
            workflow__status='active',
        )
        .select_related(
            'workflow',
            'workflow__mrf',
            'workflow__mrf__site',
            'workflow__mrf__site__client',
            'workflow__mrf__requesting_department',
            'workflow__mrf__required_department',
            'workflow__client_onboarding_request',
            'workflow__client_onboarding_request__client',
            'workflow__proposal_version',
            'workflow__proposal_version__lead',
            'workflow__proposal_version__lead__sales_person',
            'assigned_user',
            'assigned_department',
        )
        .prefetch_related('workflow__mrf__line_items')
        .order_by('-activated_at', 'workflow_id', 'step_order')
    )
    if user.is_superuser and see_all_active_for_superuser:
        return qs
    qs = qs.filter(assigned_user=user)
    if user.is_superuser:
        return qs

    from apps.access.scope import get_accessible_scope_paths
    from apps.access.querysets import _scope_q

    paths = get_accessible_scope_paths(user)
    if not paths:
        return qs.none()

    mrf_site_q = _scope_q('workflow__mrf__site__scope_node__path', paths)
    mrf_client_q = _scope_q('workflow__mrf__site__client__scope_node__path', paths)
    onboarding_client_q = _scope_q(
        'workflow__client_onboarding_request__client__scope_node__path',
        paths,
    )
    org_id = getattr(user, 'org_id', None)
    onboarding_no_client_q = (
        Q(
            workflow__client_onboarding_request__client__isnull=True,
            workflow__client_onboarding_request__org_id=org_id,
        )
        if org_id else Q(pk__in=[])
    )
    sales_proposal_org_q = (
        Q(workflow__proposal_version__lead__org_id=org_id)
        if org_id else Q(pk__in=[])
    )
    return qs.filter(
        mrf_site_q | mrf_client_q | onboarding_client_q | onboarding_no_client_q | sales_proposal_org_q
    ).distinct()


def _my_task_detail_base_queryset(user):
    """
    Active steps on active workflows in scope; assignee must be user unless superuser.
    Used for GET /api/workflow/my-tasks/{step_id}/.
    """
    from .models import WorkflowStepInstance, WorkflowAction
    from apps.mrf.models import MRFLineItem
    from apps.mobilisation.models import (
        MobilisationProposedDepartment,
        MobilisationProposedUser,
    )
    from apps.sales.models import ProposalBudgetLine, ProposalBreakupLine

    scoped_active_wf = _workflow_instance_qs_for_user(user).filter(status='active')
    qs = WorkflowStepInstance.objects.filter(
        status='active',
        workflow__status='active',
        workflow_id__in=scoped_active_wf.values('id'),
    )
    if not user.is_superuser:
        qs = qs.filter(assigned_user=user)

    step_children = WorkflowStepInstance.objects.order_by('step_order').select_related(
        'assigned_user', 'assigned_department', 'acted_by',
    )
    audit_children = WorkflowAction.objects.order_by('created_at').select_related('actor')
    line_children = MRFLineItem.objects.order_by('pk').select_related(
        'job_role', 'wage_category', 'budget_plan', 'site_role_requirement',
    )
    proposed_depts_children = MobilisationProposedDepartment.objects.order_by('pk').select_related(
        'real_site',
    )
    proposed_user_children = MobilisationProposedUser.objects.order_by('pk').select_related(
        'access_role', 'real_site',
    )
    budget_line_children = ProposalBudgetLine.objects.order_by('sort_order', 'pk').select_related(
        'job_role', 'site',
    )
    breakup_line_children = ProposalBreakupLine.objects.order_by('sort_order', 'pk').select_related(
        'job_role', 'site',
    )

    return qs.select_related(
        'workflow',
        'workflow__template',
        'workflow__initiated_by',
        'workflow__mrf',
        'workflow__mrf__site',
        'workflow__mrf__site__client',
        'workflow__mrf__requesting_department',
        'workflow__mrf__required_department',
        'workflow__mrf__requested_by',
        'workflow__mrf__budget_plan',
        'workflow__client_onboarding_request',
        'workflow__client_onboarding_request__client',
        'workflow__client_onboarding_request__requested_by',
        'workflow__client_onboarding_request__budget_plan',
        'workflow__proposal_version',
        'workflow__proposal_version__lead',
        'workflow__proposal_version__lead__sales_person',
        'workflow__proposal_version__created_by',
        'assigned_user',
        'assigned_department',
    ).prefetch_related(
        Prefetch('workflow__steps', queryset=step_children),
        Prefetch('workflow__audit_trail', queryset=audit_children),
        Prefetch('workflow__mrf__line_items', queryset=line_children),
        Prefetch(
            'workflow__client_onboarding_request__proposed_departments',
            queryset=proposed_depts_children,
        ),
        Prefetch(
            'workflow__client_onboarding_request__proposed_users',
            queryset=proposed_user_children,
        ),
        Prefetch(
            'workflow__proposal_version__budget_lines',
            queryset=budget_line_children,
        ),
        Prefetch(
            'workflow__proposal_version__breakup_lines',
            queryset=breakup_line_children,
        ),
        Prefetch('workflow__proposal_version__client_responses'),
    )


# ─── MRF Workflow Views ───────────────────────────────────────────────────────

class AvailableRoutesView(APIView):
    """
    GET /api/workflow/routes/available/?trigger_type=mrf&client=<id>&site=<id>
    GET /api/workflow/routes/available/?trigger_type=client_onboarding&client=<id>

    Returns preview of available ApprovalRoutes for the given scope.
    """
    permission_classes = [IsAuthenticated, HasAnyCapability]
    required_capabilities = ['workflow.read', 'workflow.start_workflow']

    def get(self, request):
        from .models import ApprovalRoute
        from .services import get_available_approval_routes, build_approval_route_preview

        trigger_type = request.query_params.get('trigger_type', '')
        if trigger_type not in ('mrf', 'client_onboarding', 'sales_proposal'):
            return Response(
                {
                    'detail': (
                        'trigger_type must be "mrf", "client_onboarding" (mobilisation), '
                        'or "sales_proposal".'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not hasattr(request.user, 'org') or request.user.org is None:
            return Response({'count': 0, 'results': []})

        org = request.user.org
        client = None
        site = None

        client_id = request.query_params.get('client')
        site_id = request.query_params.get('site')

        if client_id:
            from apps.sites.models import Client as ClientModel
            try:
                client = ClientModel.objects.get(pk=client_id, org=org)
            except ClientModel.DoesNotExist:
                return Response({'detail': 'Client not found.'}, status=status.HTTP_404_NOT_FOUND)

        if site_id:
            from apps.sites.models import SiteProfile as SiteModel
            try:
                site = SiteModel.objects.get(pk=site_id, org=org)
            except SiteModel.DoesNotExist:
                return Response({'detail': 'Site not found.'}, status=status.HTTP_404_NOT_FOUND)

        routes = get_available_approval_routes(trigger_type, org, client=client, site=site)
        previews = [build_approval_route_preview(r) for r in routes]

        return Response({'count': len(previews), 'results': previews})


class StartMRFWorkflowView(APIView):
    """POST /api/workflow/mrf/{mrf_id}/start/"""
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'workflow.start_workflow'

    def post(self, request, mrf_id):
        from .services import start_mrf_workflow
        from .models import ApprovalRoute
        from apps.mrf.services import check_mrf_readiness

        mrf = _get_mrf_or_404(request.user, mrf_id)

        readiness = check_mrf_readiness(mrf)
        if not readiness['ok']:
            return Response(
                {'detail': 'MRF is not ready for approval.', 'errors': readiness['errors']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        approval_route = None
        route_id = request.data.get('approval_route')
        if route_id is not None:
            try:
                approval_route = ApprovalRoute.objects.get(pk=route_id)
            except ApprovalRoute.DoesNotExist:
                return Response(
                    {'detail': 'Approval route not found.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            instance = start_mrf_workflow(mrf, actor=request.user, approval_route=approval_route)
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


# ─── Mobilisation workflow views (trigger_type client_onboarding) ─────────────

class StartClientOnboardingWorkflowView(APIView):
    """POST /api/workflow/client-onboarding/{id}/start/ — mobilisation setup workflow."""
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'workflow.start_workflow'

    def post(self, request, onboarding_id):
        from .services import start_client_onboarding_workflow
        from .models import ApprovalRoute
        from apps.mobilisation.services import check_mobilisation_readiness

        onboarding_request = _get_onboarding_or_404(request.user, onboarding_id)

        ok, errors, _ = check_mobilisation_readiness(onboarding_request)
        if not ok:
            return Response(
                {'detail': 'Onboarding setup is incomplete.', 'errors': errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        approval_route = None
        route_id = request.data.get('approval_route')
        if route_id is not None:
            try:
                approval_route = ApprovalRoute.objects.get(pk=route_id)
            except ApprovalRoute.DoesNotExist:
                return Response(
                    {'detail': 'Approval route not found.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            instance = start_client_onboarding_workflow(
                onboarding_request, actor=request.user, approval_route=approval_route,
            )
        except WorkflowConfigurationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = WorkflowInstanceSerializer(instance, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ClientOnboardingWorkflowConfigCheckView(APIView):
    """
    GET /api/workflow/client-onboarding/{id}/config-check/

    Dry-run config check for the mobilisation workflow (trigger_type client_onboarding).
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
            'mobilisation_request': onboarding_request.pk,
            'template': template_info,
            'mapping_level': mapping_level,
            'steps': steps_info,
            'errors': errors,
            'warnings': warnings,
        })


# ─── Sales proposal workflow views (trigger_type sales_proposal) ──────────────

class StartSalesProposalWorkflowView(APIView):
    """POST /api/workflow/sales-proposals/{proposal_version_id}/start/"""
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'workflow.start_workflow'

    def post(self, request, proposal_version_id):
        from .services import start_sales_proposal_workflow
        from .models import ApprovalRoute

        proposal_version = _get_proposal_version_or_404(request.user, proposal_version_id)

        approval_route = None
        route_id = request.data.get('approval_route')
        if route_id is not None:
            try:
                approval_route = ApprovalRoute.objects.get(pk=route_id)
            except ApprovalRoute.DoesNotExist:
                return Response(
                    {'detail': 'Approval route not found.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            instance = start_sales_proposal_workflow(
                proposal_version, actor=request.user, approval_route=approval_route,
            )
        except WorkflowConfigurationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = WorkflowInstanceSerializer(instance, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ─── Shared step action views ─────────────────────────────────────────────────

class MyWorkflowTasksView(APIView):
    """
    GET /api/workflow/my-tasks/

    Read-only inbox: active steps assigned to the user on active workflows.
    Authorization is queryset-based (see _my_tasks_steps_queryset).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        see_all = (
            request.user.is_superuser
            and str(request.query_params.get('all', '')).lower() == 'true'
        )
        qs = _my_tasks_steps_queryset(request.user, see_all_active_for_superuser=see_all)
        ser = WorkflowMyTaskSerializer(qs, many=True)
        data = ser.data
        return Response({'count': len(data), 'results': data})


class MyWorkflowTaskDetailView(APIView):
    """
    GET /api/workflow/my-tasks/{step_id}/

    Drawer payload for an active assigned task (or any active step for superuser).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, step_id):
        step = get_object_or_404(_my_task_detail_base_queryset(request.user), pk=step_id)
        return Response(serialize_my_workflow_task_detail(step, request))


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
        except OnboardingPreflightError as exc:
            return Response(
                {'detail': str(exc), 'errors': exc.preflight_errors},
                status=status.HTTP_400_BAD_REQUEST,
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
