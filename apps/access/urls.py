from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    AccessRoleViewSet,
    AccessRolePermissionViewSet,
    PermissionViewSet,
    UserRoleAssignmentViewSet,
    UserScopeAssignmentViewSet,
)

router = DefaultRouter()
router.register('roles', AccessRoleViewSet, basename='access-role')
router.register('permissions', PermissionViewSet, basename='permission')
router.register('role-permissions', AccessRolePermissionViewSet, basename='role-permission')
router.register('user-role-assignments', UserRoleAssignmentViewSet, basename='user-role-assignment')
router.register('user-scope-assignments', UserScopeAssignmentViewSet, basename='user-scope-assignment')

urlpatterns = [
    path('', include(router.urls)),
]
