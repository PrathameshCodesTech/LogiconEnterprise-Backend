from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    AvailableRoutesView,
    StartMRFWorkflowView,
    MRFWorkflowConfigCheckView,
    StartClientOnboardingWorkflowView,
    ClientOnboardingWorkflowConfigCheckView,
    StartSalesProposalWorkflowView,
    MyWorkflowTasksView,
    MyWorkflowTaskDetailView,
    WorkflowInstanceDetailView,
    ActOnStepView,
    ReassignStepView,
)
from .config_views import (
    ApprovalFlowViewSet,
    ApprovalStepViewSet,
    FlowRuleViewSet,
    AssignmentConfigViewSet,
    ConfigPreviewView,
)

# Config router
_config_router = DefaultRouter()
_config_router.register('config/flows', ApprovalFlowViewSet, basename='config-flow')
_config_router.register('config/steps', ApprovalStepViewSet, basename='config-step')
_config_router.register('config/rules', FlowRuleViewSet, basename='config-rule')
_config_router.register('config/assignments', AssignmentConfigViewSet, basename='config-assignment')

urlpatterns = [
    path('routes/available/', AvailableRoutesView.as_view(), name='workflow-routes-available'),
    path('my-tasks/', MyWorkflowTasksView.as_view(), name='workflow-my-tasks'),
    path(
        'my-tasks/<int:step_id>/',
        MyWorkflowTaskDetailView.as_view(),
        name='workflow-my-task-detail',
    ),

    # MRF workflow
    path('mrf/<int:mrf_id>/start/', StartMRFWorkflowView.as_view(), name='workflow-mrf-start'),
    path('mrf/<int:mrf_id>/config-check/', MRFWorkflowConfigCheckView.as_view(), name='workflow-mrf-config-check'),

    # Mobilisation workflow (legacy URL segment client-onboarding)
    path(
        'client-onboarding/<int:onboarding_id>/start/',
        StartClientOnboardingWorkflowView.as_view(),
        name='workflow-onboarding-start',
    ),
    path(
        'client-onboarding/<int:onboarding_id>/config-check/',
        ClientOnboardingWorkflowConfigCheckView.as_view(),
        name='workflow-onboarding-config-check',
    ),

    # Sales proposal internal approval workflow
    path(
        'sales-proposals/<int:proposal_version_id>/start/',
        StartSalesProposalWorkflowView.as_view(),
        name='workflow-sales-proposal-start',
    ),

    # Shared instance endpoints
    path('instances/<int:instance_id>/', WorkflowInstanceDetailView.as_view(), name='workflow-instance-detail'),
    path(
        'instances/<int:instance_id>/steps/<int:step_id>/act/',
        ActOnStepView.as_view(),
        name='workflow-step-act',
    ),
    path(
        'instances/<int:instance_id>/steps/<int:step_id>/reassign/',
        ReassignStepView.as_view(),
        name='workflow-step-reassign',
    ),

    # Config (approval setup) — router + preview
    path('', include(_config_router.urls)),
    path('config/preview/', ConfigPreviewView.as_view(), name='config-preview'),
]
