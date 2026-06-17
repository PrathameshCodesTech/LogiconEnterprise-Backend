"""Client-facing proposal document data and PDF rendering."""

from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


COMPONENT_TYPE_LABELS = {
    'earning': 'Earning',
    'employee_deduction': 'Employee Deduction',
    'employer_contribution': 'Employer Contribution',
    'statutory': 'Statutory',
    'reimbursement': 'Reimbursement',
    'equipment': 'Equipment',
    'management_fee': 'Management Fee',
    'tax': 'Tax',
    'total': 'Total',
}

CLIENT_PROPOSAL_TERMS = [
    'Commercials are stated as monthly estimates unless explicitly noted otherwise.',
    'Statutory contributions are calculated as per configured component rules.',
    'Taxes are shown separately where applicable.',
    'Final deployment is subject to operational onboarding and mutually agreed start dates.',
    'Commercial validity is governed by the proposal validity period communicated by Logicon.',
]


def _money(value):
    amount = Decimal(value or 0)
    return f"INR {amount:,.2f}"


def _decimal_string(value):
    if value is None:
        return None
    return str(value)


def _display_date(value):
    if value is None:
        return ''
    if hasattr(value, 'date'):
        value = timezone.localtime(value).date()
    return value.isoformat()


def _role_title(line):
    role = line.job_role.name if line.job_role_id else ''
    site = line.site.site_name if line.site_id else ''
    if role and site:
        return f'{role} - {site}'
    return role or site or line.description


def build_client_proposal_document_data(proposal):
    """
    Return normalized client-facing proposal data.

    Salary breakup is strict: every component must be linked to a role
    requirement and every component role must have a matching budget line.
    """
    lead = proposal.lead
    budget_lines = list(
        proposal.budget_lines
        .select_related('site', 'job_role', 'role_requirement')
        .order_by('sort_order', 'id')
    )
    breakup_lines = list(
        proposal.breakup_lines
        .select_related('site', 'job_role', 'role_requirement')
        .order_by('sort_order', 'id')
    )

    unmapped = [line for line in breakup_lines if line.role_requirement_id is None]
    if unmapped:
        raise ValueError(
            'Salary breakup is missing role mapping. Regenerate this proposal '
            'so every salary component is linked to a role requirement.'
        )

    budget_role_ids = {
        line.role_requirement_id
        for line in budget_lines
        if line.role_requirement_id is not None
    }
    orphaned = [
        line for line in breakup_lines
        if line.role_requirement_id not in budget_role_ids
    ]
    if orphaned:
        raise ValueError(
            'Salary breakup contains role components without a matching budget line. '
            'Regenerate this proposal before producing the client document.'
        )

    breakup_by_role = {}
    for line in breakup_lines:
        breakup_by_role.setdefault(line.role_requirement_id, []).append({
            'component_name': line.component_name,
            'component_type': line.component_type,
            'component_type_label': COMPONENT_TYPE_LABELS.get(
                line.component_type, line.component_type,
            ),
            'percentage': _decimal_string(line.percentage),
            'amount': _decimal_string(line.amount),
        })

    manpower_lines = []
    role_cost_structure = []
    for line in budget_lines:
        manpower_lines.append({
            'role_requirement': line.role_requirement_id,
            'role_name': line.job_role.name if line.job_role_id else line.description,
            'site_name': line.site.site_name if line.site_id else '',
            'service_category': line.service_category or '',
            'manpower_count': line.manpower_count,
            'unit_cost': _decimal_string(line.unit_cost),
            'total_cost': _decimal_string(line.total_cost),
        })
        role_cost_structure.append({
            'role_requirement': line.role_requirement_id,
            'title': _role_title(line),
            'role_name': line.job_role.name if line.job_role_id else line.description,
            'site_name': line.site.site_name if line.site_id else '',
            'manpower_count': line.manpower_count,
            'unit_cost': _decimal_string(line.unit_cost),
            'total_cost': _decimal_string(line.total_cost),
            'components': breakup_by_role.get(line.role_requirement_id, []),
        })

    valid_until = proposal.expires_at
    if valid_until is None and proposal.validity_days:
        valid_until = proposal.created_at + timedelta(days=proposal.validity_days)

    return {
        'proposal': {
            'id': proposal.pk,
            'version_number': proposal.version_number,
            'prepared_date': _display_date(proposal.created_at),
            'valid_until': _display_date(valid_until),
            'status': proposal.status,
        },
        'client': {
            'name': lead.client_name,
            'contact_person': lead.client_contact_person or '',
            'email': lead.client_email or '',
            'phone': lead.client_phone or '',
        },
        'commercial_summary': {
            'manpower_total': proposal.manpower_total,
            'subtotal_amount': _decimal_string(proposal.subtotal_amount),
            'management_fee_percent': _decimal_string(proposal.management_fee_percent),
            'management_fee_amount': _decimal_string(proposal.management_fee_amount),
            'gst_applicable': proposal.gst_applicable,
            'gst_amount': _decimal_string(proposal.gst_amount),
            'grand_total': _decimal_string(proposal.grand_total),
        },
        'manpower_lines': manpower_lines,
        'role_cost_structure': role_cost_structure,
        'terms': CLIENT_PROPOSAL_TERMS,
    }


