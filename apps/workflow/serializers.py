from rest_framework import serializers

from .models import (
    WorkflowTemplate, WorkflowStepTemplate,
    WorkflowInstance, WorkflowStepInstance, WorkflowAction,
)


# ─── Read serializers ─────────────────────────────────────────────────────────

class WorkflowStepTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowStepTemplate
        fields = [
            'id', 'template', 'order', 'code', 'name',
            'assignment_mode', 'actor_type',
            'on_approve_next', 'on_reject_target', 'on_request_changes_target',
            'requires_comment_on_reject', 'requires_comment_on_request_changes',
            'sla_hours',
        ]
        read_only_fields = fields


class WorkflowTemplateSerializer(serializers.ModelSerializer):
    steps = WorkflowStepTemplateSerializer(many=True, read_only=True)

    class Meta:
        model = WorkflowTemplate
        fields = [
            'id', 'org', 'name', 'code', 'trigger_type', 'version',
            'description', 'is_active', 'steps', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class WorkflowStepInstanceSerializer(serializers.ModelSerializer):
    assigned_user_username = serializers.CharField(
        source='assigned_user.username', read_only=True, default=None,
    )
    acted_by_username = serializers.CharField(
        source='acted_by.username', read_only=True, default=None,
    )

    class Meta:
        model = WorkflowStepInstance
        fields = [
            'id', 'workflow', 'step_template',
            'step_order', 'step_code', 'step_name',
            'assignment_mode', 'actor_type',
            'on_approve_next', 'on_reject_target', 'on_request_changes_target',
            'requires_comment_on_reject', 'requires_comment_on_request_changes',
            'sla_hours',
            'assigned_user', 'assigned_user_username', 'assigned_at',
            'assigned_department', 'assigned_department_name_snapshot', 'assigned_department_code_snapshot',
            'status', 'acted_by', 'acted_by_username', 'acted_at',
            'action_taken', 'comment',
            'activated_at', 'due_at',
        ]
        read_only_fields = fields


class WorkflowActionSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source='actor.username', read_only=True)

    class Meta:
        model = WorkflowAction
        fields = [
            'id', 'workflow', 'step_instance',
            'actor', 'actor_username', 'action', 'comment',
            'reassign_from', 'reassign_to', 'created_at',
        ]
        read_only_fields = fields


class WorkflowInstanceSerializer(serializers.ModelSerializer):
    steps = WorkflowStepInstanceSerializer(many=True, read_only=True)
    audit_trail = WorkflowActionSerializer(many=True, read_only=True)
    current_step = serializers.SerializerMethodField()
    initiated_by_username = serializers.CharField(
        source='initiated_by.username', read_only=True,
    )

    class Meta:
        model = WorkflowInstance
        fields = [
            'id', 'org', 'mrf', 'client_onboarding_request',
            'template', 'template_version',
            'approval_route', 'approval_route_name_snapshot', 'approval_route_code_snapshot',
            'status', 'initiated_by', 'initiated_by_username',
            'started_at', 'completed_at',
            'current_step', 'steps', 'audit_trail',
        ]
        read_only_fields = fields

    # 'created_at' is the canonical start timestamp from TimeStampedModel
    started_at = serializers.DateTimeField(source='created_at', read_only=True)

    def get_current_step(self, obj):
        step = obj.steps.filter(status='active').first()
        if step is None:
            return None
        return WorkflowStepInstanceSerializer(step).data


# ─── Write serializers ────────────────────────────────────────────────────────

class ActOnStepSerializer(serializers.Serializer):
    ACTION_CHOICES = [
        ('approve', 'Approve'),
        ('reject', 'Reject'),
        ('request_changes', 'Request Changes'),
    ]
    action = serializers.ChoiceField(choices=ACTION_CHOICES)
    comment = serializers.CharField(required=False, allow_blank=True, default='')


