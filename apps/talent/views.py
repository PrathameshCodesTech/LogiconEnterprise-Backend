"""
apps/talent/views.py

Phase Talent-Hiring-B: Candidate CRUD + Resume upload APIs.
Phase Talent-Manual-Intake-A: Manual resume intake endpoint.
"""

from decimal import Decimal, InvalidOperation

from rest_framework import status as http_status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from apps.access.capabilities import (
    CANDIDATE_READ, CANDIDATE_CREATE, CANDIDATE_UPDATE,
    HIRING_APP_CREATE,
    RESUME_READ, RESUME_UPLOAD,
    get_user_capabilities,
)
from apps.access.permissions import HasCapability
from apps.access.querysets import (
    filter_candidates_for_user,
    filter_resumes_for_user,
)
from apps.access.viewsets import (
    ReadAfterWriteMixin, ActionCapabilityMixin, ScopedQuerysetMixin,
)

from django.db.models import Q

from .models import (
    Candidate, Resume,
    CandidateExperience, CandidateEducation, ParsedResume,
    TalentResumeReview,
)
from .serializers import (
    CandidateSerializer, CandidateWriteSerializer,
    ResumeSerializer, ResumeWriteSerializer, ResumePatchSerializer,
    CandidateExperienceSerializer, CandidateEducationSerializer,
    CandidateSkillSerializer,
    ParsedResumeSerializer,
    ManualResumeIntakeSerializer,
    ResumeReviewQueueSerializer,
    ResumeReviewDetailSerializer,
    ResumeApplyReviewSerializer,
    TalentResumeReviewSerializer,
    ResumeDuplicateResolutionSerializer,
)
from .services import normalize_phone, compute_file_hash


# ─── CandidateViewSet ─────────────────────────────────────────────────────────

class CandidateViewSet(ReadAfterWriteMixin, ActionCapabilityMixin, ScopedQuerysetMixin, ModelViewSet):
    """
    Candidate CRUD — org-scoped.
    list/retrieve: candidate.read  |  create: candidate.create  |  patch: candidate.update
    """
    queryset = Candidate.objects.select_related('org').all()
    permission_classes = [IsAuthenticated, HasCapability]
    read_serializer_class = CandidateSerializer
    scope_filter = filter_candidates_for_user
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    action_required_capabilities = {
        'list': CANDIDATE_READ,
        'retrieve': CANDIDATE_READ,
        'create': CANDIDATE_CREATE,
        'partial_update': CANDIDATE_UPDATE,
    }

    filterset_fields = [
        'org', 'source', 'is_blacklisted',
        'lifecycle_status', 'availability_status',
        'is_duplicate', 'do_not_contact',
    ]
    search_fields = [
        'first_name', 'last_name', 'phone', 'email',
        'current_company', 'current_role', 'current_location',
    ]

    def get_queryset(self):
        qs = super().get_queryset()
        skill = self.request.query_params.get('skill', '').strip()
        if skill:
            qs = qs.filter(
                skills__normalized_skill_name__icontains=skill.lower()
            ).distinct()

        min_exp = self.request.query_params.get('min_experience', '').strip()
        if min_exp:
            try:
                qs = qs.filter(total_experience_years__gte=Decimal(min_exp))
            except InvalidOperation:
                pass

        max_exp = self.request.query_params.get('max_experience', '').strip()
        if max_exp:
            try:
                qs = qs.filter(total_experience_years__lte=Decimal(max_exp))
            except InvalidOperation:
                pass

        return qs

    def get_serializer_class(self):
        if self.action in ('create', 'partial_update', 'update'):
            return CandidateWriteSerializer
        return CandidateSerializer

    def perform_create(self, serializer):
        phone = serializer.validated_data['phone']
        phone_normalized = normalize_phone(phone)
        org = self.request.user.org if not self.request.user.is_superuser else None
        if org is None:
            raise ValidationError({'org': 'org must be specified.'})

        if Candidate.objects.filter(org=org, phone_normalized=phone_normalized).exists():
            raise ValidationError(
                {'phone': 'A candidate with this phone number already exists in your organization.'}
            )
        serializer.save(org=org, phone_normalized=phone_normalized)

    def perform_update(self, serializer):
        if 'phone' in serializer.validated_data:
            phone = serializer.validated_data['phone']
            phone_normalized = normalize_phone(phone)
            if Candidate.objects.filter(
                org=serializer.instance.org,
                phone_normalized=phone_normalized,
            ).exclude(pk=serializer.instance.pk).exists():
                raise ValidationError(
                    {'phone': 'A candidate with this phone number already exists.'}
                )
            serializer.save(phone_normalized=phone_normalized)
        else:
            serializer.save()


