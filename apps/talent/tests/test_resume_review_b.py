"""
apps/talent/tests/test_resume_review_b.py

Phase Resume-Parser-Backend-B — HR review workflow tests.

Tests cover:
  - review-queue: lists manual_review, failed, low-confidence indexed
  - review-detail: full payload for correction UI
  - apply-review: candidate update, skill/exp/edu replacement, audit record
  - resolve-duplicate: link_existing, mark_duplicate, keep_separate
  - reprocess: audit row created, errors cleared
  - review-history: audit log
"""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.capabilities import CANDIDATE_UPDATE, RESUME_READ, RESUME_UPLOAD
from apps.access.models import AccessRole, UserRoleAssignment
from apps.access.tests.utils import bootstrap_role_permissions
from apps.accounts.models import User
from apps.core.models import Organization, ScopeNode
from apps.talent.models import (
    Candidate, Resume, CandidateSkill, CandidateExperience,
    CandidateEducation, ParsedResume, TalentResumeReview,
)


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _org(code):
    return Organization.objects.create(name=f'Org {code}', code=code)


def _scope(org):
    return ScopeNode.objects.create(
        org=org, code=org.code, name=org.code,
        node_type='company', parent=None, depth=0,
        path=org.code, is_active=True,
    )


def _user(username, org):
    u = User.objects.create_user(username=username, password='pass')
    u.org = org
    u.save()
    return u


def _grant(user, org, scope, caps):
    role = AccessRole.objects.create(org=org, code=f'r_{user.username}', name=f'r_{user.username}')
    bootstrap_role_permissions(role, caps)
    UserRoleAssignment.objects.create(user=user, role=role, scope_node=scope)


def _candidate(org, phone='9876543210'):
    return Candidate.objects.create(
        org=org,
        phone=phone, phone_normalized=phone,
        first_name='Raj', last_name='Kumar',
        source='manual',
    )


def _resume(candidate, status='manual_review', **kwargs):
    defaults = dict(
        file=SimpleUploadedFile('cv.pdf', b'%PDF-fake', content_type='application/pdf'),
        original_filename='cv.pdf',
        status=status,
        source_type='manual_upload',
        manual_review_reason='No API key' if status == 'manual_review' else '',
    )
    defaults.update(kwargs)
    return Resume.objects.create(candidate=candidate, **defaults)


def _parsed_resume(resume):
    return ParsedResume.objects.create(
        resume=resume,
        parsed_json={'full_name': 'Raj Kumar', 'email': 'raj@example.com', 'skills': []},
        normalized_json={'first_name': 'Raj', 'last_name': 'Kumar', 'skills': []},
        validation_errors={},
        missing_fields=[],
        confidence=Decimal('0.60'),
    )


# ─── 1. Review Queue ──────────────────────────────────────────────────────────

