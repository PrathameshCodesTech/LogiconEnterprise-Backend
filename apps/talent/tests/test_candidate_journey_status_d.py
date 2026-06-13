"""
Candidate journey status contract.

The frontend must display these backend-derived fields instead of rebuilding
candidate state from applications/offers/deployments.
"""

from datetime import date

from django.test import TestCase

from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.deployment.models import Employee, SiteDeployment
from apps.hiring.models import HiringApplication, Offer, PipelineStage
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile
from apps.talent.models import Candidate
from apps.talent.serializers import CandidateSerializer
from apps.talent.services import candidate_journey_status


def _org(code='journey'):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _scope_tree(org):
    company = ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )
    client = Client.objects.create(
        org=org, name=f'Client {org.code}', code=f'cl-{org.code}',
        scope_node=company, is_active=True,
    )
    client_node = ScopeNode.objects.create(
        org=org, code=f'cl-{org.code}', name=f'cl-{org.code}',
        node_type='client', parent=company, depth=1,
        path=f'{org.code}/cl-{org.code}', is_active=True,
    )
    site = SiteProfile.objects.create(
        org=org, client=client, scope_node=client_node,
        name=f'Site {org.code}', code=f'site-{org.code}', is_active=True,
    )
    return company, client, site


def _user(org):
    user = User.objects.create_user(username='journey.hr', password='pass')
    user.org = org
    user.save(update_fields=['org'])
    return user


def _candidate(org, phone='9876543210'):
    return Candidate.objects.create(
        org=org,
        phone=phone,
        phone_normalized=phone,
        first_name='Test',
        last_name='Candidate',
        source='manual',
    )


class CandidateJourneyStatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = _org()
        cls.company, cls.client, cls.site = _scope_tree(cls.org)
        cls.user = _user(cls.org)
        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Electrician', code='electrician',
        )
        cls.mrf = ManpowerRequest.objects.create(
            org=cls.org, site=cls.site, mrf_type='new_hiring',
            billing_type='billable', status='approved', requested_by=cls.user,
        )
        cls.mrf_line = MRFLineItem.objects.create(
            mrf=cls.mrf, job_role=cls.job_role, headcount=2,
        )
        cls.stage = PipelineStage.objects.create(
            org=cls.org, name='Shortlisted', code='shortlisted',
            order=1, stage_type='shortlisted',
        )

    def _application(self, candidate, status='shortlisted', client_decision=None):
        return HiringApplication.objects.create(
            org=self.org,
            candidate=candidate,
            mrf=self.mrf,
            mrf_line_item=self.mrf_line,
            site=self.site,
            job_role=self.job_role,
            current_stage=self.stage,
            status=status,
            client_decision=client_decision,
        )

    def test_unknown_candidate_has_unknown_journey(self):
        candidate = _candidate(self.org, '9876543211')

        result = candidate_journey_status(candidate)

        self.assertEqual(result['journey_status'], 'unknown')
        self.assertEqual(result['journey_status_label'], 'Unknown')
        self.assertIsNone(result['latest_application_id'])

    def test_client_review_decision_maps_to_client_approved(self):
        candidate = _candidate(self.org, '9876543212')
        app = self._application(candidate, status='client_review', client_decision='approved')

        result = candidate_journey_status(candidate)

        self.assertEqual(result['journey_status'], 'client_approved')
        self.assertEqual(result['latest_application_id'], app.pk)
        self.assertEqual(result['latest_application_status'], 'client_review')

    def test_offer_status_overrides_selected_application(self):
        candidate = _candidate(self.org, '9876543213')
        app = self._application(candidate, status='selected')
        Offer.objects.create(
            hiring_application=app,
            offered_ctc='300000.00',
            joining_date=date.today(),
            status='released',
        )

        result = candidate_journey_status(candidate)

        self.assertEqual(result['journey_status'], 'offer_released')
        self.assertEqual(result['latest_offer_status'], 'released')

    def test_active_deployment_maps_to_deployed(self):
        candidate = _candidate(self.org, '9876543214')
        employee = Employee.objects.create(
            org=self.org,
            candidate=candidate,
            employee_code='EMP-JOURNEY-001',
            first_name='Test',
            last_name='Candidate',
            phone='9876543214',
            job_role=self.job_role,
            status='active',
            joined_on=date.today(),
        )
        deployment = SiteDeployment.objects.create(
            org=self.org,
            employee=employee,
            site=self.site,
            job_role=self.job_role,
            mrf_line_item=self.mrf_line,
            status='active',
            start_date=date.today(),
        )

        result = candidate_journey_status(candidate)

        self.assertEqual(result['journey_status'], 'deployed')
        self.assertEqual(result['employee_id'], employee.pk)
        self.assertEqual(result['deployment_id'], deployment.pk)
        self.assertEqual(result['deployment_status'], 'active')

    def test_exited_employee_maps_to_exited(self):
        candidate = _candidate(self.org, '9876543215')
        employee = Employee.objects.create(
            org=self.org,
            candidate=candidate,
            employee_code='EMP-JOURNEY-002',
            first_name='Test',
            last_name='Candidate',
            phone='9876543215',
            job_role=self.job_role,
            status='exited',
            joined_on=date.today(),
            exited_on=date.today(),
        )

        result = candidate_journey_status(candidate)

        self.assertEqual(result['journey_status'], 'exited')
        self.assertEqual(result['employee_id'], employee.pk)
        self.assertEqual(result['employee_status'], 'exited')

    def test_candidate_serializer_exposes_journey_fields(self):
        candidate = _candidate(self.org, '9876543216')
        self._application(candidate, status='shortlisted')

        data = CandidateSerializer(candidate).data

        self.assertEqual(data['journey_status'], 'shortlisted')
        self.assertEqual(data['journey_status_label'], 'Shortlisted')
        self.assertEqual(data['latest_application_status'], 'shortlisted')
        self.assertIn('employee_id', data)
        self.assertIn('deployment_status', data)
