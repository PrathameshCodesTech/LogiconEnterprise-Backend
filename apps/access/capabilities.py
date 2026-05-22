"""
apps/access/capabilities.py

All capability strings as constants: "resource.action"
Role → capability mappings for all Logicon internal roles and client user.
"""

# ─── Individual capability constants ─────────────────────────────────────────

# Organization
ORGANIZATION_READ = "organization.read"
ORGANIZATION_CREATE = "organization.create"
ORGANIZATION_UPDATE = "organization.update"
ORGANIZATION_DELETE = "organization.delete"

# User
USER_READ = "user.read"
USER_CREATE = "user.create"
USER_UPDATE = "user.update"
USER_DELETE = "user.delete"

# Role
ROLE_READ = "role.read"
ROLE_CREATE = "role.create"
ROLE_UPDATE = "role.update"
ROLE_DELETE = "role.delete"

# Module
MODULE_READ = "module.read"
MODULE_CREATE = "module.create"
MODULE_UPDATE = "module.update"
MODULE_DELETE = "module.delete"
MODULE_MANAGE = "module.manage_module"

# Client
CLIENT_READ = "client.read"
CLIENT_CREATE = "client.create"
CLIENT_UPDATE = "client.update"
CLIENT_DELETE = "client.delete"

# Site
SITE_READ = "site.read"
SITE_CREATE = "site.create"
SITE_UPDATE = "site.update"
SITE_DELETE = "site.delete"

# Site Role Requirement
SITE_ROLE_REQ_READ = "site_role_requirement.read"
SITE_ROLE_REQ_CREATE = "site_role_requirement.create"
SITE_ROLE_REQ_UPDATE = "site_role_requirement.update"
SITE_ROLE_REQ_DELETE = "site_role_requirement.delete"

# Job Role
JOB_ROLE_READ = "job_role.read"
JOB_ROLE_CREATE = "job_role.create"
JOB_ROLE_UPDATE = "job_role.update"
JOB_ROLE_DELETE = "job_role.delete"

# Campaign
CAMPAIGN_READ = "campaign.read"
CAMPAIGN_CREATE = "campaign.create"
CAMPAIGN_UPDATE = "campaign.update"
CAMPAIGN_DELETE = "campaign.delete"

# Submission
SUBMISSION_READ = "submission.read"
SUBMISSION_UPDATE = "submission.update"
SUBMISSION_DELETE = "submission.delete"

# Candidate
CANDIDATE_READ = "candidate.read"
CANDIDATE_CREATE = "candidate.create"
CANDIDATE_UPDATE = "candidate.update"

# Candidate shortlist
CANDIDATE_SHORTLIST = "candidate.shortlist"

# Resume
RESUME_READ = "resume.read"
RESUME_VIEW = "resume.view_resume"
RESUME_UPLOAD = "resume.upload"

# Pipeline Stage
PIPELINE_STAGE_READ = "pipeline_stage.read"

# Candidate Match
CANDIDATE_MATCH_READ = "candidate_match.read"

# MRF
MRF_READ = "mrf.read"
MRF_CREATE = "mrf.create"
MRF_UPDATE = "mrf.update"
MRF_DELETE = "mrf.delete"
MRF_APPROVE = "mrf.approve"
MRF_REJECT = "mrf.reject"
MRF_OVERRIDE_COMMERCIALS = "mrf.override_commercials"

# Department
DEPARTMENT_READ = "department.read"
DEPARTMENT_CREATE = "department.create"
DEPARTMENT_UPDATE = "department.update"
DEPARTMENT_DELETE = "department.delete"

# Workflow
WORKFLOW_READ = "workflow.read"
WORKFLOW_CREATE = "workflow.create"
WORKFLOW_START = "workflow.start_workflow"
WORKFLOW_APPROVE = "workflow.approve"
WORKFLOW_REJECT = "workflow.reject"
WORKFLOW_REASSIGN = "workflow.reassign"

