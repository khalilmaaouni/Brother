"""Run the installed-layout contract under both native clients, in virgin homes."""
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
suite = HERE / 'test_vault_portability.py'
checks = [
    'VaultPortability.test_native_client_installs_and_config',
    'VaultPortability.test_explicit_config_overrides_native_homes',
    'VaultPortability.test_codex_home_uses_shared_precedence',
    'VaultPortability.test_claude_config_uses_shared_precedence',
    'VaultPortability.test_explicit_plugin_root_beats_cache',
    'VaultPortability.test_explicit_tools_beat_plugin_root',
    'VaultPortability.test_explicit_tools_without_resolver',
    'VaultPortability.test_no_resolver_no_implicit_config',
]
result = subprocess.run([sys.executable, str(suite)] + checks)
source = (HERE.parent / 'vault_bridge.py').read_text(encoding='utf-8')
if '/Users/' in source:
    print('CHECK FAIL: bridge contains a machine-specific path')
    sys.exit(1)
print('CHECK PASS' if result.returncode == 0 else 'CHECK FAIL: installed layouts')
sys.exit(result.returncode)
