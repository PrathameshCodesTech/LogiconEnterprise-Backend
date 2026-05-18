"""
apps/access/viewsets.py

Scoped ViewSet base classes.
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet, ModelViewSet


class ReadAfterWriteMixin:
    """
    Returns read-serializer data in create (201) and update (200) responses.

    Set `read_serializer_class` on the ViewSet to the full read serializer.
    The ViewSet's `get_serializer_class` should return the write serializer
    for create/update actions.
    """
    read_serializer_class = None

    def create(self, request, *args, **kwargs):
        write_ser = self.get_serializer(data=request.data)
        write_ser.is_valid(raise_exception=True)
        self.perform_create(write_ser)
        headers = self.get_success_headers(write_ser.data)
        read_ser = self.read_serializer_class(
            write_ser.instance, context=self.get_serializer_context()
        )
        return Response(read_ser.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        write_ser = self.get_serializer(instance, data=request.data, partial=partial)
        write_ser.is_valid(raise_exception=True)
        self.perform_update(write_ser)
        read_ser = self.read_serializer_class(
            write_ser.instance, context=self.get_serializer_context()
        )
        return Response(read_ser.data)


class ActionCapabilityMixin:
    """
    Mixin that maps HTTP actions to capability strings.

    Declare `action_required_capabilities` on a ViewSet:
        action_required_capabilities = {
            'list':           'client.read',
            'retrieve':       'client.read',
            'create':         'client.create',
            'update':         'client.update',
            'partial_update': 'client.update',
            'destroy':        'client.delete',
        }

    HasCapability will call view.get_required_capability() to resolve
    the correct capability for the current action.
    """
    action_required_capabilities = {}

    def get_required_capability(self):
        return self.action_required_capabilities.get(self.action)


class ScopedQuerysetMixin:
    """
    Mixin that applies a scope filter to get_queryset().

    Declare `scope_filter` as a callable(queryset, user) → queryset.
    If not declared, non-superusers receive an empty queryset.
    """

    scope_filter = None

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user

        if user.is_superuser:
            return qs

        # Bypass descriptor protocol: accessing a plain function via self.attr
        # would bind self as the first argument. Look it up in the class MRO directly.
        scope_fn = None
        for klass in type(self).__mro__:
            if 'scope_filter' in klass.__dict__:
                scope_fn = klass.__dict__['scope_filter']
                break

        if scope_fn is not None:
            return scope_fn(qs, user)

        return qs.none()


class ScopedReadOnlyModelViewSet(ScopedQuerysetMixin, ReadOnlyModelViewSet):
    """Read-only ViewSet with automatic scope filtering."""
    pass


class ScopedModelViewSet(ActionCapabilityMixin, ScopedQuerysetMixin, ModelViewSet):
    """
    Full CRUD ViewSet with automatic scope filtering and per-action capability enforcement.

    Combine with HasCapability permission class and declare action_required_capabilities.
    """
    pass
