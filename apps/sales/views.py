"""
apps/sales/views.py

ViewSets for the sales pipeline app.

Capability map:
  SalesLead              sales_lead.read / .create / .update / .delete
  SalesLeadSite          sales_lead.read / .update
  SiteSurvey             sales_survey.read / .update / .assign
  SalesRoleRequirement   sales_survey.read / .update
  ProposalVersion        sales_proposal.read / .create / .update
  ProposalBudgetLine     sales_proposal.read / .update
  ProposalBreakupLine    sales_proposal.read / .update
  ClientProposalResponse sales_proposal.read / .update
"""

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.access.permissions import HasCapability
from apps.access.viewsets import ScopedModelViewSet
from apps.accounts.serializers import UserListSerializer

from .models import (
    SalesLead, SalesLeadSite, SiteSurvey, SalesRoleRequirement,
    ProposalVersion, ProposalBudgetLine, ProposalBreakupLine, ClientProposalResponse,
    SalesLeadActivity, SalesDocument,
    SiteSurveyScopeAnswer, SiteSurveyShiftDeployment, SiteSurveyLocationLine,
    SiteSurveyEquipmentLine, SiteSurveyIssueLine, SurveyRoleMapping,
    ProposalComponentRule,
)
from .querysets import (
    filter_leads_for_user,
    filter_lead_sites_for_user,
    filter_site_surveys_for_user,
    filter_sales_role_requirements_for_user,
    filter_proposal_versions_for_user,
    filter_proposal_budget_lines_for_user,
    filter_proposal_breakup_lines_for_user,
    filter_client_responses_for_user,
    filter_activities_for_user,
    filter_documents_for_user,
    filter_survey_children_for_user,
    filter_survey_role_mappings_for_user,
    filter_proposal_component_rules_for_user,
)
from .serializers import (
    SalesLeadSerializer, SalesLeadDetailSerializer, SalesLeadWriteSerializer,
    SalesLeadSiteSerializer, SalesLeadSiteWriteSerializer,
    SiteSurveySerializer, SiteSurveyWriteSerializer,
    SalesRoleRequirementSerializer, SalesRoleRequirementWriteSerializer,
    ProposalVersionSerializer, ProposalVersionDetailSerializer, ProposalVersionWriteSerializer,
    ProposalBudgetLineSerializer, ProposalBudgetLineWriteSerializer,
    ProposalBreakupLineSerializer, ProposalBreakupLineWriteSerializer,
    ClientProposalResponseSerializer, ClientProposalResponseWriteSerializer,
    SalesLeadActivitySerializer,
    SalesDocumentSerializer, SalesDocumentWriteSerializer,
    SiteSurveyScopeAnswerSerializer, SiteSurveyShiftDeploymentSerializer,
    SiteSurveyLocationLineSerializer, SiteSurveyEquipmentLineSerializer,
    SiteSurveyIssueLineSerializer, SiteSurveyStructuredSerializer,
    SurveyRoleMappingSerializer, ProposalComponentRuleSerializer,
)


# ─── SalesLeadViewSet ─────────────────────────────────────────────────────────

