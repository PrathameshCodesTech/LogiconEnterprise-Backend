"""Sales app exceptions."""


class ClientProposalTokenError(Exception):
    """Invalid, expired, revoked, or already-used client proposal access token."""

    def __init__(self, message, code='invalid_token'):
        super().__init__(message)
        self.code = code
