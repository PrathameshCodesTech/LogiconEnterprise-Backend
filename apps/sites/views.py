"""
apps/sites/views.py

Client, Site, SiteCommercial, SiteRoleRequirement ViewSets.

All read endpoints use scope filtering (ScopedQuerysetMixin).
Write endpoints additionally enforce per-action capabilities and scope checks.
"""

from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.access.permissions import HasCapability
from apps.access.querysets import (
    filter_clients_for_user,
    filter_sites_for_user,
    filter_site_role_requirements_for_user,
)
from apps.access.scope import actor_can_access_scope
from apps.access.viewsets import ScopedModelViewSet, ScopedReadOnlyModelViewSet
from apps.audit.services import log_audit
from apps.core.models import ScopeNode

from .models import Client, SiteProfile, SiteCommercial, SiteRoleRequirement
from .serializers import (
    ClientSerializer,
    ClientWriteSerializer,
    SiteProfileSerializer,
    SiteProfileWriteSerializer,
    SiteCommercialSerializer,
    SiteRoleRequirementSerializer,
    SiteRoleRequirementWriteSerializer,
)
from .services import _node_code_from, create_client_with_scope, create_site_with_scope


class ClientViewSet(ScopedModelViewSet):
    queryset = Client.objects.select_related(
        'org', 'scope_node', 'created_by', 'owner_sales_user',
    ).order_by('name')
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_clients_for_user
    filterset_fields = ['org', 'is_active', 'industry']
    search_fields = ['name', 'code', 'contact_name']

    action_required_capabilities = {
        'list':           'client.read',
        'retrieve':       'client.read',
        'create':         'client.create',
        'update':         'client.update',
        'partial_update': 'client.update',
        'destroy':        'client.delete',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ClientWriteSerializer
        return ClientSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        actor = request.user
        org = serializer.validated_data.get('org') if actor.is_superuser else actor.org
        if not org:
            raise ValidationError({"detail": "Your account has no organization set."})

        company_node = ScopeNode.objects.filter(org=org, node_type='company').first()
        if not company_node:
            raise ValidationError({"detail": "Organization has no company scope node."})

        if not actor.is_superuser and not actor_can_access_scope(actor, company_node):
            raise PermissionDenied("You do not have scope access to create clients.")

        code = serializer.validated_data['code']
        if Client.objects.filter(org=org, code=code).exists():
            raise ValidationError({'code': 'A client with this code already exists in this organization.'})
        node_code = _node_code_from(code)
        if not node_code:
            raise ValidationError({'code': 'Code must contain at least one letter or number.'})
        if ScopeNode.objects.filter(org=org, parent=company_node, code=node_code).exists():
            raise ValidationError({'code': 'This code conflicts with an existing scope under the company.'})

        client = create_client_with_scope(
            org=org,
            name=serializer.validated_data['name'],
            code=serializer.validated_data['code'],
            created_by=actor,
            parent_scope_node=company_node,
            **{k: v for k, v in serializer.validated_data.items()
               if k not in ('org', 'name', 'code')},
        )
        log_audit(actor, 'client.create', client, request=request)
        out = ClientSerializer(client, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        client = serializer.save()
        log_audit(self.request.user, 'client.update', client, request=self.request)

    def perform_destroy(self, instance):
        log_audit(self.request.user, 'client.delete', instance, request=self.request)
        instance.is_active = False
        instance.save(update_fields=['is_active'])


class SiteProfileViewSet(ScopedModelViewSet):
    queryset = SiteProfile.objects.select_related(
        'org', 'client', 'client__scope_node', 'scope_node', 'created_by',
    ).order_by('name')
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_sites_for_user
    filterset_fields = ['org', 'client', 'is_active', 'shift_type', 'city', 'state']
    search_fields = ['name', 'code', 'city', 'state']

    action_required_capabilities = {
        'list':           'site.read',
        'retrieve':       'site.read',
        'create':         'site.create',
        'update':         'site.update',
        'partial_update': 'site.update',
        'destroy':        'site.delete',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return SiteProfileWriteSerializer
        return SiteProfileSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        actor = request.user
        client = serializer.validated_data['client']
        org = client.org
        if not org:
            raise ValidationError({"client": "Client has no organization set."})
        if not actor.is_superuser and actor.org_id != client.org_id:
            raise PermissionDenied("You cannot create a site under a client from another organization.")
        if not client.scope_node:
            raise ValidationError({"client": "Client has no scope node assigned."})

        if not actor.is_superuser and not actor_can_access_scope(actor, client.scope_node):
            raise PermissionDenied("You do not have scope access to create a site under this client.")

        code = serializer.validated_data['code']
        if SiteProfile.objects.filter(org=org, code=code).exists():
            raise ValidationError({'code': 'A site with this code already exists in this organization.'})
        node_code = _node_code_from(code)
        if not node_code:
            raise ValidationError({'code': 'Code must contain at least one letter or number.'})
        if ScopeNode.objects.filter(org=org, parent=client.scope_node, code=node_code).exists():
            raise ValidationError({'code': 'This code conflicts with an existing site scope under the client.'})

        site = create_site_with_scope(
            org=org,
            client=client,
            name=serializer.validated_data['name'],
            code=serializer.validated_data['code'],
            created_by=actor,
            **{k: v for k, v in serializer.validated_data.items()
               if k not in ('client', 'name', 'code')},
        )
        log_audit(actor, 'site.create', site, request=request)
        out = SiteProfileSerializer(site, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        site = serializer.save()
        log_audit(self.request.user, 'site.update', site, request=self.request)

    def perform_destroy(self, instance):
        log_audit(self.request.user, 'site.delete', instance, request=self.request)
        instance.is_active = False
        instance.save(update_fields=['is_active'])


class SiteCommercialViewSet(ScopedReadOnlyModelViewSet):
    queryset = SiteCommercial.objects.filter(is_active=True).select_related(
        'site', 'site__scope_node', 'site__client__scope_node',
    )
    serializer_class = SiteCommercialSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'site.read'
    filterset_fields = ['site', 'is_active']

    def get_queryset(self):
        qs = SiteCommercial.objects.filter(is_active=True).select_related(
            'site', 'site__scope_node', 'site__client__scope_node',
        )
        user = self.request.user
        if user.is_superuser:
            return qs
        from apps.access.querysets import _scope_q, get_accessible_scope_paths
        paths = get_accessible_scope_paths(user)
        if not paths:
            return qs.none()
        site_q = _scope_q('site__scope_node__path', paths)
        client_q = _scope_q('site__client__scope_node__path', paths)
        return qs.filter(site_q | client_q).distinct()


class SiteRoleRequirementViewSet(ScopedModelViewSet):
    queryset = SiteRoleRequirement.objects.select_related(
        'site', 'site__scope_node', 'site__client__scope_node',
        'job_role', 'wage_category',
    ).order_by('site', 'job_role')
    permission_classes = [IsAuthenticated, HasCapability]
    scope_filter = filter_site_role_requirements_for_user
    filterset_fields = ['site', 'job_role', 'billing_type', 'is_active']
    search_fields = ['site__name', 'job_role__name']

    action_required_capabilities = {
        'list':           'site_role_requirement.read',
        'retrieve':       'site_role_requirement.read',
        'create':         'site_role_requirement.create',
        'update':         'site_role_requirement.update',
        'partial_update': 'site_role_requirement.update',
        'destroy':        'site_role_requirement.delete',
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return SiteRoleRequirementWriteSerializer
        return SiteRoleRequirementSerializer

    def perform_create(self, serializer):
        actor = self.request.user
        site = serializer.validated_data['site']
        if site.scope_node and not actor.is_superuser:
            if not actor_can_access_scope(actor, site.scope_node):
                raise PermissionDenied(
                    "You do not have scope access to create requirements for this site."
                )
        req = serializer.save()
        log_audit(actor, 'site_role_requirement.create', req, request=self.request)

    def perform_update(self, serializer):
        req = serializer.save()
        log_audit(self.request.user, 'site_role_requirement.update', req, request=self.request)

    def perform_destroy(self, instance):
        log_audit(self.request.user, 'site_role_requirement.delete', instance, request=self.request)
        instance.is_active = False
        instance.save(update_fields=['is_active'])
