"""Reusable workflow seeders shared by management commands and demo bootstrap."""

from .sales_proposal_workflow import seed_sales_proposal_workflow_route

__all__ = ['seed_sales_proposal_workflow_route']
