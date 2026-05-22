from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    QRCampaignViewSet,
    CampaignJobRoleViewSet,
    FormFieldViewSet,
    FormTemplateViewSet,
    FormSectionViewSet,
    FormTemplateFieldViewSet,
    IntakeSubmissionViewSet,
    CampaignQRCodeView,
)

router = DefaultRouter()
router.register('campaigns', QRCampaignViewSet, basename='qr-campaign')
router.register('campaign-job-roles', CampaignJobRoleViewSet, basename='campaign-job-role')
router.register('form-fields', FormFieldViewSet, basename='form-field')
router.register('form-templates', FormTemplateViewSet, basename='form-template')
router.register('form-sections', FormSectionViewSet, basename='form-section')
router.register('template-fields', FormTemplateFieldViewSet, basename='template-field')
router.register('submissions', IntakeSubmissionViewSet, basename='intake-submission')

urlpatterns = [
    path('', include(router.urls)),
    path('campaigns/<int:pk>/qrcode/', CampaignQRCodeView.as_view(), name='campaign-qrcode'),
]
