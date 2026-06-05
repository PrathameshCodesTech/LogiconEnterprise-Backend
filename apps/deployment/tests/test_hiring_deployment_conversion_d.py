"""
apps/deployment/tests/test_hiring_deployment_conversion_d.py

Phase Hiring-Deployment-Conversion-D — 20 tests covering:
  Allowed/blocked application statuses (1-4),
  Employee creation from Candidate (5-6),
  SiteDeployment creation and linkage (7-8),
  Candidate and application lifecycle updates (9-11),
  Idempotency (12-13),
  Employee reuse and phone conflict (14-15),
  Custom / duplicate employee_code (16-17),
  Cross-org block (18),
  Permission checks (19-20),
  Billing type default from MRF (bonus).
"""

from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.deployment.models import Employee, SiteDeployment
from apps.deployment.services import convert_hiring_application_to_deployment
from apps.hiring.models import (
    ApplicationStageHistory, HiringApplication, Offer, PipelineStage,
)
from apps.jobs.models import JobRole
from apps.mrf.models import ManpowerRequest, MRFLineItem
from apps.sites.models import Client, SiteProfile
from apps.talent.models import Candidate


# ─── Fixture helpers ──────────────────────────────────────────────────────────

def _org(code):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _scope_tree(org):
    n_co = ScopeNode.objects.create(
        org=org, code=org.code, name=org.code, node_type='company',
        parent=None, depth=0, path=org.code, is_active=True,
    )
    client = Client.objects.create(
        org=org, name=f'Client {org.code}', code=f'cl-{org.code}',
        scope_node=n_co, is_active=True,
    )
    n_cl = ScopeNode.objects.create(
        org=org, code=f'cl-{org.code}', name=f'cl-{org.code}', node_type='client',
        parent=n_co, depth=1, path=f'{org.code}/cl-{org.code}', is_active=True,
    )
    site = SiteProfile.objects.create(
        org=org, client=client, scope_node=n_cl,
        name=f'Site {org.code}', code=f'site-{org.code}', is_active=True,
    )
    return n_co, n_cl, client, site


def _role(org, code):
    r, _ = AccessRole.objects.get_or_create(org=org, code=code, defaults={'name': code})
    bootstrap_role_permissions(r)
    return r


def _user(username, org, role_obj=None, scope_node=None, is_superuser=False):
    u = User.objects.create_user(username=username, password='pass')
    u.org = org
    u.is_superuser = is_superuser
    u.is_staff = is_superuser
    u.save()
    if role_obj and scope_node:
        UserRoleAssignment.objects.create(user=u, role=role_obj, scope_node=scope_node)
    return u


def _candidate(org, phone, first='Test', last='Cand'):
    return Candidate.objects.create(
        org=org, phone=phone, phone_normalized=phone,
        first_name=first, last_name=last, source='manual',
    )


def _application(org, candidate, mrf, mrf_li, site, job_role, stage, app_status='selected'):
    return HiringApplication.objects.create(
        org=org, candidate=candidate, mrf=mrf,
        mrf_line_item=mrf_li, site=site, job_role=job_role,
        current_stage=stage, status=app_status,
    )


# ─── Shared base setup ────────────────────────────────────────────────────────

class ConversionBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org('conv')
        cls.n_co, cls.n_cl, cls.client_obj, cls.site = _scope_tree(cls.org)

        cls.r_admin = _role(cls.org, 'hr_admin')
        cls.r_exec = _role(cls.org, 'hr_executive')

        cls.hr_admin = _user('conv_hr_admin', cls.org, cls.r_admin, cls.n_co)
        cls.hr_exec = _user('conv_hr_exec', cls.org, cls.r_exec, cls.n_co)
        cls.superuser = _user('conv_super', cls.org, is_superuser=True)

        cls.job_role = JobRole.objects.create(
            org=cls.org, name='Guard CONV', code='guard-conv',
        )
        cls.mrf = ManpowerRequest.objects.create(
            org=cls.org, site=cls.site, mrf_type='new_hiring',
            billing_type='billable', status='approved', requested_by=cls.hr_admin,
        )
        cls.mrf_li = MRFLineItem.objects.create(
            mrf=cls.mrf, job_role=cls.job_role, headcount=5,
        )
        cls.stage = PipelineStage.objects.create(
            org=cls.org, name='Screening CONV', code='screening-conv',
            order=1, stage_type='screening',
        )

    def setUp(self):
        self.api = APIClient()

    def _auth(self, user):
        self.api.force_authenticate(user=user)

    def _convert_url(self, app_id):
        return f'/api/hiring/applications/{app_id}/convert-to-deployment/'

    def _make_candidate(self, phone_suffix):
        return _candidate(self.org, f'99000{phone_suffix}', first='John', last=f'User{phone_suffix}')

    def _make_application(self, candidate, app_status='selected'):
        app = _application(
            self.org, candidate, self.mrf, self.mrf_li,
            self.site, self.job_role, self.stage, app_status,
        )
        if app_status in ('selected', 'offer_accepted'):
            Offer.objects.create(
                hiring_application=app,
                offered_ctc='300000.00',
                status='accepted',
            )
            app.status = 'offer_accepted'
            app.save(update_fields=['status'])
        return app


