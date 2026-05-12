from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LocationAreaViewSet, WageCategoryViewSet, MinimumWageRateViewSet

router = DefaultRouter()
router.register('locations', LocationAreaViewSet, basename='wage-location')
router.register('categories', WageCategoryViewSet, basename='wage-category')
router.register('rates', MinimumWageRateViewSet, basename='wage-rate')

urlpatterns = [
    path('', include(router.urls)),
]
