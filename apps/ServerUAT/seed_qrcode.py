"""
ServerUAT generic QR resume intake seed.

Creates one organization-level QR campaign backed by a reusable form template.
The campaign is intentionally not tied to any client/site and exposes every
active job role for role-first resume-pool intake.

Run after:
    python manage.py seed_server_uat foundation
    python manage.py seed_server_uat masters
"""

from django.core.management.base import BaseCommand, CommandError


TEMPLATE_CODE = 'generic-resume-pool-intake'
CAMPAIGN_CODE = 'QR-RESUME-POOL'
CAMPAIGN_TOKEN = 'resume-registration'

LEGACY_CAMPAIGN_CODES = ['QR-ELECTRICIAN-UAT', 'QR-GENERIC-RESUME-UAT']

SECTIONS = [
    ('candidate-details', 'Candidate details', 'Basic candidate contact details.', 10),
    ('work-profile', 'Work profile', 'Role, location, experience, and skills.', 20),
    ('resume-upload', 'Resume upload', 'Resume document used for resume-pool parsing.', 30),
]

FIELDS = [
    {
        'section': 'candidate-details',
        'field_key': 'email',
        'label': 'Email',
        'field_type': 'email',
        'help_text': 'Candidate email address.',
        'placeholder': 'candidate@example.com',
        'is_required': False,
        'sort_order': 10,
    },
    {
        'section': 'work-profile',
        'field_key': 'current_location',
        'label': 'Current location',
        'field_type': 'text',
        'help_text': 'City or area where the candidate currently stays.',
        'placeholder': 'e.g. Pune',
        'is_required': False,
        'sort_order': 20,
    },
    {
        'section': 'work-profile',
        'field_key': 'experience_years',
        'label': 'Experience years',
        'field_type': 'number',
        'help_text': 'Total relevant experience in years.',
        'placeholder': 'e.g. 4',
        'is_required': False,
        'sort_order': 30,
        'min_value': 0,
        'max_value': 40,
    },
    {
        'section': 'work-profile',
        'field_key': 'skills',
        'label': 'Skills',
        'field_type': 'textarea',
        'help_text': 'Comma-separated skills. Example: wiring, panel maintenance.',
        'placeholder': 'wiring, panel maintenance, troubleshooting',
        'is_required': False,
        'sort_order': 40,
    },
    {
        'section': 'work-profile',
        'field_key': 'preferred_location',
        'label': 'Preferred location',
        'field_type': 'text',
        'help_text': 'Preferred work location if different from current location.',
        'placeholder': 'e.g. Mumbai, Pune',
        'is_required': False,
        'sort_order': 50,
    },
    {
        'section': 'work-profile',
        'field_key': 'joining_availability',
        'label': 'Joining availability',
        'field_type': 'date',
        'help_text': 'Earliest date when the candidate can join.',
        'placeholder': '',
        'is_required': False,
        'sort_order': 60,
    },
    {
        'section': 'resume-upload',
        'field_key': 'resume',
        'label': 'Resume',
        'field_type': 'file',
        'help_text': 'Upload PDF, DOC, or DOCX resume.',
        'placeholder': '',
        'is_required': True,
        'sort_order': 70,
    },
]


