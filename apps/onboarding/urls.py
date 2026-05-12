from rest_framework.routers import DefaultRouter

from .views import ClientOnboardingRequestViewSet

router = DefaultRouter()
router.register(r'client-requests', ClientOnboardingRequestViewSet, basename='onboarding-client-request')

urlpatterns = router.urls