# ─── ResumeViewSet ────────────────────────────────────────────────────────────

class ResumeViewSet(ReadAfterWriteMixin, ActionCapabilityMixin, ScopedQuerysetMixin, ModelViewSet):
    """
    Resume upload (Mode A) + read API — org-scoped via candidate.
    list/retrieve: resume.read  |  create: resume.upload  |  patch: candidate.update
    """
    queryset = Resume.objects.select_related('candidate', 'candidate__org').all()
    permission_classes = [IsAuthenticated, HasCapability]
    read_serializer_class = ResumeSerializer
    scope_filter = filter_resumes_for_user
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    action_required_capabilities = {
        'list': RESUME_READ,
        'retrieve': RESUME_READ,
        'create': RESUME_UPLOAD,
        'partial_update': CANDIDATE_UPDATE,
        'resume_status': RESUME_READ,
        'reprocess': RESUME_UPLOAD,
        'mark_reviewed': CANDIDATE_UPDATE,
        'review_queue': RESUME_READ,
        'review_detail': RESUME_READ,
        'review_history': RESUME_READ,
        'apply_review': CANDIDATE_UPDATE,
        'resolve_duplicate': CANDIDATE_UPDATE,
    }

    filterset_fields = ['candidate', 'parsed_status', 'status', 'source_type']

    def get_serializer_class(self):
        if self.action == 'create':
            return ResumeWriteSerializer
        if self.action == 'partial_update':
            return ResumePatchSerializer
        return ResumeSerializer

    def perform_create(self, serializer):
        candidate = serializer.validated_data['candidate']
        if not self.request.user.is_superuser:
            user_org_id = getattr(self.request.user, 'org_id', None)
            if candidate.org_id != user_org_id:
                raise ValidationError(
                    {'candidate': 'Candidate does not belong to your organization.'}
                )

        f = serializer.validated_data['file']
        serializer.save(
            original_filename=getattr(f, 'name', ''),
            content_type=getattr(f, 'content_type', ''),
            size_bytes=getattr(f, 'size', 0),
            file_hash=compute_file_hash(f),
            status='uploaded',
            uploaded_by=self.request.user,
        )
        from .services import queue_resume_processing
        queue_resume_processing(serializer.instance)

    @action(detail=True, methods=['get'], url_path='status')
    def resume_status(self, request, pk=None):
        """GET /api/talent/resumes/{id}/status/ — current parse status and confidence."""
        resume = self.get_object()
        return Response({
            'id': resume.pk,
            'status': resume.status,
            'parser_confidence': (
                float(resume.parser_confidence)
                if resume.parser_confidence is not None else None
            ),
            'extraction_confidence': (
                float(resume.extraction_confidence)
                if resume.extraction_confidence is not None else None
            ),
            'error_message': resume.error_message,
            'manual_review_reason': resume.manual_review_reason,
        })

    @action(detail=True, methods=['post'], url_path='reprocess')
    def reprocess(self, request, pk=None):
        """POST /api/talent/resumes/{id}/reprocess/ — re-queue an already-processed resume."""
        resume = self.get_object()
        override_duplicate = bool(request.data.get('override_duplicate', False))

        if resume.status == 'duplicate_file':
            if not override_duplicate:
                raise ValidationError(
                    {'detail': 'Cannot reprocess a duplicate-file resume. Pass override_duplicate=true to force.'}
                )
            if not request.user.is_superuser and CANDIDATE_UPDATE not in get_user_capabilities(request.user):
                raise PermissionDenied('candidate.update capability is required to override duplicate status.')

        previous_status = resume.status

        Resume.objects.filter(pk=resume.pk).update(
            status='extracting',
            error_message='',
            manual_review_reason='',
        )
        resume.status = 'extracting'

        if previous_status in ('manual_review', 'failed'):
            TalentResumeReview.objects.create(
                org=resume.candidate.org,
                resume=resume,
                candidate=resume.candidate,
                reviewed_by=request.user,
                review_type='reprocess',
                previous_status=previous_status,
                new_status='extracting',
                review_note=request.data.get('note', ''),
                correction_payload={},
            )

        from .tasks import process_resume_task
        process_resume_task.delay(resume.pk, force=True)
        return Response({'detail': 'Reprocessing scheduled.'})

    @action(detail=True, methods=['post'], url_path='mark-reviewed')
    def mark_reviewed(self, request, pk=None):
        """POST /api/talent/resumes/{id}/mark-reviewed/ — approve a manual_review resume."""
        resume = self.get_object()
        if resume.status != 'manual_review':
            raise ValidationError(
                {'detail': 'Only resumes with status manual_review can be marked reviewed.'}
            )
        if not hasattr(resume, 'parsed_resume'):
            raise ValidationError(
                {'detail': 'No parsed data exists; cannot mark resume as reviewed.'}
            )
        Resume.objects.filter(pk=resume.pk).update(status='indexed')
        resume.status = 'indexed'
        TalentResumeReview.objects.create(
            org=resume.candidate.org,
            resume=resume,
            candidate=resume.candidate,
            reviewed_by=request.user,
            review_type='mark_reviewed',
            previous_status='manual_review',
            new_status='indexed',
            review_note='',
            correction_payload={},
        )
        return Response({'detail': 'Resume marked as reviewed and indexed.'})

    @action(detail=False, methods=['get'], url_path='review-queue')
    def review_queue(self, request):
        """GET /api/talent/resumes/review-queue/ — resumes needing HR review."""
        qs = self.get_queryset()

        base_q = Q(status='manual_review') | Q(status='failed')

        confidence_below_str = request.query_params.get('confidence_below', '').strip()
        if confidence_below_str:
            try:
                threshold = float(confidence_below_str)
                base_q |= Q(status='indexed', parser_confidence__lt=threshold)
            except ValueError:
                pass

        qs = qs.filter(base_q)

        status_filter = request.query_params.get('status', '').strip()
        if status_filter:
            qs = qs.filter(status=status_filter)

        source_type_filter = request.query_params.get('source_type', '').strip()
        if source_type_filter:
            qs = qs.filter(source_type=source_type_filter)

        uploaded_by_filter = request.query_params.get('uploaded_by', '').strip()
        if uploaded_by_filter:
            qs = qs.filter(uploaded_by_id=uploaded_by_filter)

        candidate_search = request.query_params.get('candidate', '').strip()
        if candidate_search:
            qs = qs.filter(
                Q(candidate__first_name__icontains=candidate_search) |
                Q(candidate__last_name__icontains=candidate_search) |
                Q(candidate__phone__icontains=candidate_search)
            )

        uploaded_from = request.query_params.get('uploaded_from', '').strip()
        if uploaded_from:
            qs = qs.filter(uploaded_at__date__gte=uploaded_from)

        uploaded_to = request.query_params.get('uploaded_to', '').strip()
        if uploaded_to:
            qs = qs.filter(uploaded_at__date__lte=uploaded_to)

        reason_contains = request.query_params.get('reason_contains', '').strip()
        if reason_contains:
            qs = qs.filter(
                Q(manual_review_reason__icontains=reason_contains) |
                Q(error_message__icontains=reason_contains)
            )

        qs = qs.select_related('candidate', 'uploaded_by').prefetch_related('parsed_resume')
        qs = qs.order_by('-uploaded_at')

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = ResumeReviewQueueSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(serializer.data)

        serializer = ResumeReviewQueueSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='review-detail')
    def review_detail(self, request, pk=None):
        """GET /api/talent/resumes/{id}/review-detail/ — full data for correction UI."""
        resume = self.get_object()
        serializer = ResumeReviewDetailSerializer(resume, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='apply-review')
    def apply_review(self, request, pk=None):
        """POST /api/talent/resumes/{id}/apply-review/ — HR correction + index."""
        resume = self.get_object()
        ser = ResumeApplyReviewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        from .services import apply_review_service
        review = apply_review_service(resume, request.user, ser.validated_data)
        return Response(TalentResumeReviewSerializer(review).data)

    @action(detail=True, methods=['get'], url_path='review-history')
    def review_history(self, request, pk=None):
        """GET /api/talent/resumes/{id}/review-history/ — audit log for this resume."""
        resume = self.get_object()
        reviews = TalentResumeReview.objects.filter(resume=resume).select_related('reviewed_by')
        return Response(TalentResumeReviewSerializer(reviews, many=True).data)

    @action(detail=True, methods=['post'], url_path='resolve-duplicate')
    def resolve_duplicate(self, request, pk=None):
        """POST /api/talent/resumes/{id}/resolve-duplicate/ — conservative duplicate resolution."""
        resume = self.get_object()
        ser = ResumeDuplicateResolutionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        from .services import resolve_duplicate_service
        review = resolve_duplicate_service(resume, request.user, ser.validated_data)
        return Response(TalentResumeReviewSerializer(review).data)


