from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    HiringApplicationViewSet,
    PipelineStageViewSet,
    HiringDemandViewSet,
    CandidateMatchResultViewSet,
    InterviewPlanViewSet,
    InterviewViewSet,
    InterviewFeedbackViewSet,
    OfferViewSet,
    ClientReviewViewSet,
)

router = DefaultRouter()
router.register('applications', HiringApplicationViewSet, basename='hiring-application')
router.register('pipeline-stages', PipelineStageViewSet, basename='pipeline-stage')
router.register('demands', HiringDemandViewSet, basename='hiring-demand')
router.register('match-results', CandidateMatchResultViewSet, basename='candidate-match-result')
router.register('interview-plans', InterviewPlanViewSet, basename='interview-plan')
router.register('interviews', InterviewViewSet, basename='interview')
router.register('interview-feedbacks', InterviewFeedbackViewSet, basename='interview-feedback')
router.register('offers', OfferViewSet, basename='offer')
router.register('client-review', ClientReviewViewSet, basename='client-review')

urlpatterns = [
    path('', include(router.urls)),
]