# ═══════════════════════════════════════════════════════════════════════════════
# Group 1 — Allowed / blocked statuses (1-4)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConversionStatusGuard(ConversionBase):

    def test_01_accepted_offer_application_converts_successfully(self):
        """Application with an accepted offer can be converted."""
        cand = self._make_candidate('0101')
        app = self._make_application(cand, 'selected')
        result = convert_hiring_application_to_deployment(app, self.superuser)
        self.assertIsInstance(result['employee'], Employee)
        self.assertIsInstance(result['deployment'], SiteDeployment)

    def test_02_offer_accepted_application_converts_successfully(self):
        """Application with status='offer_accepted' can be converted."""
        cand = self._make_candidate('0201')
        app = self._make_application(cand, 'offer_accepted')
        result = convert_hiring_application_to_deployment(app, self.superuser)
        self.assertIsNotNone(result['employee'])

    def test_03_shortlisted_application_blocked(self):
        """status='shortlisted' raises ValidationError."""
        from rest_framework.exceptions import ValidationError
        cand = self._make_candidate('0301')
        app = self._make_application(cand, 'shortlisted')
        with self.assertRaises(ValidationError):
            convert_hiring_application_to_deployment(app, self.superuser)

    def test_04_rejected_application_blocked(self):
        """status='rejected' raises ValidationError."""
        from rest_framework.exceptions import ValidationError
        cand = self._make_candidate('0401')
        app = self._make_application(cand, 'rejected')
        with self.assertRaises(ValidationError):
            convert_hiring_application_to_deployment(app, self.superuser)

    def test_04b_selected_without_offer_blocked(self):
        """A selected application cannot deploy until an offer is accepted."""
        from rest_framework.exceptions import ValidationError
        cand = self._make_candidate('0402')
        app = _application(
            self.org, cand, self.mrf, self.mrf_li,
            self.site, self.job_role, self.stage, 'selected',
        )
        with self.assertRaises(ValidationError) as ctx:
            convert_hiring_application_to_deployment(app, self.superuser)
        self.assertIn('Accepted offer', str(ctx.exception))


# ═══════════════════════════════════════════════════════════════════════════════
# Group 2 — Employee and SiteDeployment creation (5-8)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConversionCreation(ConversionBase):

    def test_05_creates_employee_from_candidate(self):
        """A new Employee is created from the Candidate's data."""
        cand = self._make_candidate('0501')
        cand.email = 'test05@conv.com'
        cand.save()
        app = self._make_application(cand)
        result = convert_hiring_application_to_deployment(app, self.superuser)
        emp = result['employee']
        self.assertEqual(emp.first_name, cand.first_name)
        self.assertEqual(emp.last_name, cand.last_name)
        self.assertEqual(emp.org, self.org)
        self.assertEqual(emp.candidate, cand)
        self.assertTrue(result['created_employee'])

    def test_06_creates_site_deployment_linked_to_mrf_line_item(self):
        """SiteDeployment is linked to the MRF line item and hiring application."""
        cand = self._make_candidate('0601')
        app = self._make_application(cand)
        result = convert_hiring_application_to_deployment(app, self.superuser)
        dep = result['deployment']
        self.assertEqual(dep.mrf_line_item, self.mrf_li)
        self.assertEqual(dep.hiring_application, app)
        self.assertEqual(dep.site, self.site)
        self.assertEqual(dep.job_role, self.job_role)
        self.assertTrue(result['created_deployment'])

    def test_07_application_status_becomes_deployed(self):
        """HiringApplication status is updated to 'deployed' after conversion."""
        cand = self._make_candidate('0701')
        app = self._make_application(cand)
        PipelineStage.objects.create(
            org=self.org, name='Deployed', code='deployed',
            order=70, stage_type='onboarding', is_terminal=True,
        )
        convert_hiring_application_to_deployment(app, self.superuser)
        app.refresh_from_db()
        self.assertEqual(app.status, 'deployed')
        self.assertEqual(app.current_stage.code, 'deployed')

    def test_08_application_stage_history_created(self):
        """ApplicationStageHistory row is created for the status transition."""
        cand = self._make_candidate('0801')
        app = self._make_application(cand)
        convert_hiring_application_to_deployment(app, self.superuser)
        history = ApplicationStageHistory.objects.filter(
            hiring_application=app, to_status='deployed',
        )
        self.assertTrue(history.exists())