# ─── Read-only sub-resource viewsets ─────────────────────────────────────────

class _OrgScopedReadOnlyViewSet(ReadOnlyModelViewSet):
    """Base for org-scoped read-only viewsets that filter by org_id."""
    permission_classes = [IsAuthenticated, HasCapability]

    def _org_filter(self, qs, candidate_path: str):
        user = self.request.user
        if user.is_superuser:
            return qs
        org_id = getattr(user, 'org_id', None)
        if not org_id:
            return qs.none()
        from apps.access.scope import get_accessible_scope_paths
        if not get_accessible_scope_paths(user):
            return qs.none()
        return qs.filter(**{candidate_path: org_id})


class CandidateExperienceViewSet(_OrgScopedReadOnlyViewSet):
    queryset = CandidateExperience.objects.select_related('candidate').all()
    serializer_class = CandidateExperienceSerializer
    required_capability = CANDIDATE_READ
    filterset_fields = ['candidate']

    def get_queryset(self):
        return self._org_filter(super().get_queryset(), 'candidate__org_id')


class CandidateEducationViewSet(_OrgScopedReadOnlyViewSet):
    queryset = CandidateEducation.objects.select_related('candidate').all()
    serializer_class = CandidateEducationSerializer
    required_capability = CANDIDATE_READ
    filterset_fields = ['candidate']

    def get_queryset(self):
        return self._org_filter(super().get_queryset(), 'candidate__org_id')


