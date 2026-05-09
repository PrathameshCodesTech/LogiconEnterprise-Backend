"""
apps/sites/services.py

Atomic creation helpers for entities that require a paired ScopeNode.
Each function wraps DB writes in a transaction so partial states cannot persist.
"""

import re

from django.db import transaction

from apps.core.models import ScopeNode

from .models import Client, SiteProfile


def _node_code_from(code: str) -> str:
    """Derive a safe scope-node code from an entity code."""
    value = re.sub(r'[^a-z0-9-]+', '-', (code or '').strip().lower())
    value = re.sub(r'-+', '-', value).strip('-')
    return value


def create_client_with_scope(
    org,
    name: str,
    code: str,
    created_by,
    parent_scope_node: ScopeNode,
    **kwargs,
) -> Client:
    """
    Atomically create a Client and its associated ScopeNode.

    parent_scope_node — the company (or region) node under which the client sits.
    kwargs            — extra Client fields (contact_name, gst_number, etc.)
    """
    with transaction.atomic():
        node_code = _node_code_from(code)
        scope_node = ScopeNode.objects.create(
            org=org,
            code=node_code,
            name=name,
            node_type='client',
            parent=parent_scope_node,
            depth=parent_scope_node.depth + 1,
            path=f"{parent_scope_node.path}/{node_code}",
            is_active=True,
        )
        client = Client.objects.create(
            org=org,
            name=name,
            code=code,
            scope_node=scope_node,
            created_by=created_by,
            **kwargs,
        )
    return client


def create_site_with_scope(
    org,
    client: Client,
    name: str,
    code: str,
    created_by,
    **kwargs,
) -> SiteProfile:
    """
    Atomically create a SiteProfile and its ScopeNode under client.scope_node.

    The site path = client.scope_node.path / site-code.
    """
    if not client.scope_node:
        raise ValueError(
            f"Client '{client.code}' has no scope_node. "
            "Create the client via create_client_with_scope() first."
        )
    with transaction.atomic():
        node_code = _node_code_from(code)
        parent_node = client.scope_node
        scope_node = ScopeNode.objects.create(
            org=org,
            code=node_code,
            name=name,
            node_type='site',
            parent=parent_node,
            depth=parent_node.depth + 1,
            path=f"{parent_node.path}/{node_code}",
            is_active=True,
        )
        site = SiteProfile.objects.create(
            org=org,
            client=client,
            scope_node=scope_node,
            name=name,
            code=code,
            created_by=created_by,
            **kwargs,
        )
    return site