class ReassignStepSerializer(serializers.Serializer):
    new_user = serializers.IntegerField(help_text='Primary key of the new assignee.')
    comment = serializers.CharField(required=False, allow_blank=True, default='')


class WorkflowMyTaskSerializer(serializers.BaseSerializer):
    """
    Read-only shape for GET /api/workflow/my-tasks/.
    All keys are always present; MRF-only fields are null for client_onboarding rows.
    """

    def to_representation(self, step):
        wf = step.workflow
        mrf = wf.mrf if wf.mrf_id else None
        onboarding = wf.client_onboarding_request if wf.client_onboarding_request_id else None

        if mrf is not None:
            target_type = 'mrf'
            target_id = mrf.pk
            site = mrf.site
            client = getattr(site, 'client', None) if site else None
            target_title = f'MRF #{mrf.pk} - {site.name}' if site else f'MRF #{mrf.pk}'
            target_status = mrf.status
            target_url = f'/mrf/{mrf.pk}'
            client_id = client.pk if client else None
            client_name = client.name if client else None
            site_id = site.pk if site else None
            site_name = site.name if site else None
            req_dept = mrf.requesting_department
            reqd_dept = mrf.required_department
            requesting_department_name = req_dept.name if req_dept else None
            required_department_name = reqd_dept.name if reqd_dept else None
            if hasattr(mrf, '_prefetched_objects_cache') and 'line_items' in mrf._prefetched_objects_cache:
                line_item_count = len(mrf.line_items.all())
            else:
                line_item_count = mrf.line_items.count()
        else:
            target_type = 'client_onboarding'
            target_id = onboarding.pk
            client = onboarding.client if onboarding else None
            target_title = f'Client onboarding #{onboarding.pk} - {client.name}' if client else f'Client onboarding #{onboarding.pk}'
            target_status = onboarding.status
            target_url = f'/client-onboarding/{onboarding.pk}'
            client_id = client.pk if client else None
            client_name = client.name if client else None
            site_id = None
            site_name = None
            requesting_department_name = None
            required_department_name = None
            line_item_count = None

        dept_name = step.assigned_department_name_snapshot or None
        dept_code = step.assigned_department_code_snapshot or None
        if (not dept_name or not dept_code) and step.assigned_department_id:
            d = step.assigned_department
            if d is not None:
                dept_name = dept_name or d.name
                dept_code = dept_code or d.code

        return {
            'workflow_id': wf.pk,
            'step_id': step.pk,
            'step_code': step.step_code,
            'step_name': step.step_name,
            'step_status': step.status,
            'assigned_user': step.assigned_user_id,
            'assigned_user_username': step.assigned_user.username if step.assigned_user_id else None,
            'assigned_department_name': dept_name or None,
            'assigned_department_code': dept_code or None,
            'activated_at': step.activated_at,
            'due_at': step.due_at,
            'target_type': target_type,
            'target_id': target_id,
            'target_title': target_title,
            'target_status': target_status,
            'target_url': target_url,
            'client_id': client_id,
            'client_name': client_name,
            'site_id': site_id,
            'site_name': site_name,
            'requesting_department_name': requesting_department_name,
            'required_department_name': required_department_name,
            'line_item_count': line_item_count,
        }


def _assigned_department_display(step):
    name = step.assigned_department_name_snapshot or None
    code = step.assigned_department_code_snapshot or None
    if (not name or not code) and step.assigned_department_id:
        d = step.assigned_department
        if d is not None:
            name = name or d.name
            code = code or d.code
    return name or None, code or None


def _compact_workflow_step(step):
    dept_name, _ = _assigned_department_display(step)
    return {
        'id': step.pk,
        'step_order': step.step_order,
        'step_code': step.step_code,
        'step_name': step.step_name,
        'status': step.status,
        'assigned_user': step.assigned_user_id,
        'assigned_user_username': (
            step.assigned_user.username if step.assigned_user_id else None
        ),
        'assigned_department_name': dept_name,
        'acted_by_username': (
            step.acted_by.username if step.acted_by_id else None
        ),
        'acted_at': step.acted_at,
        'action_taken': step.action_taken or '',
    }


