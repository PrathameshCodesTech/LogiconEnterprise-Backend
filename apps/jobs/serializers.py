from rest_framework import serializers

from apps.core.models import Organization
from .models import JobRole


class JobRoleSerializer(serializers.ModelSerializer):
    """Read serializer."""
    skill_category_display = serializers.CharField(source='get_skill_category_display', read_only=True)
    hiring_lane_display = serializers.CharField(source='get_hiring_lane_display', read_only=True)

    class Meta:
        model = JobRole
        fields = [
            'id', 'org', 'name', 'code', 'description',
            'skill_category', 'skill_category_display',
            'hiring_lane', 'hiring_lane_display',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class JobRoleWriteSerializer(serializers.ModelSerializer):
    """Write serializer — org injected by view for non-superusers."""
    org = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = JobRole
        fields = [
            'org', 'name', 'code', 'description',
            'skill_category', 'hiring_lane', 'is_active',
        ]
        # Suppress auto-generated UniqueTogetherValidator for ['org','code'] —
        # org is injected by the view, so the auto-validator would flag it as required.
        validators = []

    def validate_code(self, value):
        return value.strip().lower()

    def validate(self, data):
        request = self.context.get('request')
        # Use org from context (set by view) when not a superuser
        org = data.get('org') or (
            getattr(request.user, 'org', None) if request else None
        )
        code = data.get('code', getattr(self.instance, 'code', None))
        if org and code:
            qs = JobRole.objects.filter(org=org, code=code)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {'code': 'A job role with this code already exists in your organization.'}
                )
        return data
