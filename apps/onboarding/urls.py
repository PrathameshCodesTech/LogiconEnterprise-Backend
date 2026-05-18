from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ClientOnboardingRequestViewSet,
    ProposedBudgetViewSet,
    ProposedSiteViewSet,
    ProposedDepartmentViewSet,
    ProposedSiteRoleRequirementViewSet,
    ProposedUserViewSet,
)

router = DefaultRouter()
router.register(r'client-requests', ClientOnboardingRequestViewSet, basename='onboarding-client-request')

_ps = ProposedSiteViewSet
_pd = ProposedDepartmentViewSet
_pr = ProposedSiteRoleRequirementViewSet
_pb = ProposedBudgetViewSet
_pu = ProposedUserViewSet

urlpatterns = router.urls + [
    # Proposed sites
    path(
        'client-requests/<int:request_pk>/proposed-sites/',
        _ps.as_view({'get': 'list', 'post': 'create'}),
        name='proposed-sites-list',
    ),
    path(
        'client-requests/<int:request_pk>/proposed-sites/<int:pk>/',
        _ps.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='proposed-sites-detail',
    ),
    # Proposed departments
    path(
        'client-requests/<int:request_pk>/proposed-departments/',
        _pd.as_view({'get': 'list', 'post': 'create'}),
        name='proposed-departments-list',
    ),
    path(
        'client-requests/<int:request_pk>/proposed-departments/<int:pk>/',
        _pd.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='proposed-departments-detail',
    ),
    # Proposed role requirements
    path(
        'client-requests/<int:request_pk>/proposed-role-requirements/',
        _pr.as_view({'get': 'list', 'post': 'create'}),
        name='proposed-role-requirements-list',
    ),
    path(
        'client-requests/<int:request_pk>/proposed-role-requirements/<int:pk>/',
        _pr.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='proposed-role-requirements-detail',
    ),
    # Proposed budgets
    path(
        'client-requests/<int:request_pk>/proposed-budgets/',
        _pb.as_view({'get': 'list', 'post': 'create'}),
        name='proposed-budgets-list',
    ),
    path(
        'client-requests/<int:request_pk>/proposed-budgets/<int:pk>/',
        _pb.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='proposed-budgets-detail',
    ),
    # Proposed users
    path(
        'client-requests/<int:request_pk>/proposed-users/',
        _pu.as_view({'get': 'list', 'post': 'create'}),
        name='proposed-users-list',
    ),
    path(
        'client-requests/<int:request_pk>/proposed-users/<int:pk>/',
        _pu.as_view({'get': 'retrieve', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='proposed-users-detail',
    ),
    path(
        'client-requests/<int:request_pk>/proposed-users/<int:pk>/resend-invite/',
        _pu.as_view({'post': 'resend_invite'}),
        name='proposed-users-resend-invite',
    ),
]
