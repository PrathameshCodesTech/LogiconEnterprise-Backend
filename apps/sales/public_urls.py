from django.urls import path

from .public_views import PublicProposalDocumentPdfView, PublicProposalResponseView

urlpatterns = [
    path(
        'proposal-response/<str:token>/',
        PublicProposalResponseView.as_view(),
        name='sales-public-proposal-response',
    ),
    path(
        'proposal-response/<str:token>/client-document/pdf/',
        PublicProposalDocumentPdfView.as_view(),
        name='sales-public-proposal-document-pdf',
    ),
]