# Workflow Config (approval setup)
WORKFLOW_CONFIG_READ = "workflow.config.read"
WORKFLOW_CONFIG_MANAGE = "workflow.config.manage"

# Client Onboarding
CLIENT_ONBOARDING_READ = "client_onboarding.read"
CLIENT_ONBOARDING_CREATE = "client_onboarding.create"
CLIENT_ONBOARDING_UPDATE = "client_onboarding.update"
CLIENT_ONBOARDING_DELETE = "client_onboarding.delete"

# Wage
WAGE_READ = "wage.read"
WAGE_CREATE = "wage.create"
WAGE_UPDATE = "wage.update"
WAGE_DELETE = "wage.delete"

# Budget
BUDGET_READ = "budget.read"
BUDGET_CREATE = "budget.create"
BUDGET_UPDATE = "budget.update"
BUDGET_DELETE = "budget.delete"

# Interview
INTERVIEW_READ = "interview.read"
INTERVIEW_CREATE = "interview.create"
INTERVIEW_MANAGE = "interview.manage"

# Offer
OFFER_READ = "offer.read"
OFFER_CREATE = "offer.create"
OFFER_APPROVE = "offer.approve"

# Hiring Application
HIRING_APP_READ = "hiring_application.read"
HIRING_APP_CREATE = "hiring_application.create"
HIRING_APP_UPDATE = "hiring_application.update"
HIRING_APP_MANAGE = "hiring_application.manage"

# Employee
EMPLOYEE_READ = "employee.read"
EMPLOYEE_CREATE = "employee.create"
EMPLOYEE_UPDATE = "employee.update"
EMPLOYEE_MANAGE = "employee.manage"

# Site Deployment
SITE_DEPLOYMENT_READ = "site_deployment.read"
SITE_DEPLOYMENT_CREATE = "site_deployment.create"
SITE_DEPLOYMENT_UPDATE = "site_deployment.update"
SITE_DEPLOYMENT_MANAGE = "site_deployment.manage"

# Deployment (legacy / field tracking alias)
DEPLOYMENT_READ = "deployment.read"
DEPLOYMENT_CREATE = "deployment.create"
DEPLOYMENT_MANAGE = "deployment.manage"

# Report
REPORT_READ = "report.read"
REPORT_EXPORT = "report.export"

# Field Tracking
FIELD_TRACKING_READ = "field_tracking.read"