class ParsedResumeViewSet(_OrgScopedReadOnlyViewSet):
    queryset = ParsedResume.objects.select_related('resume__candidate').all()
    serializer_class = ParsedResumeSerializer
    required_capability = RESUME_READ
    filterset_fields = ['resume']

    def get_queryset(self):
        return self._org_filter(super().get_queryset(), 'resume__candidate__org_id')


# ─── ManualResumeIntakeView ───────────────────────────────────────────────────

class ManualResumeIntakeView(APIView):
    """
    POST /api/talent/manual-resume-intake/

    Structured manual candidate + resume intake in one atomic transaction.
    Requires candidate.create. Additionally requires hiring_application.create
    if mrf or mrf_line_item fields are present.
    """
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = CANDIDATE_CREATE

    def post(self, request):
        has_mrf_fields = bool(request.data.get('mrf') or request.data.get('mrf_line_item'))
        if has_mrf_fields and not request.user.is_superuser:
            if HIRING_APP_CREATE not in get_user_capabilities(request.user):
                raise PermissionDenied(
                    'hiring_application.create is required to link an MRF line item.'
                )

        ser = ManualResumeIntakeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        from .services import manual_resume_intake
        from apps.hiring.serializers import HiringApplicationReadSerializer

        result = manual_resume_intake(request.user, ser.validated_data)
        ctx = {'request': request}

        return Response(
            {
                'candidate': CandidateSerializer(result['candidate'], context=ctx).data,
                'resume': ResumeSerializer(result['resume'], context=ctx).data,
                'skills': CandidateSkillSerializer(result['skills'], many=True).data,
                'hiring_application': (
                    HiringApplicationReadSerializer(result['hiring_application'], context=ctx).data
                    if result['hiring_application'] else None
                ),
            },
            status=http_status.HTTP_201_CREATED,
        )
