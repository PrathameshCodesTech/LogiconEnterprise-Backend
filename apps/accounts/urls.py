from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import UserViewSet, SetPasswordView

router = DefaultRouter()
router.register('users', UserViewSet, basename='user')

urlpatterns = [
    path('', include(router.urls)),
    path('set-password/', SetPasswordView.as_view(), name='set-password'),
]
