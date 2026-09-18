"""Read-only verification of already-managed production Skill provenance and video decoding."""
import json
import subprocess
from content_factory_api.s3 import get_analysis_queue, get_viral_skill_store
from content_factory_api.s2 import _queue, _media_root
from content_factory_api.s5_sources import list_templates
from content_factory_api.creator_media import skill_media_file

results = []
for skill in list_templates(get_analysis_queue(), get_viral_skill_store()):
    for source in skill['representative_sources']:
        try:
            path = skill_media_file(get_analysis_queue(), get_viral_skill_store(), _queue(), skill['skill_id'], source['video_id'], _media_root())
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,codec_name', '-of', 'json', str(path)], capture_output=True, check=True, timeout=30)
            result = json.loads(probe.stdout)
            results.append({'skill': skill['name'], 'file': str(path), 'duration': result['format']['duration'], 'streams': result['streams'], 'passed': True})
        except Exception as exc:
            results.append({'skill': skill['name'], 'passed': False, 'error': str(exc)})
print(json.dumps(results, ensure_ascii=False, indent=2))
raise SystemExit(1 if any(not r['passed'] for r in results) else 0)