def _paragraph(text, style):
    return Paragraph(str(text or ''), style)


def render_client_proposal_pdf(proposal):
    data = build_client_proposal_document_data(proposal)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Commercial Proposal v{proposal.version_number}",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='DocTitle',
        parent=styles['Title'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#0B3A75'),
        spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name='SectionTitle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=15,
        textColor=colors.HexColor('#0B3A75'),
        spaceBefore=10,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name='Small',
        parent=styles['BodyText'],
        fontSize=8,
        leading=10,
    ))

    story = [
        _paragraph('Commercial Proposal', styles['DocTitle']),
        _paragraph(
            f"Prepared for {data['client']['name']} | Proposal v{data['proposal']['version_number']}",
            styles['BodyText'],
        ),
        Spacer(1, 6),
    ]

    summary_rows = [
        ['Client', data['client']['name']],
        ['Contact', data['client']['contact_person'] or '-'],
        ['Email', data['client']['email'] or '-'],
        ['Prepared on', data['proposal']['prepared_date'] or '-'],
        ['Valid until', data['proposal']['valid_until'] or '-'],
    ]
    story.append(_table(summary_rows, [38 * mm, 118 * mm], styles))

    story.append(_paragraph('Executive Summary', styles['SectionTitle']))
    story.append(_paragraph(
        'This proposal outlines the manpower and commercial structure proposed '
        'for the agreed client requirement. Amounts are presented as monthly '
        'commercial estimates for client review and approval.',
        styles['BodyText'],
    ))

    story.append(_paragraph('Proposed Manpower', styles['SectionTitle']))
    manpower_rows = [['Role / Service', 'Site', 'Headcount', 'Monthly Rate', 'Monthly Total']]
    for line in data['manpower_lines']:
        manpower_rows.append([
            line['role_name'],
            line['site_name'] or '-',
            str(line['manpower_count']),
            _money(line['unit_cost']),
            _money(line['total_cost']),
        ])
    story.append(_table(manpower_rows, [48 * mm, 43 * mm, 22 * mm, 35 * mm, 35 * mm], styles, header=True))

    cs = data['commercial_summary']
    story.append(_paragraph('Commercial Summary', styles['SectionTitle']))
    commercial_rows = [
        ['Total manpower', str(cs['manpower_total'])],
        ['Subtotal', _money(cs['subtotal_amount'])],
        ['Management fee', _money(cs['management_fee_amount'])],
        ['GST', _money(cs['gst_amount']) if cs['gst_applicable'] else 'Not applicable'],
        ['Grand total', _money(cs['grand_total'])],
    ]
    story.append(_table(commercial_rows, [60 * mm, 70 * mm], styles))

    story.append(_paragraph('Role-wise Cost Structure', styles['SectionTitle']))
    for group in data['role_cost_structure']:
        story.append(_paragraph(
            f"{group['title']} | Headcount {group['manpower_count']} | Total {_money(group['total_cost'])}",
            styles['Heading4'],
        ))
        component_rows = [['Component', 'Type', 'Percentage', 'Amount']]
        for component in group['components']:
            component_rows.append([
                component['component_name'],
                component['component_type_label'],
                f"{component['percentage']}%" if component['percentage'] else '-',
                _money(component['amount']),
            ])
        story.append(_table(component_rows, [58 * mm, 44 * mm, 30 * mm, 36 * mm], styles, header=True))

    story.append(_paragraph('Terms and Assumptions', styles['SectionTitle']))
    for index, term in enumerate(data['terms'], start=1):
        story.append(_paragraph(f'{index}. {term}', styles['Small']))

    doc.build(story)
    return buffer.getvalue()


def _table(rows, widths, styles, header=False):
    table_rows = []
    for row in rows:
        table_rows.append([
            _paragraph(cell, styles['Small']) for cell in row
        ])
    table = Table(table_rows, colWidths=widths, hAlign='LEFT', repeatRows=1 if header else 0)
    commands = [
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#CBD5E1')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    if header:
        commands.extend([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EAF2FF')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#0B3A75')),
        ])
    table.setStyle(TableStyle(commands))
    return table
