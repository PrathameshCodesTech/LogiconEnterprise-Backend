"""
apps/talent/tests/test_talent_hiring_a.py

Phase Talent-Hiring-A — 23 tests covering model additions and service logic.
"""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.core.models import Organization, ScopeNode
from apps.sites.models import Client, SiteProfile
from apps.jobs.models import JobRole

from apps.talent.models import (
    Candidate, Resume, CandidateSkill,
    ParsedResume, CandidateExperience, CandidateEducation,
)
from apps.talent.services import (
    queue_resume_processing,
    mark_resume_manual_review,
    mark_resume_failed,
    build_candidate_profile_text,
)
from apps.hiring.models import (
    PipelineStage, ApplicationStageHistory, CandidateMatchResult, HiringApplication,
)


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _org(suffix='tha'):
    return Organization.objects.create(name=f'Org {suffix}', code=f'org-{suffix}')


def _candidate(org, phone='9876543210', first='Alice', last='Wonder'):
    return Candidate.objects.create(
        org=org,
        phone=phone,
        phone_normalized=phone,
        first_name=first,
        last_name=last,
        source='manual',
    )


def _resume(candidate):
    f = SimpleUploadedFile('cv.pdf', b'%PDF-1.4', content_type='application/pdf')
    return Resume.objects.create(
        candidate=candidate,
        file=f,
        original_filename='cv.pdf',
        content_type='application/pdf',
        size_bytes=8,
    )


# ─── TestCandidateModelAdditions ──────────────────────────────────────────────

