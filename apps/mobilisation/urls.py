from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    MobilisationSetupRequestViewSet,
    ScopedProposedDepartmentViewSet,
    ScopedProposedUserViewSet,
    ProposedDepartmentViewSet,
    ProposedDepartmentRoleViewSet,
    ProposedUserViewSet,
)

router = DefaultRouter()
router.register(r'setup-requests', MobilisationSetupRequestViewSet, basename='mobilisation-setup-request')
router.register(
    r'proposed-departments', ScopedProposedDepartmentViewSet,
    basename='mobilisation-proposed-department',
)
router.register(
    r'proposed-users', ScopedProposedUserViewSet,
    basename='mobilisation-proposed-user',
)

_pd = ProposedDepartmentViewSet
_pdr = ProposedDepartmentRoleViewSet
_pu = ProposedUserViewSet

urlpatterns = router.urls + [
    # Proposed departments
    path(
        'setup-requests/<int:request_pk>/proposed-departments/',
        _pd.as_view({'get': 'list', 'post': 'create'}),
        name='mobilisation-proposed-departments-list',
    ),
    path(
        'setup-requests/<int:request_pk>/proposed-departments/<int:pk>/',
        _pd.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='mobilisation-proposed-departments-detail',
    ),
    # Proposed department role mappings
    path(
        'setup-requests/<int:request_pk>/proposed-department-roles/',
        _pdr.as_view({'get': 'list', 'post': 'create'}),
        name='mobilisation-proposed-department-roles-list',
    ),
    path(
        'setup-requests/<int:request_pk>/proposed-department-roles/<int:pk>/',
        _pdr.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='mobilisation-proposed-department-roles-detail',
    ),
    # Proposed users
    path(
        'setup-requests/<int:request_pk>/proposed-users/',
        _pu.as_view({'get': 'list', 'post': 'create'}),
        name='mobilisation-proposed-users-list',
    ),
    path(
        'setup-requests/<int:request_pk>/proposed-users/<int:pk>/',
        _pu.as_view({'get': 'retrieve', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='mobilisation-proposed-users-detail',
    ),
    path(
        'setup-requests/<int:request_pk>/proposed-users/<int:pk>/resend-invite/',
        _pu.as_view({'post': 'resend_invite'}),
        name='mobilisation-proposed-users-resend-invite',
    ),
]
