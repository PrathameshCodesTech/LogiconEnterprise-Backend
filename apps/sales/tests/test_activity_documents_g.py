"""
apps/sales/tests/test_activity_documents_g.py

Phase Sales-Activity-Documents-G focused tests.

Activity logging —
  1.  submit_to_operations logs submitted_to_operations.
  2.  assign_survey_owner logs survey_assigned.
  3.  mark_survey_started logs survey_started.
  4.  mark_survey_completed logs survey_completed.
  5.  approve_sales_role_requirement logs role_requirement_approved.
  6.  generate_proposal_version logs proposal_generated.
  7.  submit_proposal_for_internal_approval logs proposal_submitted_internal.
  8.  workflow approval logs proposal_internally_approved.
  9.  workflow rejection logs proposal_internal_rejected.
  10. send_proposal_to_client logs proposal_sent_to_client.
  11. client response (authenticated path) logs client_response_received.
  12. clone_proposal_for_revision logs proposal_revision_created.
  13. convert_won_sales_lead_to_onboarding_setup logs converted + mobilisation_created.

Documents —
  14. upload document creates SalesDocument and document_uploaded activity.
  15. document proposal_version from different lead is rejected.
  16. document site from different lead is rejected.
  17. GET /api/sales/documents/?lead=<id> filters by lead.
  18. soft-delete via DELETE sets is_active=False and hides from default list.
  19. serializer exposes file_name, file_size, content_type.
  20. oversized file is rejected (>20 MB).
  21. disallowed content-type is rejected.
"""

import io
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import (
    SALES_LEAD_READ, SALES_LEAD_CREATE, SALES_LEAD_UPDATE,
    SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE, SALES_PROPOSAL_UPDATE,
    SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
    SALES_SURVEY_READ, SALES_SURVEY_UPDATE,
)
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.jobs.models import JobRole
from apps.sales.models import (
    SalesLead, SalesLeadSite, SalesRoleRequirement, SiteSurvey,
    SalesLeadActivity, SalesDocument,
)
from apps.sales.services import (
    assign_survey_owner,
    approve_sales_role_requirement,
    clone_proposal_for_revision,
    convert_won_sales_lead_to_onboarding_setup,
    generate_proposal_version,
    mark_lead_won_from_client_approval,
    mark_proposal_internally_approved,
    mark_survey_completed,
    mark_survey_started,
    record_client_response,
    send_proposal_to_client,
    submit_proposal_for_internal_approval,
    submit_to_operations,
)
from apps.sales.proposal_calculation import seed_default_proposal_component_rules
from apps.sales.tests.proposal_wage_fixtures import (
    ensure_location_area_mumbai,
    ensure_minimum_wage,
    ensure_wage_category,
    wire_site_and_requirement_for_wages,
)


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _org(code):
    org = Organization.objects.create(name=f'Org {code}', code=code)
    seed_default_proposal_component_rules(org=org)
    return org


def _scope_node(org):
    return ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )


def _role(org, code, caps):
    role = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})[0]
    bootstrap_role_permissions(role, caps)
    return role


def _user(username, org, role=None, scope_node=None):
    u = User.objects.create_user(username=username, password='pass')
    u.org = org
    u.save()
    if role and scope_node:
        UserRoleAssignment.objects.create(user=u, role=role, scope_node=scope_node)
    return u


def _job_role(org):
    return JobRole.objects.get_or_create(
        org=org, code='guard_g', defaults={'name': 'Guard G'},
    )[0]


def _all_sales_caps():
    return [
        SALES_LEAD_READ, SALES_LEAD_CREATE, SALES_LEAD_UPDATE,
        SALES_PROPOSAL_READ, SALES_PROPOSAL_CREATE, SALES_PROPOSAL_UPDATE,
        SALES_PROPOSAL_APPROVE, SALES_PROPOSAL_SEND_TO_CLIENT,
        SALES_SURVEY_READ, SALES_SURVEY_UPDATE,
    ]


def _base_lead(org, user, client_email='client@test.com'):
    """Create a draft lead with one site + one RR, wired for wages."""
    lead = SalesLead.objects.create(
        org=org, client_name='Acme G', client_email=client_email,
        current_stage='draft', current_status='draft', created_by=user,
    )
    site = SalesLeadSite.objects.create(lead=lead, site_name='Site G', city='Mumbai', state='MH')
    jr = _job_role(org)
    rr = SalesRoleRequirement.objects.create(lead=lead, site=site, job_role=jr, manpower_count=2)
    wage_cat = ensure_wage_category()
    location = ensure_location_area_mumbai()
    ensure_minimum_wage(org, location, wage_cat, jr, monthly_wage=12000)
    wire_site_and_requirement_for_wages(site, rr, location, wage_cat)
    return lead, site, rr, jr


