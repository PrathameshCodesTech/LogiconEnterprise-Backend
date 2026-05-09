from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ModuleActivationViewSet

router = DefaultRouter()
router.register('activations', ModuleActivationViewSet, basename='module-activation')

urlpatterns = [
    path('', include(router.urls)),
]
