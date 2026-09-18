from pathlib import Path
import pytest
from test_edit_batches import actual_video, client, manifest, _import


def test_native_export_saves_exact_bytes_without_overwriting(client, manifest, tmp_path, monkeypatch):
    destination = tmp_path / 'downloads'
    monkeypatch.setenv('CONTENT_FACTORY_EXPORT_ROOT', str(destination))
    batch = _import(client, manifest)
    resource = f"/s7/footage-batches/{batch['id']}/candidates/candidate-01/media/video"
    paths = []
    for _ in range(2):
        response = client.post('/local-exports', json={'resource': resource})
        assert response.status_code == 200, response.text
        saved = Path(response.json()['path'])
        assert saved.parent == destination.resolve()
        assert saved.read_bytes() == (manifest.parent / 'candidate.mp4').read_bytes()
        paths.append(saved)
    assert paths[0] != paths[1]
    assert not list(destination.glob('*.partial'))


@pytest.mark.parametrize('resource', ['https://example.org/video.mp4', '/s4/products', '/../../secret', '/s7/footage-batches/a/candidates/b/media/source'])
def test_native_export_only_accepts_delivery_resources(client, resource, tmp_path, monkeypatch):
    root = tmp_path / 'downloads'
    monkeypatch.setenv('CONTENT_FACTORY_EXPORT_ROOT', str(root))
    result = client.post('/local-exports', json={'resource': resource})
    assert result.status_code == 422
    assert not root.exists()


def test_native_export_refuses_changed_review_file(client, manifest, tmp_path, monkeypatch):
    root = tmp_path / 'downloads'
    monkeypatch.setenv('CONTENT_FACTORY_EXPORT_ROOT', str(root))
    batch = _import(client, manifest)
    (manifest.parent / 'candidate.mp4').write_bytes(b'changed')
    result = client.post('/local-exports', json={'resource': f"/s7/footage-batches/{batch['id']}/candidates/candidate-01/media/video"})
    assert result.status_code == 409
    assert not root.exists()


@pytest.mark.parametrize('failure', ['disk', 'corrupt'])
def test_failed_export_leaves_no_completed_or_partial_file(client, manifest, tmp_path, monkeypatch, failure):
    from content_factory_api import local_exports
    root = tmp_path / 'downloads'
    monkeypatch.setenv('CONTENT_FACTORY_EXPORT_ROOT', str(root))
    batch = _import(client, manifest)
    def broken_copy(incoming, outgoing, **kwargs):
        outgoing.write(b'partial')
        if failure == 'disk':
            raise OSError('simulated disk full')
    monkeypatch.setattr(local_exports.shutil, 'copyfileobj', broken_copy)
    result = client.post('/local-exports', json={'resource': f"/s7/footage-batches/{batch['id']}/candidates/candidate-01/media/subtitle"})
    assert result.status_code == (500 if failure == 'disk' else 409)
    assert not list(root.iterdir())
