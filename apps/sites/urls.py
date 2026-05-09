from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ClientViewSet, SiteProfileViewSet, SiteCommercialViewSet, SiteRoleRequirementViewSet

router = DefaultRouter()
router.register('clients', ClientViewSet, basename='client')
router.register('profiles', SiteProfileViewSet, basename='site-profile')
router.register('commercials', SiteCommercialViewSet, basename='site-commercial')
router.register('role-requirements', SiteRoleRequirementViewSet, basename='site-role-requirement')

urlpatterns = [
    path('', include(router.urls)),
]
