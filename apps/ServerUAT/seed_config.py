"""
ServerUAT business config seed.

This seed creates explicit proposal component rules and reusable interview
plans. It does not create workflow routes, clients, sites, SRRs, sales records,
MRFs, hiring applications, offers, or deployments.
"""

import datetime

from django.core.management.base import BaseCommand, CommandError


GENERIC_PLAN = {
    'code': 'generic_manpower_screening',
    'name': 'Generic Manpower Screening',
    'description': 'Default interview plan for manpower roles when no role-specific plan is selected.',
    'is_default': True,
    'job_role_code': None,
    'rounds': [
        {
            'round_number': 1,
            'round_type': 'hr',
            'mode': 'phone',
            'is_required': True,
            'instructions': 'Confirm basic details, availability, expected joining date, and documents.',
        },
        {
            'round_number': 2,
            'round_type': 'manager',
            'mode': 'video',
            'is_required': True,
            'instructions': 'Validate work readiness, communication, and client-site fit.',
        },
    ],
}

ROLE_PLAN_DEFINITIONS = {
    'helper': {
        'name': 'Helper Screening',
        'description': 'Short screening plan for helper roles.',
        'rounds': [
            {
                'round_number': 1,
                'round_type': 'hr',
                'mode': 'phone',
                'is_required': True,
                'instructions': 'Confirm identity, availability, shift comfort, and site-readiness.',
            },
            {
                'round_number': 2,
                'round_type': 'manager',
                'mode': 'in_person',
                'is_required': True,
                'instructions': 'Validate physical readiness, discipline, and basic site conduct.',
            },
        ],
    },
}

TECHNICAL_ROLE_CODES = [
    'electrician',
    'plumber',
    'mst',
    'hvac',
    'carpenter',
    'painter',
    'mason',
    'htp_operator',
    'wtp_operator',
]

TECHNICAL_ROUNDS = [
    {
        'round_number': 1,
        'round_type': 'hr',
        'mode': 'phone',
        'is_required': True,
        'instructions': 'Confirm basic details, experience, availability, and document readiness.',
    },
    {
        'round_number': 2,
        'round_type': 'technical',
        'mode': 'in_person',
        'is_required': True,
        'instructions': 'Validate trade knowledge, safety awareness, tools familiarity, and practical fit.',
    },
    {
        'round_number': 3,
        'round_type': 'manager',
        'mode': 'video',
        'is_required': True,
        'instructions': 'Final operations check for site fit, shift coverage, and client expectations.',
    },
]