class TestReviewQueue(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.org = _org('trq')
        self.scope = _scope(self.org)
        self.user = _user('hrq', self.org)
        _grant(self.user, self.org, self.scope, [RESUME_READ, CANDIDATE_UPDATE, RESUME_UPLOAD])
        self.api.force_authenticate(user=self.user)

        self.candidate = _candidate(self.org)
        self.resume_mr = _resume(self.candidate, status='manual_review',
                                 manual_review_reason='No API key')
        self.resume_failed = _resume(self.candidate, status='failed',
                                     error_message='Extraction error', manual_review_reason='')
        self.resume_indexed_low = _resume(
            self.candidate, status='indexed', manual_review_reason='',
            parser_confidence=Decimal('0.45'),
        )
        self.resume_indexed_ok = _resume(
            self.candidate, status='indexed', manual_review_reason='',
            parser_confidence=Decimal('0.95'),
        )

    def test_01_review_queue_lists_manual_review(self):
        resp = self.api.get('/api/talent/resumes/review-queue/')
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(self.resume_mr.pk, ids)
        self.assertNotIn(self.resume_indexed_ok.pk, ids)

    def test_02_review_queue_lists_failed(self):
        resp = self.api.get('/api/talent/resumes/review-queue/')
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(self.resume_failed.pk, ids)

    def test_03_review_queue_low_confidence_indexed_with_threshold(self):
        resp = self.api.get('/api/talent/resumes/review-queue/?confidence_below=0.5')
        self.assertEqual(resp.status_code, 200)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(self.resume_indexed_low.pk, ids)
        self.assertNotIn(self.resume_indexed_ok.pk, ids)


# ─── 2. Review Detail ─────────────────────────────────────────────────────────

class TestReviewDetail(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.org = _org('trd')
        self.scope = _scope(self.org)
        self.user = _user('hrd', self.org)
        _grant(self.user, self.org, self.scope, [RESUME_READ, CANDIDATE_UPDATE])
        self.api.force_authenticate(user=self.user)

        self.candidate = _candidate(self.org)
        self.resume = _resume(self.candidate, status='manual_review',
                              raw_text='John Doe engineer', cleaned_text='John Doe engineer')
        self.parsed = _parsed_resume(self.resume)

        CandidateSkill.objects.create(
            candidate=self.candidate, skill_name='Python',
            normalized_skill_name='python', source='parsed', source_resume=self.resume,
        )
        CandidateExperience.objects.create(
            candidate=self.candidate, job_title='Engineer',
            company_name='Acme', source_resume=self.resume,
        )
        CandidateEducation.objects.create(
            candidate=self.candidate, degree='B.Tech',
            institute='NIT', source_resume=self.resume,
        )

    def test_04_review_detail_returns_full_payload(self):
        url = f'/api/talent/resumes/{self.resume.pk}/review-detail/'
        resp = self.api.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['id'], self.resume.pk)
        self.assertIn('candidate', resp.data)
        self.assertIn('parsed_resume', resp.data)
        self.assertEqual(resp.data['raw_text'], 'John Doe engineer')
        self.assertEqual(resp.data['cleaned_text'], 'John Doe engineer')
        self.assertEqual(len(resp.data['parsed_skills']), 1)
        self.assertEqual(len(resp.data['parsed_experience']), 1)
        self.assertEqual(len(resp.data['parsed_education']), 1)


# ─── 3. Apply Review ──────────────────────────────────────────────────────────

class TestApplyReview(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.org = _org('tar')
        self.scope = _scope(self.org)
        self.user = _user('hrar', self.org)
        _grant(self.user, self.org, self.scope, [RESUME_READ, CANDIDATE_UPDATE, RESUME_UPLOAD])
        self.api.force_authenticate(user=self.user)

        self.candidate = _candidate(self.org)
        self.resume = _resume(self.candidate, status='manual_review',
                              error_message='old error', manual_review_reason='old reason')
        self.parsed = _parsed_resume(self.resume)

        CandidateSkill.objects.create(
            candidate=self.candidate, skill_name='Tally',
            normalized_skill_name='tally', source='manual', source_resume=None,
        )
        CandidateSkill.objects.create(
            candidate=self.candidate, skill_name='Excel',
            normalized_skill_name='excel', source='parsed', source_resume=self.resume,
        )

    def _apply_url(self):
        return f'/api/talent/resumes/{self.resume.pk}/apply-review/'

    def test_05_apply_review_updates_candidate(self):
        payload = {
            'candidate': {'first_name': 'Rajesh', 'current_role': 'Security Guard'},
            'review_note': 'Fixed name',
        }
        resp = self.api.post(self._apply_url(), payload, format='json')
        self.assertEqual(resp.status_code, 200)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.first_name, 'Rajesh')
        self.assertEqual(self.candidate.current_role, 'Security Guard')

    def test_06_apply_review_replaces_parsed_skills_keeps_manual(self):
        payload = {
            'skills': [{'skill_name': 'Python', 'proficiency': 'intermediate'}],
        }
        resp = self.api.post(self._apply_url(), payload, format='json')
        self.assertEqual(resp.status_code, 200)
        skills = list(CandidateSkill.objects.filter(candidate=self.candidate).values('skill_name', 'source'))
        skill_names_by_source = {s['skill_name']: s['source'] for s in skills}
        self.assertIn('Tally', skill_names_by_source)
        self.assertEqual(skill_names_by_source['Tally'], 'manual')
        self.assertIn('Python', skill_names_by_source)
        self.assertEqual(skill_names_by_source['Python'], 'reviewed')
        self.assertNotIn('Excel', skill_names_by_source)

    def test_07_apply_review_replaces_experience_and_education(self):
        CandidateExperience.objects.create(
            candidate=self.candidate, job_title='Old Job',
            company_name='Old Co', source_resume=self.resume,
        )
        CandidateEducation.objects.create(
            candidate=self.candidate, degree='Old Degree',
            institute='Old Inst', source_resume=self.resume,
        )
        payload = {
            'experience': [{'job_title': 'Security Officer', 'company_name': 'NewCo'}],
            'education': [{'degree': 'B.Com', 'institute': 'Mumbai Uni'}],
        }
        resp = self.api.post(self._apply_url(), payload, format='json')
        self.assertEqual(resp.status_code, 200)
        exps = list(CandidateExperience.objects.filter(candidate=self.candidate, source_resume=self.resume))
        edus = list(CandidateEducation.objects.filter(candidate=self.candidate, source_resume=self.resume))
        self.assertEqual(len(exps), 1)
        self.assertEqual(exps[0].job_title, 'Security Officer')
        self.assertEqual(len(edus), 1)
        self.assertEqual(edus[0].degree, 'B.Com')

    def test_08_apply_review_upserts_parsed_resume(self):
        payload = {'skills': [{'skill_name': 'Python'}], 'review_note': 'OK'}
        resp = self.api.post(self._apply_url(), payload, format='json')
        self.assertEqual(resp.status_code, 200)
        pr = ParsedResume.objects.get(resume=self.resume)
        self.assertEqual(pr.validation_errors, [])
        self.assertEqual(pr.missing_fields, [])
        self.assertEqual(pr.confidence, Decimal('1.00'))

    def test_09_apply_review_sets_resume_indexed_and_clears_errors(self):
        payload = {}
        resp = self.api.post(self._apply_url(), payload, format='json')
        self.assertEqual(resp.status_code, 200)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.status, 'indexed')
        self.assertEqual(self.resume.error_message, '')
        self.assertEqual(self.resume.manual_review_reason, '')
        self.assertEqual(self.resume.parser_confidence, Decimal('1.00'))

    def test_10_apply_review_creates_audit_record(self):
        payload = {'review_note': 'Reviewed by HR'}
        resp = self.api.post(self._apply_url(), payload, format='json')
        self.assertEqual(resp.status_code, 200)
        review = TalentResumeReview.objects.filter(resume=self.resume, review_type='correction').first()
        self.assertIsNotNone(review)
        self.assertEqual(review.previous_status, 'manual_review')
        self.assertEqual(review.new_status, 'indexed')
        self.assertEqual(review.reviewed_by, self.user)
        self.assertEqual(review.review_note, 'Reviewed by HR')

    def test_11_apply_review_rejects_invalid_phone(self):
        payload = {'candidate': {'phone': '123'}}
        resp = self.api.post(self._apply_url(), payload, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_12_cross_org_resume_blocked(self):
        other_org = _org('other-ar')
        _scope(other_org)
        other_candidate = _candidate(other_org, phone='9123456789')
        other_resume = _resume(other_candidate, status='manual_review')
        _parsed_resume(other_resume)

        resp = self.api.post(
            f'/api/talent/resumes/{other_resume.pk}/apply-review/',
            {}, format='json',
        )
        self.assertIn(resp.status_code, (403, 404))


# ─── 4. Duplicate Resolution ──────────────────────────────────────────────────

class TestDuplicateResolution(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.org = _org('tdr')
        self.scope = _scope(self.org)
        self.user = _user('hrdr', self.org)
        _grant(self.user, self.org, self.scope, [RESUME_READ, CANDIDATE_UPDATE, RESUME_UPLOAD])
        self.api.force_authenticate(user=self.user)

        self.candidate = _candidate(self.org, phone='9876543210')
        self.existing = _candidate(self.org, phone='9876543211')
        self.resume = _resume(self.candidate, status='duplicate_file', manual_review_reason='')

    def _resolve_url(self):
        return f'/api/talent/resumes/{self.resume.pk}/resolve-duplicate/'

    def test_13_resolve_duplicate_link_existing(self):
        payload = {'resolution': 'link_existing', 'candidate': self.existing.pk, 'note': 'Merged'}
        resp = self.api.post(self._resolve_url(), payload, format='json')
        self.assertEqual(resp.status_code, 200)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.candidate_id, self.existing.pk)
        self.assertEqual(self.resume.status, 'manual_review')
        review = TalentResumeReview.objects.filter(resume=self.resume, review_type='duplicate_resolution').first()
        self.assertIsNotNone(review)
        self.assertEqual(review.correction_payload['resolution'], 'link_existing')

    def test_14_resolve_duplicate_mark_duplicate(self):
        payload = {'resolution': 'mark_duplicate', 'candidate': self.existing.pk}
        resp = self.api.post(self._resolve_url(), payload, format='json')
        self.assertEqual(resp.status_code, 200)
        self.candidate.refresh_from_db()
        self.assertTrue(self.candidate.is_duplicate)
        self.assertEqual(self.candidate.duplicate_of_id, self.existing.pk)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.status, 'duplicate_file')

    def test_15_resolve_duplicate_keep_separate(self):
        self.candidate.is_duplicate = True
        self.candidate.duplicate_of = self.existing
        self.candidate.save(update_fields=['is_duplicate', 'duplicate_of'])

        payload = {'resolution': 'keep_separate', 'note': 'Confirmed separate candidate'}
        resp = self.api.post(self._resolve_url(), payload, format='json')
        self.assertEqual(resp.status_code, 200)
        self.candidate.refresh_from_db()
        self.assertFalse(self.candidate.is_duplicate)
        self.assertIsNone(self.candidate.duplicate_of)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.status, 'manual_review')