# ─── All capabilities list ─────────────────────────────────────────────────────
ALL_CAPABILITIES = [
    ORGANIZATION_READ, ORGANIZATION_CREATE, ORGANIZATION_UPDATE, ORGANIZATION_DELETE,
    USER_READ, USER_CREATE, USER_UPDATE, USER_DELETE,
    ROLE_READ, ROLE_CREATE, ROLE_UPDATE, ROLE_DELETE,
    MODULE_READ, MODULE_CREATE, MODULE_UPDATE, MODULE_DELETE, MODULE_MANAGE,
    CLIENT_READ, CLIENT_CREATE, CLIENT_UPDATE, CLIENT_DELETE,
    SITE_READ, SITE_CREATE, SITE_UPDATE, SITE_DELETE,
    SITE_ROLE_REQ_READ, SITE_ROLE_REQ_CREATE, SITE_ROLE_REQ_UPDATE, SITE_ROLE_REQ_DELETE,
    JOB_ROLE_READ, JOB_ROLE_CREATE, JOB_ROLE_UPDATE, JOB_ROLE_DELETE,
    CAMPAIGN_READ, CAMPAIGN_CREATE, CAMPAIGN_UPDATE, CAMPAIGN_DELETE,
    SUBMISSION_READ, SUBMISSION_UPDATE, SUBMISSION_DELETE,
    CANDIDATE_READ, CANDIDATE_CREATE, CANDIDATE_UPDATE, CANDIDATE_SHORTLIST,
    RESUME_READ, RESUME_VIEW,
    RESUME_UPLOAD, PIPELINE_STAGE_READ, CANDIDATE_MATCH_READ,
    MRF_READ, MRF_CREATE, MRF_UPDATE, MRF_DELETE, MRF_APPROVE, MRF_REJECT, MRF_OVERRIDE_COMMERCIALS,
    DEPARTMENT_READ, DEPARTMENT_CREATE, DEPARTMENT_UPDATE, DEPARTMENT_DELETE,
    WORKFLOW_READ, WORKFLOW_CREATE, WORKFLOW_START, WORKFLOW_APPROVE, WORKFLOW_REJECT, WORKFLOW_REASSIGN,
    WORKFLOW_CONFIG_READ, WORKFLOW_CONFIG_MANAGE,
    CLIENT_ONBOARDING_READ, CLIENT_ONBOARDING_CREATE, CLIENT_ONBOARDING_UPDATE, CLIENT_ONBOARDING_DELETE,
    WAGE_READ, WAGE_CREATE, WAGE_UPDATE, WAGE_DELETE,
    BUDGET_READ, BUDGET_CREATE, BUDGET_UPDATE, BUDGET_DELETE,
    INTERVIEW_READ, INTERVIEW_CREATE, INTERVIEW_MANAGE,
    OFFER_READ, OFFER_CREATE, OFFER_APPROVE,
    HIRING_APP_READ, HIRING_APP_CREATE, HIRING_APP_UPDATE, HIRING_APP_MANAGE,
    EMPLOYEE_READ, EMPLOYEE_CREATE, EMPLOYEE_UPDATE, EMPLOYEE_MANAGE,
    SITE_DEPLOYMENT_READ, SITE_DEPLOYMENT_CREATE, SITE_DEPLOYMENT_UPDATE, SITE_DEPLOYMENT_MANAGE,
    DEPLOYMENT_READ, DEPLOYMENT_CREATE, DEPLOYMENT_MANAGE,
    REPORT_READ, REPORT_EXPORT,
    FIELD_TRACKING_READ,
]