def _submitted_lead(org, user):
    lead, site, rr, jr = _base_lead(org, user)
    submit_to_operations(lead, user)
    survey = SiteSurvey.objects.get(lead=lead, site=site)
    return lead, site, rr, jr, survey


def _won_lead(org, user):
    """Full pipeline to won."""
    lead, site, rr, jr, survey = _submitted_lead(org, user)
    SiteSurvey.objects.filter(lead=lead).update(status='completed')
    lead.current_stage = 'site_survey_completed'
    lead.save(update_fields=['current_stage'])

    proposal = generate_proposal_version(lead, user)
    proposal.status = 'submitted_internal'
    proposal.internal_approval_status = 'in_progress'
    proposal.save(update_fields=['status', 'internal_approval_status', 'updated_at'])
    mark_proposal_internally_approved(proposal, user)
    send_proposal_to_client(proposal, user)
    record_client_response(proposal, 'approved', '', user)
    mark_lead_won_from_client_approval(lead, proposal, user)
    lead.refresh_from_db()
    proposal.refresh_from_db()
    return lead, proposal, site, rr


def _pdf_file(name='test.pdf', size=1024):
    content = b'%PDF-1.4 ' + b'x' * (size - 9)
    return SimpleUploadedFile(name, content, content_type='application/pdf')


# ─── Activity logging tests ───────────────────────────────────────────────────

