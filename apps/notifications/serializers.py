from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source='actor.username', read_only=True, default=None)
    recipient_username = serializers.CharField(source='recipient.username', read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id', 'org', 'recipient', 'recipient_username', 'actor',
            'actor_username', 'title', 'message', 'notification_type',
            'target_type', 'target_id', 'target_url', 'metadata',
            'is_read', 'read_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

