from django.urls import path

from .public_views import PublicProposalResponseView

urlpatterns = [
    path(
        'proposal-response/<str:token>/',
        PublicProposalResponseView.as_view(),
        name='sales-public-proposal-response',
    ),
]
