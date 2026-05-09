"""
apps/accounts/views.py

User CRUD ViewSet.

Capability map:
  list/retrieve    user.read
  create           user.create
  update           user.update
  partial_update   user.update
  destroy          user.delete  (soft — sets is_active=False)

Scope: users are org-scoped. Only roles that carry user.* capabilities
(admin, hr_admin) will ever reach these endpoints.
"""

from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.access.permissions import HasCapability
from apps.access.querysets import filter_users_for_user
from apps.access.viewsets import ScopedModelViewSet
from apps.audit.services import log_audit

from .models import User, normalize_phone_number
from .serializers import UserListSerializer, UserCreateSerializer, UserUpdateSerializer


class UserViewSet(ScopedModelViewSet):
    queryset = User.objects.select_related('org').order_by('last_name', 'first_name')
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_users_for_user
    filterset_fields = ['org', 'user_type', 'is_active', 'is_invited']
    search_fields = ['username', 'email', 'first_name', 'last_name', 'employee_code', 'phone_number']

    action_required_capabilities = {
        'list':           'user.read',
        'retrieve':       'user.read',
        'create':         'user.create',
        'update':         'user.update',
        'partial_update': 'user.update',
        'destroy':        'user.delete',
    }

    def get_serializer_class(self):
        if self.action == 'create':
            return UserCreateSerializer
        if self.action in ('update', 'partial_update'):
            return UserUpdateSerializer
        return UserListSerializer

    def perform_create(self, serializer):
        actor = self.request.user
        org = serializer.validated_data.get('org') if actor.is_superuser else actor.org
        if not org:
            raise ValidationError({'org': 'Organization is required.'})
        # Pre-validate phone uniqueness (DB constraint fires too late to return 400)
        phone = serializer.validated_data.get('phone_number', '')
        if phone:
            phone_norm = normalize_phone_number(phone)
            if phone_norm and User.objects.filter(org=org, phone_normalized=phone_norm).exists():
                raise ValidationError(
                    {'phone_number': 'This phone number is already registered in this organization.'}
                )
        employee_code = serializer.validated_data.get('employee_code', '')
        if employee_code and User.objects.filter(org=org, employee_code=employee_code).exists():
            raise ValidationError(
                {'employee_code': 'This employee code is already registered in this organization.'}
            )
        user = serializer.save(org=org)
        log_audit(actor, 'user.create', user, org=org, request=self.request)

    def perform_update(self, serializer):
        user = self.get_object()
        phone = serializer.validated_data.get('phone_number', None)
        if phone is not None:
            phone_norm = normalize_phone_number(phone)
            if phone_norm and User.objects.filter(
                org=user.org,
                phone_normalized=phone_norm,
            ).exclude(pk=user.pk).exists():
                raise ValidationError(
                    {'phone_number': 'This phone number is already registered in this organization.'}
                )
        employee_code = serializer.validated_data.get('employee_code', None)
        if employee_code:
            if User.objects.filter(org=user.org, employee_code=employee_code).exclude(pk=user.pk).exists():
                raise ValidationError(
                    {'employee_code': 'This employee code is already registered in this organization.'}
                )
        user = serializer.save()
        log_audit(self.request.user, 'user.update', user, request=self.request)

    def perform_destroy(self, instance):
        log_audit(self.request.user, 'user.delete', instance, request=self.request)
        instance.is_active = False
        instance.save(update_fields=['is_active'])