class TestActivityLogging(TestCase):

    def setUp(self):
        self.org = _org('alog')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_alog', _all_sales_caps())
        self.user = _user('sm_alog', self.org, self.role, self.n)

    def _activity_count(self, lead, atype):
        return SalesLeadActivity.objects.filter(lead=lead, activity_type=atype).count()

    def test_01_submit_to_operations_logs_activity(self):
        lead, *_ = _base_lead(self.org, self.user)
        submit_to_operations(lead, self.user)
        self.assertEqual(self._activity_count(lead, 'submitted_to_operations'), 1)

    def test_02_assign_survey_owner_logs_activity(self):
        lead, site, rr, jr, survey = _submitted_lead(self.org, self.user)
        assign_survey_owner(survey, self.user, self.user)
        self.assertEqual(self._activity_count(lead, 'survey_assigned'), 1)
        entry = SalesLeadActivity.objects.get(lead=lead, activity_type='survey_assigned')
        self.assertEqual(entry.metadata['assigned_to_id'], self.user.pk)

    def test_03_mark_survey_started_logs_activity(self):
        lead, site, rr, jr, survey = _submitted_lead(self.org, self.user)
        mark_survey_started(survey, self.user)
        self.assertEqual(self._activity_count(lead, 'survey_started'), 1)

    def test_04_mark_survey_completed_logs_activity(self):
        lead, site, rr, jr, survey = _submitted_lead(self.org, self.user)
        SiteSurvey.objects.filter(pk=survey.pk).update(status='in_progress')
        survey.refresh_from_db()
        mark_survey_completed(survey, self.user)
        self.assertEqual(self._activity_count(lead, 'survey_completed'), 1)

    def test_05_approve_role_requirement_logs_activity(self):
        lead, site, rr, jr, survey = _submitted_lead(self.org, self.user)
        approve_sales_role_requirement(rr, self.user)
        self.assertEqual(self._activity_count(lead, 'role_requirement_approved'), 1)
        entry = SalesLeadActivity.objects.get(lead=lead, activity_type='role_requirement_approved')
        self.assertEqual(entry.metadata['job_role'], jr.name)

    def test_06_generate_proposal_logs_activity(self):
        lead, site, rr, jr, survey = _submitted_lead(self.org, self.user)
        SiteSurvey.objects.filter(lead=lead).update(status='completed')
        lead.current_stage = 'site_survey_completed'
        lead.save(update_fields=['current_stage'])
        proposal = generate_proposal_version(lead, self.user)
        self.assertEqual(self._activity_count(lead, 'proposal_generated'), 1)
        entry = SalesLeadActivity.objects.get(lead=lead, activity_type='proposal_generated')
        self.assertEqual(entry.proposal_version_id, proposal.pk)

    def test_07_submit_internal_approval_logs_activity(self):
        from apps.workflow.tests.helpers import bootstrap_legacy_workflow
        lead, site, rr, jr, survey = _submitted_lead(self.org, self.user)
        SiteSurvey.objects.filter(lead=lead).update(status='completed')
        lead.current_stage = 'site_survey_completed'
        lead.save(update_fields=['current_stage'])
        proposal = generate_proposal_version(lead, self.user)

        bootstrap_legacy_workflow(self.org, 'sales_proposal', [(1, 'review', 'Review')], self.user)
        submit_proposal_for_internal_approval(proposal, self.user)
        self.assertEqual(self._activity_count(lead, 'proposal_submitted_internal'), 1)

    def test_10_send_to_client_logs_activity(self):
        lead, site, rr, jr, survey = _submitted_lead(self.org, self.user)
        SiteSurvey.objects.filter(lead=lead).update(status='completed')
        lead.current_stage = 'site_survey_completed'
        lead.save(update_fields=['current_stage'])
        proposal = generate_proposal_version(lead, self.user)
        proposal.status = 'submitted_internal'
        proposal.internal_approval_status = 'in_progress'
        proposal.save(update_fields=['status', 'internal_approval_status', 'updated_at'])
        mark_proposal_internally_approved(proposal, self.user)
        send_proposal_to_client(proposal, self.user)
        self.assertEqual(self._activity_count(lead, 'proposal_sent_to_client'), 1)

    def test_11_client_response_received_logs_activity(self):
        lead, site, rr, jr, survey = _submitted_lead(self.org, self.user)
        SiteSurvey.objects.filter(lead=lead).update(status='completed')
        lead.current_stage = 'site_survey_completed'
        lead.save(update_fields=['current_stage'])
        proposal = generate_proposal_version(lead, self.user)
        proposal.status = 'submitted_internal'
        proposal.internal_approval_status = 'in_progress'
        proposal.save(update_fields=['status', 'internal_approval_status', 'updated_at'])
        mark_proposal_internally_approved(proposal, self.user)
        send_proposal_to_client(proposal, self.user)
        record_client_response(proposal, 'approved', 'Looks good', self.user)
        self.assertEqual(self._activity_count(lead, 'client_response_received'), 1)
        entry = SalesLeadActivity.objects.get(lead=lead, activity_type='client_response_received')
        self.assertEqual(entry.metadata['response'], 'approved')

    def test_12_clone_revision_logs_activity(self):
        lead, site, rr, jr, survey = _submitted_lead(self.org, self.user)
        SiteSurvey.objects.filter(lead=lead).update(status='completed')
        lead.current_stage = 'site_survey_completed'
        lead.save(update_fields=['current_stage'])
        proposal = generate_proposal_version(lead, self.user)
        new_p = clone_proposal_for_revision(proposal, self.user)
        self.assertEqual(self._activity_count(lead, 'proposal_revision_created'), 1)
        entry = SalesLeadActivity.objects.get(lead=lead, activity_type='proposal_revision_created')
        self.assertEqual(entry.metadata['source_version_id'], proposal.pk)
        self.assertEqual(entry.metadata['new_version_id'], new_p.pk)

    def test_13_conversion_logs_converted_and_mobilisation_created(self):
        lead, proposal, site, rr = _won_lead(self.org, self.user)
        convert_won_sales_lead_to_onboarding_setup(lead, self.user)
        self.assertEqual(self._activity_count(lead, 'converted'), 1)
        self.assertEqual(self._activity_count(lead, 'mobilisation_created'), 1)


