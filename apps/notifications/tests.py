from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Organization
from apps.notifications.models import Notification
from apps.notifications.services import create_notification
from apps.sales.models import SalesLead, SalesLeadSite
from apps.sales.services import mark_survey_completed, submit_to_operations


class NotificationApiTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name='Org A', code='orga')
        self.org_b = Organization.objects.create(name='Org B', code='orgb')
        self.user_a = User.objects.create_user(
            username='user.a',
            email='a@example.com',
            password='pass',
        )
        self.user_a.org = self.org_a
        self.user_a.save(update_fields=['org'])
        self.user_b = User.objects.create_user(
            username='user.b',
            email='b@example.com',
            password='pass',
        )
        self.user_b.org = self.org_b
        self.user_b.save(update_fields=['org'])
        self.actor = User.objects.create_user(username='actor', password='pass')
        self.actor.org = self.org_a
        self.actor.save(update_fields=['org'])
        self.api = APIClient()
        self.api.force_authenticate(self.user_a)

    def test_create_notification_service_persists_for_recipient(self):
        n = create_notification(
            recipient=self.user_a,
            actor=self.actor,
            title='Task assigned',
            message='Please review this task.',
            notification_type='workflow_task_assigned',
            target_type='mrf',
            target_id=10,
            target_url='/mrf/10',
        )
        self.assertIsNotNone(n)
        self.assertEqual(n.org, self.org_a)
        self.assertEqual(n.recipient, self.user_a)
        self.assertFalse(n.is_read)

    def test_list_returns_only_current_user_notifications(self):
        own = create_notification(recipient=self.user_a, title='Mine')
        create_notification(recipient=self.user_b, title='Other')

        resp = self.api.get('/api/notifications/')
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data.get('results', resp.data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], own.pk)
        self.assertEqual(rows[0]['title'], 'Mine')

    def test_unread_count_and_mark_read(self):
        n = create_notification(recipient=self.user_a, title='Unread')
        create_notification(recipient=self.user_a, title='Read')
        Notification.objects.filter(title='Read').update(is_read=True)

        count_resp = self.api.get('/api/notifications/unread-count/')
        self.assertEqual(count_resp.status_code, 200, count_resp.data)
        self.assertEqual(count_resp.data['unread_count'], 1)

        mark_resp = self.api.post(f'/api/notifications/{n.pk}/mark-read/')
        self.assertEqual(mark_resp.status_code, 200, mark_resp.data)
        n.refresh_from_db()
        self.assertTrue(n.is_read)
        self.assertIsNotNone(n.read_at)

    def test_mark_all_read_only_updates_current_user(self):
        own = create_notification(recipient=self.user_a, title='Mine')
        other = create_notification(recipient=self.user_b, title='Other')

        resp = self.api.post('/api/notifications/mark-all-read/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['updated'], 1)

        own.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(own.is_read)
        self.assertFalse(other.is_read)

    def test_sales_submit_to_operations_notifies_operations_owner(self):
        lead = SalesLead.objects.create(
            org=self.org_a,
            client_name='Acme Manufacturing',
            sales_person=self.actor,
            created_by=self.actor,
        )
        SalesLeadSite.objects.create(lead=lead, site_name='Acme Plant 1')

        submit_to_operations(lead, self.actor, operations_owner=self.user_a)

        n = Notification.objects.get(
            recipient=self.user_a,
            notification_type='sales_survey_assigned',
        )
        self.assertIn('Acme Plant 1', n.title)
        self.assertEqual(n.target_type, 'site_survey')
        self.assertTrue(n.target_url.startswith('/sales/operations-surveys/'))

    def test_sales_survey_completed_notifies_sales_person(self):
        lead = SalesLead.objects.create(
            org=self.org_a,
            client_name='Beta Logistics',
            sales_person=self.actor,
            created_by=self.actor,
        )
        SalesLeadSite.objects.create(lead=lead, site_name='Beta Hub')
        submit_to_operations(lead, self.actor, operations_owner=self.user_a)
        survey = lead.surveys.get()

        mark_survey_completed(survey, self.user_a)

        n = Notification.objects.get(
            recipient=self.actor,
            notification_type='sales_survey_completed',
        )
        self.assertEqual(n.target_type, 'sales_lead')
        self.assertEqual(n.target_id, lead.pk)
        self.assertEqual(n.target_url, f'/sales/leads/{lead.pk}')
