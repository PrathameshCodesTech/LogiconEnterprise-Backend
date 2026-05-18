"""
Logicon ATS Enterprise Backend — Root URL Configuration
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.views import EmailTokenObtainPairView

urlpatterns = [
    # Django Admin
    path('lms-console/', admin.site.urls),

    # JWT Authentication (email + password)
    path('api/token/', EmailTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # App APIs
    path('api/accounts/', include('apps.accounts.urls')),
    path('api/core/', include('apps.core.urls')),
    path('api/modules/', include('apps.modules.urls')),
    path('api/jobs/', include('apps.jobs.urls')),
    path('api/sites/', include('apps.sites.urls')),
    path('api/wages/', include('apps.wages.urls')),
    path('api/intake/', include('apps.intake.urls')),
    path('api/public/', include('apps.intake.public_urls')),
    path('api/talent/', include('apps.talent.urls')),
    path('api/mrf/', include('apps.mrf.urls')),
    path('api/onboarding/', include('apps.onboarding.urls')),
    path('api/workflow/', include('apps.workflow.urls')),
    path('api/audit/', include('apps.audit.urls')),
    path('api/access/', include('apps.access.urls')),
    path('api/hiring/', include('apps.hiring.urls')),
    path('api/deployment/', include('apps.deployment.urls')),
    path('api/budgets/', include('apps.budgets.urls')),
    path('api/dashboard/', include('apps.dashboard.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