class Command(BaseCommand):
    help = 'Seed ServerUAT generic QR resume intake campaign and reusable form template.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== ServerUAT Generic QR Resume Intake Seed ===\n'))

        org = self._get_org()
        template = self._seed_template(org)
        self._seed_sections_and_fields(template)
        campaign = self._seed_campaign(org, template)
        attached_count = self._attach_all_active_job_roles(org, campaign)
        self._deactivate_legacy_campaigns(org)

        self.stdout.write(
            self.style.SUCCESS(
                '\n[OK] ServerUAT generic QR resume intake seed complete.\n'
                f'Campaign: {campaign.title or campaign.name}\n'
                f'Token: {campaign.token}\n'
                f'Job roles attached: {attached_count}\n'
                f'Public API: /api/public/campaigns/{campaign.token}/\n'
                f'Frontend apply route: /apply/{campaign.token}\n'
            )
        )

    def _get_org(self):
        from apps.core.models import Organization

        try:
            return Organization.objects.get(code='logicon')
        except Organization.DoesNotExist as exc:
            raise CommandError(
                'Organization "logicon" does not exist. Run seed_server_uat foundation first.'
            ) from exc

    def _seed_template(self, org):
        from apps.intake.models import FormTemplate

        template, created = FormTemplate.objects.get_or_create(
            org=org,
            code=TEMPLATE_CODE,
            defaults={
                'name': 'Resume Pool Intake Form',
                'description': 'Reusable generic QR intake template for role-tagged resume submissions.',
                'is_active': True,
            },
        )
        expected = {
            'name': 'Resume Pool Intake Form',
            'description': 'Reusable generic QR intake template for role-tagged resume submissions.',
            'is_active': True,
        }
        changed_fields = self._apply_expected(template, expected)
        if changed_fields:
            template.save(update_fields=changed_fields)
        self.stdout.write(
            f'  [FormTemplate] {template.code} / {template.name} - {"CREATED" if created else "EXISTS"}'
        )
        return template

    def _seed_sections_and_fields(self, template):
        from apps.intake.models import FormSection, FormTemplateField

        sections = {}
        for code, name, description, sort_order in SECTIONS:
            section, created = FormSection.objects.get_or_create(
                template=template,
                code=code,
                defaults={
                    'name': name,
                    'description': description,
                    'sort_order': sort_order,
                    'is_active': True,
                },
            )
            expected = {
                'name': name,
                'description': description,
                'sort_order': sort_order,
                'is_active': True,
            }
            changed_fields = self._apply_expected(section, expected)
            if changed_fields:
                section.save(update_fields=changed_fields)
            sections[code] = section
            self.stdout.write(
                f'  [FormSection] {code} / {name} - {"CREATED" if created else "EXISTS"}'
            )

        for item in FIELDS:
            section = sections[item['section']]
            field, created = FormTemplateField.objects.get_or_create(
                section=section,
                role=None,
                field_key=item['field_key'],
                defaults={
                    'label': item['label'],
                    'field_type': item['field_type'],
                    'help_text': item.get('help_text', ''),
                    'placeholder': item.get('placeholder', ''),
                    'options': item.get('options', []),
                    'is_required': item.get('is_required', False),
                    'sort_order': item.get('sort_order', 0),
                    'min_value': item.get('min_value'),
                    'max_value': item.get('max_value'),
                    'is_active': True,
                },
            )
            expected = {
                'label': item['label'],
                'field_type': item['field_type'],
                'help_text': item.get('help_text', ''),
                'placeholder': item.get('placeholder', ''),
                'options': item.get('options', []),
                'is_required': item.get('is_required', False),
                'sort_order': item.get('sort_order', 0),
                'min_value': item.get('min_value'),
                'max_value': item.get('max_value'),
                'is_active': True,
            }
            changed_fields = self._apply_expected(field, expected)
            if changed_fields:
                field.save(update_fields=changed_fields)
            self.stdout.write(
                f'  [FormTemplateField] {field.field_key} / {field.label} - '
                f'{"CREATED" if created else "EXISTS"}'
            )

    def _seed_campaign(self, org, template):
        from apps.intake.models import QRCampaign

        campaign, created = QRCampaign.objects.get_or_create(
            org=org,
            code=CAMPAIGN_CODE,
            defaults={
                'site': None,
                'form_template': template,
                'name': 'Generic Resume Pool QR',
                'title': 'Resume Registration',
                'token': CAMPAIGN_TOKEN,
                'is_active': True,
                'allow_duplicates': True,
                'requires_otp': False,
                'shuffle_fields': False,
                'default_language': 'en',
                'enabled_languages': ['en'],
            },
        )
        expected = {
            'site': None,
            'form_template': template,
            'name': 'Generic Resume Pool QR',
            'title': 'Resume Registration',
            'token': CAMPAIGN_TOKEN,
            'is_active': True,
            'allow_duplicates': True,
            'requires_otp': False,
            'shuffle_fields': False,
            'default_language': 'en',
            'enabled_languages': ['en'],
        }
        changed_fields = self._apply_expected(campaign, expected)
        if changed_fields:
            campaign.save(update_fields=changed_fields)
        self.stdout.write(
            f'  [QRCampaign] {campaign.code} / token={campaign.token} - '
            f'{"CREATED" if created else "EXISTS"}'
        )
        return campaign

    def _attach_all_active_job_roles(self, org, campaign):
        from apps.intake.models import CampaignJobRole
        from apps.jobs.models import JobRole

        active_roles = list(JobRole.objects.filter(org=org, is_active=True).order_by('name', 'id'))
        if not active_roles:
            raise CommandError('No active job roles found. Run seed_server_uat masters first.')

        active_role_ids = {role.id for role in active_roles}
        CampaignJobRole.objects.filter(campaign=campaign).exclude(job_role_id__in=active_role_ids).update(
            is_active=False
        )
        for role in active_roles:
            CampaignJobRole.objects.update_or_create(
                campaign=campaign,
                job_role=role,
                defaults={'is_active': True},
            )
            self.stdout.write(f'  [CampaignJobRole] {role.code} / {role.name} - READY')
        return len(active_roles)

    def _deactivate_legacy_campaigns(self, org):
        from apps.intake.models import QRCampaign

        updated = QRCampaign.objects.filter(
            org=org,
            code__in=LEGACY_CAMPAIGN_CODES,
            is_active=True,
        ).update(is_active=False)
        if updated:
            self.stdout.write(f'  [Cleanup] Deactivated legacy QR campaigns: {updated}')

    def _apply_expected(self, instance, expected):
        changed_fields = []
        for field, value in expected.items():
            if getattr(instance, field) != value:
                setattr(instance, field, value)
                changed_fields.append(field)
        return changed_fields
