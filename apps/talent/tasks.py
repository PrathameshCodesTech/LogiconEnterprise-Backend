"""
apps/talent/tasks.py

Celery tasks for the talent pipeline.
"""

from celery import shared_task


@shared_task(bind=True, max_retries=0, name='talent.process_resume')
def process_resume_task(self, resume_id: int, force: bool = False) -> None:
    """
    Parse a resume through the full extraction → LLM → validation → persist pipeline.

    Skips resumes that are already 'indexed' (unless force=True).
    Skips 'duplicate_file' resumes unconditionally.
    All status transitions and error handling are inside run_pipeline().
    """
    from apps.talent.models import Resume
    from apps.talent.resume_parser.orchestration import run_pipeline

    try:
        resume = Resume.objects.select_related('candidate').get(pk=resume_id)
    except Resume.DoesNotExist:
        return

    if resume.status == 'duplicate_file':
        return

    if resume.status == 'indexed' and not force:
        return

    run_pipeline(resume)


@shared_task(bind=True, max_retries=0, name='talent.process_resume_import_item')
def process_resume_import_item_task(self, item_id: int) -> None:
    """Process one file from a bulk resume import batch."""
    from apps.talent.services import process_resume_import_item

    process_resume_import_item(item_id)