# ═══════════════════════════════════════════════════════════════════════════════
# Group 3 — Candidate lifecycle updates (9-11)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConversionLifecycle(ConversionBase):

    def test_09_candidate_lifecycle_status_becomes_employee_converted(self):
        """Candidate.lifecycle_status is set to 'employee_converted'."""
        cand = self._make_candidate('0901')
        app = self._make_application(cand)
        convert_hiring_application_to_deployment(app, self.superuser)
        cand.refresh_from_db()
        self.assertEqual(cand.lifecycle_status, 'employee_converted')

    def test_10_candidate_availability_status_becomes_currently_deployed(self):
        """Candidate.availability_status is set to 'currently_deployed'."""
        cand = self._make_candidate('1001')
        app = self._make_application(cand)
        convert_hiring_application_to_deployment(app, self.superuser)
        cand.refresh_from_db()
        self.assertEqual(cand.availability_status, 'currently_deployed')

    def test_11_deployment_start_date_defaults_to_today(self):
        """deployment_start_date defaults to today when not supplied."""
        cand = self._make_candidate('1101')
        app = self._make_application(cand)
        result = convert_hiring_application_to_deployment(app, self.superuser)
        self.assertEqual(result['deployment'].start_date, date.today())


# ═══════════════════════════════════════════════════════════════════════════════
# Group 4 — Idempotency (12-13)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConversionIdempotency(ConversionBase):

    def test_12_second_conversion_is_idempotent(self):
        """Converting the same application twice returns the same records."""
        cand = self._make_candidate('1201')
        app = self._make_application(cand)
        r1 = convert_hiring_application_to_deployment(app, self.superuser)
        r2 = convert_hiring_application_to_deployment(app, self.superuser)
        self.assertEqual(r1['employee'].pk, r2['employee'].pk)
        self.assertEqual(r1['deployment'].pk, r2['deployment'].pk)
        self.assertFalse(r2['created_deployment'])

    def test_13_idempotent_no_duplicate_employee_or_deployment(self):
        """No duplicate Employee/SiteDeployment rows after repeated calls."""
        cand = self._make_candidate('1301')
        app = self._make_application(cand)
        convert_hiring_application_to_deployment(app, self.superuser)
        convert_hiring_application_to_deployment(app, self.superuser)
        self.assertEqual(Employee.objects.filter(candidate=cand).count(), 1)
        self.assertEqual(SiteDeployment.objects.filter(hiring_application=app).count(), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 5 — Employee reuse and phone conflict (14-15)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmployeeReuse(ConversionBase):

    def test_14_existing_employee_for_same_candidate_is_reused(self):
        """If Employee already exists for candidate, it is reused not duplicated."""
        cand = self._make_candidate('1401')
        existing_emp = Employee.objects.create(
            org=self.org, candidate=cand,
            employee_code='EMP-CONV-EXISTING-1401',
            first_name=cand.first_name, last_name=cand.last_name,
            phone=cand.phone, job_role=self.job_role,
        )
        app = self._make_application(cand)
        result = convert_hiring_application_to_deployment(app, self.superuser)
        self.assertEqual(result['employee'].pk, existing_emp.pk)
        self.assertFalse(result['created_employee'])
        self.assertEqual(Employee.objects.filter(candidate=cand).count(), 1)

    def test_15_same_phone_different_candidate_blocked_by_default(self):
        """Same normalized phone on Employee linked to different candidate → ValidationError."""
        from rest_framework.exceptions import ValidationError
        phone_a = '9900001501'
        phone_b = '9900001502'

        cand_a = _candidate(self.org, phone_a, first='Alice', last='A1501')
        emp_a = Employee.objects.create(
            org=self.org, candidate=cand_a,
            employee_code='EMP-CONV-1501',
            first_name='Alice', last_name='A1501',
            phone=phone_a,
        )

        cand_b = _candidate(self.org, phone_b, first='Bob', last='B1501')
        # Force emp_a's phone_normalized to match cand_b's phone so the service
        # detects the collision (emp_a.candidate != cand_b).
        Employee.objects.filter(pk=emp_a.pk).update(phone_normalized=phone_b)

        app_b = self._make_application(cand_b)
        with self.assertRaises(ValidationError):
            convert_hiring_application_to_deployment(app_b, self.superuser)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 6 — Employee code (16-17)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmployeeCode(ConversionBase):

    def test_16_custom_employee_code_accepted(self):
        """Providing a custom employee_code uses it."""
        cand = self._make_candidate('1601')
        app = self._make_application(cand)
        result = convert_hiring_application_to_deployment(
            app, self.superuser, employee_code='CUSTOM-1601',
        )
        self.assertEqual(result['employee'].employee_code, 'CUSTOM-1601')

    def test_17_duplicate_employee_code_rejected(self):
        """Providing an already-used employee_code raises ValidationError."""
        from rest_framework.exceptions import ValidationError
        cand_a = self._make_candidate('1701')
        Employee.objects.create(
            org=self.org, candidate=cand_a,
            employee_code='DUP-CODE-1701',
            first_name='A', last_name='B',
        )
        cand_b = self._make_candidate('1702')
        app_b = self._make_application(cand_b)
        with self.assertRaises(ValidationError):
            convert_hiring_application_to_deployment(
                app_b, self.superuser, employee_code='DUP-CODE-1701',
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Group 7 — Cross-org and permissions (18-20)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConversionSecurity(ConversionBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org2 = _org('conv2')
        cls.n_co2, cls.n_cl2, cls.client2, cls.site2 = _scope_tree(cls.org2)
        cls.r_admin2 = _role(cls.org2, 'hr_admin')
        # Non-superuser — scoped only to org2 so they cannot see org1's applications
        cls.hr2 = _user('conv2_hr', cls.org2, cls.r_admin2, cls.n_co2)

    def test_18_cross_org_application_not_accessible(self):
        """User from org2 cannot retrieve org1's application via API."""
        cand = self._make_candidate('1801')
        app = self._make_application(cand, 'selected')
        self._auth(self.hr2)
        resp = self.api.post(self._convert_url(app.pk), {}, format='json')
        # 404 because scope filter hides the application from org2 user
        self.assertIn(resp.status_code, (403, 404))

    def test_19_permission_without_deployment_create_blocked(self):
        """hr_executive has no deployment.create → 403."""
        cand = self._make_candidate('1901')
        app = self._make_application(cand, 'selected')
        self._auth(self.hr_exec)
        resp = self.api.post(self._convert_url(app.pk), {}, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_20_superuser_can_convert_via_api(self):
        """Superuser converts an application successfully via API."""
        cand = self._make_candidate('2001')
        app = self._make_application(cand, 'selected')
        self._auth(self.superuser)
        resp = self.api.post(
            self._convert_url(app.pk),
            {'deployment_status': 'planned'},
            format='json',
        )
        self.assertIn(resp.status_code, (200, 201), resp.data)
        self.assertIn('employee', resp.data)
        self.assertIn('deployment', resp.data)
        self.assertEqual(resp.data['application']['status'], 'deployed')


# ═══════════════════════════════════════════════════════════════════════════════
# Bonus — billing_type defaults from MRF
# ═══════════════════════════════════════════════════════════════════════════════

class TestConversionDefaults(ConversionBase):

    def test_billing_type_defaults_from_mrf_when_not_supplied(self):
        """billing_type falls back to mrf.billing_type ('billable')."""
        cand = self._make_candidate('B101')
        app = self._make_application(cand)
        result = convert_hiring_application_to_deployment(app, self.superuser)
        self.assertEqual(result['deployment'].billing_type, self.mrf.billing_type)

    def test_deployment_start_date_uses_supplied_value(self):
        """Provided deployment_start_date is used."""
        cand = self._make_candidate('B201')
        app = self._make_application(cand)
        target = date(2026, 6, 1)
        result = convert_hiring_application_to_deployment(
            app, self.superuser, deployment_start_date=target,
        )
        self.assertEqual(result['deployment'].start_date, target)
