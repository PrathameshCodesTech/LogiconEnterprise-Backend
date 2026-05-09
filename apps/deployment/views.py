from rest_framework.permissions import IsAuthenticated

from apps.access.permissions import HasCapability
from apps.access.querysets import filter_employees_for_user, filter_site_deployments_for_user
from apps.access.viewsets import ScopedReadOnlyModelViewSet

from .models import Employee, SiteDeployment
from .serializers import EmployeeSerializer, SiteDeploymentSerializer


class EmployeeViewSet(ScopedReadOnlyModelViewSet):
    queryset = Employee.objects.select_related(
        'org', 'candidate', 'user', 'job_role',
    ).order_by('last_name', 'first_name')
    serializer_class = EmployeeSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'employee.read'
    scope_filter = filter_employees_for_user
    filterset_fields = ['org', 'job_role', 'status']
    search_fields = ['employee_code', 'first_name', 'last_name', 'phone', 'email']


class SiteDeploymentViewSet(ScopedReadOnlyModelViewSet):
    queryset = SiteDeployment.objects.select_related(
        'org', 'employee', 'site', 'site__scope_node',
        'site__client__scope_node', 'job_role',
    ).order_by('-start_date')
    serializer_class = SiteDeploymentSerializer
    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = 'site_deployment.read'
    scope_filter = filter_site_deployments_for_user
    filterset_fields = ['org', 'site', 'employee', 'job_role', 'status', 'billing_type']
