"""
apps/intake/serializers.py

Management serializers (authenticated): QRCampaignSerializer, FormFieldSerializer, etc.
Public serializers (AllowAny): PublicCampaignSerializer, SubmissionCreateSerializer,
                               SubmissionResponseSerializer.
"""

import json
import re
import random
from datetime import datetime, date as date_type

from django.db.models import Q
from rest_framework import serializers

from .constants import SUPPORTED_LANGUAGES, LANGUAGE_NATIVE_LABELS
from .models import (
    QRCampaign, CampaignJobRole, FormField,
    IntakeSubmission, IntakeSubmissionAnswer, IntakeDocument,
)
from .services import normalize_mobile, normalize_role_title, validate_mobile

_NAME_RE = re.compile(r"^[^\d_@#$%^&*+=<>?/\\|{}\[\]~`]+$")


# ─── Management serializers ───────────────────────────────────────────────────

class CampaignJobRoleInlineSerializer(serializers.ModelSerializer):
    """Compact read-only nested view of campaign job roles embedded in campaign responses."""
    job_role_name = serializers.CharField(source='job_role.name', read_only=True)
    job_role_code = serializers.CharField(source='job_role.code', read_only=True)

    class Meta:
        model = CampaignJobRole
        fields = ['id', 'job_role', 'job_role_name', 'job_role_code', 'is_active']
        read_only_fields = ['id', 'job_role', 'job_role_name', 'job_role_code', 'is_active']


class QRCampaignSerializer(serializers.ModelSerializer):
    """Read serializer — used for list/retrieve/create/update responses."""
    campaign_roles = CampaignJobRoleInlineSerializer(
        source='campaign_job_roles', many=True, read_only=True,
    )

    class Meta:
        model = QRCampaign
        fields = [
            'id', 'org', 'site', 'name', 'title', 'code', 'token',
            'is_active', 'starts_at', 'ends_at', 'allow_duplicates',
            'requires_otp', 'shuffle_fields', 'default_language',
            'enabled_languages', 'campaign_roles', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'org', 'site', 'name', 'title', 'code', 'token',
            'is_active', 'starts_at', 'ends_at', 'allow_duplicates',
            'requires_otp', 'shuffle_fields', 'default_language',
            'enabled_languages', 'created_at', 'updated_at',
        ]


class QRCampaignWriteSerializer(serializers.ModelSerializer):
    """Write serializer — org injected by view."""
    class Meta:
        model = QRCampaign
        fields = [
            'name', 'title', 'code', 'site',
            'is_active', 'starts_at', 'ends_at', 'allow_duplicates',
            'requires_otp', 'shuffle_fields', 'default_language',
            'enabled_languages',
        ]

    def validate(self, data):
        valid_codes = {code for code, _ in SUPPORTED_LANGUAGES}
        instance = getattr(self, 'instance', None)

        # For PATCH, fall back to existing instance values when field is absent.
        enabled = data.get(
            'enabled_languages',
            instance.enabled_languages if instance else [],
        )
        default = data.get(
            'default_language',
            instance.default_language if instance else 'en',
        )

        if enabled:
            invalid = [c for c in enabled if c not in valid_codes]
            if invalid:
                raise serializers.ValidationError(
                    {'enabled_languages': f"Invalid language codes: {invalid}"}
                )
        if enabled and default not in enabled:
            raise serializers.ValidationError(
                {'default_language': 'default_language must be in enabled_languages.'}
            )
        return data


