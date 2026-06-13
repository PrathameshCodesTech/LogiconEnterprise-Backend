from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CandidateViewSet, ResumeViewSet,
    CandidateExperienceViewSet, CandidateEducationViewSet, CandidateSkillViewSet,
    ParsedResumeViewSet,
    ManualResumeIntakeView,
)

router = DefaultRouter()
router.register('candidates', CandidateViewSet, basename='candidate')
router.register('resumes', ResumeViewSet, basename='resume')
router.register('experiences', CandidateExperienceViewSet, basename='candidate-experience')
router.register('educations', CandidateEducationViewSet, basename='candidate-education')
router.register('skills', CandidateSkillViewSet, basename='candidate-skill')
router.register('parsed-resumes', ParsedResumeViewSet, basename='parsed-resume')

urlpatterns = [
    path('', include(router.urls)),
    path('manual-resume-intake/', ManualResumeIntakeView.as_view(), name='manual-resume-intake'),
]
