"""Summary output for Logicon demo seed."""

from apps.core.flowv2_seed.flowv2_constants import (
    LOGICON_DEMO_ORG_CODE,
    LOGICON_DEMO_PASSWORD,
    LOGICON_DEMO_USER_DEFS,
)


def build_logicon_demo_summary(context, sales_context, *, frontend_base_url='http://127.0.0.1:5173'):
    """Return a plain dict with the IDs and URLs needed for browser E2E."""
    org = context['org']
    proposal = sales_context['proposal']
    lead = sales_context['lead']
    survey = sales_context['survey']
    users = context['users']
    return {
        'org': {'id': org.pk, 'code': org.code, 'name': org.name},
        'password': LOGICON_DEMO_PASSWORD,
        'users': {
            key: {
                'username': spec['username'],
                'email': spec['email'],
                'role': spec['role_code'],
                'id': users[key].pk,
            }
            for key, spec in LOGICON_DEMO_USER_DEFS.items()
        },
        'sales': {
            'lead_id': lead.pk,
            'survey_id': survey.pk,
            'proposal_id': proposal.pk,
            'proposal_version': proposal.version_number,
            'grand_total': str(proposal.grand_total),
        },
        'urls': {
            'login': f'{frontend_base_url}/login',
            'sales_dashboard': f'{frontend_base_url}/sales/dashboard',
            'lead_detail': f'{frontend_base_url}/sales/leads/{lead.pk}',
            'survey': f'{frontend_base_url}/sales/surveys/{survey.pk}',
            'proposal': f'{frontend_base_url}/sales/proposals/{proposal.pk}',
            'my_tasks': f'{frontend_base_url}/my-tasks',
        },
    }


def print_logicon_demo_summary(summary, writer):
    """Pretty-print the summary to stdout."""
    if writer is None:
        return
    writer('')
    writer('=== Logicon Demo Summary ===')
    writer(f"Org: {summary['org']['name']} ({summary['org']['code']}) id={summary['org']['id']}")
    writer(f"Password for all users: {summary['password']}")
    writer('')
    writer('Users:')
    for key, row in summary['users'].items():
        writer(f"  {key}: {row['username']} / {row['email']} ({row['role']})")
    writer('')
    writer('Sales objects:')
    for key, value in summary['sales'].items():
        writer(f'  {key}: {value}')
    writer('')
    writer('Frontend URLs:')
    for key, value in summary['urls'].items():
        writer(f'  {key}: {value}')
    writer('')
    writer(f"Search convention: rg \"Logicon\" or rg \"{LOGICON_DEMO_ORG_CODE}\"")






