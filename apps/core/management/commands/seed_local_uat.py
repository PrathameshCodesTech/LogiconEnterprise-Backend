"""
Management entry point for LocalUAT seed files.

Local-only UAT seeds live under apps/LocalUAT so they are easy to separate
from server UAT seed files.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


LOCAL_UAT_DIR = Path(__file__).resolve().parents[3] / 'LocalUAT'


class Command(BaseCommand):
    help = 'Run LocalUAT seed files through a Django management command.'

    def add_arguments(self, parser):
        parser.add_argument(
            'seed',
            nargs='?',
            default='qrcode',
            choices=['qrcode'],
            help='LocalUAT seed to run. Currently available: qrcode.',
        )

    def handle(self, *args, **options):
        seed_name = options['seed']
        if seed_name == 'qrcode':
            self._run_seed_file('seed_qrcode.py')
            return
        raise CommandError(f'Unknown LocalUAT seed: {seed_name}')

    def _run_seed_file(self, filename):
        seed_path = LOCAL_UAT_DIR / filename
        if not seed_path.exists():
            raise CommandError(f'LocalUAT seed file not found: {seed_path}')

        module_name = f'_local_uat_{seed_path.stem}'
        spec = spec_from_file_location(module_name, seed_path)
        if spec is None or spec.loader is None:
            raise CommandError(f'Could not load LocalUAT seed file: {seed_path}')

        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        seed_command_cls = getattr(module, 'Command', None)
        if seed_command_cls is None:
            raise CommandError(f'LocalUAT seed file has no Command class: {seed_path}')

        seed_command = seed_command_cls()
        seed_command.stdout = self.stdout
        seed_command.stderr = self.stderr
        seed_command.handle()