class Command(BaseCommand):
    help = 'Seed ServerUAT config: proposal component rules and interview plans.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== ServerUAT Config Seed ===\n'))

        org = self._get_org()
        component_counts = self._seed_component_rules(org)
        plans = self._seed_interview_plans(org)

        self.stdout.write(
            self.style.SUCCESS(
                '\n[OK] ServerUAT config seed complete. '
                f'Component rules: {component_counts}. Interview plans: {plans}.\n'
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

    def _seed_component_rules(self, org):
        from apps.sales.proposal_calculation import seed_default_proposal_component_rules

        counts = seed_default_proposal_component_rules(
            org=org,
            overwrite=False,
            effective_from=datetime.date(2026, 1, 1),
        )
        self.stdout.write(
            '  [ProposalComponentRule] '
            f'created={counts["created"]}, updated={counts["updated"]}, unchanged={counts["unchanged"]}'
        )
        return counts

    def _seed_interview_plans(self, org):
        from apps.hiring.models import InterviewPlan, InterviewPlanRound
        from apps.jobs.models import JobRole

        job_roles = {
            role.code: role
            for role in JobRole.objects.filter(org=org, is_active=True)
        }

        created = 0
        updated = 0
        unchanged = 0

        count_result = self._upsert_plan(
            org=org,
            job_role=None,
            definition=GENERIC_PLAN,
            plan_model=InterviewPlan,
            round_model=InterviewPlanRound,
        )
        created += count_result['created']
        updated += count_result['updated']
        unchanged += count_result['unchanged']

        for role_code in TECHNICAL_ROLE_CODES:
            role = job_roles.get(role_code)
            if role is None:
                self.stderr.write(f'  [InterviewPlan] skipped {role_code}: job role missing')
                continue
            definition = {
                'code': f'{role_code}_technical_screening',
                'name': f'{role.name} Technical Screening',
                'description': f'Role-specific technical interview plan for {role.name}.',
                'is_default': False,
                'job_role_code': role_code,
                'rounds': TECHNICAL_ROUNDS,
            }
            count_result = self._upsert_plan(
                org=org,
                job_role=role,
                definition=definition,
                plan_model=InterviewPlan,
                round_model=InterviewPlanRound,
            )
            created += count_result['created']
            updated += count_result['updated']
            unchanged += count_result['unchanged']

        for role_code, partial_definition in ROLE_PLAN_DEFINITIONS.items():
            role = job_roles.get(role_code)
            if role is None:
                self.stderr.write(f'  [InterviewPlan] skipped {role_code}: job role missing')
                continue
            definition = {
                'code': f'{role_code}_screening',
                'name': partial_definition['name'],
                'description': partial_definition['description'],
                'is_default': False,
                'job_role_code': role_code,
                'rounds': partial_definition['rounds'],
            }
            count_result = self._upsert_plan(
                org=org,
                job_role=role,
                definition=definition,
                plan_model=InterviewPlan,
                round_model=InterviewPlanRound,
            )
            created += count_result['created']
            updated += count_result['updated']
            unchanged += count_result['unchanged']

        return {'created': created, 'updated': updated, 'unchanged': unchanged}

    def _upsert_plan(self, *, org, job_role, definition, plan_model, round_model):
        plan, was_created = plan_model.objects.get_or_create(
            org=org,
            code=definition['code'],
            defaults={
                'job_role': job_role,
                'name': definition['name'],
                'description': definition['description'],
                'is_default': definition['is_default'],
                'is_active': True,
            },
        )
        changed_fields = []
        for field, value in {
            'job_role': job_role,
            'name': definition['name'],
            'description': definition['description'],
            'is_default': definition['is_default'],
            'is_active': True,
        }.items():
            if getattr(plan, field) != value:
                setattr(plan, field, value)
                changed_fields.append(field)
        if changed_fields:
            plan.save(update_fields=changed_fields)

        existing_rounds = {
            round_obj.round_number: round_obj
            for round_obj in plan.rounds.filter(is_active=True)
        }
        wanted_round_numbers = set()
        rounds_changed = False
        for round_definition in definition['rounds']:
            round_number = round_definition['round_number']
            wanted_round_numbers.add(round_number)
            round_obj = existing_rounds.get(round_number)
            defaults = {
                'round_type': round_definition['round_type'],
                'mode': round_definition['mode'],
                'is_required': round_definition['is_required'],
                'is_active': True,
                'instructions': round_definition['instructions'],
            }
            if round_obj is None:
                round_model.objects.create(
                    plan=plan,
                    round_number=round_number,
                    **defaults,
                )
                rounds_changed = True
                continue
            round_changed_fields = []
            for field, value in defaults.items():
                if getattr(round_obj, field) != value:
                    setattr(round_obj, field, value)
                    round_changed_fields.append(field)
            if round_changed_fields:
                round_obj.save(update_fields=round_changed_fields)
                rounds_changed = True

        stale_rounds = plan.rounds.filter(is_active=True).exclude(
            round_number__in=wanted_round_numbers
        )
        if stale_rounds.exists():
            stale_rounds.update(is_active=False)
            rounds_changed = True

        if was_created:
            status = 'CREATED'
            counts = {'created': 1, 'updated': 0, 'unchanged': 0}
        elif changed_fields or rounds_changed:
            status = 'UPDATED'
            counts = {'created': 0, 'updated': 1, 'unchanged': 0}
        else:
            status = 'EXISTS'
            counts = {'created': 0, 'updated': 0, 'unchanged': 1}

        role_label = job_role.code if job_role else 'generic'
        self.stdout.write(
            f'  [InterviewPlan] {definition["code"]} ({role_label}) - {status}'
        )
        return counts