# ─── 5. Reprocess Audit ───────────────────────────────────────────────────────

class TestReprocessAudit(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.org = _org('trpa')
        self.scope = _scope(self.org)
        self.user = _user('hrpa', self.org)
        _grant(self.user, self.org, self.scope, [RESUME_READ, CANDIDATE_UPDATE, RESUME_UPLOAD])
        self.api.force_authenticate(user=self.user)

        self.candidate = _candidate(self.org)
        self.resume = _resume(
            self.candidate, status='manual_review',
            error_message='old error', manual_review_reason='old reason',
        )

    def test_16_reprocess_creates_audit_row_and_clears_errors(self):
        url = f'/api/talent/resumes/{self.resume.pk}/reprocess/'
        resp = self.api.post(url, {'note': 'Retrying after API key added'}, format='json')
        self.assertEqual(resp.status_code, 200)

        self.resume.refresh_from_db()
        self.assertEqual(self.resume.status, 'extracting')
        self.assertEqual(self.resume.error_message, '')
        self.assertEqual(self.resume.manual_review_reason, '')

        review = TalentResumeReview.objects.filter(resume=self.resume, review_type='reprocess').first()
        self.assertIsNotNone(review)
        self.assertEqual(review.previous_status, 'manual_review')
        self.assertEqual(review.new_status, 'extracting')
        self.assertEqual(review.review_note, 'Retrying after API key added')


# ─── 6. Review History ────────────────────────────────────────────────────────

class TestReviewHistory(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.org = _org('trh')
        self.scope = _scope(self.org)
        self.user = _user('hrh', self.org)
        _grant(self.user, self.org, self.scope, [RESUME_READ, CANDIDATE_UPDATE, RESUME_UPLOAD])
        self.api.force_authenticate(user=self.user)

        self.candidate = _candidate(self.org)
        self.resume = _resume(self.candidate, status='manual_review')
        _parsed_resume(self.resume)

        TalentResumeReview.objects.create(
            org=self.org,
            resume=self.resume,
            candidate=self.candidate,
            reviewed_by=self.user,
            review_type='reprocess',
            previous_status='failed',
            new_status='extracting',
            review_note='First retry',
            correction_payload={},
        )
        TalentResumeReview.objects.create(
            org=self.org,
            resume=self.resume,
            candidate=self.candidate,
            reviewed_by=self.user,
            review_type='correction',
            previous_status='manual_review',
            new_status='indexed',
            review_note='Corrected by HR',
            correction_payload={},
        )

    def test_17_review_history_returns_audit_rows(self):
        url = f'/api/talent/resumes/{self.resume.pk}/review-history/'
        resp = self.api.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 2)
        types = {r['review_type'] for r in resp.data}
        self.assertIn('reprocess', types)
        self.assertIn('correction', types)
        notes = {r['review_note'] for r in resp.data}
        self.assertIn('First retry', notes)
        self.assertIn('Corrected by HR', notes)
