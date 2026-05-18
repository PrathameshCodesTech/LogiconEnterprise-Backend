class WorkflowConfigurationError(Exception):
    """Raised when workflow configuration is missing or invalid."""
    pass


class OnboardingPreflightError(WorkflowConfigurationError):
    """Raised when onboarding preflight checks block a final approval step."""

    def __init__(self, detail, errors):
        super().__init__(detail)
        self.preflight_errors = errors
