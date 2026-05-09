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
from apps.intake.models import QRCampaign, CampaignJobRole, FormField


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
                'shuffle_fields': True,
                'default_language': 'en',
                'enabled_languages': ['en', 'hi', 'mr'],
            },
        )
        if not created:
            campaign.site = site
            campaign.is_active = True
            campaign.allow_duplicates = True
            campaign.requires_otp = False
            campaign.shuffle_fields = True
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

        common_fields = [
            ('Age', 'age', 'number', 0, {'min_value': 18, 'max_value': 60, 'is_required': True}, {
                'hi': {'label': 'आयु'},
                'mr': {'label': 'वय'},
            }),
            ('Gender', 'gender', 'select', 1,
             {'options': ['Male', 'Female', 'Other', 'Prefer not to say']}, {
                 'hi': {'label': 'लिंग', 'options': ['पुरुष', 'महिला', 'अन्य', 'बताना नहीं चाहता']},
                 'mr': {'label': 'लिंग', 'options': ['पुरुष', 'स्त्री', 'इतर', 'सांगणे पसंत नाही']},
             }),
            ('Current Location', 'current_location', 'text', 2, {}, {
                'hi': {'label': 'वर्तमान स्थान'},
                'mr': {'label': 'सध्याचे स्थान'},
            }),
            ('Experience Years', 'experience_years', 'number', 3, {'min_value': 0, 'max_value': 40}, {
                'hi': {'label': 'अनुभव वर्ष'},
                'mr': {'label': 'अनुभवाची वर्षे'},
            }),
            ('Expected Salary', 'expected_salary', 'number', 4, {}, {
                'hi': {'label': 'अपेक्षित वेतन'},
                'mr': {'label': 'अपेक्षित वेतन'},
            }),
            ('Joining Availability', 'joining_availability', 'date', 5, {}, {
                'hi': {'label': 'उपलब्धता तिथि'},
                'mr': {'label': 'उपलब्धतेची तारीख'},
            }),
            ('Resume', 'resume', 'file', 6, {'is_required': True}, {
                'hi': {'label': 'रिज्यूमे'},
                'mr': {'label': 'रेझ्युमे'},
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

        self.stdout.write(self.style.SUCCESS('\nDemo data seeded successfully!'))
        self.stdout.write(f'  Organization : {org.name}')
        if site:
            self.stdout.write(f'  Site         : {site.name}')
        self.stdout.write(f'  Campaign     : {campaign.title or campaign.name}')
        self.stdout.write(self.style.SUCCESS(f'  Token        : {campaign.token}'))
        self.stdout.write(f'  Public URL   : /api/public/campaigns/{campaign.token}/')
