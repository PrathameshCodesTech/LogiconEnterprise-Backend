from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MeView, OrganizationViewSet, ScopeNodeViewSet, DepartmentViewSet

router = DefaultRouter()
router.register('organizations', OrganizationViewSet, basename='organization')
router.register('scope-nodes', ScopeNodeViewSet, basename='scope-node')
router.register('departments', DepartmentViewSet, basename='department')

urlpatterns = [
    path('me/', MeView.as_view(), name='me'),
    path('', include(router.urls)),
]
