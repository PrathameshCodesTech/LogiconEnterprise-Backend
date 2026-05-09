from rest_framework.permissions import IsAuthenticated

from apps.access.permissions import HasCapability
from apps.access.querysets import filter_hiring_applications_for_user
from apps.access.viewsets import ScopedReadOnlyModelViewSet

from .models import HiringApplication, Interview, InterviewFeedback, Offer
from .serializers import (
    HiringApplicationSerializer,
    InterviewSerializer,
    InterviewFeedbackSerializer,
    OfferSerializer,
)


class HiringApplicationViewSet(ScopedReadOnlyModelViewSet):
    queryset = HiringApplication.objects.select_related(
        'org', 'candidate', 'mrf', 'mrf_line_item',
        'site', 'site__scope_node', 'site__client__scope_node',
        'job_role',
    ).order_by('-created_at')
    serializer_class = HiringApplicationSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'hiring_application.read'
    scope_filter = filter_hiring_applications_for_user
    filterset_fields = ['org', 'site', 'job_role', 'status', 'client_visible', 'client_decision']
    search_fields = ['candidate__first_name', 'candidate__last_name', 'candidate__phone']


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
        from apps.access.querysets import _scope_q, get_accessible_scope_paths
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
        from apps.access.querysets import _scope_q, get_accessible_scope_paths
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
        from apps.access.querysets import _scope_q, get_accessible_scope_paths
        paths = get_accessible_scope_paths(user)
        if not paths:
            return qs.none()
        site_q = _scope_q('hiring_application__site__scope_node__path', paths)
        client_q = _scope_q('hiring_application__site__client__scope_node__path', paths)
        return qs.filter(site_q | client_q).distinct()

    filterset_fields = ['status', 'joining_date']
