"""Constants for the Logicon demo seed."""

from decimal import Decimal


LOGICON_DEMO_ORG_CODE = 'logicon'
LOGICON_DEMO_ORG_NAME = 'Logicon Facility Management'
LOGICON_DEMO_PASSWORD = 'Logicon@12345'
LOGICON_DEMO_RESETTABLE_ORG_CODES = {'logicon-sandbox', 'logicon-demo-reset'}

LOGICON_DEMO_USER_DEFS = {
    'admin': {
        'username': 'logicon.admin',
        'email': 'admin@logicon.example.com',
        'first_name': 'Logicon',
        'last_name': 'Admin',
        'role_code': 'admin',
        'department_code': 'admin',
    },
    'sales': {
        'username': 'rohan.sales',
        'email': 'rohan.sales@logicon.example.com',
        'first_name': 'Rohan',
        'last_name': 'Sales',
        'role_code': 'sales_manager',
        'department_code': 'sales',
    },
    'operations': {
        'username': 'alice.ops',
        'email': 'alice.ops@logicon.example.com',
        'first_name': 'Alice',
        'last_name': 'Operations',
        'role_code': 'operations_manager',
        'department_code': 'operations',
    },
    'finance': {
        'username': 'bhakti.finance',
        'email': 'bhakti.finance@logicon.example.com',
        'first_name': 'Bhakti',
        'last_name': 'Finance',
        'role_code': 'finance_manager',
        'department_code': 'finance',
    },
    'hr': {
        'username': 'meera.hr',
        'email': 'meera.hr@logicon.example.com',
        'first_name': 'Meera',
        'last_name': 'HR',
        'role_code': 'hr_manager',
        'department_code': 'hr',
    },
    'client': {
        'username': 'priya.client',
        'email': 'priya.client@acme.example.com',
        'first_name': 'Priya',
        'last_name': 'Client',
        'role_code': 'client_admin',
        'department_code': 'client_success',
        'user_type': 'client',
    },
}

LOGICON_DEMO_DEPARTMENTS = [
    ('admin', 'Administration'),
    ('sales', 'Sales'),
    ('operations', 'Operations'),
    ('finance', 'Finance'),
    ('hr', 'Human Resources'),
    ('client_success', 'Client Success'),
]

LOGICON_DEMO_JOB_ROLES = [
    ('tech_supervisor', 'Tech Supervisor', 'supervisor', 'skilled', Decimal('22000.00')),
    ('electrician', 'Electrician', 'skilled', 'skilled', Decimal('19000.00')),
    ('plumber', 'Plumber', 'skilled', 'skilled', Decimal('17500.00')),
    ('stp_operator', 'STP', 'skilled', 'skilled', Decimal('18000.00')),
    ('hk_supervisor', 'HK Supervisor', 'supervisor', 'skilled', Decimal('21000.00')),
    ('janitor', 'Janitor', 'unskilled', 'unskilled', Decimal('14500.00')),
]

LOGICON_DEMO_LEAD_CLIENT_NAME = 'Acme Manufacturing Pvt Ltd'
LOGICON_DEMO_LEAD_SITE_NAME = 'Acme Pune Plant'
LOGICON_DEMO_EXISTING_CLIENT_CODE = 'beta-industries'