# ─── Default role permission presets used by seed commands. ──────────────────
# Runtime permission checks use AccessRolePermission rows in the database.
ROLE_CAPABILITIES = {
    # Full system admin (Logicon internal — replaces tenant_admin/org_admin)
    'admin': ALL_CAPABILITIES,

    # HR Admin — hiring lifecycle, MRF, workflow, candidate management
    'hr_admin': [
        USER_READ,
        ROLE_READ,
        CLIENT_READ,
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        DEPARTMENT_READ, DEPARTMENT_CREATE, DEPARTMENT_UPDATE, DEPARTMENT_DELETE,
        CAMPAIGN_READ, CAMPAIGN_CREATE, CAMPAIGN_UPDATE, CAMPAIGN_DELETE,
        SUBMISSION_READ, SUBMISSION_UPDATE,
        CANDIDATE_READ, CANDIDATE_CREATE, CANDIDATE_UPDATE, CANDIDATE_SHORTLIST,
        RESUME_READ, RESUME_VIEW, RESUME_UPLOAD,
        MRF_READ, MRF_CREATE, MRF_UPDATE, MRF_DELETE, MRF_APPROVE, MRF_REJECT, MRF_OVERRIDE_COMMERCIALS,
        WORKFLOW_READ, WORKFLOW_START, WORKFLOW_APPROVE, WORKFLOW_REJECT, WORKFLOW_REASSIGN,
        WORKFLOW_CONFIG_READ, WORKFLOW_CONFIG_MANAGE,
        CLIENT_ONBOARDING_READ, CLIENT_ONBOARDING_UPDATE,
        WAGE_READ,
        BUDGET_READ,
        INTERVIEW_READ, INTERVIEW_CREATE, INTERVIEW_MANAGE,
        OFFER_READ, OFFER_CREATE, OFFER_APPROVE,
        HIRING_APP_READ, HIRING_APP_CREATE, HIRING_APP_UPDATE, HIRING_APP_MANAGE,
        PIPELINE_STAGE_READ, CANDIDATE_MATCH_READ,
        EMPLOYEE_READ, EMPLOYEE_CREATE, EMPLOYEE_UPDATE, EMPLOYEE_MANAGE,
        SITE_DEPLOYMENT_READ, SITE_DEPLOYMENT_CREATE, SITE_DEPLOYMENT_UPDATE, SITE_DEPLOYMENT_MANAGE,
        DEPLOYMENT_READ, DEPLOYMENT_CREATE,
        REPORT_READ, REPORT_EXPORT,
    ],

    # HR Executive — day-to-day hiring ops
    'hr_executive': [
        CLIENT_READ,
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        DEPARTMENT_READ,
        CAMPAIGN_READ, CAMPAIGN_CREATE, CAMPAIGN_UPDATE,
        SUBMISSION_READ, SUBMISSION_UPDATE,
        CANDIDATE_READ, CANDIDATE_CREATE, CANDIDATE_UPDATE, CANDIDATE_SHORTLIST,
        RESUME_READ, RESUME_VIEW, RESUME_UPLOAD,
        MRF_READ, MRF_CREATE,
        WORKFLOW_READ, WORKFLOW_START,
        WAGE_READ,
        INTERVIEW_READ, INTERVIEW_CREATE,
        OFFER_READ, OFFER_CREATE,
        HIRING_APP_READ, HIRING_APP_CREATE, HIRING_APP_UPDATE,
        PIPELINE_STAGE_READ, CANDIDATE_MATCH_READ,
        EMPLOYEE_READ,
        SITE_DEPLOYMENT_READ,
        DEPLOYMENT_READ,
        REPORT_READ,
    ],

    # HR Manager — HR approval/config owner, same operating rights as HR Admin.
    'hr_manager': [
        USER_READ,
        ROLE_READ,
        CLIENT_READ,
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        DEPARTMENT_READ, DEPARTMENT_CREATE, DEPARTMENT_UPDATE, DEPARTMENT_DELETE,
        CAMPAIGN_READ, CAMPAIGN_CREATE, CAMPAIGN_UPDATE, CAMPAIGN_DELETE,
        SUBMISSION_READ, SUBMISSION_UPDATE,
        CANDIDATE_READ, CANDIDATE_CREATE, CANDIDATE_UPDATE, CANDIDATE_SHORTLIST,
        RESUME_READ, RESUME_VIEW, RESUME_UPLOAD,
        MRF_READ, MRF_CREATE, MRF_UPDATE, MRF_DELETE, MRF_APPROVE, MRF_REJECT, MRF_OVERRIDE_COMMERCIALS,
        WORKFLOW_READ, WORKFLOW_START, WORKFLOW_APPROVE, WORKFLOW_REJECT, WORKFLOW_REASSIGN,
        WORKFLOW_CONFIG_READ, WORKFLOW_CONFIG_MANAGE,
        CLIENT_ONBOARDING_READ, CLIENT_ONBOARDING_UPDATE,
        WAGE_READ,
        BUDGET_READ,
        INTERVIEW_READ, INTERVIEW_CREATE, INTERVIEW_MANAGE,
        OFFER_READ, OFFER_CREATE, OFFER_APPROVE,
        HIRING_APP_READ, HIRING_APP_CREATE, HIRING_APP_UPDATE, HIRING_APP_MANAGE,
        PIPELINE_STAGE_READ, CANDIDATE_MATCH_READ,
        EMPLOYEE_READ, EMPLOYEE_CREATE, EMPLOYEE_UPDATE, EMPLOYEE_MANAGE,
        SITE_DEPLOYMENT_READ, SITE_DEPLOYMENT_CREATE, SITE_DEPLOYMENT_UPDATE, SITE_DEPLOYMENT_MANAGE,
        DEPLOYMENT_READ, DEPLOYMENT_CREATE,
        REPORT_READ, REPORT_EXPORT,
    ],

    # Finance — wage/budget review, financial MRF approval step
    'finance': [
        CLIENT_READ,
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        DEPARTMENT_READ,
        MRF_READ, MRF_APPROVE, MRF_REJECT,
        WORKFLOW_READ, WORKFLOW_APPROVE, WORKFLOW_REJECT,
        WORKFLOW_CONFIG_READ,
        CLIENT_ONBOARDING_READ,
        WAGE_READ, WAGE_CREATE, WAGE_UPDATE, WAGE_DELETE,
        BUDGET_READ, BUDGET_CREATE, BUDGET_UPDATE, BUDGET_DELETE,
        REPORT_READ, REPORT_EXPORT,
    ],

    # Finance Executive — can review assigned workflow steps and read finance data.
    'finance_executive': [
        CLIENT_READ,
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        DEPARTMENT_READ,
        MRF_READ,
        WORKFLOW_READ, WORKFLOW_APPROVE, WORKFLOW_REJECT,
        CLIENT_ONBOARDING_READ,
        WAGE_READ,
        BUDGET_READ,
        REPORT_READ,
    ],

    # Finance Manager — finance approval and wage/budget master owner.
    'finance_manager': [
        CLIENT_READ,
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        DEPARTMENT_READ,
        MRF_READ, MRF_APPROVE, MRF_REJECT, MRF_OVERRIDE_COMMERCIALS,
        WORKFLOW_READ, WORKFLOW_APPROVE, WORKFLOW_REJECT,
        WORKFLOW_CONFIG_READ,
        CLIENT_ONBOARDING_READ,
        WAGE_READ, WAGE_CREATE, WAGE_UPDATE, WAGE_DELETE,
        BUDGET_READ, BUDGET_CREATE, BUDGET_UPDATE, BUDGET_DELETE,
        REPORT_READ, REPORT_EXPORT,
    ],

    # HoD — department-level approval in workflow
    'hod': [
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        DEPARTMENT_READ,
        SUBMISSION_READ,
        CANDIDATE_READ, CANDIDATE_SHORTLIST,
        MRF_READ, MRF_CREATE,
        WORKFLOW_READ, WORKFLOW_START, WORKFLOW_APPROVE,
        WORKFLOW_CONFIG_READ,
        CLIENT_ONBOARDING_READ,
        INTERVIEW_READ, INTERVIEW_CREATE,
        OFFER_READ, OFFER_APPROVE,
        HIRING_APP_READ, PIPELINE_STAGE_READ,
        EMPLOYEE_READ,
        SITE_DEPLOYMENT_READ,
        DEPLOYMENT_READ,
        REPORT_READ,
    ],

    # Sales Manager — client/site management, MRF visibility, onboarding, reporting
    'sales_manager': [
        CLIENT_READ, CLIENT_CREATE, CLIENT_UPDATE,
        SITE_READ, SITE_CREATE, SITE_UPDATE,
        SITE_ROLE_REQ_READ, SITE_ROLE_REQ_CREATE, SITE_ROLE_REQ_UPDATE,
        JOB_ROLE_READ,
        DEPARTMENT_READ,
        CAMPAIGN_READ, CAMPAIGN_CREATE,
        SUBMISSION_READ,
        CANDIDATE_READ,
        MRF_READ, MRF_CREATE,
        WORKFLOW_READ, WORKFLOW_START,
        WORKFLOW_CONFIG_READ,
        CLIENT_ONBOARDING_READ, CLIENT_ONBOARDING_CREATE, CLIENT_ONBOARDING_UPDATE,
        WAGE_READ,
        BUDGET_READ,
        REPORT_READ, REPORT_EXPORT,
    ],

    # Sales Executive — creates clients/sites/campaigns, raises MRF, creates onboarding requests
    'sales_executive': [
        CLIENT_READ, CLIENT_CREATE, CLIENT_UPDATE,
        SITE_READ, SITE_CREATE, SITE_UPDATE,
        SITE_ROLE_REQ_READ, SITE_ROLE_REQ_CREATE,
        JOB_ROLE_READ,
        CAMPAIGN_READ, CAMPAIGN_CREATE,
        SUBMISSION_READ,
        CANDIDATE_READ,
        MRF_READ, MRF_CREATE,
        WORKFLOW_READ, WORKFLOW_START,
        CLIENT_ONBOARDING_READ, CLIENT_ONBOARDING_CREATE, CLIENT_ONBOARDING_UPDATE,
        WAGE_READ,
        REPORT_READ,
    ],

    # Operations Executive — site operations, MRF creation, assigned workflow action.
    'operations_executive': [
        CLIENT_READ,
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        DEPARTMENT_READ,
        CAMPAIGN_READ,
        SUBMISSION_READ,
        CANDIDATE_READ, CANDIDATE_SHORTLIST,
        MRF_READ, MRF_CREATE, MRF_UPDATE,
        WORKFLOW_READ, WORKFLOW_START, WORKFLOW_APPROVE, WORKFLOW_REJECT,
        CLIENT_ONBOARDING_READ, CLIENT_ONBOARDING_UPDATE,
        WAGE_READ,
        INTERVIEW_READ, INTERVIEW_CREATE,
        HIRING_APP_READ, HIRING_APP_UPDATE, PIPELINE_STAGE_READ,
        EMPLOYEE_READ,
        SITE_DEPLOYMENT_READ, SITE_DEPLOYMENT_CREATE, SITE_DEPLOYMENT_UPDATE,
        DEPLOYMENT_READ, DEPLOYMENT_CREATE,
        FIELD_TRACKING_READ,
        REPORT_READ,
    ],

    # Operations Manager — operational setup owner for clients/sites and approval flow.
    'operations_manager': [
        USER_READ,
        CLIENT_READ, CLIENT_UPDATE,
        SITE_READ, SITE_CREATE, SITE_UPDATE,
        SITE_ROLE_REQ_READ, SITE_ROLE_REQ_CREATE, SITE_ROLE_REQ_UPDATE,
        JOB_ROLE_READ,
        DEPARTMENT_READ, DEPARTMENT_CREATE, DEPARTMENT_UPDATE,
        CAMPAIGN_READ, CAMPAIGN_CREATE, CAMPAIGN_UPDATE,
        SUBMISSION_READ,
        CANDIDATE_READ, CANDIDATE_SHORTLIST,
        MRF_READ, MRF_CREATE, MRF_UPDATE, MRF_APPROVE, MRF_REJECT, MRF_OVERRIDE_COMMERCIALS,
        WORKFLOW_READ, WORKFLOW_START, WORKFLOW_APPROVE, WORKFLOW_REJECT, WORKFLOW_REASSIGN,
        WORKFLOW_CONFIG_READ,
        CLIENT_ONBOARDING_READ, CLIENT_ONBOARDING_UPDATE,
        WAGE_READ,
        BUDGET_READ,
        INTERVIEW_READ, INTERVIEW_CREATE, INTERVIEW_MANAGE,
        HIRING_APP_READ, HIRING_APP_UPDATE, PIPELINE_STAGE_READ, CANDIDATE_MATCH_READ,
        EMPLOYEE_READ,
        SITE_DEPLOYMENT_READ, SITE_DEPLOYMENT_CREATE, SITE_DEPLOYMENT_UPDATE, SITE_DEPLOYMENT_MANAGE,
        DEPLOYMENT_READ, DEPLOYMENT_CREATE, DEPLOYMENT_MANAGE,
        FIELD_TRACKING_READ,
        REPORT_READ, REPORT_EXPORT,
    ],

    # Site Manager — site-level operations and field deployment
    'site_manager': [
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        CAMPAIGN_READ,
        SUBMISSION_READ,
        CANDIDATE_READ, CANDIDATE_SHORTLIST,
        MRF_READ, MRF_CREATE,
        WORKFLOW_READ, WORKFLOW_START,
        INTERVIEW_READ, INTERVIEW_CREATE,
        OFFER_READ,
        HIRING_APP_READ, HIRING_APP_UPDATE, PIPELINE_STAGE_READ,
        EMPLOYEE_READ,
        SITE_DEPLOYMENT_READ, SITE_DEPLOYMENT_CREATE, SITE_DEPLOYMENT_UPDATE,
        DEPLOYMENT_READ, DEPLOYMENT_CREATE,
        FIELD_TRACKING_READ,
        REPORT_READ,
    ],

    # Field Supervisor — attendance and deployment tracking
    'field_supervisor': [
        SITE_READ,
        SUBMISSION_READ,
        CANDIDATE_READ,
        EMPLOYEE_READ,
        SITE_DEPLOYMENT_READ,
        DEPLOYMENT_READ, DEPLOYMENT_CREATE,
        FIELD_TRACKING_READ,
    ],

    # Client Admin — scoped to client node, can see all child sites and raise MRFs
    'client_admin': [
        CLIENT_READ,
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        DEPARTMENT_READ,
        WAGE_READ,
        BUDGET_READ,
        MRF_READ, MRF_CREATE, MRF_UPDATE, MRF_OVERRIDE_COMMERCIALS,
        WORKFLOW_READ, WORKFLOW_START,
        REPORT_READ,
    ],

    # Client Site User — scoped to one site node
    'client_site_user': [
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        DEPARTMENT_READ,
        WAGE_READ,
        BUDGET_READ,
        MRF_READ, MRF_CREATE, MRF_UPDATE, MRF_OVERRIDE_COMMERCIALS,
        WORKFLOW_READ, WORKFLOW_START,
        REPORT_READ,
    ],

    # Site Supervisor — site-level requester/approver from client side
    'site_supervisor': [
        SITE_READ, SITE_ROLE_REQ_READ,
        JOB_ROLE_READ,
        DEPARTMENT_READ,
        WAGE_READ,
        MRF_READ, MRF_CREATE, MRF_UPDATE, MRF_OVERRIDE_COMMERCIALS,
        WORKFLOW_READ, WORKFLOW_START,
        DEPLOYMENT_READ,
        FIELD_TRACKING_READ,
        REPORT_READ,
    ],

    # Legacy generic client role; keep for backward-compatible assignments.
    'client_user': [
        SITE_READ,
        SUBMISSION_READ,
        CANDIDATE_READ,
        MRF_READ,
        DEPLOYMENT_READ,
        REPORT_READ,
    ],
}


