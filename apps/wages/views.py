from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import WageCategory, MinimumWageRate
from .serializers import WageCategorySerializer, MinimumWageRateSerializer


class WageCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WageCategory.objects.all()
    serializer_class = WageCategorySerializer
    permission_classes = [IsAuthenticated]
    search_fields = ['name', 'code']


class MinimumWageRateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MinimumWageRate.objects.filter(is_active=True)
    serializer_class = MinimumWageRateSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['state', 'city', 'wage_category', 'is_active']
    search_fields = ['state', 'city']
