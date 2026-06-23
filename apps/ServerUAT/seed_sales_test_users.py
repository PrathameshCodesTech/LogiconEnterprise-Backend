"""
ServerUAT isolated internal sales-user seed.

Creates dedicated sales-manager accounts for ownership UAT. Every account is
internal, belongs to the Sales department, and receives only the sales_manager
role at the company scope. The seed deliberately rejects conflicting existing
assignments so a test user cannot become a mixed persona.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


PASSWORD = "SalesUAT@2026"

SALES_TEST_USERS = [
    {
        "key": "rakesh-sardar",
        "username": "rakesh.sardar",
        "email": "rakesh.sardar@logicon.local",
        "first_name": "Rakesh",
        "last_name": "Sardar",
        "phone": "9000030001",
    },
    {
        "key": "soma-sharma",
        "username": "soma.sharma",
        "email": "soma.sharma@logicon.local",
        "first_name": "Soma",
        "last_name": "Sharma",
        "phone": "9000030002",
    },
    {
        "key": "murali-sales",
        "username": "murali.sales",
        "email": "murali.sales@logicon.local",
        "first_name": "Murali",
        "last_name": "",
        "phone": "9000030003",
    },
    {
        "key": "sanket-ware",
        "username": "sanket.ware",
        "email": "sanket.ware@logicon.local",
        "first_name": "Sanket",
        "last_name": "Ware",
        "phone": "9000030004",
    },
    {
        "key": "dular-chand-sales",
        "username": "dular.chand.sales",
        "email": "dular.chand.sales@logicon.local",
        "first_name": "Dular",
        "last_name": "Chand",
        "phone": "9000030005",
    },
]

REQUIRED_CAPABILITIES = {
    "sales_lead.read",
    "sales_lead.create",
    "sales_lead.update",
    "sales_proposal.create",
}


class Command(BaseCommand):
    help = (
        "Seed five isolated internal Sales Manager UAT accounts for ownership "
        "testing."
    )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== ServerUAT Sales User Seed ===\n"))
        org, company, sales_department, sales_role = self._load_prerequisites()

        for definition in SALES_TEST_USERS:
            with transaction.atomic():
                user, created = self._upsert_sales_user(
                    org,
                    company,
                    sales_department,
                    sales_role,
                    definition,
                )
            self.stdout.write(
                self.style.SUCCESS(
                    f"  [{definition['key']}] {user.email} "
                    f"(sales_manager @ {company.path}) - "
                    f"{'CREATED' if created else 'EXISTS'}"
                )
            )

        self.stdout.write(self.style.SUCCESS("\n[OK] ServerUAT sales-user seed complete.\n"))
        self.stdout.write("  Login password for all sales UAT users: SalesUAT@2026\n")

    def _load_prerequisites(self):
        from apps.access.models import AccessRole, AccessRolePermission
        from apps.core.models import Department, Organization, ScopeNode

        try:
            org = Organization.objects.get(code="logicon", is_active=True)
        except Organization.DoesNotExist as exc:
            raise CommandError(
                "Missing active organization 'logicon'. Run seed_server_uat foundation first."
            ) from exc

        try:
            company = ScopeNode.objects.get(
                org=org,
                parent__isnull=True,
                node_type="company",
                is_active=True,
            )
        except ScopeNode.DoesNotExist as exc:
            raise CommandError(
                "Missing active company scope. Run seed_server_uat foundation first."
            ) from exc

        try:
            sales_department = Department.objects.get(
                org=org,
                code="sales",
                client__isnull=True,
                site__isnull=True,
                is_active=True,
            )
        except Department.DoesNotExist as exc:
            raise CommandError(
                "Missing active organization-level Sales department. Run seed_server_uat foundation first."
            ) from exc

        try:
            sales_role = AccessRole.objects.get(org=org, code="sales_manager", is_active=True)
        except AccessRole.DoesNotExist as exc:
            raise CommandError(
                "Missing active sales_manager role. Run seed_server_uat foundation first."
            ) from exc

        role_capabilities = set(
            AccessRolePermission.objects.filter(role=sales_role).values_list(
                "permission__code", flat=True
            )
        )
        missing = sorted(REQUIRED_CAPABILITIES - role_capabilities)
        if missing:
            raise CommandError(
                "sales_manager is missing required capabilities: " + ", ".join(missing)
            )

        return org, company, sales_department, sales_role

    def _upsert_sales_user(self, org, company, sales_department, sales_role, definition):
        from apps.access.models import UserRoleAssignment
        from apps.accounts.models import User

        user_by_email = User.objects.filter(email__iexact=definition["email"]).first()
        user_by_username = User.objects.filter(username=definition["username"]).first()
        if user_by_email and user_by_username and user_by_email.pk != user_by_username.pk:
            raise CommandError(
                f"Email and username for '{definition['key']}' belong to different users."
            )

        user = user_by_email or user_by_username
        if user is not None and user.org_id != org.id:
            raise CommandError(
                f"Email '{definition['email']}' already belongs to another organization."
            )

        if user is not None:
            incompatible = UserRoleAssignment.objects.filter(user=user).exclude(
                role=sales_role,
                scope_node=company,
            )
            if incompatible.exists():
                assignments = ", ".join(
                    f"{assignment.role.code}@{assignment.scope_node.path}"
                    for assignment in incompatible.select_related("role", "scope_node")
                )
                raise CommandError(
                    f"Sales test account '{definition['email']}' already has incompatible access: "
                    f"{assignments}."
                )

        created = user is None
        if created:
            user = User(username=definition["username"])

        user.username = definition["username"]
        user.email = definition["email"]
        user.first_name = definition["first_name"]
        user.last_name = definition["last_name"]
        user.phone_number = definition["phone"]
        user.user_type = "internal"
        user.org = org
        user.department = sales_department
        user.is_active = True
        user.is_invited = False
        user.set_password(PASSWORD)
        user.save()

        UserRoleAssignment.objects.get_or_create(
            user=user,
            role=sales_role,
            scope_node=company,
        )
        return user, created