def _compact_audit_entry(entry):
    return {
        'id': entry.pk,
        'action': entry.action,
        'actor_username': entry.actor.username if entry.actor_id else None,
        'comment': entry.comment or '',
        'created_at': entry.created_at,
    }


def _serialize_workflow_drawer(workflow, current_step_id, steps_sorted=None, audit_sorted=None):
    if steps_sorted is None:
        steps_sorted = sorted(list(workflow.steps.all()), key=lambda s: s.step_order)
    if audit_sorted is None:
        audit_sorted = sorted(list(workflow.audit_trail.all()), key=lambda a: a.created_at)
    tpl = workflow.template
    tpl_id = workflow.template_id
    return {
        'id': workflow.pk,
        'status': workflow.status,
        'template': tpl_id,
        'template_name': tpl.name if tpl_id else None,
        'template_version': workflow.template_version,
        'started_at': workflow.created_at,
        'completed_at': workflow.completed_at,
        'current_step_id': current_step_id,
        'steps': [_compact_workflow_step(s) for s in steps_sorted],
        'audit_trail': [_compact_audit_entry(a) for a in audit_sorted],
    }


def _decimal_str(val):
    if val is None:
        return None
    return format(val, 'f')


def _mrf_budget_reservation_summary(mrf):
    from decimal import Decimal
    from django.db.models import Sum
    from apps.budgets.models import BudgetReservation

    reserved = BudgetReservation.objects.filter(
        mrf=mrf, status='reserved',
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    committed = BudgetReservation.objects.filter(
        mrf=mrf, status='committed',
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    latest = BudgetReservation.objects.filter(mrf=mrf).order_by('-created_at').first()

    return {
        'budget_reserved_amount': str(reserved.quantize(Decimal('0.01'))),
        'budget_committed_amount': str(committed.quantize(Decimal('0.01'))),
        'budget_reservation_status': latest.status if latest else None,
    }


def _serialize_mrf_drawer(mrf):
    from apps.mrf.services import get_resolved_budget_context

    site = mrf.site
    client = getattr(site, 'client', None) if site else None
    req_d = mrf.requesting_department
    reqd = mrf.required_department
    bp = mrf.budget_plan
    data = {
        'id': mrf.pk,
        'status': mrf.status,
        'mrf_type': mrf.mrf_type,
        'requested_by_username': (
            mrf.requested_by.username if mrf.requested_by_id else None
        ),
        'requested_by_type': mrf.requested_by_type,
        'site': site.pk if site else None,
        'site_name': site.name if site else None,
        'client_id': client.pk if client else None,
        'client_name': client.name if client else None,
        'requesting_department': mrf.requesting_department_id,
        'requesting_department_name': req_d.name if req_d else None,
        'required_department': mrf.required_department_id,
        'required_department_name': reqd.name if reqd else None,
        'billing_type': mrf.billing_type,
        'required_by_date': mrf.required_by_date,
        'reason': mrf.reason or '',
        'client_visible': mrf.client_visible,
        'budget_plan': mrf.budget_plan_id,
        'budget_plan_name': bp.name if bp else None,
        'budget_plan_code': bp.code if bp else None,
        'budget_plan_amount': _decimal_str(bp.amount) if bp else None,
        'budget_plan_currency': bp.currency if bp else None,
        'budget_plan_status': bp.status if bp else None,
    }
    data.update(_mrf_budget_reservation_summary(mrf))
    data.update(get_resolved_budget_context(mrf))
    return data


def _serialize_mrf_line_item_drawer(li):
    jr = li.job_role
    wc = li.wage_category
    bp = li.budget_plan
    return {
        'id': li.pk,
        'job_role': li.job_role_id,
        'job_role_name': jr.name if jr else None,
        'headcount': li.headcount,
        'site_role_requirement': li.site_role_requirement_id,
        'wage_category_name': wc.name if wc else None,
        'wage_min_requested': _decimal_str(li.wage_min_requested),
        'wage_max_requested': _decimal_str(li.wage_max_requested),
        'billing_rate_snapshot': _decimal_str(li.billing_rate_snapshot),
        'budget_plan': li.budget_plan_id,
        'budget_plan_name': bp.name if bp else None,
    }


def _onboarding_notes(req):
    blocks = []
    if (req.operations_notes or '').strip():
        blocks.append(f'[Operations] {req.operations_notes.strip()}')
    if (req.hr_notes or '').strip():
        blocks.append(f'[HR] {req.hr_notes.strip()}')
    if (req.finance_notes or '').strip():
        blocks.append(f'[Finance] {req.finance_notes.strip()}')
    return '\n\n'.join(blocks)


def _serialize_proposed_site(site):
    la = site.location_area
    return {
        'id': site.pk,
        'name': site.name,
        'code': site.code,
        'address': site.address or '',
        'city': site.city or '',
        'state': site.state or '',
        'pincode': site.pincode or '',
        'contact_person': site.contact_person or '',
        'contact_phone': site.contact_phone or '',
        'contact_email': site.contact_email or '',
        'location_area': site.location_area_id,
        'location_area_name': la.name if la else None,
        'is_active': site.is_active,
    }


def _serialize_proposed_department(dept):
    ps = dept.proposed_site
    return {
        'id': dept.pk,
        'name': dept.name,
        'code': dept.code,
        'scope_level': dept.scope_level,
        'description': dept.description or '',
        'proposed_site': dept.proposed_site_id,
        'proposed_site_name': ps.name if ps else None,
        'is_active': dept.is_active,
    }


def _serialize_proposed_srr(srr):
    ps = srr.proposed_site
    pd = srr.proposed_department
    jr = srr.job_role
    wc = srr.wage_category
    return {
        'id': srr.pk,
        'proposed_site': srr.proposed_site_id,
        'proposed_site_name': ps.name if ps else None,
        'proposed_department': srr.proposed_department_id,
        'proposed_department_name': pd.name if pd else None,
        'job_role': srr.job_role_id,
        'job_role_name': jr.name if jr else None,
        'approved_headcount': srr.approved_headcount,
        'billing_type': srr.billing_type,
        'billing_rate': _decimal_str(srr.billing_rate),
        'wage_min': _decimal_str(srr.wage_min),
        'wage_max': _decimal_str(srr.wage_max),
        'shift_hours': _decimal_str(srr.shift_hours),
        'wage_category': srr.wage_category_id,
        'wage_category_name': wc.name if wc else None,
        'effective_from': srr.effective_from,
        'effective_to': srr.effective_to,
        'is_active': srr.is_active,
    }


def _serialize_proposed_user(pu):
    ar = pu.access_role
    ps = pu.proposed_site
    return {
        'id': pu.pk,
        'full_name': pu.full_name,
        'email': pu.email,
        'phone': pu.phone or '',
        'user_type': pu.user_type,
        'access_role': pu.access_role_id,
        'access_role_code': ar.code if ar else None,
        'access_role_name': ar.name if ar else None,
        'scope_level': pu.scope_level,
        'proposed_site': pu.proposed_site_id,
        'proposed_site_name': ps.name if ps else None,
        'is_primary_contact': pu.is_primary_contact,
        'send_invite_on_finalization': pu.send_invite_on_finalization,
        'is_active': pu.is_active,
        'created_user': pu.created_user_id,
        'invite_status': pu.invite_status,
    }


def _serialize_proposed_budget(budget):
    ps = budget.proposed_site
    pd = budget.proposed_department
    return {
        'id': budget.pk,
        'name': budget.name,
        'code': budget.code,
        'budget_nature': budget.budget_nature,
        'budget_type': budget.budget_type,
        'scope_level': budget.scope_level,
        'proposed_site': budget.proposed_site_id,
        'proposed_site_name': ps.name if ps else None,
        'proposed_department': budget.proposed_department_id,
        'proposed_department_name': pd.name if pd else None,
        'amount': _decimal_str(budget.amount),
        'currency': budget.currency,
        'period_start': budget.period_start,
        'period_end': budget.period_end,
        'notes': budget.notes or '',
        'is_active': budget.is_active,
    }


def _serialize_onboarding_drawer(req):
    cl = req.client
    bp = req.budget_plan
    cc = req.created_client
    return {
        'id': req.pk,
        'status': req.status,
        'onboarding_type': req.onboarding_type,
        'client': cl.pk if cl else None,
        'client_name': cl.name if cl else None,
        'requested_by_username': (
            req.requested_by.username if req.requested_by_id else None
        ),
        'summary': req.summary or '',
        'expected_sites_count': req.expected_site_count,
        'budget_plan': req.budget_plan_id,
        'budget_plan_name': bp.name if bp else None,
        'notes': _onboarding_notes(req),
        'proposed_client_name': req.proposed_client_name or '',
        'proposed_client_code': req.proposed_client_code or '',
        'proposed_contact_name': req.proposed_contact_name or '',
        'proposed_contact_email': req.proposed_contact_email or '',
        'proposed_contact_phone': req.proposed_contact_phone or '',
        'proposed_industry': req.proposed_industry or '',
        'proposed_billing_address': req.proposed_billing_address or '',
        'proposed_gst_number': req.proposed_gst_number or '',
        'finalization_status': req.finalization_status,
        'created_client': cc.pk if cc else None,
        'proposed_sites': [
            _serialize_proposed_site(s) for s in req.proposed_sites.all()
        ],
        'proposed_departments': [
            _serialize_proposed_department(d) for d in req.proposed_departments.all()
        ],
        'proposed_role_requirements': [
            _serialize_proposed_srr(s) for s in req.proposed_role_requirements.all()
        ],
        'proposed_budgets': [
            _serialize_proposed_budget(b) for b in req.proposed_budgets.all()
        ],
        'proposed_users': [
            _serialize_proposed_user(u) for u in req.proposed_users.all()
        ],
    }


def serialize_my_workflow_task_detail(step, request):
    """
    Build the GET /api/workflow/my-tasks/{step_id}/ response body.
    Caller must ensure `step` is already authorized (active assignee or superuser path).
    """
    from django.urls import reverse

    wf = step.workflow
    steps_sorted = sorted(list(wf.steps.all()), key=lambda s: s.step_order)
    active = next((s for s in steps_sorted if s.status == 'active'), None)
    current_step_id = active.pk if active else None
    audit_sorted = sorted(list(wf.audit_trail.all()), key=lambda a: a.created_at)

    task_data = WorkflowMyTaskSerializer().to_representation(step)

    workflow_data = _serialize_workflow_drawer(
        wf, current_step_id, steps_sorted=steps_sorted, audit_sorted=audit_sorted,
    )

    if wf.mrf_id:
        mrf = wf.mrf
        line_items = sorted(mrf.line_items.all(), key=lambda x: x.pk)
        target_payload = {
            'type': 'mrf',
            'mrf': _serialize_mrf_drawer(mrf),
            'line_items': [_serialize_mrf_line_item_drawer(li) for li in line_items],
        }
    else:
        req = wf.client_onboarding_request
        target_payload = {
            'type': 'client_onboarding',
            'client_onboarding': _serialize_onboarding_drawer(req),
            'line_items': [],
        }

    act_path = reverse(
        'workflow-step-act',
        kwargs={'instance_id': wf.pk, 'step_id': step.pk},
    )

    actions = {
        'can_approve': True,
        'can_reject': True,
        'can_request_changes': True,
        'act_url': act_path,
    }

    return {
        'task': task_data,
        'workflow': workflow_data,
        'target': target_payload,
        'actions': actions,
    }