class SalesLeadViewSet(ScopedModelViewSet):
    queryset = SalesLead.objects.select_related(
        'org', 'sales_person', 'created_by', 'final_approved_proposal',
    ).order_by('-created_at')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_leads_for_user

    filterset_fields = ['org', 'current_stage', 'current_status', 'sales_person', 'operations_owner', 'priority']
    search_fields = ['client_name', 'client_contact_person', 'client_email']

    action_required_capabilities = {
        'list':           'sales_lead.read',
        'retrieve':       'sales_lead.read',
        'create':         'sales_lead.create',
        'update':         'sales_lead.update',
        'partial_update': 'sales_lead.update',
        'destroy':        'sales_lead.delete',
        'submit_to_operations': 'sales_lead.update',
        'eligible_operations_owners': 'sales_lead.update',
        'generate_proposal':    'sales_proposal.create',
        'mark_survey_started':  'sales_survey.update',
        'mark_survey_completed': 'sales_survey.update',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return SalesLeadWriteSerializer
        if self.action == 'retrieve':
            return SalesLeadDetailSerializer
        return SalesLeadSerializer

    def perform_create(self, serializer):
        user = self.request.user
        org = getattr(user, 'org', None)
        serializer.save(org=org, created_by=user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        out = SalesLeadSerializer(serializer.instance, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        out = SalesLeadSerializer(serializer.instance, context={'request': request})
        return Response(out.data)

    @action(detail=True, methods=['post'], url_path='submit-to-operations')
    def submit_to_operations(self, request, pk=None):
        from apps.sales.services import submit_to_operations, validate_operations_survey_owner
        lead = self.get_object()
        operations_owner_id = request.data.get('operations_owner')
        operations_owner = None
        if operations_owner_id:
            from apps.accounts.models import User as UserModel
            try:
                operations_owner = UserModel.objects.get(pk=operations_owner_id, org=lead.org)
            except UserModel.DoesNotExist:
                return Response(
                    {'detail': 'operations_owner not found in this org.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                validate_operations_survey_owner(lead.org, operations_owner)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            submit_to_operations(lead, request.user, operations_owner=operations_owner)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        out = SalesLeadSerializer(lead, context={'request': request})
        return Response(out.data)

    @action(detail=True, methods=['get'], url_path='eligible-operations-owners')
    def eligible_operations_owners(self, request, pk=None):
        lead = self.get_object()
        from apps.accounts.models import User as UserModel
        from apps.sales.services import get_operations_department

        operations_department = get_operations_department(lead.org)
        if operations_department is None:
            return Response(
                {
                    'detail': (
                        'Operations department is not configured for this organization. '
                        'Create an active org-level department with code "operations".'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        users = list(
            UserModel.objects
            .select_related('org', 'department')
            .filter(
                org=lead.org,
                user_type='internal',
                is_active=True,
                department=operations_department,
            )
            .order_by('last_name', 'first_name', 'username', 'id')
        )
        if not users:
            return Response(
                {
                    'detail': (
                        'No active internal users are assigned to the Operations department. '
                        'Assign operations users to department code "operations" before submitting to operations.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = UserListSerializer(users, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='generate-proposal')
    def generate_proposal(self, request, pk=None):
        from apps.sales.services import generate_proposal_version
        lead = self.get_object()
        try:
            proposal = generate_proposal_version(lead, request.user)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        out = ProposalVersionDetailSerializer(proposal, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)


# ─── SalesLeadSiteViewSet ─────────────────────────────────────────────────────

class SalesLeadSiteViewSet(ScopedModelViewSet):
    queryset = SalesLeadSite.objects.select_related('lead', 'location_area').order_by('lead', 'site_name')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_lead_sites_for_user

    filterset_fields = ['lead', 'is_active']

    action_required_capabilities = {
        'list':           'sales_lead.read',
        'retrieve':       'sales_lead.read',
        'create':         'sales_lead.update',
        'update':         'sales_lead.update',
        'partial_update': 'sales_lead.update',
        'destroy':        'sales_lead.update',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return SalesLeadSiteWriteSerializer
        return SalesLeadSiteSerializer


# ─── SiteSurveyViewSet ────────────────────────────────────────────────────────

class SiteSurveyViewSet(ScopedModelViewSet):
    queryset = SiteSurvey.objects.select_related(
        'lead', 'site', 'survey_done_by', 'assigned_to',
    ).order_by('-created_at')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_site_surveys_for_user

    filterset_fields = ['lead', 'site', 'status', 'feasibility_status', 'assigned_to']

    action_required_capabilities = {
        'list':                 'sales_survey.read',
        'retrieve':             'sales_survey.read',
        'create':               'sales_survey.update',
        'update':               'sales_survey.update',
        'partial_update':       'sales_survey.update',
        'destroy':              'sales_survey.update',
        'assign_owner':         'sales_survey.assign',
        'mark_started':         'sales_survey.update',
        'mark_completed':       'sales_survey.update',
        'structured':           'sales_survey.read',
        'seed_default_lines':   'sales_survey.update',
        'generate_role_requirements': 'sales_survey.update',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return SiteSurveyWriteSerializer
        return SiteSurveySerializer

    @action(detail=True, methods=['post'], url_path='assign-owner')
    def assign_owner(self, request, pk=None):
        from apps.sales.services import assign_survey_owner
        from apps.accounts.models import User as UserModel
        survey = self.get_object()
        assigned_to_id = request.data.get('assigned_to')
        if not assigned_to_id:
            return Response({'detail': 'assigned_to is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            assigned_to = UserModel.objects.get(pk=assigned_to_id, org=survey.lead.org)
        except UserModel.DoesNotExist:
            return Response({'detail': 'User not found in this org.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            assign_survey_owner(survey, assigned_to, request.user)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        out = SiteSurveySerializer(survey, context={'request': request})
        return Response(out.data)

    @action(detail=True, methods=['post'], url_path='mark-started')
    def mark_started(self, request, pk=None):
        from apps.sales.services import mark_survey_started
        survey = self.get_object()
        mark_survey_started(survey, request.user)
        out = SiteSurveySerializer(survey, context={'request': request})
        return Response(out.data)

    @action(detail=True, methods=['post'], url_path='mark-completed')
    def mark_completed(self, request, pk=None):
        from apps.sales.services import mark_survey_completed
        survey = self.get_object()
        try:
            mark_survey_completed(survey, request.user)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        out = SiteSurveySerializer(survey, context={'request': request})
        return Response(out.data)

    @action(detail=True, methods=['get'], url_path='structured')
    def structured(self, request, pk=None):
        """Return survey header + all 5 child tables grouped."""
        survey = self.get_object()
        data = SiteSurveyStructuredSerializer(
            {
                'survey': survey,
                'scope_answers': survey.scope_answers.all(),
                'shift_deployments': survey.shift_deployments.all(),
                'location_lines': survey.location_lines.all(),
                'equipment_lines': survey.equipment_lines.all(),
                'issue_lines': survey.issue_lines.all(),
            },
            context={'request': request},
        ).data
        return Response(data)

    @action(detail=True, methods=['post'], url_path='seed-default-lines')
    def seed_default_lines(self, request, pk=None):
        from apps.sales.services import seed_default_survey_lines
        survey = self.get_object()
        overwrite = bool(request.data.get('overwrite', False))
        counts = seed_default_survey_lines(survey, overwrite=overwrite)
        return Response({
            'detail': 'seeded',
            'overwrite': overwrite,
            'counts': counts,
        })

    @action(detail=True, methods=['post'], url_path='generate-role-requirements')
    def generate_role_requirements(self, request, pk=None):
        """
        Convert the survey's SiteSurveyShiftDeployment item rows into
        SalesRoleRequirement rows for the lead/site.

        Returns {'created': [...], 'skipped': [...], 'errors': [...]}.
        """
        from apps.sales.services import generate_role_requirements_from_survey
        survey = self.get_object()
        result = generate_role_requirements_from_survey(survey, request.user)
        return Response(result)


# ─── SalesRoleRequirementViewSet ──────────────────────────────────────────────

class SalesRoleRequirementViewSet(ScopedModelViewSet):
    queryset = SalesRoleRequirement.objects.select_related(
        'lead', 'site', 'survey', 'job_role', 'wage_category',
    ).order_by('lead', 'site', 'job_role__name')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_sales_role_requirements_for_user

    filterset_fields = ['lead', 'site', 'survey', 'job_role', 'is_active']

    action_required_capabilities = {
        'list':           'sales_survey.read',
        'retrieve':       'sales_survey.read',
        'create':         'sales_survey.update',
        'update':         'sales_survey.update',
        'partial_update': 'sales_survey.update',
        'destroy':        'sales_survey.update',
        'approve':        'sales_survey.update',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return SalesRoleRequirementWriteSerializer
        return SalesRoleRequirementSerializer

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        from apps.sales.services import approve_sales_role_requirement
        rr = self.get_object()
        if rr.approved_by_operations:
            return Response({'detail': 'Already approved.'}, status=status.HTTP_400_BAD_REQUEST)
        approve_sales_role_requirement(rr, request.user)
        out = SalesRoleRequirementSerializer(rr, context={'request': request})
        return Response(out.data)


# ─── ProposalVersionViewSet ───────────────────────────────────────────────────

class ProposalVersionViewSet(ScopedModelViewSet):
    queryset = ProposalVersion.objects.select_related(
        'lead', 'created_by', 'source_version',
    ).prefetch_related(
        'budget_lines__job_role', 'budget_lines__site',
        'breakup_lines__job_role', 'breakup_lines__site',
        'client_responses__sent_to_client_by',
    ).order_by('lead', '-version_number')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_proposal_versions_for_user

    filterset_fields = ['lead', 'status', 'internal_approval_status', 'client_approval_status']

    action_required_capabilities = {
        'list':           'sales_proposal.read',
        'retrieve':       'sales_proposal.read',
        'create':         'sales_proposal.create',
        'update':         'sales_proposal.update',
        'partial_update': 'sales_proposal.update',
        'destroy':        'sales_proposal.update',
        'submit_internal_approval': 'sales_proposal.update',
        'mark_internally_approved': 'sales_proposal.approve',
        'send_to_client':           'sales_proposal.send_to_client',
        'client_response':          'sales_proposal.update',
        'clone_revision':           'sales_proposal.create',
        'convert_to_onboarding':    'sales_proposal.update',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ProposalVersionWriteSerializer
        if self.action == 'retrieve':
            return ProposalVersionDetailSerializer
        return ProposalVersionSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by=request.user)
        out = ProposalVersionSerializer(serializer.instance, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        from apps.sales.proposal_calculation import assert_proposal_editable
        assert_proposal_editable(serializer.instance)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        from apps.sales.proposal_calculation import assert_proposal_editable
        assert_proposal_editable(instance)
        super().perform_destroy(instance)

    @action(detail=True, methods=['post'], url_path='submit-internal-approval')
    def submit_internal_approval(self, request, pk=None):
        from apps.sales.services import submit_proposal_for_internal_approval
        from apps.workflow.models import ApprovalRoute

        proposal = self.get_object()
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
            submit_proposal_for_internal_approval(
                proposal, request.user, approval_route=approval_route,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        out = ProposalVersionSerializer(proposal, context={'request': request})
        return Response(out.data)

    @action(detail=True, methods=['post'], url_path='mark-internally-approved')
    def mark_internally_approved(self, request, pk=None):
        from apps.sales.services import mark_proposal_internally_approved
        proposal = self.get_object()
        try:
            mark_proposal_internally_approved(proposal, request.user)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        out = ProposalVersionSerializer(proposal, context={'request': request})
        return Response(out.data)

    @action(detail=True, methods=['post'], url_path='send-to-client')
    def send_to_client(self, request, pk=None):
        from apps.sales.services import send_proposal_to_client
        proposal = self.get_object()
        expires_days = request.data.get('expires_days', 7)
        try:
            expires_days = int(expires_days)
        except (TypeError, ValueError):
            return Response(
                {'detail': 'expires_days must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if expires_days < 1 or expires_days > 90:
            return Response(
                {'detail': 'expires_days must be between 1 and 90.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        email_subject = str(request.data.get('email_subject') or '').strip()
        if '\n' in email_subject or '\r' in email_subject:
            return Response(
                {'detail': 'email_subject must be a single line.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(email_subject) > 255:
            return Response(
                {'detail': 'email_subject must be 255 characters or fewer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        email_body = request.data.get('email_body')
        if email_body is None:
            email_body = request.data.get('note', '')
        email_body = str(email_body or '').strip()
        if len(email_body) > 5000:
            return Response(
                {'detail': 'email_body must be 5000 characters or fewer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = send_proposal_to_client(
                proposal,
                request.user,
                recipient_email=request.data.get('recipient_email'),
                recipient_name=request.data.get('recipient_name'),
                expires_days=expires_days,
                email_subject=email_subject,
                email_body=email_body,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        out = ProposalVersionSerializer(result['proposal'], context={'request': request})
        return Response({
            'proposal': out.data,
            'token_expires_at': result['token_expires_at'],
            'email_sent': result['email_sent'],
            'recipient_email': result['recipient_email'],
            'email_subject': result.get('email_subject', ''),
            'email_body': result.get('email_body', ''),
        })

    @action(detail=True, methods=['post'], url_path='client-response')
    def client_response(self, request, pk=None):
        from apps.sales.services import record_client_response
        proposal = self.get_object()
        response_val = request.data.get('client_response')
        remarks = request.data.get('remarks', '')
        if not response_val:
            return Response(
                {'detail': 'client_response is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            record_client_response(
                proposal, response_val, remarks, request.user,
                responded_by_name=request.data.get('responded_by_name', ''),
                responded_by_email=request.data.get('responded_by_email', ''),
                next_action_due_date=request.data.get('next_action_due_date') or None,
                meeting_notes=request.data.get('meeting_notes', ''),
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        out = ProposalVersionSerializer(proposal, context={'request': request})
        return Response(out.data)

    @action(detail=True, methods=['post'], url_path='clone-revision')
    def clone_revision(self, request, pk=None):
        from apps.sales.services import clone_proposal_for_revision
        proposal = self.get_object()
        new_proposal = clone_proposal_for_revision(proposal, request.user)
        out = ProposalVersionDetailSerializer(new_proposal, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='convert-to-onboarding')
    def convert_to_onboarding(self, request, pk=None):
        from apps.sales.services import (
            convert_won_sales_lead_to_onboarding_setup,
            mark_lead_won_from_client_approval,
        )
        from apps.access.scope import user_has_capability
        from apps.mobilisation.serializers import MobilisationSetupRequestSerializer

        from apps.sales.services import validate_proposal_ready_for_mobilisation_conversion

        if not user_has_capability(request.user, 'mobilisation.create'):
            return Response(
                {'detail': 'You do not have permission to create mobilisation requests.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        proposal = self.get_object()
        lead = proposal.lead

        try:
            validate_proposal_ready_for_mobilisation_conversion(lead, proposal)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if lead.current_stage != 'won' and proposal.client_approval_status == 'approved':
            try:
                mark_lead_won_from_client_approval(lead, proposal, request.user)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        from apps.mobilisation.models import MobilisationSetupRequest
        already_existed = MobilisationSetupRequest.objects.filter(source_sales_lead=lead).exists()

        operations_owner = None
        operations_owner_id = (
            request.data.get('operations_owner')
            or request.data.get('assigned_operations_owner')
        )
        if operations_owner_id:
            from apps.accounts.models import User
            try:
                operations_owner = User.objects.get(pk=operations_owner_id)
            except (User.DoesNotExist, ValueError, TypeError):
                return Response(
                    {'detail': 'Operations owner not found.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            onboarding_request = convert_won_sales_lead_to_onboarding_setup(
                lead, request.user, proposal=proposal, operations_owner=operations_owner,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        out = MobilisationSetupRequestSerializer(onboarding_request, context={'request': request})
        http_status = status.HTTP_200_OK if already_existed else status.HTTP_201_CREATED
        return Response(out.data, status=http_status)


# ─── ProposalBudgetLineViewSet ────────────────────────────────────────────────

class ProposalBudgetLineViewSet(ScopedModelViewSet):
    queryset = ProposalBudgetLine.objects.select_related(
        'proposal_version', 'site', 'job_role',
    ).order_by('proposal_version', 'sort_order', 'id')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_proposal_budget_lines_for_user

    filterset_fields = ['proposal_version', 'site', 'job_role']

    action_required_capabilities = {
        'list':           'sales_proposal.read',
        'retrieve':       'sales_proposal.read',
        'create':         'sales_proposal.update',
        'update':         'sales_proposal.update',
        'partial_update': 'sales_proposal.update',
        'destroy':        'sales_proposal.update',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ProposalBudgetLineWriteSerializer
        return ProposalBudgetLineSerializer

    def perform_create(self, serializer):
        from apps.sales.proposal_calculation import assert_proposal_editable
        proposal = serializer.validated_data.get('proposal_version')
        assert_proposal_editable(proposal)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        from apps.sales.proposal_calculation import assert_proposal_editable
        proposal = (
            serializer.validated_data.get('proposal_version')
            or serializer.instance.proposal_version
        )
        assert_proposal_editable(proposal)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        from apps.sales.proposal_calculation import assert_proposal_editable
        assert_proposal_editable(instance.proposal_version)
        super().perform_destroy(instance)


# ─── ProposalBreakupLineViewSet ───────────────────────────────────────────────

class ProposalBreakupLineViewSet(ScopedModelViewSet):
    queryset = ProposalBreakupLine.objects.select_related(
        'proposal_version', 'site', 'job_role',
    ).order_by('proposal_version', 'sort_order', 'id')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_proposal_breakup_lines_for_user

    filterset_fields = ['proposal_version', 'site', 'job_role', 'component_type']

    action_required_capabilities = {
        'list':           'sales_proposal.read',
        'retrieve':       'sales_proposal.read',
        'create':         'sales_proposal.update',
        'update':         'sales_proposal.update',
        'partial_update': 'sales_proposal.update',
        'destroy':        'sales_proposal.update',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ProposalBreakupLineWriteSerializer
        return ProposalBreakupLineSerializer

    def perform_create(self, serializer):
        from apps.sales.proposal_calculation import assert_proposal_editable
        proposal = serializer.validated_data.get('proposal_version')
        assert_proposal_editable(proposal)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        from apps.sales.proposal_calculation import assert_proposal_editable
        proposal = (
            serializer.validated_data.get('proposal_version')
            or serializer.instance.proposal_version
        )
        assert_proposal_editable(proposal)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        from apps.sales.proposal_calculation import assert_proposal_editable
        assert_proposal_editable(instance.proposal_version)
        super().perform_destroy(instance)


# ─── ClientProposalResponseViewSet ────────────────────────────────────────────

class ClientProposalResponseViewSet(ScopedModelViewSet):
    queryset = ClientProposalResponse.objects.select_related(
        'lead', 'proposal_version', 'sent_to_client_by',
    ).order_by('-created_at')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_client_responses_for_user

    filterset_fields = ['lead', 'proposal_version', 'client_response']

    action_required_capabilities = {
        'list':           'sales_proposal.read',
        'retrieve':       'sales_proposal.read',
        'create':         'sales_proposal.update',
        'update':         'sales_proposal.update',
        'partial_update': 'sales_proposal.update',
        'destroy':        'sales_proposal.update',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ClientProposalResponseWriteSerializer
        return ClientProposalResponseSerializer


# ─── SalesLeadActivityViewSet ─────────────────────────────────────────────────

class SalesLeadActivityViewSet(ScopedModelViewSet):
    """Read-only timeline of events for a sales lead."""
    queryset = SalesLeadActivity.objects.select_related(
        'lead', 'actor', 'proposal_version', 'site',
    ).order_by('-created_at')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_activities_for_user
    http_method_names = ['get', 'head', 'options']

    filterset_fields = ['lead', 'proposal_version', 'activity_type']

    action_required_capabilities = {
        'list':     'sales_lead.read',
        'retrieve': 'sales_lead.read',
    }

    def get_serializer_class(self):
        return SalesLeadActivitySerializer


# ─── SalesDocumentViewSet ─────────────────────────────────────────────────────

class SalesDocumentViewSet(ScopedModelViewSet):
    """Upload and manage sales document attachments."""
    queryset = SalesDocument.objects.select_related(
        'lead', 'uploaded_by', 'proposal_version', 'site',
    ).order_by('-created_at')

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_documents_for_user

    filterset_fields = ['lead', 'proposal_version', 'site', 'document_type', 'is_active']

    action_required_capabilities = {
        'list':           'sales_lead.read',
        'retrieve':       'sales_lead.read',
        'create':         'sales_lead.update',
        'update':         'sales_lead.update',
        'partial_update': 'sales_lead.update',
        'destroy':        'sales_lead.update',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return SalesDocumentWriteSerializer
        return SalesDocumentSerializer

    def create(self, request, *args, **kwargs):
        write_ser = self.get_serializer(data=request.data)
        write_ser.is_valid(raise_exception=True)
        write_ser.save()
        read_ser = SalesDocumentSerializer(write_ser.instance, context=self.get_serializer_context())
        return Response(read_ser.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Phase H: Survey child viewsets ───────────────────────────────────────────

class _SurveyChildViewSetBase(ScopedModelViewSet):
    """Shared config for the 5 SiteSurvey child viewsets."""

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_survey_children_for_user

    action_required_capabilities = {
        'list':           'sales_survey.read',
        'retrieve':       'sales_survey.read',
        'create':         'sales_survey.update',
        'update':         'sales_survey.update',
        'partial_update': 'sales_survey.update',
        'destroy':        'sales_survey.update',
    }


class SiteSurveyScopeAnswerViewSet(_SurveyChildViewSetBase):
    queryset = SiteSurveyScopeAnswer.objects.select_related(
        'survey', 'survey__lead', 'survey__site',
    ).order_by('category', 'sort_order', 'id')
    serializer_class = SiteSurveyScopeAnswerSerializer
    filterset_fields = ['survey', 'category']
    search_fields = ['field_key', 'field_label']


class SiteSurveyShiftDeploymentViewSet(_SurveyChildViewSetBase):
    queryset = SiteSurveyShiftDeployment.objects.select_related(
        'survey', 'survey__lead', 'job_role',
    ).order_by('sort_order', 'id')
    serializer_class = SiteSurveyShiftDeploymentSerializer
    filterset_fields = ['survey', 'line_type', 'job_role']
    search_fields = ['description', 'job_role__name', 'job_role__code']


class SiteSurveyLocationLineViewSet(_SurveyChildViewSetBase):
    queryset = SiteSurveyLocationLine.objects.select_related(
        'survey', 'survey__lead',
    ).order_by('sort_order', 'id')
    serializer_class = SiteSurveyLocationLineSerializer
    filterset_fields = ['survey', 'line_type']
    search_fields = ['location_name']


class SiteSurveyEquipmentLineViewSet(_SurveyChildViewSetBase):
    queryset = SiteSurveyEquipmentLine.objects.select_related(
        'survey', 'survey__lead',
    ).order_by('equipment_category', 'sort_order', 'id')
    serializer_class = SiteSurveyEquipmentLineSerializer
    filterset_fields = ['survey', 'equipment_category', 'line_type']
    search_fields = ['description']


class SiteSurveyIssueLineViewSet(_SurveyChildViewSetBase):
    queryset = SiteSurveyIssueLine.objects.select_related(
        'survey', 'survey__lead',
    ).order_by('sort_order', 'id')
    serializer_class = SiteSurveyIssueLineSerializer
    filterset_fields = ['survey']
    search_fields = ['issue', 'improvement_details']


# ─── Phase H: Proposal component rules ────────────────────────────────────────

class SurveyRoleMappingViewSet(ScopedModelViewSet):
    queryset = SurveyRoleMapping.objects.select_related(
        'org', 'job_role', 'wage_category',
    ).order_by('description_text')
    serializer_class = SurveyRoleMappingSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_survey_role_mappings_for_user
    filterset_fields = ['org', 'job_role', 'wage_category', 'service_category', 'is_active']
    search_fields = ['description_text', 'job_role__name', 'wage_category__name']

    action_required_capabilities = {
        'list':           'sales_survey.read',
        'retrieve':       'sales_survey.read',
        'create':         'sales_survey.update',
        'update':         'sales_survey.update',
        'partial_update': 'sales_survey.update',
        'destroy':        'sales_survey.update',
    }


class ProposalComponentRuleViewSet(ScopedModelViewSet):
    queryset = ProposalComponentRule.objects.select_related('org').order_by(
        'org_id', 'sort_order', 'code',
    )
    serializer_class = ProposalComponentRuleSerializer

    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_proposal_component_rules_for_user

    filterset_fields = ['org', 'code', 'component_type', 'calculation_type', 'is_active']
    search_fields = ['code', 'component_name']

    action_required_capabilities = {
        'list':           'sales_proposal.read',
        'retrieve':       'sales_proposal.read',
        'create':         'sales_proposal.update',
        'update':         'sales_proposal.update',
        'partial_update': 'sales_proposal.update',
        'destroy':        'sales_proposal.update',
    }


# ─── Phase I: Sales Dashboard Summary ─────────────────────────────────────────

from rest_framework.views import APIView  # noqa: E402


class SalesDashboardSummaryView(APIView):
    """
    GET /api/sales/dashboard/summary/

    Returns aggregated sales counts and recent activity, scoped to the user's org.

    Requires capability `sales_lead.read`. Users without it receive HTTP 403,
    NOT a silently zero-filled response.
    """
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'sales_lead.read'

    def get(self, request):
        from django.db.models import Count
        from apps.sales.models import (
            SalesLead, SalesLeadActivity, ProposalVersion,
        )
        from apps.sales.models import SiteSurvey as _SiteSurvey
        from apps.sales.querysets import (
            filter_leads_for_user,
            filter_site_surveys_for_user,
            filter_proposal_versions_for_user,
        )
        from apps.mobilisation.models import MobilisationSetupRequest

        user = request.user

        leads_qs = filter_leads_for_user(SalesLead.objects.all(), user)
        surveys_qs = filter_site_surveys_for_user(_SiteSurvey.objects.all(), user)
        proposals_qs = filter_proposal_versions_for_user(
            ProposalVersion.objects.all(), user,
        )

        leads_total = leads_qs.count()
        by_stage = [
            {'stage': r['current_stage'], 'count': r['count']}
            for r in leads_qs.values('current_stage')
                             .annotate(count=Count('id'))
                             .order_by('current_stage')
        ]
        by_type = [
            {'lead_type': r['lead_type'], 'count': r['count']}
            for r in leads_qs.values('lead_type')
                             .annotate(count=Count('id'))
                             .order_by('lead_type')
        ]

        pending_assignment = surveys_qs.filter(
            status='pending', assigned_to__isnull=True,
        ).count()
        in_progress = surveys_qs.filter(status='in_progress').count()
        completed = surveys_qs.filter(status='completed').count()

        proposal_status_keys = {
            'draft': 'draft',
            'pending_internal_approval': 'submitted_internal',
            'internally_approved': 'internally_approved',
            'sent_to_client': 'sent_to_client',
            'client_approved': 'client_approved',
            'client_rejected': 'client_rejected',
            'revision_requested': 'client_revision_required',
        }
        proposals_counts = {key: 0 for key in proposal_status_keys}
        status_aggregates = {
            row['status']: row['count']
            for row in proposals_qs.values('status').annotate(count=Count('id'))
        }
        for key, raw_status in proposal_status_keys.items():
            proposals_counts[key] = int(status_aggregates.get(raw_status, 0))

        won_leads_qs = leads_qs.filter(current_stage='won')
        converted_lead_ids = set(
            MobilisationSetupRequest.objects
            .filter(source_sales_lead__in=won_leads_qs)
            .values_list('source_sales_lead_id', flat=True)
        )
        converted = len(converted_lead_ids)
        won_pending_mobilisation = (
            won_leads_qs.exclude(pk__in=converted_lead_ids).count()
        )

        recent_activity_qs = (
            SalesLeadActivity.objects
            .filter(lead__in=leads_qs)
            .select_related('actor', 'lead')
            .order_by('-created_at')[:10]
        )
        recent_activity = [
            {
                'type': a.activity_type,
                'title': a.title,
                'actor': a.actor.username if a.actor_id else None,
                'lead_id': a.lead_id,
                'created_at': a.created_at,
            }
            for a in recent_activity_qs
        ]

        return Response({
            'leads': {
                'total': leads_total,
                'by_stage': by_stage,
                'by_type': by_type,
            },
            'surveys': {
                'pending_assignment': pending_assignment,
                'in_progress': in_progress,
                'completed': completed,
            },
            'proposals': proposals_counts,
            'conversion': {
                'won_pending_mobilisation': won_pending_mobilisation,
                'converted': converted,
            },
            'recent_activity': recent_activity,
        })
