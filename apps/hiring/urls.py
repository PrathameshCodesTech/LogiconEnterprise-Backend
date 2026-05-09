from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    HiringApplicationViewSet,
    InterviewViewSet,
    InterviewFeedbackViewSet,
    OfferViewSet,
)

router = DefaultRouter()
router.register('applications', HiringApplicationViewSet, basename='hiring-application')
router.register('interviews', InterviewViewSet, basename='interview')
router.register('interview-feedbacks', InterviewFeedbackViewSet, basename='interview-feedback')
router.register('offers', OfferViewSet, basename='offer')

urlpatterns = [
    path('', include(router.urls)),
]
