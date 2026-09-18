import pytest

from content_factory_api.internal_config_package import export_config, import_config
from content_factory_api.s3_settings import GatewaySettingsStore

PASSWORD = 'test-only-delivery-password-not-a-real-key'


@pytest.fixture
def configured(tmp_path):
    source = tmp_path / 'original.json'
    GatewaySettingsStore(source).save(base_url='https://example.com/v1', model='test-model',
        api_mode='chat_completions', api_key='fake-unit-test-secret-123456')
    return source


def test_round_trip_preserves_routes_and_reprotects_secret(configured, tmp_path):
    original = configured.read_bytes()
    package = export_config(configured, PASSWORD)
    assert b'fake-unit-test-secret' not in package
    assert b'example.com' not in package
    destination = tmp_path / '同事 资料' / 'gateway.json'
    import_config(package, PASSWORD, destination)
    source = GatewaySettingsStore(configured).load()
    imported = GatewaySettingsStore(destination).load()
    assert imported.api_key == source.api_key
    assert imported.routing == source.routing
    assert imported.model == source.model
    assert imported.base_url == source.base_url
    assert configured.read_bytes() == original
    assert b'fake-unit-test-secret' not in destination.read_bytes()


@pytest.mark.parametrize('damage', ['password', 'ciphertext', 'truncated'])
def test_failed_import_never_changes_config(configured, tmp_path, damage):
    package = export_config(configured, PASSWORD)
    password = PASSWORD
    if damage == 'password':
        password += 'wrong'
    elif damage == 'ciphertext':
        package = package[:-1] + bytes([package[-1] ^ 1])
    else:
        package = package[:20]
    destination = tmp_path / 'target.json'
    with pytest.raises(ValueError):
        import_config(package, password, destination)
    assert not destination.exists()


def test_reinstallation_does_not_overwrite_colleague_config(configured):
    before = configured.read_bytes()
    package = export_config(configured, PASSWORD)
    with pytest.raises(FileExistsError):
        import_config(package, PASSWORD, configured)
    assert configured.read_bytes() == before


def test_export_requires_strong_delivery_password(configured):
    with pytest.raises(ValueError):
        export_config(configured, '123')
