"""
apps/mrf/serializers.py

Management serializers for ManpowerRequest and MRFLineItem.

Read serializers   — returned from all endpoints (list/retrieve/create/update responses).
Write serializers  — used for create/update request bodies; org and requested_by are injected
                     by the view, not accepted from the client.
"""

from datetime import date

from rest_framework import serializers

from .models import ManpowerRequest, MRFLineItem


# ─── MRF Line Item ────────────────────────────────────────────────────────────

class MRFLineItemSerializer(serializers.ModelSerializer):
    """Read serializer — all fields read-only."""

    class Meta:
        model = MRFLineItem
        fields = [
            'id', 'mrf', 'site_role_requirement', 'job_role', 'headcount',
            'replacement_for_employee', 'required_skills', 'wage_category',
            'min_wage_snapshot', 'wage_min_requested', 'wage_max_requested',
            'billing_rate_snapshot', 'budget_min', 'budget_max',
        ]
        read_only_fields = fields


class MRFLineItemWriteSerializer(serializers.ModelSerializer):
    """Write serializer for line item create/update."""

    class Meta:
        model = MRFLineItem
        fields = [
            'mrf', 'site_role_requirement', 'job_role', 'headcount',
            'replacement_for_employee', 'required_skills', 'wage_category',
            'wage_min_requested', 'wage_max_requested',
            'billing_rate_snapshot', 'budget_min', 'budget_max',
        ]

    def validate_headcount(self, value):
        if value < 1:
            raise serializers.ValidationError("Headcount must be at least 1.")
        return value

    def validate(self, data):
        instance = self.instance

        # Resolve effective mrf and site_role_requirement (for PATCH partial updates)
        mrf = data.get('mrf') or (instance.mrf if instance else None)
        srr = data.get('site_role_requirement', instance.site_role_requirement if instance else None)

        # site_role_requirement.site must match MRF.site
        if mrf and srr and srr.site_id != mrf.site_id:
            raise serializers.ValidationError({
                'site_role_requirement': (
                    'SiteRoleRequirement must belong to the same site as the MRF.'
                )
            })

        # wage range
        wage_min = data.get('wage_min_requested', instance.wage_min_requested if instance else None)
        wage_max = data.get('wage_max_requested', instance.wage_max_requested if instance else None)
        if wage_min is not None and wage_max is not None and wage_min > wage_max:
            raise serializers.ValidationError({
                'wage_min_requested': 'wage_min_requested cannot exceed wage_max_requested.'
            })

        # budget range
        budget_min = data.get('budget_min', instance.budget_min if instance else None)
        budget_max = data.get('budget_max', instance.budget_max if instance else None)
        if budget_min is not None and budget_max is not None and budget_min > budget_max:
            raise serializers.ValidationError({
                'budget_min': 'budget_min cannot exceed budget_max.'
            })

        return data


# ─── Manpower Request ─────────────────────────────────────────────────────────

class ManpowerRequestSerializer(serializers.ModelSerializer):
    """Read serializer — used for all responses (list, retrieve, create, update)."""
    line_items = MRFLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = ManpowerRequest
        fields = [
            'id', 'org', 'site', 'requested_by', 'requested_by_type',
            'mrf_type', 'status', 'department', 'billing_type',
            'required_by_date', 'reason', 'client_visible',
            'submitted_at', 'approved_at', 'rejected_at',
            'line_items', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'org', 'requested_by',
            'submitted_at', 'approved_at', 'rejected_at',
            'created_at', 'updated_at',
        ]


class ManpowerRequestWriteSerializer(serializers.ModelSerializer):
    """
    Write serializer for MRF create/update.

    org and requested_by are injected by the view from the actor context.
    submitted_at / approved_at / rejected_at are managed by workflow transitions (deferred).
    """

    class Meta:
        model = ManpowerRequest
        fields = [
            'site', 'requested_by_type', 'mrf_type', 'billing_type',
            'department', 'required_by_date', 'reason', 'client_visible', 'status',
        ]

    def validate_required_by_date(self, value):
        if value and value < date.today():
            raise serializers.ValidationError("required_by_date cannot be in the past.")
        return value

    def validate_status(self, value):
        # Approval/rejection transitions are handled by dedicated workflow endpoints (Phase 4F+).
        terminal = {'approved', 'rejected'}
        if value in terminal:
            raise serializers.ValidationError(
                f"Cannot set status to '{value}' directly. Use the approval workflow."
            )
        return value
