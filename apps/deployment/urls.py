from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DeploymentHistoryViewSet,
    EmployeeViewSet,
    SiteDeploymentViewSet,
)

router = DefaultRouter()
router.register('employees', EmployeeViewSet, basename='employee')
router.register('site-deployments', SiteDeploymentViewSet, basename='site-deployment')
router.register('history', DeploymentHistoryViewSet, basename='deployment-history')

urlpatterns = [
    path('', include(router.urls)),
]
