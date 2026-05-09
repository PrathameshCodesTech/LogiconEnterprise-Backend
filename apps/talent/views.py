from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import Candidate, Resume
from .serializers import CandidateSerializer, ResumeSerializer


class CandidateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Candidate.objects.all()
    serializer_class = CandidateSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['org', 'source', 'is_blacklisted']
    search_fields = ['first_name', 'last_name', 'phone', 'email']


class ResumeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Resume.objects.all()
    serializer_class = ResumeSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['candidate', 'parsed_status']
