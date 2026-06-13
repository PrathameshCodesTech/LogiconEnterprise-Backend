from datetime import date

from django.test import TestCase

from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.querysets import (
    filter_budget_plans_for_user,
    filter_clients_for_user,
    filter_mobilisation_requests_for_user as filter_shared_mobilisation_requests_for_user,
    filter_sites_for_user,
)
from apps.accounts.models import User
from apps.budgets.models import BudgetPlan
from apps.core.models import Organization, ScopeNode
from apps.mobilisation.models import MobilisationSetupRequest
from apps.mobilisation.querysets import (
    filter_mobilisation_requests_for_user as filter_mobilisation_app_requests_for_user,
)
from apps.sales.models import SalesLead
from apps.sales.querysets import filter_leads_for_user
from apps.sites.models import Client, SiteProfile


class SalesOwnershipFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Sales Ownership Org', code='sales-own')
        cls.company = ScopeNode.objects.create(
            org=cls.org,
            code='sales-own',
            name='Sales Ownership',
            node_type='company',
            path='sales-own',
            depth=0,
            is_active=True,
        )
        cls.sales_role = AccessRole.objects.create(
            org=cls.org, name='Sales Manager', code='sales_manager',
        )
        cls.finance_role = AccessRole.objects.create(
            org=cls.org, name='Finance Manager', code='finance_manager',
        )

        cls.sales_a = cls._make_user('sales.a', cls.sales_role)
        cls.sales_b = cls._make_user('sales.b', cls.sales_role)
        cls.finance = cls._make_user('finance', cls.finance_role)

        cls.lead_a = SalesLead.objects.create(
            org=cls.org,
            client_name='A Client',
            sales_person=cls.sales_a,
            created_by=cls.sales_a,
        )
        cls.lead_b = SalesLead.objects.create(
            org=cls.org,
            client_name='B Client',
            sales_person=cls.sales_b,
            created_by=cls.sales_b,
        )

        cls.client_a = cls._make_client('Client A', 'client-a', cls.sales_a, cls.lead_a)
        cls.client_b = cls._make_client('Client B', 'client-b', cls.sales_b, cls.lead_b)
        cls.site_a = cls._make_site(cls.client_a, 'Site A', 'site-a', cls.sales_a, cls.lead_a)
        cls.site_b = cls._make_site(cls.client_b, 'Site B', 'site-b', cls.sales_b, cls.lead_b)

        cls.budget_a = cls._make_budget('Budget A', 'budget-a', cls.client_a, cls.sales_a, cls.lead_a)
        cls.budget_b = cls._make_budget('Budget B', 'budget-b', cls.client_b, cls.sales_b, cls.lead_b)

        cls.mob_a = cls._make_mobilisation(cls.client_a, cls.budget_a, cls.sales_a, cls.lead_a)
        cls.mob_b = cls._make_mobilisation(cls.client_b, cls.budget_b, cls.sales_b, cls.lead_b)

    @classmethod
    def _make_user(cls, username, role):
        user = User.objects.create_user(username=username, password='pass')
        user.org = cls.org
        user.save(update_fields=['org'])
        UserRoleAssignment.objects.create(user=user, role=role, scope_node=cls.company)
        return user

    @classmethod
    def _make_client(cls, name, code, owner, lead):
        node = ScopeNode.objects.create(
            org=cls.org,
            parent=cls.company,
            code=code,
            name=name,
            node_type='client',
            path=f'{cls.company.path}/{code}',
            depth=1,
            is_active=True,
        )
        return Client.objects.create(
            org=cls.org,
            name=name,
            code=code,
            scope_node=node,
            created_by=owner,
            owner_sales_user=owner,
            source_type='sales_conversion',
            source_sales_lead=lead,
        )

    @classmethod
    def _make_site(cls, client, name, code, owner, lead):
        node = ScopeNode.objects.create(
            org=cls.org,
            parent=client.scope_node,
            code=code,
            name=name,
            node_type='site',
            path=f'{client.scope_node.path}/{code}',
            depth=2,
            is_active=True,
        )
        return SiteProfile.objects.create(
            org=cls.org,
            client=client,
            scope_node=node,
            name=name,
            code=code,
            created_by=owner,
            source_type='sales_conversion',
            source_sales_lead=lead,
        )

    @classmethod
    def _make_budget(cls, name, code, client, owner, lead):
        return BudgetPlan.objects.create(
            org=cls.org,
            name=name,
            code=code,
            budget_nature='billable',
            budget_type='onboarding',
            client=client,
            period_start=date(2026, 1, 1),
            amount='10000.00',
            status='active',
            created_by=owner,
            source_type='sales_conversion',
            source_sales_lead=lead,
        )

    @classmethod
    def _make_mobilisation(cls, client, budget, owner, lead):
        return MobilisationSetupRequest.objects.create(
            org=cls.org,
            client=client,
            requested_by=owner,
            budget_plan=budget,
            mobilisation_type='new_client',
            status='draft',
            source_sales_lead=lead,
        )

    def assertOnlySalesARecord(self, queryset, expected):
        self.assertEqual(list(queryset.values_list('id', flat=True)), [expected.id])

    def test_sales_persona_sees_only_owned_sales_leads(self):
        result = filter_leads_for_user(SalesLead.objects.order_by('id'), self.sales_a)
        self.assertOnlySalesARecord(result, self.lead_a)

    def test_sales_persona_sees_only_owned_clients(self):
        result = filter_clients_for_user(Client.objects.order_by('id'), self.sales_a)
        self.assertOnlySalesARecord(result, self.client_a)

    def test_sales_persona_sees_only_owned_sites(self):
        result = filter_sites_for_user(SiteProfile.objects.order_by('id'), self.sales_a)
        self.assertOnlySalesARecord(result, self.site_a)

    def test_sales_persona_sees_only_owned_budget_plans(self):
        result = filter_budget_plans_for_user(BudgetPlan.objects.order_by('id'), self.sales_a)
        self.assertOnlySalesARecord(result, self.budget_a)

    def test_sales_persona_sees_only_owned_mobilisation_requests(self):
        shared_result = filter_shared_mobilisation_requests_for_user(
            MobilisationSetupRequest.objects.order_by('id'),
            self.sales_a,
        )
        app_result = filter_mobilisation_app_requests_for_user(
            MobilisationSetupRequest.objects.order_by('id'),
            self.sales_a,
        )

        self.assertOnlySalesARecord(shared_result, self.mob_a)
        self.assertOnlySalesARecord(app_result, self.mob_a)

    def test_non_sales_persona_scope_filter_remains_unchanged(self):
        clients = filter_clients_for_user(Client.objects.order_by('id'), self.finance)
        budgets = filter_budget_plans_for_user(BudgetPlan.objects.order_by('id'), self.finance)

        self.assertEqual(list(clients.values_list('id', flat=True)), [self.client_a.id, self.client_b.id])
        self.assertEqual(list(budgets.values_list('id', flat=True)), [self.budget_a.id, self.budget_b.id])
