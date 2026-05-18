"""
apps/hiring/views.py

Phase Talent-Hiring-B: Hiring pipeline operational APIs.
"""

from decimal import Decimal, InvalidOperation

from django.db.models import Count, Q
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from apps.access.capabilities import (
    HIRING_APP_READ, HIRING_APP_CREATE, HIRING_APP_UPDATE, HIRING_APP_MANAGE,
    PIPELINE_STAGE_READ, CANDIDATE_MATCH_READ, MRF_READ,
    get_user_capabilities,
)
from apps.access.permissions import HasCapability
from apps.access.querysets import (
    filter_hiring_applications_for_user,
    filter_mrf_line_items_for_user,
    filter_match_results_for_user,
    filter_pipeline_stages_for_user,
    _scope_q, get_accessible_scope_paths,
)
from apps.access.viewsets import (
    ReadAfterWriteMixin, ActionCapabilityMixin, ScopedQuerysetMixin,
    ScopedReadOnlyModelViewSet,
)

from .models import (
    HiringApplication, ApplicationStageHistory, PipelineStage,
    CandidateMatchResult, Interview, InterviewFeedback, Offer,
)
from .serializers import (
    HiringApplicationReadSerializer,
    HiringApplicationCreateSerializer,
    HiringApplicationPatchSerializer,
    PipelineStageSerializer,
    HiringDemandSerializer,
    CandidateMatchResultSerializer,
    InterviewSerializer,
    InterviewFeedbackSerializer,
    OfferSerializer,
)


# ─── PipelineStageViewSet ─────────────────────────────────────────────────────

class PipelineStageViewSet(ScopedReadOnlyModelViewSet):
    """Read-only pipeline stages — org-scoped, active only, ordered by position."""
    queryset = PipelineStage.objects.filter(is_active=True).order_by('order')
    serializer_class = PipelineStageSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = PIPELINE_STAGE_READ
    scope_filter = filter_pipeline_stages_for_user
    filterset_fields = ['stage_type', 'is_terminal']


# ─── HiringApplicationViewSet ─────────────────────────────────────────────────

class HiringApplicationViewSet(
    ReadAfterWriteMixin, ActionCapabilityMixin, ScopedQuerysetMixin, ModelViewSet
):
    """
    Hiring Application CRUD + move-stage action — site-scoped.
    list/retrieve: hiring_application.read
    create: hiring_application.create
    partial_update: hiring_application.update
    move_stage: hiring_application.update (+ manage for terminal-stage exit)
    """
    queryset = HiringApplication.objects.select_related(
        'org', 'candidate', 'mrf', 'mrf_line_item',
        'site', 'site__scope_node', 'site__client__scope_node',
        'job_role', 'current_stage',
    ).order_by('-created_at')
    permission_classes = [IsAuthenticated, HasCapability]
    read_serializer_class = HiringApplicationReadSerializer
    scope_filter = filter_hiring_applications_for_user
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    action_required_capabilities = {
        'list': HIRING_APP_READ,
        'retrieve': HIRING_APP_READ,
        'create': HIRING_APP_CREATE,
        'partial_update': HIRING_APP_UPDATE,
        'move_stage': HIRING_APP_UPDATE,
    }

    filterset_fields = ['org', 'site', 'job_role', 'status', 'client_visible', 'client_decision']
    search_fields = ['candidate__first_name', 'candidate__last_name', 'candidate__phone']

    def get_serializer_class(self):
        if self.action == 'create':
            return HiringApplicationCreateSerializer
        if self.action == 'partial_update':
            return HiringApplicationPatchSerializer
        return HiringApplicationReadSerializer

    def perform_create(self, serializer):
        mrf = serializer.validated_data['mrf']
        mrf_li = serializer.validated_data.get('mrf_line_item')
        candidate = serializer.validated_data['candidate']

        if candidate.is_blacklisted:
            raise ValidationError(
                {'candidate': 'Cannot create an application for a blacklisted candidate.'}
            )

        if not self.request.user.is_superuser:
            user_org_id = getattr(self.request.user, 'org_id', None)
            if candidate.org_id != user_org_id:
                raise ValidationError(
                    {'candidate': 'Candidate does not belong to your organization.'}
                )

        site = mrf.site
        job_role = mrf_li.job_role if mrf_li else None

        current_stage = serializer.validated_data.get('current_stage')
        if current_stage is None:
            current_stage = (
                PipelineStage.objects.filter(org=mrf.org, is_active=True)
                .order_by('order')
                .first()
            )

        instance = serializer.save(
            org=mrf.org,
            site=site,
            job_role=job_role,
            current_stage=current_stage,
        )

        ApplicationStageHistory.objects.create(
            hiring_application=instance,
            from_stage=None,
            to_stage=current_stage,
            from_status='',
            to_status=instance.status,
            moved_by=self.request.user,
            comment='Application created.',
        )

    @action(detail=True, methods=['post'], url_path='move-stage')
    def move_stage(self, request, pk=None):
        """
        Move an application to a new stage and/or status.
        Body: { stage_id?, status?, comment? }
        Terminal-stage exit additionally requires hiring_application.manage.
        """
        application = self.get_object()

        stage_id = request.data.get('stage_id')
        new_status = request.data.get('status')
        comment = request.data.get('comment', '')

        if not stage_id and not new_status:
            raise ValidationError(
                {'non_field_errors': 'Provide at least one of: stage_id, status.'}
            )

        new_stage = None
        if stage_id:
            try:
                new_stage = PipelineStage.objects.get(pk=stage_id, org=application.org)
            except PipelineStage.DoesNotExist:
                raise ValidationError(
                    {'stage_id': 'Pipeline stage not found or does not belong to this org.'}
                )

        if (
            application.current_stage
            and application.current_stage.is_terminal
            and not request.user.is_superuser
            and HIRING_APP_MANAGE not in get_user_capabilities(request.user)
        ):
            raise PermissionDenied(
                'Moving out of a terminal stage requires hiring_application.manage.'
            )

        old_stage = application.current_stage
        old_status = application.status

        update_fields = []
        if new_stage is not None:
            application.current_stage = new_stage
            update_fields.append('current_stage')
        if new_status is not None:
            application.status = new_status
            update_fields.append('status')

        application.save(update_fields=update_fields)

        ApplicationStageHistory.objects.create(
            hiring_application=application,
            from_stage=old_stage,
            to_stage=application.current_stage,
            from_status=old_status,
            to_status=application.status,
            moved_by=request.user,
            comment=comment,
        )

        return Response(
            HiringApplicationReadSerializer(
                application, context=self.get_serializer_context()
            ).data
        )


