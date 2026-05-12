"""
apps/wages/views.py

Writable ViewSets for LocationArea, WageCategory, MinimumWageRate.
Lookup helper endpoint: GET /api/wages/rates/lookup/
"""

import datetime

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.access.permissions import HasCapability
from apps.access.viewsets import ActionCapabilityMixin

from .models import LocationArea, WageCategory, MinimumWageRate
from .serializers import (
    LocationAreaSerializer,
    LocationAreaWriteSerializer,
    WageCategorySerializer,
    WageCategoryWriteSerializer,
    MinimumWageRateSerializer,
    MinimumWageRateWriteSerializer,
)
from .services import get_applicable_minimum_wage


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _org_filter(qs, user):
    """Filter wage queryset to the user's org (or all for superuser)."""
    if user.is_superuser:
        return qs
    org_id = getattr(user, 'org_id', None)
    if org_id:
        from django.db.models import Q
        return qs.filter(Q(org_id=org_id) | Q(org__isnull=True))
    return qs.filter(org__isnull=True)


# ─── Location Area ────────────────────────────────────────────────────────────

class LocationAreaViewSet(ActionCapabilityMixin, ModelViewSet):
    queryset = LocationArea.objects.select_related('parent').order_by('name')
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['area_type', 'parent', 'is_active']
    search_fields = ['name', 'code', 'state_name']

    action_required_capabilities = {
        'list':           'wage.read',
        'retrieve':       'wage.read',
        'create':         'wage.create',
        'update':         'wage.update',
        'partial_update': 'wage.update',
        'destroy':        'wage.delete',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return LocationAreaWriteSerializer
        return LocationAreaSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        out = LocationAreaSerializer(instance, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        out = LocationAreaSerializer(instance, context={'request': request})
        return Response(out.data)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])


# ─── Wage Category ────────────────────────────────────────────────────────────

class WageCategoryViewSet(ActionCapabilityMixin, ModelViewSet):
    queryset = WageCategory.objects.all().order_by('name')
    permission_classes = [IsAuthenticated, HasCapability]
    search_fields = ['name', 'code']

    action_required_capabilities = {
        'list':           'wage.read',
        'retrieve':       'wage.read',
        'create':         'wage.create',
        'update':         'wage.update',
        'partial_update': 'wage.update',
        'destroy':        'wage.delete',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return WageCategoryWriteSerializer
        return WageCategorySerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        out = WageCategorySerializer(instance, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        out = WageCategorySerializer(instance, context={'request': request})
        return Response(out.data)

    def perform_destroy(self, instance):
        instance.delete()


# ─── Minimum Wage Rate ────────────────────────────────────────────────────────

class MinimumWageRateViewSet(ActionCapabilityMixin, ModelViewSet):
    permission_classes = [IsAuthenticated, HasCapability]
    filterset_fields = ['org', 'location', 'state', 'city', 'wage_category', 'role', 'is_active']
    search_fields = ['state', 'city', 'source_note', 'wage_category__name', 'wage_category__code', 'role__name', 'role__code']

    action_required_capabilities = {
        'list':           'wage.read',
        'retrieve':       'wage.read',
        'create':         'wage.create',
        'update':         'wage.update',
        'partial_update': 'wage.update',
        'destroy':        'wage.delete',
        'lookup':         'wage.read',
    }

    def get_queryset(self):
        qs = MinimumWageRate.objects.select_related(
            'org', 'location', 'wage_category', 'role',
        ).order_by('-effective_from')
        return _org_filter(qs, self.request.user)

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return MinimumWageRateWriteSerializer
        return MinimumWageRateSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        out = MinimumWageRateSerializer(instance, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        out = MinimumWageRateSerializer(instance, context={'request': request})
        return Response(out.data)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])

    @action(detail=False, methods=['get'], url_path='lookup')
    def lookup(self, request):
        """
        GET /api/wages/rates/lookup/?job_role=<id>&wage_category=<id>&site=<id>&date=YYYY-MM-DD
        Returns the applicable MinimumWageRate for the given parameters.
        """
        from apps.jobs.models import JobRole
        from apps.sites.models import SiteProfile

        wage_category_id = request.query_params.get('wage_category')
        job_role_id = request.query_params.get('job_role')
        site_id = request.query_params.get('site')
        location_id = request.query_params.get('location')
        date_str = request.query_params.get('date')

        if not wage_category_id:
            return Response({'detail': 'wage_category is required.'}, status=400)

        try:
            from .models import WageCategory as WC
            wage_category = WC.objects.get(pk=wage_category_id)
        except WC.DoesNotExist:
            return Response({'detail': 'wage_category not found.'}, status=404)

        on_date = datetime.date.today()
        if date_str:
            try:
                on_date = datetime.date.fromisoformat(date_str)
            except ValueError:
                return Response({'detail': 'date must be YYYY-MM-DD.'}, status=400)

        role = None
        if job_role_id:
            try:
                role = JobRole.objects.get(pk=job_role_id)
            except JobRole.DoesNotExist:
                return Response({'detail': 'job_role not found.'}, status=404)

        site = None
        if site_id:
            try:
                site = SiteProfile.objects.get(pk=site_id)
            except SiteProfile.DoesNotExist:
                return Response({'detail': 'site not found.'}, status=404)

        location = None
        if location_id:
            try:
                location = LocationArea.objects.get(pk=location_id)
            except LocationArea.DoesNotExist:
                return Response({'detail': 'location not found.'}, status=404)

        org = getattr(request.user, 'org', None)

        rate = get_applicable_minimum_wage(
            wage_category=wage_category,
            on_date=on_date,
            site=site,
            location=location,
            role=role,
            org=org,
        )

        if rate is None:
            return Response({'detail': 'No applicable wage rate found.'}, status=404)

        return Response(MinimumWageRateSerializer(rate, context={'request': request}).data)