CLIENT_FACING_ROLE_CODES = frozenset({
    'client_admin',
    'client_site_user',
    'site_supervisor',
    'client_user',
})


def is_client_facing_user(user) -> bool:
    """
    Return True when the user's active role assignments are all client-facing roles.

    Superusers always return False (treated as internal).
    Users with no role assignments return False.
    Users who hold at least one internal role alongside a client role return False.
    """
    if user.is_superuser:
        return False
    from apps.access.models import UserRoleAssignment
    role_codes = set(
        UserRoleAssignment.objects
        .filter(user=user, role__is_active=True)
        .values_list('role__code', flat=True)
    )
    if not role_codes:
        return False
    return role_codes.issubset(CLIENT_FACING_ROLE_CODES)


def get_capabilities_for_role(role_code: str) -> list:
    """Return list of capability strings for a given role code."""
    return ROLE_CAPABILITIES.get(role_code, [])


def get_user_capabilities(user) -> list:
    """
    Return sorted unique capability strings for the user, sourced from DB.

    Superuser gets ALL_CAPABILITIES. Normal users get the union of Permission.code
    values across all active AccessRolePermission rows for their role assignments.
    ROLE_CAPABILITIES is NOT consulted at runtime — only AccessRolePermission rows matter.
    """
    if user.is_superuser:
        return sorted(ALL_CAPABILITIES)

    from apps.access.models import UserRoleAssignment

    codes = (
        UserRoleAssignment.objects
        .filter(user=user, role__is_active=True)
        .values_list('role__role_permissions__permission__code', flat=True)
    )
    return sorted({c for c in codes if c})
