"""Sales-ready seed for Logicon demo."""

from decimal import Decimal

from apps.core.flowv2_seed.flowv2_constants import (
    LOGICON_DEMO_LEAD_CLIENT_NAME,
    LOGICON_DEMO_LEAD_SITE_NAME,
)


def _write(writer, message):
    if writer:
        writer(message)


def seed_logicon_demo_sales_ready(
    context,
    *,
    writer=None,
    won_ready=False,
    client_name=LOGICON_DEMO_LEAD_CLIENT_NAME,
    site_name=LOGICON_DEMO_LEAD_SITE_NAME,
):
    """Seed one sales lead with survey, role requirements, and generated proposal."""
    return seed_logicon_demo_sales_case(
        context,
        writer=writer,
        won_ready=won_ready,
        client_name=client_name,
        site_name=site_name,
    )


def seed_logicon_demo_sales_case(
    context,
    *,
    writer=None,
    won_ready=False,
    client_name=LOGICON_DEMO_LEAD_CLIENT_NAME,
    site_name=LOGICON_DEMO_LEAD_SITE_NAME,
):
    """Seed one sales lead with survey, role requirements, and generated proposal."""
    org = context['org']
    users = context['users']
    location = context['location']
    job_roles = context['job_roles']
    wage_categories = context['wage_categories']

    lead, site = _seed_lead_and_site(
        org,
        users['sales'],
        location,
        writer,
        client_name=client_name,
        site_name=site_name,
    )
    survey = _seed_survey_and_rows(lead, site, users, writer)
    role_requirements = _seed_role_requirements(
        lead, site, survey, job_roles, wage_categories, users['operations'], writer,
    )
    proposal = _seed_generated_proposal(lead, users['sales'], writer)
    if won_ready:
        proposal = _seed_won_ready_proposal(lead, proposal, users, writer)
    return {
        'lead': lead,
        'site': site,
        'survey': survey,
        'role_requirements': role_requirements,
        'proposal': proposal,
    }


def _seed_lead_and_site(
    org,
    sales_user,
    location,
    writer,
    *,
    client_name=LOGICON_DEMO_LEAD_CLIENT_NAME,
    site_name=LOGICON_DEMO_LEAD_SITE_NAME,
):
    from apps.sales.models import SalesLead, SalesLeadSite

    lead = SalesLead.objects.filter(
        org=org,
        lead_type='new_client',
        client_name=client_name,
    ).first()
    if lead is None:
        lead = SalesLead.objects.create(
            org=org,
            lead_type='new_client',
            client_name=client_name,
            client_contact_person='Amit Kulkarni',
            client_email=f"amit.kulkarni+{client_name.lower().replace(' ', '.').replace('&', 'and')}@example.com",
            client_phone='9876500010',
            sales_person=sales_user,
            created_by=sales_user,
            current_stage='draft',
            current_status='draft',
            lead_source='Referral',
            industry='Manufacturing',
            priority='high',
            requirement_details='Integrated facility management manpower for the Pune plant.',
        )
        created = True
    else:
        created = False

    site, _ = SalesLeadSite.objects.get_or_create(
        lead=lead,
        site_name=site_name,
        defaults={
            'site_address': 'Plot 42, Chakan Industrial Area, Pune',
            'city': 'Pune',
            'state': 'Maharashtra',
            'location_area': location,
            'remarks': 'Primary manufacturing site for browser verification.',
            'is_active': True,
        },
    )
    if site.location_area_id != location.pk:
        site.location_area = location
        site.save(update_fields=['location_area', 'updated_at'])
    _write(writer, f'[LogiconSeed] Sales lead {lead.pk}: {"created" if created else "exists"}')
    return lead, site


def _seed_survey_and_rows(lead, site, users, writer):
    from apps.sales.models import SiteSurvey
    from apps.sales.services import (
        assign_survey_owner,
        mark_survey_completed,
        mark_survey_started,
        seed_default_survey_lines,
        submit_to_operations,
    )

    if lead.current_stage == 'draft':
        submit_to_operations(lead, users['sales'], operations_owner=users['operations'])
        lead.refresh_from_db()

    survey, _ = SiteSurvey.objects.get_or_create(
        lead=lead,
        site=site,
        defaults={'status': 'pending', 'assigned_to': users['operations']},
    )
    seed_counts = seed_default_survey_lines(survey, overwrite=False)
    if survey.assigned_to_id is None:
        assign_survey_owner(survey, users['operations'], users['operations'])
        survey.refresh_from_db()
    if survey.status == 'pending':
        mark_survey_started(survey, users['operations'])
        survey.refresh_from_db()
    if survey.status != 'completed':
        mark_survey_completed(survey, users['operations'])
        survey.refresh_from_db()
    lead.refresh_from_db()
    _write(writer, f'[LogiconSeed] Site survey {survey.pk}: completed; rows={seed_counts}')
    return survey


