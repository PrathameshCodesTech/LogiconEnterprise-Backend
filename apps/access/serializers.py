from rest_framework import serializers

from .models import AccessRole, Permission, AccessRolePermission, UserScopeAssignment, UserRoleAssignment


class AccessRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccessRole
        fields = ['id', 'org', 'name', 'code', 'node_type_scope', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']


class AccessRoleWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccessRole
        fields = ['org', 'name', 'code', 'node_type_scope', 'is_active']
        validators = []
        extra_kwargs = {
            'org': {'required': False, 'allow_null': True},
        }

    def validate_code(self, value):
        return value.strip().lower()


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ['id', 'code', 'resource', 'action', 'description']


class AccessRolePermissionSerializer(serializers.ModelSerializer):
    role_code = serializers.CharField(source='role.code', read_only=True)
    role_name = serializers.CharField(source='role.name', read_only=True)
    permission_code = serializers.CharField(source='permission.code', read_only=True)
    permission_resource = serializers.CharField(source='permission.resource', read_only=True)
    permission_action = serializers.CharField(source='permission.action', read_only=True)
    permission_description = serializers.CharField(source='permission.description', read_only=True)

    class Meta:
        model = AccessRolePermission
        fields = [
            'id', 'role', 'role_code', 'role_name',
            'permission', 'permission_code', 'permission_resource',
            'permission_action', 'permission_description',
        ]


class AccessRolePermissionWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccessRolePermission
        fields = ['role', 'permission']
        validators = []

    def validate(self, data):
        role = data.get('role')
        permission = data.get('permission')
        if role and not role.is_active:
            raise serializers.ValidationError({'role': 'Cannot attach permission to an inactive role.'})
        if role and permission:
            if AccessRolePermission.objects.filter(role=role, permission=permission).exists():
                raise serializers.ValidationError(
                    {'permission': 'This permission is already attached to this role.'}
                )
        return data


class UserScopeAssignmentSerializer(serializers.ModelSerializer):
    scope_node_path = serializers.CharField(source='scope_node.path', read_only=True)

    class Meta:
        model = UserScopeAssignment
        fields = ['id', 'user', 'scope_node', 'scope_node_path', 'assignment_type', 'created_at']
        read_only_fields = ['created_at', 'scope_node_path']


class UserRoleAssignmentSerializer(serializers.ModelSerializer):
    role_code = serializers.CharField(source='role.code', read_only=True)
    role_name = serializers.CharField(source='role.name', read_only=True)
    role_node_type_scope = serializers.CharField(source='role.node_type_scope', read_only=True)
    scope_node_path = serializers.CharField(source='scope_node.path', read_only=True)

    class Meta:
        model = UserRoleAssignment
        fields = [
            'id', 'user', 'role', 'role_code', 'role_name',
            'role_node_type_scope', 'scope_node', 'scope_node_path', 'created_at',
        ]
        read_only_fields = ['created_at', 'role_code', 'role_name', 'role_node_type_scope', 'scope_node_path']

    def validate(self, data):
        role = data.get('role') or (self.instance.role if self.instance else None)
        scope_node = data.get('scope_node') or (self.instance.scope_node if self.instance else None)
        if role and scope_node and role.node_type_scope:
            if role.node_type_scope != scope_node.node_type:
                raise serializers.ValidationError({
                    'scope_node': (
                        f"Role '{role.code}' can only be assigned to nodes of type "
                        f"'{role.node_type_scope}', but got '{scope_node.node_type}'."
                    )
                })
        return data
