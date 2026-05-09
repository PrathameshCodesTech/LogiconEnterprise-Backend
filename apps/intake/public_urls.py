from django.urls import path
from .public_views import PublicCampaignDetailView, PublicSubmissionCreateView

urlpatterns = [
    path('campaigns/<str:token>/', PublicCampaignDetailView.as_view(), name='public-campaign-detail'),
    path('submissions/', PublicSubmissionCreateView.as_view(), name='public-submission-create'),
]