def _seed_role_requirements(lead, site, survey, job_roles, wage_categories, approver, writer):
    from apps.sales.models import SalesRoleRequirement
    from apps.sales.services import approve_sales_role_requirement

    specs = [
        ('electrician', 'skilled', 2, Decimal('8.0'), Decimal('26.0')),
        ('plumber', 'skilled', 2, Decimal('8.0'), Decimal('26.0')),
        ('hk_supervisor', 'skilled', 1, Decimal('8.0'), Decimal('26.0')),
        ('janitor', 'unskilled', 8, Decimal('8.0'), Decimal('26.0')),
    ]
    result = []
    for role_code, wage_code, headcount, shift_hours, working_days in specs:
        rr, created = SalesRoleRequirement.objects.get_or_create(
            lead=lead,
            site=site,
            survey=survey,
            job_role=job_roles[role_code],
            defaults={
                'wage_category': wage_categories[wage_code],
                'service_category': 'Facility Management',
                'manpower_count': headcount,
                'shift_hours': shift_hours,
                'working_days': working_days,
                'remarks': 'Seeded role requirement for browser verification.',
                'is_active': True,
                'created_from_survey': True,
            },
        )
        changed = []
        if rr.wage_category_id != wage_categories[wage_code].pk:
            rr.wage_category = wage_categories[wage_code]
            changed.append('wage_category')
        if rr.manpower_count != headcount:
            rr.manpower_count = headcount
            changed.append('manpower_count')
        if changed:
            rr.save(update_fields=changed + ['updated_at'])
        if not rr.approved_by_operations:
            approve_sales_role_requirement(rr, approver)
            rr.refresh_from_db()
        result.append(rr)
        _write(writer, f'[LogiconSeed] Role requirement {role_code}: {"created" if created else "exists"}')
    return result


def _seed_generated_proposal(lead, sales_user, writer):
    from apps.sales.models import ProposalVersion
    from apps.sales.services import generate_proposal_version

    proposal = ProposalVersion.objects.filter(lead=lead).order_by('-version_number').first()
    if proposal is None:
        if lead.current_stage not in {'site_survey_completed', 'budget_generated', 'sales_review'}:
            lead.current_stage = 'site_survey_completed'
            lead.save(update_fields=['current_stage', 'updated_at'])
        proposal = generate_proposal_version(lead, sales_user)
        created = True
    else:
        created = False
    _write(writer, f'[LogiconSeed] Proposal v{proposal.version_number}: {"created" if created else "exists"}')
    return proposal


def _seed_won_ready_proposal(lead, proposal, users, writer):
    """
    Advance the demo proposal to the exact point before mobilisation conversion.

    This intentionally avoids sending email. It marks the same state that the
    normal internal approval + client approval flow would produce:
    internally approved proposal, client-approved final version, and won lead.
    """
    from django.utils import timezone
    from apps.sales.models import ClientProposalResponse, ProposalVersion
    from apps.sales.services import (
        mark_lead_won_from_client_approval,
        mark_proposal_internally_approved,
        record_client_response,
    )

    proposal.refresh_from_db()
    lead.refresh_from_db()

    if proposal.internal_approval_status != 'approved':
        if proposal.status != 'submitted_internal':
            proposal.status = 'submitted_internal'
            proposal.internal_approval_status = 'in_progress'
            proposal.submitted_internal_at = timezone.now()
            proposal.save(update_fields=[
                'status', 'internal_approval_status', 'submitted_internal_at', 'updated_at',
            ])
        mark_proposal_internally_approved(proposal, users['finance'])
        proposal.refresh_from_db()
        lead.refresh_from_db()

    if proposal.client_approval_status != 'approved':
        now = timezone.now()
        proposal.status = 'sent_to_client'
        proposal.client_approval_status = 'pending'
        proposal.sent_to_client_at = proposal.sent_to_client_at or now
        proposal.save(update_fields=[
            'status', 'client_approval_status', 'sent_to_client_at', 'updated_at',
        ])
        ClientProposalResponse.objects.get_or_create(
            lead=lead,
            proposal_version=proposal,
            client_response='pending',
            defaults={'sent_to_client_at': proposal.sent_to_client_at},
        )
        record_client_response(
            proposal,
            'approved',
            'Seeded client approval for mobilisation conversion testing.',
            users['sales'],
            responded_by_name='Priya Client',
            responded_by_email='priya.client@acme.example.com',
        )
        proposal.refresh_from_db()
        lead.refresh_from_db()

    if lead.current_stage != 'won':
        mark_lead_won_from_client_approval(lead, proposal, users['sales'])
        proposal.refresh_from_db()
        lead.refresh_from_db()

    ProposalVersion.objects.filter(lead=lead).exclude(pk=proposal.pk).update(
        is_final_approved_version=False,
    )

    _write(
        writer,
        f'[LogiconSeed] Won-ready proposal {proposal.pk}: lead_stage={lead.current_stage}, '
        f'proposal_status={proposal.status}',
    )
    return proposal






