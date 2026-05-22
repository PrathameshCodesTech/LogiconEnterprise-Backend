"""
manage.py seed_qr_demo

Creates a demo QR campaign with 5 job roles, common fields (EN/HI/MR),
and role-specific fields mirroring the working QR backend seed.

Safe to run multiple times (uses get_or_create + update pattern).
"""

from django.core.management.base import BaseCommand

from apps.core.models import Organization
from apps.jobs.models import JobRole
from apps.sites.models import SiteProfile
from apps.intake.models import (
    QRCampaign, CampaignJobRole, FormField,
    FormTemplate, FormSection, FormTemplateField,
)

JOINING_OPTIONS = [
    'Immediate', 'Within 3 days', 'Within 7 days', 'Within 15 days',
    'Within 1 month', 'Within 3 months', 'After 3 months',
]
JOINING_OPTIONS_HI = [
    'तुरंत', '3 दिनों में', '7 दिनों में', '15 दिनों में',
    '1 महीने में', '3 महीने में', '3 महीने के बाद',
]
JOINING_OPTIONS_MR = [
    'लगेच', '3 दिवसांत', '7 दिवसांत', '15 दिवसांत',
    '1 महिन्यात', '3 महिन्यांत', '3 महिन्यांनंतर',
]


class Command(BaseCommand):
    help = 'Seed demo QR campaign data (idempotent)'

    def handle(self, *args, **options):
        self.stdout.write('Seeding QR demo data...')

        org = Organization.objects.filter(is_active=True).first()
        if not org:
            self.stderr.write(
                self.style.ERROR('No active organization found. Run seed_foundation first.')
            )
            return

        site = SiteProfile.objects.filter(org=org, is_active=True).first()

        role_data = [
            ('Housekeeping', 'HOUSEKEEPING'),
            ('Security Guard', 'SECURITY-GUARD'),
            ('Electrician', 'ELECTRICIAN'),
            ('Plumber', 'PLUMBER'),
            ('Supervisor', 'SUPERVISOR'),
        ]
        roles = {}
        for name, code in role_data:
            role, created = JobRole.objects.get_or_create(
                org=org,
                code=code,
                defaults={'name': name, 'description': '', 'is_active': True},
            )
            if not created and role.name != name:
                role.name = name
                role.save(update_fields=['name'])
            roles[code] = role

        campaign, created = QRCampaign.objects.get_or_create(
            org=org,
            code='QR-DEMO-2024',
            defaults={
                'name': 'Logicon Facility Hiring Drive',
                'title': 'Logicon Facility Hiring Drive',
                'site': site,
                'is_active': True,
                'allow_duplicates': True,
                'requires_otp': False,
                'shuffle_fields': False,
                'default_language': 'en',
                'enabled_languages': ['en', 'hi', 'mr'],
            },
        )
        if not created:
            campaign.site = site
            campaign.is_active = True
            campaign.allow_duplicates = True
            campaign.requires_otp = False
            campaign.shuffle_fields = False
            campaign.default_language = 'en'
            campaign.enabled_languages = ['en', 'hi', 'mr']
            campaign.title = campaign.title or campaign.name
            campaign.save(update_fields=[
                'site', 'is_active', 'allow_duplicates', 'requires_otp',
                'shuffle_fields', 'default_language', 'enabled_languages', 'title',
            ])

        for role in roles.values():
            CampaignJobRole.objects.get_or_create(
                campaign=campaign,
                job_role=role,
                defaults={'is_active': True},
            )

        def upsert_field(role, label, key, ftype, order, extra, translations):
            defaults = {
                'label': label,
                'field_type': ftype,
                'sort_order': order,
                'is_active': True,
                'is_required': False,
                'translations': translations,
                'options': [],
                'help_text': '',
                'placeholder': '',
                'min_value': None,
                'max_value': None,
            }
            defaults.update(extra)
            field, created = FormField.objects.get_or_create(
                campaign=campaign,
                field_key=key,
                role=role,
                defaults=defaults,
            )
            if not created:
                for attr, value in defaults.items():
                    setattr(field, attr, value)
                field.save(update_fields=list(defaults.keys()))
            return field

        # Common fields — fixed order matching the systematic public form:
        # General info first, then documents
        common_fields = [
            ('Experience Years', 'experience_years', 'number', 0,
             {'min_value': 0, 'max_value': 40}, {
                 'hi': {'label': 'अनुभव वर्ष'},
                 'mr': {'label': 'अनुभवाची वर्षे'},
             }),
            ('Current Location', 'current_location', 'text', 1, {}, {
                'hi': {'label': 'वर्तमान स्थान'},
                'mr': {'label': 'सध्याचे स्थान'},
            }),
            ('Expected Salary', 'expected_salary', 'number', 2, {}, {
                'hi': {'label': 'अपेक्षित वेतन'},
                'mr': {'label': 'अपेक्षित वेतन'},
            }),
            ('Age', 'age', 'number', 3,
             {'min_value': 18, 'max_value': 60, 'is_required': True}, {
                 'hi': {'label': 'आयु'},
                 'mr': {'label': 'वय'},
             }),
            ('Gender', 'gender', 'select', 4,
             {'options': ['Male', 'Female', 'Other', 'Prefer not to say']}, {
                 'hi': {'label': 'लिंग', 'options': ['पुरुष', 'महिला', 'अन्य', 'बताना नहीं चाहता']},
                 'mr': {'label': 'लिंग', 'options': ['पुरुष', 'स्त्री', 'इतर', 'सांगणे पसंत नाही']},
             }),
            ('Joining Availability', 'joining_availability', 'select', 5,
             {'options': JOINING_OPTIONS}, {
                 'hi': {'label': 'उपलब्धता', 'options': JOINING_OPTIONS_HI},
                 'mr': {'label': 'उपलब्धता', 'options': JOINING_OPTIONS_MR},
             }),
            # Documents
            ('Resume', 'resume', 'file', 6, {'is_required': True}, {
                'hi': {'label': 'रेज्यूमे'},
                'mr': {'label': 'रेझ्युमे'},
            }),
            ('ID Proof', 'id_proof', 'file', 7, {}, {
                'hi': {'label': 'पहचान प्रमाण'},
                'mr': {'label': 'ओळखपत्र'},
            }),
            ('Certificate', 'certificate', 'file', 8, {}, {
                'hi': {'label': 'प्रमाणपत्र'},
                'mr': {'label': 'प्रमाणपत्र'},
            }),
            ('Other Documents', 'other_documents', 'file', 9, {}, {
                'hi': {'label': 'अन्य दस्तावेज़'},
                'mr': {'label': 'इतर कागदपत्रे'},
            }),
        ]
        for label, key, ftype, order, extra, translations in common_fields:
            upsert_field(None, label, key, ftype, order, extra, translations)

        security_role = roles['SECURITY-GUARD']
        for label, key, ftype, order, extra, translations in [
            ('Height (cm)', 'height', 'number', 0, {'min_value': 150, 'max_value': 220}, {
                'hi': {'label': 'ऊंचाई (सेमी)'},
                'mr': {'label': 'उंची (सेमी)'},
            }),
            ('Has Security Experience', 'has_security_experience', 'boolean', 1, {}, {
                'hi': {'label': 'सुरक्षा अनुभव है?'},
                'mr': {'label': 'सुरक्षा अनुभव आहे का?'},
            }),
            ('Has License', 'has_license', 'boolean', 2, {}, {
                'hi': {'label': 'लाइसेंस है?'},
                'mr': {'label': 'परवाना आहे का?'},
            }),
        ]:
            upsert_field(security_role, label, key, ftype, order, extra, translations)

        electrician_role = roles['ELECTRICIAN']
        for label, key, ftype, order, extra, translations in [
            ('Certification', 'certification', 'text', 0, {}, {
                'hi': {'label': 'प्रमाणीकरण'},
                'mr': {'label': 'प्रमाणपत्र'},
            }),
            ('Years Electrical Experience', 'years_electrical_experience', 'number', 1,
             {'min_value': 0}, {
                 'hi': {'label': 'विद्युत अनुभव वर्ष'},
                 'mr': {'label': 'विद्युत अनुभवाची वर्षे'},
             }),
        ]:
            upsert_field(electrician_role, label, key, ftype, order, extra, translations)

        housekeeping_role = roles['HOUSEKEEPING']
        upsert_field(
            housekeeping_role,
            'Shift Preference', 'shift_preference', 'select', 0,
            {'options': ['Morning', 'Afternoon', 'Night', 'Any']},
            {
                'hi': {'label': 'पाली प्राथमिकता', 'options': ['सुबह', 'दोपहर', 'रात', 'कोई भी']},
                'mr': {'label': 'शिफ्ट प्राधान्य', 'options': ['सकाळ', 'दुपार', 'रात्र', 'कोणतीही']},
            },
        )

        # ── Form Template ──────────────────────────────────────────────────────
        template, _ = FormTemplate.objects.get_or_create(
            org=org,
            name='Logicon Standard Application Form',
            defaults={
                'code': 'facility_hiring_standard',
                'description': 'Reusable application form for Logicon hiring drives.',
                'is_active': True,
            },
        )
        # Ensure code and description are up-to-date on existing template
        update_fields = []
        if template.code != 'facility_hiring_standard':
            template.code = 'facility_hiring_standard'
            update_fields.append('code')
        if not template.description:
            template.description = 'Reusable application form for Logicon hiring drives.'
            update_fields.append('description')
        if update_fields:
            template.save(update_fields=update_fields)

        # ── Sections (4 canonical sections) ───────────────────────────────────
        section_defs = [
            ('Contact Details', 'contact_details', 0, 'Applicant contact information.'),
            ('Applying For', 'applying_for', 1, 'Role and position being applied for.'),
            ('General Information', 'general_information', 2, 'General background and experience details.'),
            ('Documents', 'documents', 3, 'Supporting documents and file uploads.'),
        ]
        sections = {}
        for sec_name, sec_code, sec_order, sec_desc in section_defs:
            sec, _ = FormSection.objects.get_or_create(
                template=template,
                name=sec_name,
                defaults={
                    'code': sec_code,
                    'sort_order': sec_order,
                    'description': sec_desc,
                    'is_active': True,
                },
            )
            # Always sync code, sort_order, description, is_active
            changed = []
            if sec.code != sec_code:
                sec.code = sec_code
                changed.append('code')
            if sec.sort_order != sec_order:
                sec.sort_order = sec_order
                changed.append('sort_order')
            if sec.description != sec_desc:
                sec.description = sec_desc
                changed.append('description')
            if not sec.is_active:
                sec.is_active = True
                changed.append('is_active')
            if changed:
                sec.save(update_fields=changed)
            sections[sec_code] = sec

        gen_section = sections['general_information']
        doc_section = sections['documents']

        def upsert_template_field(section, role, label, key, ftype, order, extra, translations):
            defaults = {
                'label': label,
                'field_type': ftype,
                'sort_order': order,
                'is_active': True,
                'is_required': False,
                'translations': translations,
                'options': [],
                'help_text': '',
                'placeholder': '',
                'min_value': None,
                'max_value': None,
            }
            defaults.update(extra)
            field, created = FormTemplateField.objects.get_or_create(
                section=section,
                field_key=key,
                role=role,
                defaults=defaults,
            )
            if not created:
                for attr, value in defaults.items():
                    setattr(field, attr, value)
                field.save(update_fields=list(defaults.keys()))
            return field

        gen_fields = [
            ('Experience Years', 'experience_years', 'number', 0,
             {'min_value': 0, 'max_value': 40}, {
                 'hi': {'label': 'अनुभव वर्ष'},
                 'mr': {'label': 'अनुभवाची वर्षे'},
             }),
            ('Current Location', 'current_location', 'text', 1, {}, {
                'hi': {'label': 'वर्तमान स्थान'},
                'mr': {'label': 'सध्याचे स्थान'},
            }),
            ('Expected Salary', 'expected_salary', 'number', 2, {}, {
                'hi': {'label': 'अपेक्षित वेतन'},
                'mr': {'label': 'अपेक्षित वेतन'},
            }),
            ('Age', 'age', 'number', 3,
             {'min_value': 18, 'max_value': 60, 'is_required': True}, {
                 'hi': {'label': 'आयु'},
                 'mr': {'label': 'वय'},
             }),
            ('Gender', 'gender', 'select', 4,
             {'options': ['Male', 'Female', 'Other', 'Prefer not to say']}, {
                 'hi': {'label': 'लिंग', 'options': ['पुरुष', 'महिला', 'अन्य', 'बताना नहीं चाहता']},
                 'mr': {'label': 'लिंग', 'options': ['पुरुष', 'स्त्री', 'इतर', 'सांगणे पसंत नाही']},
             }),
            ('Joining Availability', 'joining_availability', 'select', 5,
             {'options': JOINING_OPTIONS}, {
                 'hi': {'label': 'उपलब्धता', 'options': JOINING_OPTIONS_HI},
                 'mr': {'label': 'उपलब्धता', 'options': JOINING_OPTIONS_MR},
             }),
        ]
        for label, key, ftype, order, extra, translations in gen_fields:
            upsert_template_field(gen_section, None, label, key, ftype, order, extra, translations)

        doc_fields = [
            ('Resume', 'resume', 'file', 0, {'is_required': True}, {
                'hi': {'label': 'रेज्यूमे'},
                'mr': {'label': 'रेझ्युमे'},
            }),
            ('ID Proof', 'id_proof', 'file', 1, {}, {
                'hi': {'label': 'पहचान प्रमाण'},
                'mr': {'label': 'ओळखपत्र'},
            }),
            ('Certificate', 'certificate', 'file', 2, {}, {
                'hi': {'label': 'प्रमाणपत्र'},
                'mr': {'label': 'प्रमाणपत्र'},
            }),
            ('Other Documents', 'other_documents', 'file', 3, {}, {
                'hi': {'label': 'अन्य दस्तावेज़'},
                'mr': {'label': 'इतर कागदपत्रे'},
            }),
        ]
        for label, key, ftype, order, extra, translations in doc_fields:
            upsert_template_field(doc_section, None, label, key, ftype, order, extra, translations)

        # ── Role-specific template fields ──────────────────────────────────────
        security_role = roles['SECURITY-GUARD']
        for label, key, ftype, order, extra, translations in [
            ('Height (cm)', 'height', 'number', 0, {'min_value': 150, 'max_value': 220}, {
                'hi': {'label': 'ऊंचाई (सेमी)'},
                'mr': {'label': 'उंची (सेमी)'},
            }),
            ('Has Security Experience', 'has_security_experience', 'boolean', 1, {}, {
                'hi': {'label': 'सुरक्षा अनुभव है?'},
                'mr': {'label': 'सुरक्षा अनुभव आहे का?'},
            }),
            ('Has License', 'has_license', 'boolean', 2, {}, {
                'hi': {'label': 'लाइसेंस है?'},
                'mr': {'label': 'परवाना आहे का?'},
            }),
        ]:
            upsert_template_field(gen_section, security_role, label, key, ftype, order, extra, translations)

        electrician_role = roles['ELECTRICIAN']
        for label, key, ftype, order, extra, translations in [
            ('Certification', 'certification', 'text', 0, {}, {
                'hi': {'label': 'प्रमाणीकरण'},
                'mr': {'label': 'प्रमाणपत्र'},
            }),
            ('Years Electrical Experience', 'years_electrical_experience', 'number', 1,
             {'min_value': 0}, {
                 'hi': {'label': 'विद्युत अनुभव वर्ष'},
                 'mr': {'label': 'विद्युत अनुभवाची वर्षे'},
             }),
        ]:
            upsert_template_field(gen_section, electrician_role, label, key, ftype, order, extra, translations)

        housekeeping_role = roles['HOUSEKEEPING']
        upsert_template_field(
            gen_section, housekeeping_role,
            'Shift Preference', 'shift_preference', 'select', 0,
            {'options': ['Morning', 'Afternoon', 'Night', 'Any']},
            {
                'hi': {'label': 'पाली प्राथमिकता', 'options': ['सुबह', 'दोपहर', 'रात', 'कोई भी']},
                'mr': {'label': 'शिफ्ट प्राधान्य', 'options': ['सकाळ', 'दुपार', 'रात्र', 'कोणतीही']},
            },
        )

        # Attach template to campaign
        if not campaign.form_template_id:
            campaign.form_template = template
            campaign.save(update_fields=['form_template'])

        self.stdout.write(self.style.SUCCESS('\nDemo data seeded successfully!'))
        self.stdout.write(f'  Organization : {org.name}')
        if site:
            self.stdout.write(f'  Site         : {site.name}')
        self.stdout.write(f'  Campaign     : {campaign.title or campaign.name}')
        self.stdout.write(self.style.SUCCESS(f'  Token        : {campaign.token}'))
        self.stdout.write(f'  Template     : {template.name} (code: {template.code})')
        self.stdout.write(f'  Sections     : {", ".join(s.code for s in sections.values())}')
        self.stdout.write(f'  Public URL   : /api/public/campaigns/{campaign.token}/')
