from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import BudgetPlanViewSet

router = DefaultRouter()
router.register('plans', BudgetPlanViewSet, basename='budget-plan')

urlpatterns = [
    path('', include(router.urls)),
]
