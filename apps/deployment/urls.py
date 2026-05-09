from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import EmployeeViewSet, SiteDeploymentViewSet

router = DefaultRouter()
router.register('employees', EmployeeViewSet, basename='employee')
router.register('site-deployments', SiteDeploymentViewSet, basename='site-deployment')

urlpatterns = [
    path('', include(router.urls)),
]
