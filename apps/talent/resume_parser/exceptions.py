"""Exceptions for the resume parsing pipeline."""


class ManualReviewRequired(Exception):
    """
    Raised when the pipeline cannot proceed automatically.
    The resume will be flagged for human review with this message as the reason.
    """
