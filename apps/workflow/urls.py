from django.urls import path

from .views import (
    StartMRFWorkflowView,
    MRFWorkflowConfigCheckView,
    StartClientOnboardingWorkflowView,
    ClientOnboardingWorkflowConfigCheckView,
    WorkflowInstanceDetailView,
    ActOnStepView,
    ReassignStepView,
)

urlpatterns = [
    # MRF workflow
    path('mrf/<int:mrf_id>/start/', StartMRFWorkflowView.as_view(), name='workflow-mrf-start'),
    path('mrf/<int:mrf_id>/config-check/', MRFWorkflowConfigCheckView.as_view(), name='workflow-mrf-config-check'),

    # Client onboarding workflow
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
]