class CampaignJobRoleSerializer(serializers.ModelSerializer):
    """Read/write serializer for CampaignJobRole admin endpoint."""
    job_role_name = serializers.CharField(source='job_role.name', read_only=True)
    job_role_code = serializers.CharField(source='job_role.code', read_only=True)

    class Meta:
        model = CampaignJobRole
        fields = ['id', 'campaign', 'job_role', 'is_active', 'job_role_name', 'job_role_code']
        read_only_fields = ['job_role_name', 'job_role_code']

    def validate(self, data):
        instance = self.instance
        campaign = data.get('campaign') or (instance.campaign if instance else None)
        job_role = data.get('job_role') or (instance.job_role if instance else None)

        if campaign and job_role and campaign.org_id != job_role.org_id:
            raise serializers.ValidationError(
                {'job_role': 'Job role must belong to the same organization as the campaign.'}
            )

        if campaign and job_role:
            qs = CampaignJobRole.objects.filter(campaign=campaign, job_role=job_role)
            if instance:
                qs = qs.exclude(pk=instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    'A campaign job role for this campaign and job role already exists.'
                )

        return data


class FormFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = FormField
        fields = [
            'id', 'campaign', 'role', 'label', 'field_key', 'field_type',
            'help_text', 'placeholder', 'options', 'is_required', 'sort_order',
            'min_length', 'max_length', 'min_value', 'max_value',
            'translations', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class IntakeSubmissionAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntakeSubmissionAnswer
        fields = [
            'id', 'field', 'field_label_snapshot', 'field_type_snapshot',
            'value', 'created_at',
        ]
        read_only_fields = fields


class IntakeDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntakeDocument
        fields = [
            'id', 'document_type', 'file',
            'original_filename', 'content_type', 'size_bytes', 'uploaded_at',
        ]
        read_only_fields = fields


class IntakeSubmissionListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for submission list view."""
    class Meta:
        model = IntakeSubmission
        fields = [
            'id', 'campaign', 'site', 'candidate', 'job_role',
            'first_name', 'middle_name', 'last_name', 'full_name',
            'other_role_title', 'mobile_number', 'mobile_number_normalized',
            'status', 'language', 'is_possible_duplicate',
            'submitted_at', 'updated_at',
        ]
        read_only_fields = fields


class IntakeSubmissionDetailSerializer(serializers.ModelSerializer):
    """Full detail serializer including answers, documents, and metadata."""
    answers = IntakeSubmissionAnswerSerializer(many=True, read_only=True)
    documents = IntakeDocumentSerializer(many=True, read_only=True)

    class Meta:
        model = IntakeSubmission
        fields = [
            'id', 'campaign', 'site', 'candidate', 'job_role',
            'first_name', 'middle_name', 'last_name', 'full_name',
            'other_role_title', 'mobile_number', 'mobile_number_normalized',
            'status', 'language', 'is_possible_duplicate', 'duplicate_reason',
            'ip_address', 'user_agent',
            'submitted_at', 'updated_at',
            'answers', 'documents',
        ]
        read_only_fields = fields


class SubmissionStatusUpdateSerializer(serializers.Serializer):
    """PATCH serializer for updating submission status."""
    status = serializers.ChoiceField(choices=IntakeSubmission.STATUS_CHOICES)
    note = serializers.CharField(required=False, allow_blank=True, default='')

    # Review history is deferred — note is accepted but not stored in this phase.


# Public serializers

class PublicSiteSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()


class PublicJobRoleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    code = serializers.CharField()


class PublicFormFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = FormField
        fields = [
            'id', 'label', 'field_key', 'field_type', 'help_text',
            'placeholder', 'options', 'is_required', 'sort_order',
            'min_length', 'max_length', 'min_value', 'max_value',
            'role', 'translations',
        ]


class PublicCampaignSerializer(serializers.ModelSerializer):
    site = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    common_fields = serializers.SerializerMethodField()
    role_fields = serializers.SerializerMethodField()
    settings = serializers.SerializerMethodField()
    languages = serializers.SerializerMethodField()

    class Meta:
        model = QRCampaign
        fields = [
            'id', 'title', 'token', 'site',
            'roles', 'common_fields', 'role_fields', 'settings',
            'default_language', 'enabled_languages', 'languages',
        ]

    def get_site(self, obj):
        if not obj.site:
            return None
        site = obj.site
        return {
            'id': site.id,
            'name': site.name,
            'city': getattr(site, 'city', ''),
            'state': getattr(site, 'state', ''),
        }

    def get_roles(self, obj):
        active_cjrs = obj.campaign_job_roles.filter(is_active=True).select_related('job_role')
        return [
            {'id': cjr.job_role.id, 'name': cjr.job_role.name, 'code': cjr.job_role.code}
            for cjr in active_cjrs
        ]

    def get_common_fields(self, obj):
        fields = list(
            obj.form_fields.filter(is_active=True, role__isnull=True).order_by('sort_order', 'id')
        )
        if obj.shuffle_fields:
            random.shuffle(fields)
        return PublicFormFieldSerializer(fields, many=True).data

    def get_role_fields(self, obj):
        fields = obj.form_fields.filter(is_active=True, role__isnull=False).order_by('sort_order', 'id')
        result = {}
        for f in fields:
            key = str(f.role_id)
            if key not in result:
                result[key] = []
            result[key].append(PublicFormFieldSerializer(f).data)
        if obj.shuffle_fields:
            for values in result.values():
                random.shuffle(values)
        return result

    def get_settings(self, obj):
        return {
            'shuffle_fields': obj.shuffle_fields,
            'requires_otp': obj.requires_otp,
            'allow_duplicates': obj.allow_duplicates,
        }

    def get_languages(self, obj):
        lang_map = dict(SUPPORTED_LANGUAGES)
        result = []
        for code in (obj.enabled_languages or ['en']):
            if code in lang_map:
                result.append({
                    'code': code,
                    'label': lang_map[code],
                    'native_label': LANGUAGE_NATIVE_LABELS.get(code, lang_map[code]),
                })
        return result


# Submission create serializer

def _validate_name_part(value: str, field_label: str, required: bool = False, min_len: int = 2) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        if required:
            raise serializers.ValidationError(f"{field_label} is required.")
        return ''
    if min_len and len(cleaned) < min_len:
        raise serializers.ValidationError(f"{field_label} must be at least {min_len} characters.")
    if not _NAME_RE.match(cleaned):
        raise serializers.ValidationError(
            f"{field_label} cannot contain numbers or special characters."
        )
    return cleaned


class SubmissionCreateSerializer(serializers.Serializer):
    campaign_token = serializers.CharField()
    job_role_id = serializers.IntegerField(required=False, allow_null=True)
    first_name = serializers.CharField(required=False, allow_blank=True, default='')
    middle_name = serializers.CharField(required=False, allow_blank=True, default='')
    last_name = serializers.CharField(required=False, allow_blank=True, default='')
    mobile_number = serializers.CharField()
    language = serializers.CharField(required=False, allow_blank=True, default='')
    other_role_title = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=200,
    )
    answers = serializers.JSONField(required=False, default=list)

    def validate_mobile_number(self, value):
        return validate_mobile(value)

    def validate_first_name(self, value):
        return _validate_name_part(value, 'First name', required=True, min_len=2)

    def validate_middle_name(self, value):
        return _validate_name_part(value, 'Middle name', required=False, min_len=0)

    def validate_last_name(self, value):
        return _validate_name_part(value, 'Last name', required=True, min_len=2)

    def validate_other_role_title(self, value):
        if value:
            cleaned = value.strip()
            if len(cleaned) < 2:
                raise serializers.ValidationError("Role title must be at least 2 characters.")
            return cleaned
        return ''

    def validate_answers(self, value):
        if value in ('', None):
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise serializers.ValidationError("Answers must be valid JSON.")
        if not isinstance(value, list):
            raise serializers.ValidationError("Answers must be a list.")
        normalized = []
        for ans in value:
            if not isinstance(ans, dict):
                raise serializers.ValidationError("Each answer must be an object.")
            if 'field_id' not in ans:
                raise serializers.ValidationError("Each answer must include field_id.")
            if 'value' not in ans:
                raise serializers.ValidationError("Each answer must include value.")
            try:
                field_id = int(ans['field_id'])
            except (TypeError, ValueError):
                raise serializers.ValidationError("field_id must be an integer.")
            normalized.append({'field_id': field_id, 'value': ans['value']})
        return normalized

    def validate(self, data):
        campaign = self._resolve_campaign(data['campaign_token'])
        job_role = self._resolve_job_role(campaign, data.get('job_role_id'))
        other_role_title = data.get('other_role_title', '').strip()

        if job_role and other_role_title:
            data['other_role_title'] = ''
            other_role_title = ''

        if not job_role and not other_role_title:
            raise serializers.ValidationError(
                "Please select a role or enter a role title."
            )

        data['campaign'] = campaign
        data['job_role'] = job_role

        lang = data.get('language', '').strip()
        if not lang:
            lang = campaign.default_language
        enabled = campaign.enabled_languages or ['en']
        if lang not in enabled:
            raise serializers.ValidationError(
                f"Language '{lang}' is not enabled for this campaign. Allowed: {enabled}"
            )
        data['language'] = lang

        answers = data.get('answers', [])
        provided_field_ids = set()
        if answers:
            field_ids = [a['field_id'] for a in answers]
            fields_map = {
                f.id: f
                for f in FormField.objects.filter(
                    id__in=field_ids, campaign=campaign, is_active=True,
                )
            }
            validated_answers = []
            for ans in answers:
                field = fields_map.get(ans['field_id'])
                if not field:
                    raise serializers.ValidationError(
                        f"Field {ans['field_id']} does not belong to this campaign."
                    )
                if field.role_id is not None and (job_role is None or field.role_id != job_role.id):
                    raise serializers.ValidationError(
                        f"Field '{field.label}' is not valid for the selected role."
                    )
                provided_field_ids.add(field.id)
                self._validate_field_value(field, ans['value'])
                validated_answers.append({'field': field, 'value': ans['value']})
            data['answers'] = validated_answers

        self._validate_required_fields(campaign, job_role, provided_field_ids, data.get('answers', []))

        answers_by_key = {
            ans['field'].field_key: ans['value']
            for ans in data.get('answers', [])
            if 'field' in ans
        }
        self._validate_business_rules(answers_by_key)

        return data

    # Helpers

    def _resolve_campaign(self, token: str) -> QRCampaign:
        from django.utils import timezone
        try:
            campaign = QRCampaign.objects.get(token=token)
        except QRCampaign.DoesNotExist:
            raise serializers.ValidationError("Invalid campaign token.")
        if not campaign.is_active:
            raise serializers.ValidationError("This campaign is no longer active.")
        now = timezone.now()
        if campaign.starts_at and campaign.starts_at > now:
            raise serializers.ValidationError("This campaign has not started yet.")
        if campaign.ends_at and campaign.ends_at < now:
            raise serializers.ValidationError("This campaign has ended.")
        return campaign

    def _resolve_job_role(self, campaign, job_role_id):
        if job_role_id is None:
            return None
        try:
            cjr = campaign.campaign_job_roles.select_related('job_role').get(
                job_role_id=job_role_id, is_active=True,
            )
            return cjr.job_role
        except Exception:
            raise serializers.ValidationError(
                "Selected role is not valid for this campaign."
            )

    def _validate_required_fields(self, campaign, job_role, provided_field_ids, validated_answers):
        required_fields = FormField.objects.filter(
            campaign=campaign,
            is_active=True,
            is_required=True,
        ).exclude(field_type='file').filter(Q(role__isnull=True) | Q(role=job_role))
        answer_values = {
            ans['field'].id: ans['value']
            for ans in validated_answers
            if 'field' in ans
        }
        missing = []
        for field in required_fields:
            if field.id not in provided_field_ids or answer_values.get(field.id) in ('', None, []):
                missing.append(field.label)
        if missing:
            raise serializers.ValidationError(
                f"Required fields missing: {', '.join(missing)}."
            )

    def _validate_field_value(self, field, value):
        if value in ('', None, []):
            if field.is_required:
                raise serializers.ValidationError(f"'{field.label}' is required.")
            return
        if field.field_type == 'select' and field.options:
            if value not in field.options:
                raise serializers.ValidationError(
                    f"'{value}' is not a valid option for '{field.label}'."
                )
        elif field.field_type == 'multi_select' and field.options:
            if not isinstance(value, list):
                raise serializers.ValidationError(f"'{field.label}' expects a list.")
            invalid = [v for v in value if v not in field.options]
            if invalid:
                raise serializers.ValidationError(
                    f"Invalid options {invalid} for '{field.label}'."
                )
        elif field.field_type in ('text', 'textarea'):
            if not isinstance(value, str):
                raise serializers.ValidationError(f"'{field.label}' must be text.")
            if field.min_length and len(value) < field.min_length:
                raise serializers.ValidationError(
                    f"'{field.label}' must be at least {field.min_length} characters."
                )
            if field.max_length and len(value) > field.max_length:
                raise serializers.ValidationError(
                    f"'{field.label}' exceeds max length of {field.max_length}."
                )
        elif field.field_type == 'number':
            try:
                num = float(value)
            except (TypeError, ValueError):
                raise serializers.ValidationError(f"'{field.label}' must be a number.")
            if field.min_value is not None and num < float(field.min_value):
                raise serializers.ValidationError(
                    f"'{field.label}' must be at least {field.min_value}."
                )
            if field.max_value is not None and num > float(field.max_value):
                raise serializers.ValidationError(
                    f"'{field.label}' must be at most {field.max_value}."
                )
        elif field.field_type == 'email':
            if value and not re.match(r'^[^@]+@[^@]+\.[^@]+$', str(value)):
                raise serializers.ValidationError(f"'{field.label}' must be a valid email.")
        elif field.field_type == 'date':
            if value:
                try:
                    datetime.fromisoformat(str(value))
                except ValueError:
                    raise serializers.ValidationError(
                        f"'{field.label}' must be a valid ISO date (YYYY-MM-DD)."
                    )

    def _validate_business_rules(self, answers_by_key: dict) -> None:
        def _to_float(val):
            if val is None or val == '':
                return None
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        age = _to_float(answers_by_key.get('age'))
        if age is not None:
            if age < 18 or age > 60:
                raise serializers.ValidationError("Age must be between 18 and 60.")

        exp = _to_float(answers_by_key.get('experience_years'))
        if exp is not None and age is not None:
            if exp > age - 14:
                raise serializers.ValidationError(
                    "Experience years cannot be greater than age minus 14."
                )

        salary = _to_float(answers_by_key.get('expected_salary'))
        if salary is not None and salary > 500000:
            raise serializers.ValidationError("Expected salary cannot exceed 500000.")

        joining_raw = answers_by_key.get('joining_availability')
        if joining_raw and str(joining_raw).strip():
            try:
                joining = datetime.fromisoformat(str(joining_raw)).date()
                if joining < date_type.today():
                    raise serializers.ValidationError(
                        "Joining availability cannot be in the past."
                    )
            except ValueError:
                pass


class SubmissionResponseSerializer(serializers.ModelSerializer):
    """Response serializer returned after a successful public submission."""
    answers = IntakeSubmissionAnswerSerializer(many=True, read_only=True)
    documents = IntakeDocumentSerializer(many=True, read_only=True)

    class Meta:
        model = IntakeSubmission
        fields = [
            'id', 'campaign', 'site', 'candidate', 'job_role',
            'first_name', 'middle_name', 'last_name', 'full_name',
            'other_role_title', 'mobile_number', 'mobile_number_normalized',
            'status', 'language', 'is_possible_duplicate',
            'submitted_at', 'answers', 'documents',
        ]
        read_only_fields = fields
