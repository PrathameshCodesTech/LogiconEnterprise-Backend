from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    SalesLeadViewSet,
    SalesLeadSiteViewSet,
    SiteSurveyViewSet,
    SalesRoleRequirementViewSet,
    ProposalVersionViewSet,
    ProposalBudgetLineViewSet,
    ProposalBreakupLineViewSet,
    ClientProposalResponseViewSet,
    SalesLeadActivityViewSet,
    SalesDocumentViewSet,
    SiteSurveyScopeAnswerViewSet,
    SiteSurveyShiftDeploymentViewSet,
    SiteSurveyLocationLineViewSet,
    SiteSurveyEquipmentLineViewSet,
    SiteSurveyIssueLineViewSet,
    SurveyRoleMappingViewSet,
    ProposalComponentRuleViewSet,
    SalesDashboardSummaryView,
)

router = DefaultRouter()
router.register('leads', SalesLeadViewSet, basename='sales-lead')
router.register('lead-sites', SalesLeadSiteViewSet, basename='sales-lead-site')
router.register('site-surveys', SiteSurveyViewSet, basename='site-survey')
router.register('role-requirements', SalesRoleRequirementViewSet, basename='sales-role-requirement')
router.register('proposal-versions', ProposalVersionViewSet, basename='proposal-version')
router.register('proposal-budget-lines', ProposalBudgetLineViewSet, basename='proposal-budget-line')
router.register('proposal-breakup-lines', ProposalBreakupLineViewSet, basename='proposal-breakup-line')
router.register('client-responses', ClientProposalResponseViewSet, basename='client-proposal-response')
router.register('activities', SalesLeadActivityViewSet, basename='sales-activity')
router.register('documents', SalesDocumentViewSet, basename='sales-document')
router.register('site-survey-scope-answers', SiteSurveyScopeAnswerViewSet, basename='survey-scope-answer')
router.register('site-survey-shift-deployments', SiteSurveyShiftDeploymentViewSet, basename='survey-shift-deployment')
router.register('site-survey-location-lines', SiteSurveyLocationLineViewSet, basename='survey-location-line')
router.register('site-survey-equipment-lines', SiteSurveyEquipmentLineViewSet, basename='survey-equipment-line')
router.register('site-survey-issue-lines', SiteSurveyIssueLineViewSet, basename='survey-issue-line')
router.register('survey-role-mappings', SurveyRoleMappingViewSet, basename='survey-role-mapping')
router.register('proposal-component-rules', ProposalComponentRuleViewSet, basename='proposal-component-rule')

urlpatterns = [
    path('public/', include('apps.sales.public_urls')),
    path(
        'dashboard/summary/',
        SalesDashboardSummaryView.as_view(),
        name='sales-dashboard-summary',
    ),
] + router.urls