# ─── HiringDemandViewSet ──────────────────────────────────────────────────────

class HiringDemandViewSet(ReadOnlyModelViewSet):
    """
    Read-only hiring demand: approved MRF line items annotated with application counts.
    Site-scoped via mrf → site scope paths.
    """
    serializer_class = HiringDemandSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = MRF_READ
    filterset_fields = ['mrf', 'job_role']

    def get_queryset(self):
        from apps.mrf.models import MRFLineItem
        qs = MRFLineItem.objects.filter(mrf__status='approved').select_related(
            'mrf', 'mrf__site', 'mrf__site__client', 'job_role',
        ).order_by('mrf', 'id').annotate(
            application_count=Count('hiring_applications'),
            shortlisted_count=Count(
                'hiring_applications',
                filter=Q(hiring_applications__status='shortlisted'),
            ),
            selected_count=Count(
                'hiring_applications',
                filter=Q(hiring_applications__status='selected'),
            ),
            offer_accepted_count=Count(
                'hiring_applications',
                filter=Q(hiring_applications__status='offer_accepted'),
            ),
        )
        user = self.request.user
        if user.is_superuser:
            return qs
        return filter_mrf_line_items_for_user(qs, user)

    @action(detail=True, methods=['get'], url_path='candidate-pool')
    def candidate_pool(self, request, pk=None):
        """
        GET /api/hiring/demands/{id}/candidate-pool/

        Returns active candidates in the demand's org that are not yet linked
        to this hiring demand (mrf_line_item). Supports optional filters:
        ?role=, ?location=, ?skill=, ?min_experience=, ?max_experience=
        """
        demand = self.get_object()
        org_id = demand.mrf.org_id

        from apps.talent.models import Candidate
        from apps.talent.serializers import CandidateSerializer

        linked_candidate_ids = HiringApplication.objects.filter(
            mrf_line_item=demand,
        ).values_list('candidate_id', flat=True)

        qs = Candidate.objects.filter(
            org_id=org_id,
            is_blacklisted=False,
            lifecycle_status__in=['active', 'available'],
        ).exclude(id__in=linked_candidate_ids).distinct()

        role = request.query_params.get('role', '').strip()
        if role:
            qs = qs.filter(current_role__icontains=role)

        location = request.query_params.get('location', '').strip()
        if location:
            qs = qs.filter(current_location__icontains=location)

        skill = request.query_params.get('skill', '').strip()
        if skill:
            qs = qs.filter(
                skills__normalized_skill_name__icontains=skill.lower()
            ).distinct()

        min_exp = request.query_params.get('min_experience', '').strip()
        if min_exp:
            try:
                qs = qs.filter(total_experience_years__gte=Decimal(min_exp))
            except InvalidOperation:
                pass

        max_exp = request.query_params.get('max_experience', '').strip()
        if max_exp:
            try:
                qs = qs.filter(total_experience_years__lte=Decimal(max_exp))
            except InvalidOperation:
                pass

        page = self.paginate_queryset(qs)
        ctx = {'request': request}
        if page is not None:
            return self.get_paginated_response(
                CandidateSerializer(page, many=True, context=ctx).data
            )
        return Response(CandidateSerializer(qs, many=True, context=ctx).data)


