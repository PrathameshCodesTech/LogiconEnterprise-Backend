from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    HiringApplicationViewSet,
    PipelineStageViewSet,
    HiringDemandViewSet,
    CandidateMatchResultViewSet,
    InterviewViewSet,
    InterviewFeedbackViewSet,
    OfferViewSet,
)

router = DefaultRouter()
router.register('applications', HiringApplicationViewSet, basename='hiring-application')
router.register('pipeline-stages', PipelineStageViewSet, basename='pipeline-stage')
router.register('demands', HiringDemandViewSet, basename='hiring-demand')
router.register('match-results', CandidateMatchResultViewSet, basename='candidate-match-result')
router.register('interviews', InterviewViewSet, basename='interview')
router.register('interview-feedbacks', InterviewFeedbackViewSet, basename='interview-feedback')
router.register('offers', OfferViewSet, basename='offer')

urlpatterns = [
    path('', include(router.urls)),
]
