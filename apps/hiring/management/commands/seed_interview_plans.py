from django.core.management.base import BaseCommand

from apps.core.models import Organization
from apps.hiring.models import InterviewPlan, InterviewPlanRound


DEFAULT_PLANS = [
    {
        'code': 'basic-hr',
        'name': 'Basic HR screening',
        'description': 'Single HR verification round.',
        'is_default': True,
        'rounds': [
            {'round_number': 1, 'round_type': 'hr', 'mode': 'phone'},
        ],
    },
    {
        'code': 'worker-hr-technical',
        'name': 'Worker HR + technical',
        'description': 'HR verification followed by technical validation.',
        'is_default': False,
        'rounds': [
            {'round_number': 1, 'round_type': 'hr', 'mode': 'phone'},
            {'round_number': 2, 'round_type': 'technical', 'mode': 'in_person'},
        ],
    },
    {
        'code': 'supervisor-full',
        'name': 'Supervisor full screening',
        'description': 'HR, technical, and manager rounds for supervisory roles.',
        'is_default': False,
        'rounds': [
            {'round_number': 1, 'round_type': 'hr', 'mode': 'phone'},
            {'round_number': 2, 'round_type': 'technical', 'mode': 'in_person'},
            {'round_number': 3, 'round_type': 'manager', 'mode': 'video'},
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed standard interview plans for every organization.'

    def handle(self, *args, **options):
        created_plans = 0
        updated_plans = 0
        created_rounds = 0

        for org in Organization.objects.all().order_by('id'):
            for plan_data in DEFAULT_PLANS:
                plan, created = InterviewPlan.objects.update_or_create(
                    org=org,
                    code=plan_data['code'],
                    defaults={
                        'name': plan_data['name'],
                        'description': plan_data['description'],
                        'is_default': plan_data['is_default'],
                        'is_active': True,
                    },
                )
                if created:
                    created_plans += 1
                else:
                    updated_plans += 1

                for round_data in plan_data['rounds']:
                    _, round_created = InterviewPlanRound.objects.update_or_create(
                        plan=plan,
                        round_number=round_data['round_number'],
                        defaults={
                            'round_type': round_data['round_type'],
                            'mode': round_data['mode'],
                            'is_required': True,
                            'is_active': True,
                        },
                    )
                    if round_created:
                        created_rounds += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Interview plans seeded. created={created_plans}, '
                f'updated={updated_plans}, rounds_created={created_rounds}'
            )
        )
