"""
apps/hiring/views.py

Phase Talent-Hiring-B: Hiring pipeline operational APIs.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from apps.access.capabilities import (
    HIRING_APP_READ, HIRING_APP_CREATE, HIRING_APP_UPDATE, HIRING_APP_MANAGE,
    PIPELINE_STAGE_READ, CANDIDATE_MATCH_READ, MRF_READ,
    DEPLOYMENT_CREATE, EMPLOYEE_CREATE, SITE_DEPLOYMENT_CREATE,
    INTERVIEW_READ, INTERVIEW_CREATE, INTERVIEW_MANAGE,
    OFFER_READ, OFFER_CREATE, OFFER_UPDATE, OFFER_APPROVE, OFFER_MANAGE,
    get_user_capabilities, is_client_facing_user,
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
    CandidateMatchResult, Interview, InterviewFeedback, InterviewPlan, Offer,
)
from .serializers import (
    HiringApplicationReadSerializer,
    HiringApplicationCreateSerializer,
    HiringApplicationPatchSerializer,
    PipelineStageSerializer,
    HiringDemandSerializer,
    CandidateMatchResultSerializer,
    ApplyInterviewPlanSerializer,
    InterviewPlanSerializer,
    InterviewSerializer,
    InterviewFeedbackSerializer,
    OfferSerializer,
    OfferCreateSerializer,
    OfferUpdateSerializer,
    OfferActionSerializer,
    CandidatePoolResultSerializer,
    ShortlistCandidateSerializer,
    SendToClientReviewSerializer,
    BulkSendToClientReviewSerializer,
    ClientDecisionSerializer,
    ClientReviewApplicationSerializer,
)
from .lifecycle import (
    STAGE_CLIENT_APPROVED,
    STAGE_CLIENT_REVIEW,
    STAGE_INTERVIEW,
    STAGE_REJECTED_CLOSED,
    STAGE_SHORTLISTED,
    default_initial_stage,
    transition_application,
)


# ─── Client review helper ────────────────────────────────────────────────────

_SEND_REVIEW_ALLOWED = {
    'shortlisted', 'draft', 'client_review',
    'interview_scheduled', 'interview_in_progress', 'selected',
}
_SEND_REVIEW_BLOCKED = {'rejected', 'offer_declined', 'cancelled', 'deployed'}


def _apply_send_to_client_review(application, actor, note=''):
    """
    Mark an application client-visible and pending.
    Returns 'sent' or 'skipped' (already in state with no note to update).
    Raises ValidationError for blocked statuses.
    """
    if application.status in _SEND_REVIEW_BLOCKED:
        raise ValidationError({
            'non_field_errors': (
                f"Cannot send application in status '{application.status}' to client review."
            )
        })

    already_pending = (
        application.client_visible
        and application.client_decision == 'pending'
        and application.status == 'client_review'
    )
    if already_pending and not note:
        return 'skipped'

    old_status = application.status
    new_status = 'client_review' if old_status in ('shortlisted', 'draft') else old_status

    transition_application(
        application,
        actor=actor,
        status=new_status,
        stage_code=STAGE_CLIENT_REVIEW,
        comment=note or 'Sent to client review.',
        extra_attrs={
            'client_visible': True,
            'client_decision': 'pending',
        },
    )
    return 'sent'


def _required_plan_round_ids(application):
    plan = application.interview_plan
    if plan is None:
        return set()
    return set(
        plan.rounds
        .filter(is_required=True, is_active=True)
        .values_list('id', flat=True)
    )


def _passed_required_plan_round_ids(application):
    return set(
        Interview.objects
        .filter(
            hiring_application=application,
            planned_round__isnull=False,
            status='completed',
            feedbacks__recommendation='proceed',
        )
        .values_list('planned_round_id', flat=True)
        .distinct()
    )


def _all_required_plan_rounds_passed(application):
    required_ids = _required_plan_round_ids(application)
    if not required_ids:
        return True
    return required_ids.issubset(_passed_required_plan_round_ids(application))


def _next_interview_bucket(application, interviews, feedbacks):
    if application.status in {'rejected', 'cancelled', 'offer_declined'}:
        return 'on_hold'
    if application.status in {'offer_released', 'offer_accepted', 'deployed'}:
        return 'closed'

    latest_feedback = sorted(
        feedbacks,
        key=lambda f: f.created_at,
        reverse=True,
    )
    if latest_feedback and latest_feedback[0].recommendation == 'hold':
        return 'on_hold'

    feedback_interview_ids = {f.interview_id for f in feedbacks}

    active = [
        i for i in sorted(interviews, key=lambda iv: (iv.round_number, iv.id))
        if i.status in {'pending', 'scheduled', 'rescheduled', 'no_show'}
    ]
    if active:
        return active[0].round_type

    completed_without_feedback = [
        i for i in interviews
        if i.status == 'completed' and i.id not in feedback_interview_ids
    ]
    if completed_without_feedback:
        return 'feedback_pending'

    if application.interview_plan_id:
        if _all_required_plan_rounds_passed(application):
            return 'cleared_for_offer'
        return 'ready_for_screening'

    if latest_feedback and latest_feedback[0].recommendation == 'proceed':
        return 'cleared_for_offer'

    if application.client_decision == 'approved' or application.status == 'selected':
        return 'ready_for_screening'
    return 'closed'


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
        'job_role', 'current_stage', 'offer',
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
        'convert_to_deployment': HIRING_APP_READ,
        'send_to_client_review': HIRING_APP_UPDATE,
        'client_decision': HIRING_APP_UPDATE,
        'apply_interview_plan': INTERVIEW_MANAGE,
        'interview_pipeline': INTERVIEW_READ,
    }

    filterset_fields = ['org', 'site', 'job_role', 'status', 'client_visible', 'client_decision']
    search_fields = ['candidate__first_name', 'candidate__last_name', 'candidate__phone']

    def get_queryset(self):
        qs = super().get_queryset()
        if is_client_facing_user(self.request.user):
            if self.action == 'client_decision':
                return qs.filter(client_visible=True)
            return qs.none()
        return qs

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
            current_stage = default_initial_stage(mrf.org)

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

    @action(detail=True, methods=['post'], url_path='apply-interview-plan')
    def apply_interview_plan(self, request, pk=None):
        """
        Select an interview plan and materialize its active rounds as pending interviews.
        Body: { plan }
        """
        application = self.get_object()
        serializer = ApplyInterviewPlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = serializer.validated_data['plan']

        if plan.org_id != application.org_id:
            raise ValidationError({'plan': 'Interview plan does not belong to this application organization.'})
        if plan.job_role_id and plan.job_role_id != application.job_role_id:
            raise ValidationError({'plan': 'Interview plan is configured for a different job role.'})
        if application.status in {'rejected', 'cancelled', 'deployed', 'offer_released', 'offer_accepted', 'offer_declined'}:
            raise ValidationError({
                'non_field_errors': f"Cannot apply an interview plan to application in status '{application.status}'."
            })

        created_count = 0
        attached_count = 0
        with transaction.atomic():
            application.interview_plan = plan
            application.save(update_fields=['interview_plan', 'updated_at'])

            for round_obj in plan.rounds.filter(is_active=True).order_by('round_number', 'id'):
                interview = (
                    Interview.objects
                    .filter(hiring_application=application, planned_round=round_obj)
                    .first()
                )
                if interview is None:
                    interview = (
                        Interview.objects
                        .filter(
                            hiring_application=application,
                            planned_round__isnull=True,
                            round_type=round_obj.round_type,
                            round_number=round_obj.round_number,
                        )
                        .first()
                    )
                if interview is not None:
                    if interview.planned_round_id != round_obj.id:
                        interview.planned_round = round_obj
                        interview.save(update_fields=['planned_round', 'updated_at'])
                        attached_count += 1
                    continue

                Interview.objects.create(
                    hiring_application=application,
                    planned_round=round_obj,
                    round_type=round_obj.round_type,
                    round_number=round_obj.round_number,
                    mode=round_obj.mode,
                    status='pending',
                    scheduled_by=request.user,
                )
                created_count += 1

            transition_application(
                application,
                actor=request.user,
                status='interview_in_progress',
                stage_code=STAGE_INTERVIEW,
                comment=f"Interview plan applied: {plan.name}.",
            )

        application.refresh_from_db()
        interviews = Interview.objects.filter(hiring_application=application).order_by('round_number', 'id')
        return Response({
            'application': HiringApplicationReadSerializer(
                application, context=self.get_serializer_context()
            ).data,
            'plan': InterviewPlanSerializer(plan).data,
            'interviews': InterviewSerializer(interviews, many=True).data,
            'created_count': created_count,
            'attached_count': attached_count,
        })

    @action(detail=False, methods=['get'], url_path='interview-pipeline')
    def interview_pipeline(self, request):
        """
        Interview-focused pipeline summary grouped by actual interview state.
        """
        qs = self.filter_queryset(self.get_queryset()).filter(
            status__in=[
                'selected',
                'interview_scheduled',
                'interview_in_progress',
                'rejected',
                'cancelled',
                'offer_declined',
            ],
        ).prefetch_related('interviews__feedbacks')

        bucket_order = [
            'ready_for_screening',
            'hr',
            'technical',
            'manager',
            'client',
            'final',
            'feedback_pending',
            'on_hold',
            'cleared_for_offer',
        ]
        buckets = {
            key: {'key': key, 'count': 0, 'applications': []}
            for key in bucket_order
        }

        for application in qs:
            interviews = list(application.interviews.all())
            feedbacks = [f for interview in interviews for f in interview.feedbacks.all()]
            bucket = _next_interview_bucket(application, interviews, feedbacks)
            if bucket == 'closed':
                continue
            if bucket not in buckets:
                bucket = 'ready_for_screening'
            buckets[bucket]['count'] += 1
            buckets[bucket]['applications'].append({
                'application': HiringApplicationReadSerializer(
                    application, context=self.get_serializer_context()
                ).data,
                'interviews': InterviewSerializer(interviews, many=True).data,
                'feedbacks': InterviewFeedbackSerializer(feedbacks, many=True).data,
                'required_round_ids': sorted(_required_plan_round_ids(application)),
                'passed_round_ids': sorted(_passed_required_plan_round_ids(application)),
            })

        return Response({'buckets': [buckets[key] for key in bucket_order]})

    @action(detail=True, methods=['post'], url_path='send-to-client-review')
    def send_to_client_review(self, request, pk=None):
        """
        POST /api/hiring/applications/{id}/send-to-client-review/

        Marks the application as client-visible and sets client_decision=pending.
        Moves status to client_review if currently shortlisted/draft.
        """
        application = self.get_object()
        serializer = SendToClientReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = serializer.validated_data.get('note', '')

        result = _apply_send_to_client_review(application, request.user, note)

        return Response({
            'result': result,
            'application': HiringApplicationReadSerializer(
                application, context=self.get_serializer_context()
            ).data,
        })

    @action(detail=True, methods=['post'], url_path='client-decision')
    def client_decision(self, request, pk=None):
        """
        POST /api/hiring/applications/{id}/client-decision/

        Records a client approval or rejection.
        approved → status becomes selected (if currently client_review).
        rejected → status becomes rejected.
        Requires override=true + manage capability to re-decide.
        """
        application = self.get_object()

        if not application.client_visible:
            raise ValidationError(
                {'non_field_errors': 'Application has not been sent to client review.'}
            )

        serializer = ClientDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        decision = serializer.validated_data['decision']
        note = serializer.validated_data.get('note', '')
        override = serializer.validated_data.get('override', False)

        already_decided = application.client_decision in ('approved', 'rejected')
        if already_decided:
            if not override:
                raise ValidationError({
                    'non_field_errors': (
                        f"Client decision already recorded as '{application.client_decision}'. "
                        "Send override=true with hiring_application.manage to change it."
                    )
                })
            if not request.user.is_superuser and HIRING_APP_MANAGE not in get_user_capabilities(request.user):
                raise PermissionDenied(
                    'hiring_application.manage is required to override an existing client decision.'
                )

        new_status = None
        stage_code = None
        if decision == 'approved' and application.status == 'client_review':
            new_status = 'selected'
            stage_code = STAGE_CLIENT_APPROVED
        elif decision == 'rejected':
            new_status = 'rejected'
            stage_code = STAGE_REJECTED_CLOSED

        transition_application(
            application,
            actor=request.user,
            status=new_status,
            stage_code=stage_code,
            comment=note or f'Client decision: {decision}.',
            extra_attrs={
                'client_decision': decision,
                'client_decision_by': request.user,
                'client_decision_at': timezone.now(),
                'client_decision_note': note,
            },
        )

        return Response(
            HiringApplicationReadSerializer(
                application, context=self.get_serializer_context()
            ).data
        )

    @action(detail=True, methods=['post'], url_path='convert-to-deployment')
    def convert_to_deployment(self, request, pk=None):
        """
        POST /api/hiring/applications/{id}/convert-to-deployment/

        Converts a selected/offer-accepted application into Employee + SiteDeployment.
        Requires deployment.create OR (employee.create + site_deployment.create).
        """
        if not request.user.is_superuser:
            caps = get_user_capabilities(request.user)
            has_perm = (
                DEPLOYMENT_CREATE in caps
                or (EMPLOYEE_CREATE in caps and SITE_DEPLOYMENT_CREATE in caps)
            )
            if not has_perm:
                raise PermissionDenied(
                    'deployment.create (or employee.create + site_deployment.create) required.'
                )

        application = self.get_object()

        from apps.deployment.serializers import (
            HiringDeploymentConversionSerializer,
            HiringDeploymentConversionResultSerializer,
        )
        from apps.deployment.services import convert_hiring_application_to_deployment

        serializer = HiringDeploymentConversionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        result = convert_hiring_application_to_deployment(
            application=application,
            actor=request.user,
            employee_code=payload.get('employee_code'),
            joined_on=payload.get('joined_on'),
            deployment_start_date=payload.get('deployment_start_date'),
            deployment_status=payload.get('deployment_status', 'planned'),
            shift_hours=payload.get('shift_hours'),
            billing_type=payload.get('billing_type'),
            allow_existing_employee=payload.get('allow_existing_employee', False),
        )
        result['application'] = application

        out = HiringDeploymentConversionResultSerializer(
            result, context=self.get_serializer_context()
        )
        http_status = (
            status.HTTP_201_CREATED if result['created_deployment'] else status.HTTP_200_OK
        )
        return Response(out.data, status=http_status)


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
                filter=Q(hiring_applications__status__in=[
                    'selected',
                    'interview_scheduled',
                    'interview_in_progress',
                    'offer_released',
                    'offer_accepted',
                    'deployed',
                ]),
            ),
            offer_accepted_count=Count(
                'hiring_applications',
                filter=Q(hiring_applications__status__in=['offer_accepted', 'deployed']),
            ),
        )
        user = self.request.user
        if user.is_superuser:
            return qs
        if is_client_facing_user(user):
            return qs.none()
        return filter_mrf_line_items_for_user(qs, user)

    @action(detail=True, methods=['get'], url_path='candidate-pool')
    def candidate_pool(self, request, pk=None):
        """
        GET /api/hiring/demands/{id}/candidate-pool/

        Returns candidates eligible for this demand.
        ?ranked=true (default): scored + ranked via matching engine.
        ?ranked=false: flat-filtered list (legacy behaviour).
        Ranked supports: skills, min_experience, max_experience, location,
                         min_score, save_results (default true).
        """
        demand = self.get_object()
        org_id = demand.mrf.org_id

        from apps.talent.models import Candidate
        from apps.talent.serializers import CandidateSerializer
        from apps.hiring.matching.services import rank_candidates

        linked_candidate_ids = HiringApplication.objects.filter(
            mrf_line_item=demand,
        ).values_list('candidate_id', flat=True)

        base_qs = Candidate.objects.filter(
            org_id=org_id,
            is_blacklisted=False,
            do_not_contact=False,
            lifecycle_status__in=['active', 'available'],
        ).exclude(id__in=linked_candidate_ids).distinct()

        ranked_param = request.query_params.get('ranked', 'true').strip().lower()
        if ranked_param == 'false':
            # Legacy flat-filter path
            role = request.query_params.get('role', '').strip()
            if role:
                base_qs = base_qs.filter(current_role__icontains=role)

            location = request.query_params.get('location', '').strip()
            if location:
                base_qs = base_qs.filter(current_location__icontains=location)

            skill = request.query_params.get('skill', '').strip()
            if skill:
                base_qs = base_qs.filter(
                    skills__normalized_skill_name__icontains=skill.lower()
                ).distinct()

            min_exp = request.query_params.get('min_experience', '').strip()
            if min_exp:
                try:
                    base_qs = base_qs.filter(total_experience_years__gte=Decimal(min_exp))
                except InvalidOperation:
                    pass

            max_exp = request.query_params.get('max_experience', '').strip()
            if max_exp:
                try:
                    base_qs = base_qs.filter(total_experience_years__lte=Decimal(max_exp))
                except InvalidOperation:
                    pass

            ctx = {'request': request}
            page = self.paginate_queryset(base_qs)
            if page is not None:
                return self.get_paginated_response(
                    CandidateSerializer(page, many=True, context=ctx).data
                )
            return Response(CandidateSerializer(base_qs, many=True, context=ctx).data)

        # Ranked path
        save_results = request.query_params.get('save_results', 'true').strip().lower() != 'false'
        results = rank_candidates(
            demand, base_qs, request.query_params, save_results=save_results,
            user=request.user,
        )

        min_score_raw = request.query_params.get('min_score', '').strip()
        if min_score_raw:
            try:
                min_score = float(min_score_raw)
                results = [r for r in results if r['score'] >= min_score]
            except ValueError:
                pass

        ctx = {'request': request}
        page = self.paginate_queryset(results)
        if page is not None:
            return self.get_paginated_response(
                CandidatePoolResultSerializer(page, many=True, context=ctx).data
            )
        return Response(CandidatePoolResultSerializer(results, many=True, context=ctx).data)

    @action(detail=True, methods=['post'], url_path='send-shortlisted-to-client-review')
    def send_shortlisted_to_client_review(self, request, pk=None):
        """
        POST /api/hiring/demands/{id}/send-shortlisted-to-client-review/

        Sends all shortlisted applications (or the specified subset) for this
        demand to client review. Returns {sent, skipped, errors}.
        Requires hiring_application.update capability.
        """
        if not request.user.is_superuser:
            caps = get_user_capabilities(request.user)
            if HIRING_APP_UPDATE not in caps and HIRING_APP_MANAGE not in caps:
                raise PermissionDenied('hiring_application.update capability required.')

        demand = self.get_object()
        serializer = BulkSendToClientReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        application_ids = serializer.validated_data.get('application_ids')
        note = serializer.validated_data.get('note', '')

        qs = HiringApplication.objects.filter(mrf_line_item=demand)
        if application_ids is not None:
            qs = qs.filter(pk__in=application_ids)
        else:
            qs = qs.filter(status='shortlisted')

        sent = skipped = 0
        errors = []
        for app in qs.select_related('candidate', 'current_stage'):
            try:
                result = _apply_send_to_client_review(app, request.user, note)
                if result == 'sent':
                    sent += 1
                else:
                    skipped += 1
            except (ValidationError, Exception) as exc:
                errors.append({'application_id': app.pk, 'error': str(exc)})

        return Response({'sent': sent, 'skipped': skipped, 'errors': errors})

    @action(detail=True, methods=['post'], url_path='shortlist-candidate')
    def shortlist_candidate(self, request, pk=None):
        """
        POST /api/hiring/demands/{id}/shortlist-candidate/

        Body: { candidate, match_result?, comment? }
        Creates a HiringApplication in the first active pipeline stage.
        Requires hiring_application.create capability.
        """
        from apps.access.capabilities import HIRING_APP_CREATE
        from apps.access.capabilities import get_user_capabilities
        if not request.user.is_superuser:
            if HIRING_APP_CREATE not in get_user_capabilities(request.user):
                raise PermissionDenied('hiring_application.create capability required.')

        demand = self.get_object()
        serializer = ShortlistCandidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        candidate = serializer.validated_data['candidate']
        match_result = serializer.validated_data.get('match_result')
        comment = serializer.validated_data.get('comment', '')

        if candidate.is_blacklisted:
            raise ValidationError(
                {'candidate': 'Cannot shortlist a blacklisted candidate.'}
            )

        if not request.user.is_superuser:
            user_org_id = getattr(request.user, 'org_id', None)
            if candidate.org_id != user_org_id:
                raise ValidationError(
                    {'candidate': 'Candidate does not belong to your organization.'}
                )

        mrf = demand.mrf
        if mrf.status != 'approved':
            raise ValidationError({'non_field_errors': 'MRF must be approved to shortlist candidates.'})

        if HiringApplication.objects.filter(candidate=candidate, mrf_line_item=demand).exists():
            raise ValidationError(
                {'non_field_errors': 'This candidate is already linked to this hiring demand.'}
            )

        first_stage = default_initial_stage(mrf.org)

        match_score = None
        if match_result:
            match_score = match_result.final_score if match_result.final_score is not None else match_result.match_score

        application = HiringApplication.objects.create(
            org=mrf.org,
            candidate=candidate,
            mrf=mrf,
            mrf_line_item=demand,
            site=mrf.site,
            job_role=demand.job_role,
            current_stage=first_stage,
            status='shortlisted',
            shortlisted_by=request.user,
            shortlisted_at=timezone.now(),
            match_score=match_score,
        )

        ApplicationStageHistory.objects.create(
            hiring_application=application,
            from_stage=None,
            to_stage=first_stage,
            from_status='',
            to_status='shortlisted',
            moved_by=request.user,
            comment=comment or 'Shortlisted via candidate pool.',
        )

        return Response(
            HiringApplicationReadSerializer(
                application, context=self.get_serializer_context()
            ).data,
            status=status.HTTP_201_CREATED,
        )


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

class InterviewPlanViewSet(ActionCapabilityMixin, ModelViewSet):
    queryset = InterviewPlan.objects.select_related('org', 'job_role').prefetch_related('rounds')
    serializer_class = InterviewPlanSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    filterset_fields = ['job_role', 'is_default', 'is_active']
    search_fields = ['name', 'code']

    action_required_capabilities = {
        'list': INTERVIEW_READ,
        'retrieve': INTERVIEW_READ,
        'create': INTERVIEW_MANAGE,
        'partial_update': INTERVIEW_MANAGE,
    }

    def get_queryset(self):
        qs = self.queryset
        user = self.request.user
        if user.is_superuser:
            return qs
        org_id = getattr(user, 'org_id', None)
        if org_id is None:
            return qs.none()
        return qs.filter(org_id=org_id)

    def perform_create(self, serializer):
        org = serializer.validated_data.get('org')
        if not self.request.user.is_superuser:
            user_org_id = getattr(self.request.user, 'org_id', None)
            if org is not None and org.pk != user_org_id:
                raise PermissionDenied('Cross-org interview plan creation is not allowed.')
            serializer.save(org_id=user_org_id)
            return
        serializer.save()


class InterviewViewSet(ActionCapabilityMixin, ModelViewSet):
    queryset = Interview.objects.select_related(
        'hiring_application',
        'hiring_application__site__scope_node',
        'hiring_application__site__client__scope_node',
        'planned_round',
        'interviewer', 'scheduled_by',
    ).order_by('-scheduled_at')
    serializer_class = InterviewSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    action_required_capabilities = {
        'list': INTERVIEW_READ,
        'retrieve': INTERVIEW_READ,
        'create': INTERVIEW_CREATE,
        'partial_update': INTERVIEW_MANAGE,
    }

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

    def perform_create(self, serializer):
        application = serializer.validated_data['hiring_application']
        if not self.request.user.is_superuser and application.org_id != getattr(self.request.user, 'org_id', None):
            raise PermissionDenied('Cross-org interview scheduling is not allowed.')
        if application.status in {'rejected', 'cancelled', 'deployed', 'offer_released', 'offer_accepted', 'offer_declined'}:
            raise ValidationError({
                'non_field_errors': f"Cannot schedule interview for application in status '{application.status}'."
            })
        planned_round = serializer.validated_data.get('planned_round')
        if planned_round is not None:
            if planned_round.plan.org_id != application.org_id:
                raise ValidationError({'planned_round': 'Planned round does not belong to this application organization.'})
            if application.interview_plan_id and planned_round.plan_id != application.interview_plan_id:
                raise ValidationError({'planned_round': 'Planned round is not part of the selected interview plan.'})
            application.interview_plan = planned_round.plan
            application.save(update_fields=['interview_plan', 'updated_at'])
        scheduled_by = serializer.validated_data.get('scheduled_by') or self.request.user
        serializer.save(scheduled_by=scheduled_by)
        transition_application(
            application,
            actor=self.request.user,
            status='interview_scheduled',
            stage_code=STAGE_INTERVIEW,
            comment=f"Interview round scheduled: {serializer.instance.round_type}.",
        )

    def perform_update(self, serializer):
        interview = serializer.save()
        application = interview.hiring_application
        if interview.status in {'pending', 'scheduled', 'rescheduled'}:
            transition_application(
                application,
                actor=self.request.user,
                status='interview_scheduled' if interview.status in {'scheduled', 'rescheduled'} else 'interview_in_progress',
                stage_code=STAGE_INTERVIEW,
                comment=f"Interview round {interview.status}: {interview.round_type}.",
            )


class InterviewFeedbackViewSet(ActionCapabilityMixin, ModelViewSet):
    queryset = InterviewFeedback.objects.select_related(
        'interview', 'interview__hiring_application__site__scope_node',
        'interview__hiring_application__site__client__scope_node',
        'given_by',
    )
    serializer_class = InterviewFeedbackSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    action_required_capabilities = {
        'list': INTERVIEW_READ,
        'retrieve': INTERVIEW_READ,
        'create': INTERVIEW_CREATE,
        'partial_update': INTERVIEW_MANAGE,
    }

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

    def perform_create(self, serializer):
        interview = serializer.validated_data['interview']
        application = interview.hiring_application
        if not self.request.user.is_superuser and application.org_id != getattr(self.request.user, 'org_id', None):
            raise PermissionDenied('Cross-org interview feedback is not allowed.')
        given_by = serializer.validated_data.get('given_by') or self.request.user
        serializer.save(given_by=given_by)

        if interview.status != 'completed':
            interview.status = 'completed'
            interview.save(update_fields=['status', 'updated_at'])

        recommendation = serializer.instance.recommendation
        if recommendation == 'reject':
            transition_application(
                application,
                actor=self.request.user,
                status='rejected',
                stage_code=STAGE_REJECTED_CLOSED,
                comment='Interview feedback recommendation: reject.',
            )
        elif recommendation == 'proceed':
            if _all_required_plan_rounds_passed(application):
                transition_application(
                    application,
                    actor=self.request.user,
                    status='selected',
                    stage_code=STAGE_CLIENT_APPROVED,
                    comment='All required interview rounds passed.',
                )
            else:
                transition_application(
                    application,
                    actor=self.request.user,
                    status='interview_in_progress',
                    stage_code=STAGE_INTERVIEW,
                    comment='Interview feedback recommendation: proceed. More rounds remain.',
                )
        elif recommendation == 'hold':
            transition_application(
                application,
                actor=self.request.user,
                status='interview_in_progress',
                stage_code=STAGE_INTERVIEW,
                comment='Interview feedback recommendation: hold.',
            )


class OfferViewSet(ActionCapabilityMixin, ModelViewSet):
    """
    Service-driven offer lifecycle. Direct status PATCH is blocked —
    use the release/accept/decline/withdraw/expire actions instead.
    """
    queryset = Offer.objects.select_related(
        'hiring_application',
        'hiring_application__site__scope_node',
        'hiring_application__site__client__scope_node',
        'hiring_application__candidate',
        'released_by',
    ).order_by('-created_at')
    serializer_class = OfferSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    filterset_fields = ['status', 'joining_date', 'hiring_application']

    action_required_capabilities = {
        'list': OFFER_READ,
        'retrieve': OFFER_READ,
        'create': OFFER_CREATE,
        'partial_update': OFFER_UPDATE,
        'release': OFFER_APPROVE,
        'accept': OFFER_UPDATE,
        'decline': OFFER_UPDATE,
        'withdraw': OFFER_MANAGE,
        'expire': OFFER_MANAGE,
    }

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

    def get_serializer_class(self):
        if self.action == 'create':
            return OfferCreateSerializer
        if self.action == 'partial_update':
            return OfferUpdateSerializer
        if self.action in ('release', 'accept', 'decline', 'withdraw', 'expire'):
            return OfferActionSerializer
        return OfferSerializer

    def create(self, request, *args, **kwargs):
        from apps.hiring.offer_services import create_or_update_offer
        serializer = OfferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = serializer.validated_data['hiring_application']
        if not request.user.is_superuser:
            if application.org_id != getattr(request.user, 'org_id', None):
                raise PermissionDenied('Cross-org offer creation is not allowed.')
        payload = {k: v for k, v in serializer.validated_data.items()
                   if k != 'hiring_application'}
        offer = create_or_update_offer(application, request.user, payload)
        return Response(
            OfferSerializer(offer, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        from apps.hiring.offer_services import create_or_update_offer
        offer = self.get_object()
        serializer = OfferUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = create_or_update_offer(
            offer.hiring_application, request.user, serializer.validated_data,
        )
        return Response(OfferSerializer(updated, context=self.get_serializer_context()).data)

    def _action_view(self, request, pk, service_fn, **kwargs):
        offer = self.get_object()
        ser = OfferActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        note = ser.validated_data.get('note', '')
        updated = service_fn(offer, request.user, note=note, **kwargs)
        return Response(OfferSerializer(updated, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'], url_path='release')
    def release(self, request, pk=None):
        from apps.hiring.offer_services import release_offer
        return self._action_view(request, pk, release_offer)

    @action(detail=True, methods=['post'], url_path='accept')
    def accept(self, request, pk=None):
        from apps.hiring.offer_services import accept_offer
        return self._action_view(request, pk, accept_offer)

    @action(detail=True, methods=['post'], url_path='decline')
    def decline(self, request, pk=None):
        from apps.hiring.offer_services import decline_offer
        return self._action_view(request, pk, decline_offer)

    @action(detail=True, methods=['post'], url_path='withdraw')
    def withdraw(self, request, pk=None):
        from apps.hiring.offer_services import withdraw_offer
        return self._action_view(request, pk, withdraw_offer)

    @action(detail=True, methods=['post'], url_path='expire')
    def expire(self, request, pk=None):
        from apps.hiring.offer_services import expire_offer
        return self._action_view(request, pk, expire_offer)


# ─── ClientReviewViewSet ──────────────────────────────────────────────────────

class ClientReviewViewSet(ScopedQuerysetMixin, ReadOnlyModelViewSet):
    """
    GET /api/hiring/client-review/

    Returns HiringApplications that are client-visible (client_visible=True),
    scoped to the requesting user's site/client access.
    Supports ?only_pending=true to filter to pending decisions.
    """
    queryset = HiringApplication.objects.filter(client_visible=True).select_related(
        'org', 'candidate', 'mrf', 'mrf_line_item',
        'site', 'site__client', 'site__scope_node', 'site__client__scope_node',
        'job_role', 'current_stage', 'client_decision_by',
    ).order_by('-updated_at')
    serializer_class = ClientReviewApplicationSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = HIRING_APP_READ
    scope_filter = filter_hiring_applications_for_user
    filterset_fields = [
        'client_decision', 'site', 'mrf', 'mrf_line_item', 'status', 'job_role',
    ]
    search_fields = ['candidate__first_name', 'candidate__last_name', 'candidate__phone']

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('only_pending', '').lower() == 'true':
            qs = qs.filter(client_decision='pending')
        return qs
