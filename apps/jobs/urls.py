from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import JobRoleViewSet

router = DefaultRouter()
router.register('roles', JobRoleViewSet, basename='job-role')

urlpatterns = [
    path('', include(router.urls)),
]