class TestCandidateModelAdditions(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org()
        cls.c = _candidate(cls.org)

    # test_01
    def test_01_new_fields_have_correct_defaults(self):
        c = self.c
        self.assertEqual(c.lifecycle_status, 'active')
        self.assertEqual(c.availability_status, 'unknown')
        self.assertEqual(c.preferred_location, '')
        self.assertIsNone(c.notice_period_days)
        self.assertEqual(c.current_company, '')
        self.assertEqual(c.current_role, '')
        self.assertEqual(c.source_reference, '')
        self.assertIsNone(c.duplicate_of)
        self.assertFalse(c.is_duplicate)
        self.assertFalse(c.do_not_contact)

    # test_02
    def test_02_duplicate_of_self_reference(self):
        original = _candidate(self.org, phone='9000000001', first='Bob', last='Smith')
        dup = _candidate(self.org, phone='9000000002', first='Robert', last='Smith')
        dup.duplicate_of = original
        dup.is_duplicate = True
        dup.lifecycle_status = 'duplicate'
        dup.save()

        dup.refresh_from_db()
        self.assertEqual(dup.duplicate_of_id, original.pk)
        self.assertIn(dup, original.duplicates.all())

    # test_03
    def test_03_full_name_with_middle_name(self):
        c = Candidate(
            org=self.org, phone='9111111111', phone_normalized='9111111111',
            first_name='John', middle_name='R', last_name='Doe', source='manual',
        )
        self.assertEqual(c.full_name, 'John R Doe')

    # test_03b
    def test_03b_full_name_without_middle_name(self):
        c = Candidate(
            org=self.org, phone='9222222222', phone_normalized='9222222222',
            first_name='Alice', middle_name='', last_name='Wonder', source='manual',
        )
        self.assertEqual(c.full_name, 'Alice Wonder')


# ─── TestResumeModelAdditions ─────────────────────────────────────────────────

class TestResumeModelAdditions(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(suffix='rm')
        cls.candidate = _candidate(cls.org)

    # test_04
    def test_04_resume_status_defaults_to_uploaded(self):
        r = _resume(self.candidate)
        self.assertEqual(r.status, 'uploaded')

    # test_05
    def test_05_resume_source_type_default(self):
        r = _resume(self.candidate)
        self.assertEqual(r.source_type, 'manual_upload')

    # test_06
    def test_06_resume_qr_intake_source_type(self):
        f = SimpleUploadedFile('resume.pdf', b'%PDF', content_type='application/pdf')
        r = Resume.objects.create(
            candidate=self.candidate,
            file=f,
            original_filename='resume.pdf',
            size_bytes=4,
            source_type='qr_intake',
        )
        self.assertEqual(r.source_type, 'qr_intake')


# ─── TestCandidateSkillAdditions ──────────────────────────────────────────────

class TestCandidateSkillAdditions(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(suffix='cs')
        cls.candidate = _candidate(cls.org)
        cls.resume = _resume(cls.candidate)

    # test_07
    def test_07_skill_with_new_fields(self):
        skill = CandidateSkill.objects.create(
            candidate=self.candidate,
            skill_name='Python',
            years_experience=Decimal('3.0'),
            proficiency='advanced',
            source='parsed',
            source_resume=self.resume,
        )
        skill.refresh_from_db()
        self.assertEqual(skill.years_experience, Decimal('3.0'))
        self.assertEqual(skill.proficiency, 'advanced')
        self.assertEqual(skill.source, 'parsed')
        self.assertEqual(skill.source_resume_id, self.resume.pk)


# ─── TestNewModels ────────────────────────────────────────────────────────────

class TestNewModels(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(suffix='nm')
        cls.candidate = _candidate(cls.org)
        cls.resume = _resume(cls.candidate)

    # test_08
    def test_08_parsed_resume_one_to_one(self):
        pr = ParsedResume.objects.create(
            resume=self.resume,
            summary='Senior engineer with 10y exp.',
            career_level='senior',
            primary_domain='engineering',
            confidence=Decimal('92.50'),
        )
        self.assertEqual(pr.resume_id, self.resume.pk)
        self.assertEqual(self.resume.parsed_resume.pk, pr.pk)

    # test_09
    def test_09_candidate_experience(self):
        from datetime import date
        exp = CandidateExperience.objects.create(
            candidate=self.candidate,
            job_title='Software Engineer',
            company_name='Acme Corp',
            start_date=date(2020, 1, 1),
            end_date=date(2023, 6, 30),
            is_current=False,
            duration_months=41,
            source_resume=self.resume,
        )
        self.assertEqual(exp.job_title, 'Software Engineer')
        self.assertIn(exp, self.candidate.experiences.all())

    # test_10
    def test_10_candidate_education(self):
        edu = CandidateEducation.objects.create(
            candidate=self.candidate,
            degree='B.Tech',
            specialization='Computer Science',
            institute='IIT Bombay',
            start_year=2016,
            end_year=2020,
        )
        self.assertEqual(edu.institute, 'IIT Bombay')
        self.assertIn(edu, self.candidate.educations.all())


# ─── TestTalentServices ───────────────────────────────────────────────────────

class TestTalentServices(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(suffix='svc')
        cls.candidate = _candidate(cls.org)

    def setUp(self):
        self.resume = _resume(self.candidate)

    # test_11
    def test_11_queue_resume_processing_sets_extracting(self):
        queue_resume_processing(self.resume)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.status, 'extracting')

    # test_12
    def test_12_mark_resume_manual_review(self):
        mark_resume_manual_review(self.resume, 'Scanned PDF, needs OCR check')
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.status, 'manual_review')
        self.assertIn('OCR', self.resume.manual_review_reason)

    # test_13
    def test_13_mark_resume_failed(self):
        mark_resume_failed(self.resume, 'Extraction timeout')
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.status, 'failed')
        self.assertEqual(self.resume.error_message, 'Extraction timeout')

    # test_14
    def test_14_build_candidate_profile_text(self):
        CandidateSkill.objects.create(
            candidate=self.candidate, skill_name='Java', proficiency='expert',
        )
        text = build_candidate_profile_text(self.candidate)
        self.assertIn('Alice', text)
        self.assertIn('Java', text)


# ─── TestHiringModelAdditions ─────────────────────────────────────────────────

class TestHiringModelAdditions(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(suffix='hm')

    # test_15
    def test_15_pipeline_stage_creation(self):
        stage = PipelineStage.objects.create(
            org=self.org, name='Initial Screening', code='screening',
            order=1, stage_type='screening', is_terminal=False,
        )
        self.assertEqual(str(stage), f'{self.org} — Initial Screening (screening)')
        self.assertTrue(stage.is_active)

    # test_16
    def test_16_application_stage_history(self):
        stage1 = PipelineStage.objects.create(
            org=self.org, name='Sourcing', code='sourcing-hm',
            order=0, stage_type='sourcing',
        )
        stage2 = PipelineStage.objects.create(
            org=self.org, name='Screening', code='screen-hm',
            order=1, stage_type='screening',
        )

        # ApplicationStageHistory requires a HiringApplication — build minimal one
        n_co = ScopeNode.objects.create(
            org=self.org, code='hm-co', name='hm-co', node_type='company',
            parent=None, depth=0, path='hm-co', is_active=True,
        )
        client = Client.objects.create(
            org=self.org, name='Client HM', code='client-hm',
            scope_node=n_co, is_active=True,
        )
        n_cl = ScopeNode.objects.create(
            org=self.org, code='cl-hm', name='cl-hm', node_type='client',
            parent=n_co, depth=1, path='hm-co/cl-hm', is_active=True,
        )
        site = SiteProfile.objects.create(
            org=self.org, client=client, scope_node=n_cl,
            name='Site HM', code='site-hm', is_active=True,
        )
        candidate = _candidate(self.org, phone='9000000099')
        job_role = JobRole.objects.create(org=self.org, name='Guard HM', code='guard-hm')

        from apps.mrf.models import ManpowerRequest, MRFLineItem
        from apps.accounts.models import User
        user = User.objects.create_user(
            username='mrf-test-user-hm', password='x', org=self.org,
        )
        mrf = ManpowerRequest.objects.create(
            org=self.org, site=site, mrf_type='new_hiring',
            billing_type='billable', status='approved', requested_by=user,
        )
        mrf_line_item = MRFLineItem.objects.create(
            mrf=mrf, job_role=job_role, headcount=1,
        )

        app = HiringApplication.objects.create(
            org=self.org, candidate=candidate, mrf=mrf,
            mrf_line_item=mrf_line_item, site=site, job_role=job_role,
            current_stage=stage1,
        )

        history = ApplicationStageHistory.objects.create(
            hiring_application=app,
            from_stage=stage1,
            to_stage=stage2,
            from_status='draft',
            to_status='shortlisted',
        )
        self.assertEqual(history.hiring_application_id, app.pk)
        self.assertEqual(history.from_stage_id, stage1.pk)
        self.assertEqual(history.to_stage_id, stage2.pk)


# ─── TestQRIntakeLinksResume ──────────────────────────────────────────────────

class TestQRIntakeLinksResume(TestCase):
    """QR intake resume document should auto-create a talent.Resume."""

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(suffix='qrl')
        n_co = ScopeNode.objects.create(
            org=cls.org, code='qrl-co', name='qrl-co', node_type='company',
            parent=None, depth=0, path='qrl-co', is_active=True,
        )
        client = Client.objects.create(
            org=cls.org, name='Client QRL', code='client-qrl',
            scope_node=n_co, is_active=True,
        )
        n_cl = ScopeNode.objects.create(
            org=cls.org, code='cl-qrl', name='cl-qrl', node_type='client',
            parent=n_co, depth=1, path='qrl-co/cl-qrl', is_active=True,
        )
        site = SiteProfile.objects.create(
            org=cls.org, client=client, scope_node=n_cl,
            name='Site QRL', code='site-qrl', is_active=True,
        )

        from apps.intake.models import QRCampaign, CampaignJobRole
        cls.job_role = JobRole.objects.create(org=cls.org, name='Guard QRL', code='guard-qrl')
        cls.campaign = QRCampaign.objects.create(
            org=cls.org, site=site, name='QRL Camp', title='QRL Drive',
            code='QRL-CAMP', is_active=True, allow_duplicates=True,
            requires_otp=False, shuffle_fields=False,
            default_language='en', enabled_languages=['en'],
        )
        CampaignJobRole.objects.create(
            campaign=cls.campaign, job_role=cls.job_role, is_active=True,
        )

    # test_17
    def test_17_qr_intake_resume_doc_creates_talent_resume(self):
        from django.test import RequestFactory
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.intake.services import create_intake_submission, create_intake_documents
        from apps.intake.models import IntakeSubmission

        validated = {
            'campaign': self.campaign,
            'job_role': self.job_role,
            'first_name': 'Test',
            'middle_name': '',
            'last_name': 'Candidate',
            'mobile_number': '9123456789',
        }
        submission = create_intake_submission(validated)

        pdf = SimpleUploadedFile('resume.pdf', b'%PDF-1.4', content_type='application/pdf')

        class _MultiValueDict(dict):
            def getlist(self, key):
                v = self.get(key, [])
                return v if isinstance(v, list) else [v]

        files = _MultiValueDict({'resume': [pdf]})
        docs = create_intake_documents(submission, files)

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].document_type, 'resume')

        talent_resumes = Resume.objects.filter(
            candidate=submission.candidate,
            source_type='qr_intake',
            source_intake_document=docs[0],
        )
        self.assertTrue(talent_resumes.exists(), "talent.Resume was not created for QR intake resume doc")
        tr = talent_resumes.first()
        self.assertIn(tr.status, ('uploaded', 'extracting'))

    # test_18
    def test_18_link_resume_idempotent_duplicate_call(self):
        """Calling _link_resume_if_needed twice for the same doc produces exactly one Resume."""
        from apps.intake.services import create_intake_submission, create_intake_documents, _link_resume_if_needed
        from apps.intake.models import IntakeDocument

        validated = {
            'campaign': self.campaign,
            'job_role': self.job_role,
            'first_name': 'Idem',
            'middle_name': '',
            'last_name': 'Potent',
            'mobile_number': '9111111119',
        }
        submission = create_intake_submission(validated)

        pdf = SimpleUploadedFile('cv2.pdf', b'%PDF-1.4', content_type='application/pdf')

        class _MVD(dict):
            def getlist(self, key):
                v = self.get(key, [])
                return v if isinstance(v, list) else [v]

        docs = create_intake_documents(submission, _MVD({'resume': [pdf]}))
        doc = docs[0]

        # Call again — must not raise and must not create a second Resume
        _link_resume_if_needed(doc, submission.candidate)

        count = Resume.objects.filter(source_intake_document=doc).count()
        self.assertEqual(count, 1, "Idempotent call created a duplicate talent.Resume")


# ─── TestCandidateMatchResultScoreBreakdown ───────────────────────────────────

class TestCandidateMatchResultScoreBreakdown(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.org = _org(suffix='cmr')
        candidate = _candidate(cls.org, phone='9000000088')

        n_co = ScopeNode.objects.create(
            org=cls.org, code='cmr-co', name='cmr-co', node_type='company',
            parent=None, depth=0, path='cmr-co', is_active=True,
        )
        client = Client.objects.create(
            org=cls.org, name='Client CMR', code='client-cmr',
            scope_node=n_co, is_active=True,
        )
        n_cl = ScopeNode.objects.create(
            org=cls.org, code='cl-cmr', name='cl-cmr', node_type='client',
            parent=n_co, depth=1, path='cmr-co/cl-cmr', is_active=True,
        )
        site = SiteProfile.objects.create(
            org=cls.org, client=client, scope_node=n_cl,
            name='Site CMR', code='site-cmr', is_active=True,
        )
        job_role = JobRole.objects.create(org=cls.org, name='Analyst CMR', code='analyst-cmr')

        from apps.accounts.models import User
        user = User.objects.create_user(
            username='cmr-test-user', password='x', org=cls.org,
        )
        from apps.mrf.models import ManpowerRequest, MRFLineItem
        mrf = ManpowerRequest.objects.create(
            org=cls.org, site=site, mrf_type='new_hiring',
            billing_type='billable', status='approved', requested_by=user,
        )
        mrf_line_item = MRFLineItem.objects.create(mrf=mrf, job_role=job_role, headcount=1)

        cls.candidate = candidate
        cls.mrf_line_item = mrf_line_item

    # test_19
    def test_19_full_score_breakdown_stored(self):
        mr = CandidateMatchResult.objects.create(
            org=self.org,
            candidate=self.candidate,
            mrf_line_item=self.mrf_line_item,
            final_score=Decimal('82.50'),
            role_score=Decimal('90.00'),
            skill_score=Decimal('85.00'),
            experience_score=Decimal('78.00'),
            location_score=Decimal('95.00'),
            industry_score=Decimal('70.00'),
            education_score=Decimal('80.00'),
            salary_score=Decimal('75.00'),
            semantic_score=Decimal('88.00'),
            match_source='ai',
            is_auto_match=True,
        )
        mr.refresh_from_db()
        self.assertEqual(mr.final_score, Decimal('82.50'))
        self.assertEqual(mr.role_score, Decimal('90.00'))
        self.assertEqual(mr.skill_score, Decimal('85.00'))
        self.assertEqual(mr.experience_score, Decimal('78.00'))
        self.assertEqual(mr.semantic_score, Decimal('88.00'))

    # test_20
    def test_20_matched_and_missing_skills_default_to_lists(self):
        mr = CandidateMatchResult.objects.create(
            org=self.org,
            candidate=self.candidate,
            mrf_line_item=self.mrf_line_item,
            final_score=Decimal('70.00'),
        )
        mr.refresh_from_db()
        self.assertIsInstance(mr.matched_skills, list)
        self.assertIsInstance(mr.missing_skills, list)
        self.assertEqual(mr.matched_skills, [])
        self.assertEqual(mr.missing_skills, [])

    # test_21
    def test_21_warnings_defaults_to_list(self):
        mr = CandidateMatchResult.objects.create(
            org=self.org,
            candidate=self.candidate,
            mrf_line_item=self.mrf_line_item,
        )
        mr.refresh_from_db()
        self.assertIsInstance(mr.warnings, list)
        self.assertIsInstance(mr.match_reason, list)
        self.assertEqual(mr.warnings, [])

    # test_22
    def test_22_match_score_legacy_field_still_works(self):
        """Backward compat: creating a result with only match_score (no final_score) still works."""
        mr = CandidateMatchResult.objects.create(
            org=self.org,
            candidate=self.candidate,
            mrf_line_item=self.mrf_line_item,
            match_score=Decimal('65.00'),
        )
        mr.refresh_from_db()
        self.assertEqual(mr.match_score, Decimal('65.00'))
        self.assertIsNone(mr.final_score)

    # test_23
    def test_23_match_result_stores_skills_lists(self):
        mr = CandidateMatchResult.objects.create(
            org=self.org,
            candidate=self.candidate,
            mrf_line_item=self.mrf_line_item,
            final_score=Decimal('77.00'),
            matched_skills=['Python', 'Django', 'SQL'],
            missing_skills=['Kubernetes'],
            match_reason=['Strong Python background', 'Lacks containerisation experience'],
            warnings=['Salary expectation above budget'],
        )
        mr.refresh_from_db()
        self.assertEqual(mr.matched_skills, ['Python', 'Django', 'SQL'])
        self.assertEqual(mr.missing_skills, ['Kubernetes'])
        self.assertEqual(len(mr.match_reason), 2)
        self.assertEqual(mr.warnings, ['Salary expectation above budget'])
