"""One explicitly requested paid text probe using a migrated local config."""
import argparse
import json
from pathlib import Path
import sys

from content_factory_api.s3_gateway import call_gateway
from content_factory_api.s3_settings import GatewaySettingsStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--allow-paid-call', action='store_true')
    args = parser.parse_args()
    if not args.allow_paid_call:
        parser.error('This probe makes one real model request; explicit opt-in is required')
    if args.report.exists() or not args.report.resolve().is_relative_to(Path('E:/Codex工作盘').resolve()):
        parser.error('Use a new report path on the work drive')
    config = GatewaySettingsStore(args.config).load()
    if config is None:
        raise RuntimeError('No migrated model configuration')
    report = {'test':'migrated-config-minimal-text-call', 'clean_windows':False,
              'multimodal_quality_test':False, 'passed':False}
    try:
        result = call_gateway(config.for_model(), context_json='{"task":"Return ok as true."}',
            keyframe_data_urls=[], output_schema={'type':'object','properties':{'ok':{'type':'boolean'}},
                'required':['ok'],'additionalProperties':False},
            timeout_seconds=120, developer_instructions='Return only the requested JSON object.',
            schema_name='deployment_connectivity')
        report.update(passed=result.content == {'ok':True} and result.outcome == 'completed',
                      completed_event=result.completed_event_received, http_status=result.http_status)
    except Exception as exc:
        # Do not publish provider error bodies, URLs, credentials or configuration.
        report['error_type'] = type(exc).__name__
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
    if not report['passed']:
        sys.exit(1)


if __name__ == '__main__':
    main()
