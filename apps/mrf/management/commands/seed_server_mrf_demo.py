"""
Backward-compatible alias for the workflow-only MRF server seed.

This command no longer creates demo clients, sites, budgets, SRRs, or MRFs.
Use seed_server_mrf_workflow directly for new deployments.
"""

from apps.mrf.management.commands.seed_server_mrf_workflow import Command
