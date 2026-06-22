"""
Management entry point for ServerUAT seed files.

The actual UAT seed logic intentionally lives under apps/ServerUAT so server
seed files are easy to identify later. This command only exposes those files
through Django's management-command system.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


SERVER_UAT_DIR = Path(__file__).resolve().parents[3] / 'ServerUAT'


class Command(BaseCommand):
    help = 'Run ServerUAT seed files through a Django management command.'

    def add_arguments(self, parser):
        parser.add_argument(
            'seed',
            nargs='?',
            default='foundation',
            choices=['foundation', 'masters', 'config', 'survey', 'workflows', 'qrcode', 'client_portals'],
            help='ServerUAT seed to run. Currently available: foundation, masters, config, survey, workflows, qrcode, client_portals.',
        )

    def handle(self, *args, **options):
        seed_name = options['seed']
        if seed_name == 'foundation':
            self._run_seed_file('seed_foundation.py')
            return
        if seed_name == 'masters':
            self._run_seed_file('seed_masters.py')
            return
        if seed_name == 'config':
            self._run_seed_file('seed_config.py')
            return
        if seed_name == 'survey':
            self._run_seed_file('seed_survey.py')
            return
        if seed_name == 'workflows':
            self._run_seed_file('seed_workflows.py')
            return
        if seed_name == 'qrcode':
            self._run_seed_file('seed_qrcode.py')
            return
        if seed_name == 'client_portals':
            self._run_seed_file('seed_client_portals.py')
            return
        raise CommandError(f'Unknown ServerUAT seed: {seed_name}')

    def _run_seed_file(self, filename):
        seed_path = SERVER_UAT_DIR / filename
        if not seed_path.exists():
            raise CommandError(f'ServerUAT seed file not found: {seed_path}')

        module_name = f'_server_uat_{seed_path.stem}'
        spec = spec_from_file_location(module_name, seed_path)
        if spec is None or spec.loader is None:
            raise CommandError(f'Could not load ServerUAT seed file: {seed_path}')

        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        seed_command_cls = getattr(module, 'Command', None)
        if seed_command_cls is None:
            raise CommandError(f'ServerUAT seed file has no Command class: {seed_path}')

        seed_command = seed_command_cls()
        seed_command.stdout = self.stdout
        seed_command.stderr = self.stderr
        seed_command.handle()
