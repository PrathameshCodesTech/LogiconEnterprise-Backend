"""
ServerUAT isolated client-portal seed.

Creates three independent post-onboarding UAT clients. Each client has its own
client scope, site scope, billable budget, six approved site roles, and one
client-admin login. This seed is intentionally for portal/MRF UAT only: it
does not create fictional sales leads, proposals, or mobilisation history.
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


PASSWORD = "ClientUAT@2026"
EFFECTIVE_FROM = date(2026, 1, 1)
BUDGET_AMOUNT = Decimal("10000000.00")
WAGE_LOCATION_CODE = "pune_metro"

PORTALS = [
    {
        "key": "a",
        "client_name": "Client Portal UAT A",
        "client_code": "client-portal-uat-a",
        "site_name": "Client Portal UAT A Main Site",
        "site_code": "client-portal-uat-a-main",
        "email": "client.portal.a@logicon.local",
        "username": "client.portal.a",
        "first_name": "Client",
        "last_name": "Portal A",
        "phone": "9000010001",
    },
    {
        "key": "b",
        "client_name": "Client Portal UAT B",
        "client_code": "client-portal-uat-b",
        "site_name": "Client Portal UAT B Main Site",
        "site_code": "client-portal-uat-b-main",
        "email": "client.portal.b@logicon.local",
        "username": "client.portal.b",
        "first_name": "Client",
        "last_name": "Portal B",
        "phone": "9000010002",
    },
    {
        "key": "c",
        "client_name": "Client Portal UAT C",
        "client_code": "client-portal-uat-c",
        "site_name": "Client Portal UAT C Main Site",
        "site_code": "client-portal-uat-c-main",
        "email": "client.portal.c@logicon.local",
        "username": "client.portal.c",
        "first_name": "Client",
        "last_name": "Portal C",
        "phone": "9000010003",
    },
]

# Explicit UAT commercial rates. Wage values and wage categories come strictly
# from the configured Pune Metro wage master for the matching job role.
ROLE_REQUIREMENTS = [
    ("electrician", "skilled", Decimal("44393.00")),
    ("plumber", "skilled", Decimal("43000.00")),
    ("hvac", "skilled", Decimal("51000.00")),
    ("carpenter", "skilled", Decimal("42000.00")),
    ("painter", "skilled", Decimal("40000.00")),
    ("helper", "unskilled", Decimal("34000.00")),
]

REQUIRED_CLIENT_CAPABILITIES = {
    "mrf.read",
    "mrf.create",
    "mrf.update",
    "workflow.read",
    "workflow.start_workflow",
}


class Command(BaseCommand):
    help = (
        "Seed three isolated ServerUAT client portals with site MRF test data "
        "and one client-admin login per client."
    )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== ServerUAT Client Portal Seed ===\n"))

        org, actor, company_node, client_role, location, roles, categories = self._load_prerequisites()

        for definition in PORTALS:
            with transaction.atomic():
                client = self._upsert_client(org, actor, company_node, definition)
                site = self._upsert_site(org, actor, client, location, definition)
                budget = self._upsert_budget(org, actor, client, definition)
                self._upsert_role_requirements(site, org, location, roles, categories)
                user = self._upsert_client_admin(org, client, client_role, definition)

            self.stdout.write(
                self.style.SUCCESS(
                    f"  [Portal {definition['key'].upper()}] client={client.code}, "
                    f"site={site.code}, budget={budget.code}, login={user.email}"
                )
            )

        self.stdout.write(self.style.SUCCESS("\n[OK] ServerUAT client portal seed complete.\n"))
        self.stdout.write("  Login password for all three UAT users: ClientUAT@2026\n")

    def _load_prerequisites(self):
        from apps.access.models import AccessRole, AccessRolePermission
        from apps.accounts.models import User
        from apps.core.models import Organization, ScopeNode
        from apps.jobs.models import JobRole
        from apps.wages.models import LocationArea, WageCategory

        try:
            org = Organization.objects.get(code="logicon", is_active=True)
        except Organization.DoesNotExist as exc:
            raise CommandError("Missing active organization 'logicon'. Run seed_server_uat foundation first.") from exc

        company_node = ScopeNode.objects.filter(
            org=org,
            parent__isnull=True,
            node_type="company",
            is_active=True,
        ).first()
        if company_node is None:
            raise CommandError("Missing active company scope node. Run seed_server_uat foundation first.")

        actor = User.objects.filter(org=org, username="admin.logicon", is_active=True).first()
        if actor is None:
            raise CommandError("Missing active admin.logicon user. Run seed_server_uat foundation first.")

        client_role = AccessRole.objects.filter(
            org=org,
            code="client_admin",
            is_active=True,
        ).first()
        if client_role is None:
            raise CommandError("Missing active client_admin role. Run seed_server_uat foundation first.")

        role_caps = set(
            AccessRolePermission.objects.filter(role=client_role)
            .values_list("permission__code", flat=True)
        )
        missing_caps = sorted(REQUIRED_CLIENT_CAPABILITIES - role_caps)
        if missing_caps:
            raise CommandError(
                "client_admin role is missing required capabilities: "
                f"{', '.join(missing_caps)}. Re-run seed_server_uat foundation."
            )

        location = LocationArea.objects.filter(
            parent__isnull=True,
            code=WAGE_LOCATION_CODE,
            is_active=True,
        ).first()
        if location is None:
            raise CommandError(
                f"Missing active wage location '{WAGE_LOCATION_CODE}'. Run seed_server_uat masters first."
            )

        role_codes = [item[0] for item in ROLE_REQUIREMENTS]
        roles = {
            role.code: role
            for role in JobRole.objects.filter(org=org, code__in=role_codes, is_active=True)
        }
        missing_roles = sorted(set(role_codes) - set(roles))
        if missing_roles:
            raise CommandError(
                "Missing active job roles: "
                f"{', '.join(missing_roles)}. Run seed_server_uat masters first."
            )
        non_billable_roles = sorted(
            role.code
            for role in roles.values()
            if role.hiring_lane != "client_billable"
        )
        if non_billable_roles:
            raise CommandError(
                "UAT site roles must be client_billable, but found: "
                f"{', '.join(non_billable_roles)}. Correct the job-role master first."
            )

        categories = {
            category.code: category
            for category in WageCategory.objects.filter(
                code__in={item[1] for item in ROLE_REQUIREMENTS}
            )
        }
        missing_categories = sorted({item[1] for item in ROLE_REQUIREMENTS} - set(categories))
        if missing_categories:
            raise CommandError(
                "Missing wage categories: " + ", ".join(missing_categories)
            )

        self._validate_mrf_workflow(org)
        return org, actor, company_node, client_role, location, roles, categories

    def _validate_mrf_workflow(self, org):
        from apps.workflow.services import get_available_approval_routes, resolve_workflow_template

        try:
            template = resolve_workflow_template("mrf", org)
            routes = get_available_approval_routes("mrf", org)
        except Exception as exc:
            raise CommandError(f"MRF workflow is not ready: {exc}") from exc

        if not routes:
            raise CommandError("MRF workflow has no active approval route.")
        self.stdout.write(
            f"  [Workflow] template={template.code}, routes={', '.join(route.code for route in routes)}"
        )

    def _upsert_client(self, org, actor, company_node, definition):
        from apps.core.models import ScopeNode
        from apps.sites.models import Client
        from apps.sites.services import create_client_with_scope

        client = Client.objects.filter(org=org, code=definition["client_code"]).first()
        if client is None:
            client = create_client_with_scope(
                org=org,
                name=definition["client_name"],
                code=definition["client_code"],
                created_by=actor,
                parent_scope_node=company_node,
                contact_name=f"{definition['first_name']} {definition['last_name']}",
                contact_email=definition["email"],
                contact_phone=definition["phone"],
                industry="UAT",
                billing_address="ServerUAT isolated client portal test data.",
                owner_sales_user=actor,
                is_active=True,
                source_type="manual_admin",
            )
            return client

        expected_path = f"{company_node.path}/{definition['client_code']}"
        scope_node = client.scope_node
        if scope_node is None:
            scope_node = ScopeNode.objects.filter(org=org, path=expected_path).first()
        if scope_node is None:
            raise CommandError(
                f"Existing client '{client.code}' has no valid scope node at '{expected_path}'."
            )
        if scope_node.parent_id != company_node.id or scope_node.node_type != "client":
            raise CommandError(f"Existing client '{client.code}' has an incompatible scope node.")

        changed_fields = []
        expected = {
            "name": definition["client_name"],
            "scope_node": scope_node,
            "contact_name": f"{definition['first_name']} {definition['last_name']}",
            "contact_email": definition["email"],
            "contact_phone": definition["phone"],
            "industry": "UAT",
            "billing_address": "ServerUAT isolated client portal test data.",
            "owner_sales_user": actor,
            "is_active": True,
            "source_type": "manual_admin",
        }
        for field, value in expected.items():
            current = getattr(client, f"{field}_id") if hasattr(value, "pk") else getattr(client, field)
            wanted = value.pk if hasattr(value, "pk") else value
            if current != wanted:
                setattr(client, field, value)
                changed_fields.append(field)
        if changed_fields:
            client.save(update_fields=changed_fields + ["updated_at"])
        return client

    def _upsert_site(self, org, actor, client, location, definition):
        from apps.core.models import ScopeNode
        from apps.sites.models import SiteProfile
        from apps.sites.services import create_site_with_scope

        site = SiteProfile.objects.filter(org=org, code=definition["site_code"]).first()
        if site is None:
            return create_site_with_scope(
                org=org,
                client=client,
                name=definition["site_name"],
                code=definition["site_code"],
                created_by=actor,
                location_area=location,
                address="ServerUAT isolated client portal test site.",
                city=location.name,
                state=location.state_name,
                pincode="411001",
                shift_type="day",
                contact_person=f"{definition['first_name']} {definition['last_name']}",
                contact_phone=definition["phone"],
                contact_email=definition["email"],
                is_active=True,
                source_type="manual_admin",
            )

        if site.client_id != client.id:
            raise CommandError(
                f"Existing site '{site.code}' belongs to another client and cannot be reused."
            )
        expected_path = f"{client.scope_node.path}/{definition['site_code']}"
        scope_node = site.scope_node or ScopeNode.objects.filter(org=org, path=expected_path).first()
        if scope_node is None or scope_node.parent_id != client.scope_node_id or scope_node.node_type != "site":
            raise CommandError(f"Existing site '{site.code}' has no compatible site scope node.")

        changed_fields = []
        expected = {
            "name": definition["site_name"],
            "scope_node": scope_node,
            "location_area": location,
            "address": "ServerUAT isolated client portal test site.",
            "city": location.name,
            "state": location.state_name,
            "pincode": "411001",
            "shift_type": "day",
            "contact_person": f"{definition['first_name']} {definition['last_name']}",
            "contact_phone": definition["phone"],
            "contact_email": definition["email"],
            "is_active": True,
            "source_type": "manual_admin",
        }
        for field, value in expected.items():
            current = getattr(site, f"{field}_id") if hasattr(value, "pk") else getattr(site, field)
            wanted = value.pk if hasattr(value, "pk") else value
            if current != wanted:
                setattr(site, field, value)
                changed_fields.append(field)
        if changed_fields:
            site.save(update_fields=changed_fields + ["updated_at"])
        return site

    def _upsert_budget(self, org, actor, client, definition):
        from apps.budgets.models import BudgetPlan

        code = f"budget-{definition['client_code']}"
        budget, created = BudgetPlan.objects.get_or_create(
            org=org,
            code=code,
            is_active=True,
            defaults={
                "name": f"{definition['client_name']} Billable Budget",
                "budget_nature": "billable",
                "budget_type": "manpower",
                "client": client,
                "site": None,
                "department": None,
                "period_start": EFFECTIVE_FROM,
                "period_end": None,
                "amount": BUDGET_AMOUNT,
                "currency": "INR",
                "status": "active",
                "notes": "ServerUAT isolated client portal MRF budget.",
                "created_by": actor,
                "updated_by": actor,
                "source_type": "manual_admin",
            },
        )
        if not created and budget.client_id != client.id:
            raise CommandError(f"Existing budget '{code}' belongs to another client.")

        changed_fields = []
        expected = {
            "name": f"{definition['client_name']} Billable Budget",
            "budget_nature": "billable",
            "budget_type": "manpower",
            "client": client,
            "site": None,
            "department": None,
            "period_start": EFFECTIVE_FROM,
            "period_end": None,
            "amount": BUDGET_AMOUNT,
            "currency": "INR",
            "status": "active",
            "notes": "ServerUAT isolated client portal MRF budget.",
            "is_active": True,
            "updated_by": actor,
            "source_type": "manual_admin",
        }
        for field, value in expected.items():
            current = getattr(budget, f"{field}_id") if hasattr(value, "pk") else getattr(budget, field)
            wanted = value.pk if hasattr(value, "pk") else value
            if current != wanted:
                setattr(budget, field, value)
                changed_fields.append(field)
        if changed_fields:
            budget.save(update_fields=changed_fields + ["updated_at"])
        return budget

    def _upsert_role_requirements(self, site, org, location, roles, categories):
        from apps.sites.models import SiteRoleRequirement
        from apps.wages.services import get_applicable_minimum_wage

        for role_code, category_code, billing_rate in ROLE_REQUIREMENTS:
            role = roles[role_code]
            category = categories[category_code]
            wage_rate = get_applicable_minimum_wage(
                category,
                EFFECTIVE_FROM,
                location=location,
                role=role,
                org=org,
            )
            if wage_rate is None:
                raise CommandError(
                    f"No active wage master for {role_code} / {category_code} / {location.code}."
                )

            SiteRoleRequirement.objects.filter(
                site=site,
                job_role=role,
                is_active=True,
            ).exclude(effective_from=EFFECTIVE_FROM).update(is_active=False)

            requirement, _ = SiteRoleRequirement.objects.update_or_create(
                site=site,
                job_role=role,
                effective_from=EFFECTIVE_FROM,
                is_active=True,
                defaults={
                    "department": None,
                    "approved_headcount": 10,
                    "billing_rate": billing_rate,
                    "wage_min": wage_rate.monthly_wage,
                    "wage_max": wage_rate.monthly_wage,
                    "shift_hours": Decimal("8.0"),
                    "billing_type": "billable",
                    "wage_category": category,
                    "effective_to": None,
                    "wage_rate": wage_rate,
                    "wage_rate_monthly_snapshot": wage_rate.monthly_wage,
                    "wage_rate_daily_snapshot": wage_rate.daily_wage,
                    "wage_rate_effective_from_snapshot": wage_rate.effective_from,
                    "wage_rate_source_snapshot": wage_rate.source_note,
                    "source_type": "manual_admin",
                    "source_sales_lead": None,
                    "source_proposal_version": None,
                },
            )
            self.stdout.write(
                f"    [SiteRoleRequirement] {site.code} / {role.code}: "
                f"approved=10, billing={requirement.billing_rate}"
            )

    def _upsert_client_admin(self, org, client, client_role, definition):
        from apps.access.models import UserRoleAssignment
        from apps.accounts.models import User

        user_by_email = User.objects.filter(email__iexact=definition["email"]).first()
        user_by_username = User.objects.filter(username=definition["username"]).first()
        if user_by_email and user_by_username and user_by_email.pk != user_by_username.pk:
            raise CommandError(
                f"Email and username for portal {definition['key'].upper()} belong to different users."
            )
        user = user_by_email or user_by_username
        if user is not None and user.org_id != org.id:
            raise CommandError(
                f"Email '{definition['email']}' already belongs to another organization."
            )
        if user is not None:
            incompatible_assignments = UserRoleAssignment.objects.filter(user=user).exclude(
                role=client_role,
                scope_node=client.scope_node,
            )
            if incompatible_assignments.exists():
                assignments = ", ".join(
                    f"{assignment.role.code}@{assignment.scope_node.path}"
                    for assignment in incompatible_assignments.select_related("role", "scope_node")
                )
                raise CommandError(
                    f"Test account '{definition['email']}' already has incompatible access: {assignments}."
                )

        if user is None:
            user = User(
                username=definition["username"],
                email=definition["email"],
                first_name=definition["first_name"],
                last_name=definition["last_name"],
                phone_number=definition["phone"],
                user_type="client",
                org=org,
                is_active=True,
                is_invited=False,
            )
        else:
            user.username = definition["username"]
            user.email = definition["email"]
            user.first_name = definition["first_name"]
            user.last_name = definition["last_name"]
            user.phone_number = definition["phone"]
            user.user_type = "client"
            user.org = org
            user.is_active = True
            user.is_invited = False

        # This is a direct UAT credential seed, not the email-invite flow.
        user.set_password(PASSWORD)
        user.save()

        UserRoleAssignment.objects.get_or_create(
            user=user,
            role=client_role,
            scope_node=client.scope_node,
        )
        return user