class TestWorkflowActivityHooks(TestCase):
    """Workflow approval/rejection wires proposal_internally_approved/rejected."""

    def setUp(self):
        from apps.workflow.tests.helpers import bootstrap_legacy_workflow
        self.org = _org('wfhook')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_wfhook', _all_sales_caps())
        self.user = _user('sm_wfhook', self.org, self.role, self.n)
        lead, site, rr, jr, survey = _submitted_lead(self.org, self.user)
        SiteSurvey.objects.filter(lead=lead).update(status='completed')
        lead.current_stage = 'site_survey_completed'
        lead.save(update_fields=['current_stage'])
        self.proposal = generate_proposal_version(lead, self.user)
        self.lead = lead
        bootstrap_legacy_workflow(self.org, 'sales_proposal', [(1, 'review', 'Review')], self.user)

    def _start_workflow(self):
        from apps.workflow.services import start_sales_proposal_workflow
        instance = start_sales_proposal_workflow(self.proposal, self.user)
        step = instance.steps.filter(status='active').first()
        return instance, step

    def test_08_workflow_approval_logs_proposal_internally_approved(self):
        from apps.workflow.services import act_on_step
        instance, step = self._start_workflow()
        act_on_step(step, self.user, 'approve')
        self.assertEqual(
            SalesLeadActivity.objects.filter(
                lead=self.lead, activity_type='proposal_internally_approved',
            ).count(),
            1,
        )

    def test_09_workflow_rejection_logs_proposal_internal_rejected(self):
        from apps.workflow.services import act_on_step
        instance, step = self._start_workflow()
        act_on_step(step, self.user, 'reject', 'not good')
        self.assertEqual(
            SalesLeadActivity.objects.filter(
                lead=self.lead, activity_type='proposal_internal_rejected',
            ).count(),
            1,
        )


# ─── Document tests ───────────────────────────────────────────────────────────