# ─── CandidateMatchResultViewSet ──────────────────────────────────────────────

class CandidateMatchResultViewSet(ScopedReadOnlyModelViewSet):
    """
    Read-only candidate match results — site-scoped via mrf_line_item → mrf → site.
    """
    queryset = CandidateMatchResult.objects.select_related(
        'org', 'candidate', 'mrf_line_item',
        'mrf_line_item__mrf__site__scope_node',
        'mrf_line_item__mrf__site__client__scope_node',
        'created_by',
    ).order_by('-final_score', '-match_score')
    serializer_class = CandidateMatchResultSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = CANDIDATE_MATCH_READ
    scope_filter = filter_match_results_for_user
    filterset_fields = ['org', 'candidate', 'mrf_line_item', 'match_source', 'is_auto_match']


# ─── Interview / InterviewFeedback / Offer ────────────────────────────────────

class InterviewViewSet(ScopedReadOnlyModelViewSet):
    queryset = Interview.objects.select_related(
        'hiring_application',
        'hiring_application__site__scope_node',
        'hiring_application__site__client__scope_node',
        'interviewer', 'scheduled_by',
    ).order_by('-scheduled_at')
    serializer_class = InterviewSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'interview.read'

    def get_queryset(self):
        qs = self.queryset
        user = self.request.user
        if user.is_superuser:
            return qs
        paths = get_accessible_scope_paths(user)
        if not paths:
            return qs.none()
        site_q = _scope_q('hiring_application__site__scope_node__path', paths)
        client_q = _scope_q('hiring_application__site__client__scope_node__path', paths)
        return qs.filter(site_q | client_q).distinct()

    filterset_fields = ['hiring_application', 'round_type', 'status', 'mode']


class InterviewFeedbackViewSet(ScopedReadOnlyModelViewSet):
    queryset = InterviewFeedback.objects.select_related(
        'interview', 'interview__hiring_application__site__scope_node',
        'interview__hiring_application__site__client__scope_node',
        'given_by',
    )
    serializer_class = InterviewFeedbackSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'interview.read'

    def get_queryset(self):
        qs = self.queryset
        user = self.request.user
        if user.is_superuser:
            return qs
        paths = get_accessible_scope_paths(user)
        if not paths:
            return qs.none()
        site_q = _scope_q('interview__hiring_application__site__scope_node__path', paths)
        client_q = _scope_q('interview__hiring_application__site__client__scope_node__path', paths)
        return qs.filter(site_q | client_q).distinct()

    filterset_fields = ['interview', 'recommendation', 'given_by']


class OfferViewSet(ScopedReadOnlyModelViewSet):
    queryset = Offer.objects.select_related(
        'hiring_application',
        'hiring_application__site__scope_node',
        'hiring_application__site__client__scope_node',
        'released_by',
    ).order_by('-created_at')
    serializer_class = OfferSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'offer.read'

    def get_queryset(self):
        qs = self.queryset
        user = self.request.user
        if user.is_superuser:
            return qs
        paths = get_accessible_scope_paths(user)
        if not paths:
            return qs.none()
        site_q = _scope_q('hiring_application__site__scope_node__path', paths)
        client_q = _scope_q('hiring_application__site__client__scope_node__path', paths)
        return qs.filter(site_q | client_q).distinct()

    filterset_fields = ['status', 'joining_date']
