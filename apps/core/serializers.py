from rest_framework import serializers

from apps.sites.models import Client, SiteProfile
from .models import Organization, ScopeNode, Department


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ['id', 'name', 'code', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']


class ScopeNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScopeNode
        fields = [
            'id', 'org', 'parent', 'name', 'code', 'node_type',
            'path', 'depth', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class DepartmentSerializer(serializers.ModelSerializer):
    """Read serializer — all fields plus convenience name fields."""
    client_name = serializers.CharField(source='client.name', read_only=True, default=None)
    site_name = serializers.CharField(source='site.name', read_only=True, default=None)

    class Meta:
        model = Department
        fields = [
            'id', 'org', 'client', 'client_name', 'site', 'site_name',
            'name', 'code', 'description', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class DepartmentWriteSerializer(serializers.ModelSerializer):
    """Write serializer — org injected by view for non-superusers."""
    org = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.all(),
        required=False,
        allow_null=True,
    )
    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(),
        required=False,
        allow_null=True,
    )
    site = serializers.PrimaryKeyRelatedField(
        queryset=SiteProfile.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Department
        fields = ['org', 'client', 'site', 'name', 'code', 'description', 'is_active']
        # Suppress auto-generated UniqueTogetherValidator — org is injected by view
        validators = []

    def validate_code(self, value):
        return value.strip().lower()

    def validate(self, data):
        request = self.context.get('request')
        org = data.get('org') or (
            getattr(request.user, 'org', None) if request else None
        )
        site = data.get('site')
        client = data.get('client')

        errors = {}

        # Auto-fill client from site
        if site is not None:
            if client is None:
                data['client'] = site.client
                client = site.client
            elif client.pk != site.client_id:
                errors['client'] = (
                    f'Client must match the site\'s client (expected "{site.client}").'
                )

        # Org consistency checks
        if org and client and client.org_id != org.pk:
            errors['client'] = 'Client must belong to the same organization.'
        if org and site and site.org_id != org.pk:
            errors['site'] = 'Site must belong to the same organization.'

        if errors:
            raise serializers.ValidationError(errors)

        # Duplicate active code at same scope
        if org:
            code = data.get('code', getattr(self.instance, 'code', None))
            qs = Department.objects.filter(org=org, code=code, is_active=True)
            if site is not None:
                qs = qs.filter(site=site)
            elif client is not None:
                qs = qs.filter(client=client, site__isnull=True)
            else:
                qs = qs.filter(client__isnull=True, site__isnull=True)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {'code': 'An active department with this code already exists at this scope.'}
                )

        return data
