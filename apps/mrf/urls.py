from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ManpowerRequestViewSet, MRFLineItemViewSet

router = DefaultRouter()
router.register('requests', ManpowerRequestViewSet, basename='mrf')
router.register('line-items', MRFLineItemViewSet, basename='mrf-line-item')

urlpatterns = [
    path('', include(router.urls)),
]