class TestSalesDocuments(TestCase):

    def setUp(self):
        self.org = _org('doc')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_doc', _all_sales_caps())
        self.user = _user('sm_doc', self.org, self.role, self.n)
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.user)
        self.lead, self.site, self.rr, self.jr, self.survey = _submitted_lead(self.org, self.user)

    def test_14_upload_creates_document_and_activity(self):
        before_docs = SalesDocument.objects.filter(lead=self.lead).count()
        before_acts = SalesLeadActivity.objects.filter(
            lead=self.lead, activity_type='document_uploaded',
        ).count()
        f = _pdf_file()
        resp = self.client_api.post(
            '/api/sales/documents/',
            {
                'lead': self.lead.pk,
                'document_type': 'rfp',
                'title': 'RFP Document',
                'file': f,
            },
            format='multipart',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(SalesDocument.objects.filter(lead=self.lead).count(), before_docs + 1)
        self.assertEqual(
            SalesLeadActivity.objects.filter(
                lead=self.lead, activity_type='document_uploaded',
            ).count(),
            before_acts + 1,
        )

    def test_15_proposal_version_from_different_lead_rejected(self):
        from apps.sales.models import ProposalVersion
        other_lead = SalesLead.objects.create(
            org=self.org, client_name='Other', client_email='x@x.com',
            current_stage='draft', current_status='draft', created_by=self.user,
        )
        other_proposal = ProposalVersion.objects.create(
            lead=other_lead, version_number=1, grand_total=0, subtotal_amount=0,
            management_fee_amount=0, gst_amount=0, manpower_total=0,
            status='generated', internal_approval_status='not_started',
            client_approval_status='not_sent',
        )
        f = _pdf_file()
        resp = self.client_api.post(
            '/api/sales/documents/',
            {
                'lead': self.lead.pk,
                'proposal_version': other_proposal.pk,
                'document_type': 'rfp',
                'title': 'Bad Proposal Link',
                'file': f,
            },
            format='multipart',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('proposal_version', resp.data)

    def test_16_site_from_different_lead_rejected(self):
        other_lead = SalesLead.objects.create(
            org=self.org, client_name='Other2', client_email='y@y.com',
            current_stage='draft', current_status='draft', created_by=self.user,
        )
        other_site = SalesLeadSite.objects.create(lead=other_lead, site_name='Other Site 2')
        f = _pdf_file()
        resp = self.client_api.post(
            '/api/sales/documents/',
            {
                'lead': self.lead.pk,
                'site': other_site.pk,
                'document_type': 'site_survey_document',
                'title': 'Bad Site Link',
                'file': f,
            },
            format='multipart',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('site', resp.data)

    def test_17_list_filters_by_lead(self):
        other_lead = SalesLead.objects.create(
            org=self.org, client_name='Other3', client_email='z@z.com',
            current_stage='draft', current_status='draft', created_by=self.user,
        )
        # Upload to self.lead
        f1 = _pdf_file('a.pdf')
        self.client_api.post(
            '/api/sales/documents/',
            {'lead': self.lead.pk, 'document_type': 'rfp', 'title': 'Doc A', 'file': f1},
            format='multipart',
        )
        resp = self.client_api.get(f'/api/sales/documents/?lead={self.lead.pk}')
        self.assertEqual(resp.status_code, 200)
        for doc in resp.data['results']:
            self.assertEqual(doc['lead'], self.lead.pk)

    def test_18_soft_delete_hides_document(self):
        f = _pdf_file('del.pdf')
        create_resp = self.client_api.post(
            '/api/sales/documents/',
            {'lead': self.lead.pk, 'document_type': 'rfp', 'title': 'To Delete', 'file': f},
            format='multipart',
        )
        doc_id = create_resp.data['id']
        del_resp = self.client_api.delete(f'/api/sales/documents/{doc_id}/')
        self.assertEqual(del_resp.status_code, 204)
        doc = SalesDocument.objects.get(pk=doc_id)
        self.assertFalse(doc.is_active)
        list_resp = self.client_api.get(f'/api/sales/documents/?lead={self.lead.pk}&is_active=True')
        ids = [d['id'] for d in list_resp.data.get('results', [])]
        self.assertNotIn(doc_id, ids)

    def test_19_serializer_exposes_file_metadata(self):
        f = _pdf_file('meta.pdf', size=2048)
        resp = self.client_api.post(
            '/api/sales/documents/',
            {'lead': self.lead.pk, 'document_type': 'rfp', 'title': 'Meta Test', 'file': f},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 201)
        doc = SalesDocument.objects.get(pk=resp.data['id'])
        self.assertEqual(doc.file_name, 'meta.pdf')
        self.assertEqual(doc.file_size, 2048)
        self.assertEqual(doc.content_type, 'application/pdf')

        read_resp = self.client_api.get(f'/api/sales/documents/{doc.pk}/')
        self.assertEqual(read_resp.data['file_name'], 'meta.pdf')
        self.assertEqual(read_resp.data['file_size'], 2048)
        self.assertEqual(read_resp.data['content_type'], 'application/pdf')
        self.assertIn('file_url', read_resp.data)

    def test_20_oversized_file_rejected(self):
        big_content = b'x' * (21 * 1024 * 1024)
        big_file = SimpleUploadedFile('big.pdf', big_content, content_type='application/pdf')
        resp = self.client_api.post(
            '/api/sales/documents/',
            {'lead': self.lead.pk, 'document_type': 'rfp', 'title': 'Big', 'file': big_file},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('file', resp.data)

    def test_21_disallowed_content_type_rejected(self):
        bad_file = SimpleUploadedFile('evil.exe', b'MZ\x90\x00', content_type='application/octet-stream')
        resp = self.client_api.post(
            '/api/sales/documents/',
            {'lead': self.lead.pk, 'document_type': 'other', 'title': 'Evil', 'file': bad_file},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('file', resp.data)


class TestActivityEndpoint(TestCase):
    """GET /api/sales/activities/?lead=<id> returns activities for that lead."""

    def setUp(self):
        self.org = _org('actep')
        self.n = _scope_node(self.org)
        self.role = _role(self.org, 'sm_actep', _all_sales_caps())
        self.user = _user('sm_actep', self.org, self.role, self.n)
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.user)
        self.lead, self.site, self.rr, self.jr, self.survey = _submitted_lead(self.org, self.user)

    def test_list_activities_filters_by_lead(self):
        resp = self.client_api.get(f'/api/sales/activities/?lead={self.lead.pk}')
        self.assertEqual(resp.status_code, 200)
        for entry in resp.data.get('results', []):
            self.assertEqual(entry['lead'], self.lead.pk)
        types = {e['activity_type'] for e in resp.data.get('results', [])}
        self.assertIn('submitted_to_operations', types)

    def test_list_activities_filters_by_type(self):
        resp = self.client_api.get(
            f'/api/sales/activities/?lead={self.lead.pk}&activity_type=submitted_to_operations'
        )
        self.assertEqual(resp.status_code, 200)
        for entry in resp.data.get('results', []):
            self.assertEqual(entry['activity_type'], 'submitted_to_operations')

    def test_recent_activities_on_lead_detail(self):
        resp = self.client_api.get(f'/api/sales/leads/{self.lead.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('recent_activities', resp.data)
        self.assertIn('documents_count', resp.data)
        self.assertIsInstance(resp.data['recent_activities'], list)
        self.assertLessEqual(len(resp.data['recent_activities']), 10)
