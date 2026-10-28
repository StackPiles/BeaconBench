"""Offline checks for M0 design files; never starts a model or downloads data."""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
LIMIT_KEYS = {'total_output_tokens', 'tool_calls', 'agent_turns', 'framework_deadline_seconds'}
PRIVATE_PATTERNS = {
    'credential-like token': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9_-]{25,})'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'private host address': re.compile(r'\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b'),
    'personal absolute path': re.compile(r'(?:[A-Za-z]:[\\/]+Users[\\/]+[^\s"<>]+|' + '/' + 'home' + r'/[^/\s]+/)'),
    'credential in URL': re.compile(r'https?://[^/\s:@]+:[^/\s@]+@'),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def parse_json(text: str):
    def invalid_constant(value):
        raise ValueError('Non-finite JSON constant: ' + value)
    def finite_float(value):
        result = float(value)
        require(math.isfinite(result), 'JSON number is outside the finite range')
        return result
    return json.loads(text, parse_constant=invalid_constant, parse_float=finite_float)


def read_json(path: Path):
    return parse_json(path.read_text(encoding='utf-8'))


def public_files(root: Path) -> list[Path]:
    if (root / '.git').exists():
        result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                cwd=root, capture_output=True, check=True)
        names = result.stdout.decode('utf-8').split('\0')
        paths = sorted({root / name for name in names if name})
    else:
        directories = ['docs', 'catalog', 'suites', 'protocols', 'schemas', 'examples', 'tools', 'tests', 'ui']
        paths = [x for name in directories for x in (root / name).rglob('*') if x.is_file()]
        paths += [root / name for name in ['README.md', 'AGENTS.md', 'NOTICE.md', 'LICENSE', '.gitignore', '.gitattributes', 'pyproject.toml', 'uv.lock'] if (root / name).is_file()]
        paths = sorted(set(paths))
    for path in paths:
        require(path.resolve().is_relative_to(root.resolve()), 'Path escapes the repository')
        require(not path.is_symlink(), 'Public source is a symbolic link')
    return paths


def private_terms(root: Path) -> tuple[str, ...]:
    """Read an optional ignored denylist without including its values in output."""
    path = root / '.beaconbench/private-terms.txt'
    if not path.exists():
        return ()
    return tuple(line.strip() for line in path.read_text(encoding='utf-8').splitlines()
                 if line.strip() and not line.lstrip().startswith('#'))


def check_no_private_content(path: str, content: str, denied_terms=()) -> None:
    parts = Path(path).parts
    require(not any(x in {'runs', 'weights', 'datasets', 'local', '.venv', '.git'} for x in parts), 'Private artifact path in publication')
    require(not (Path(path).name.startswith('.env') and Path(path).name != '.env.example'), 'Environment file in publication')
    for label, pattern in PRIVATE_PATTERNS.items():
        require(pattern.search(content) is None, path + ': ' + label)
    normalized = re.sub(r'\s+', '', content).casefold()
    for term in denied_terms:
        normalized_term = re.sub(r'\s+', '', term).casefold()
        require(not normalized_term or normalized_term not in normalized,
                path + ': private name from local denylist')


def validate_blueprints(catalog, quick, detailed, protocol) -> None:
    ids = [x['id'] for x in catalog['entries']]
    require(len(ids) == len(set(ids)), 'Duplicate catalog ID')
    require(all(x['execution_ready'] is False for x in catalog['entries']), 'M0 catalog cannot claim execution readiness')
    require(quick['protocol_id'] == detailed['protocol_id'] == protocol['protocol_id'], 'Quick and Detailed protocol mismatch')
    require(quick['subset_of'] == detailed['suite_id'], 'Quick subset relationship missing')
    require(quick['trial_seeds'] == detailed['trial_seeds'][:quick['trials_per_case']], 'Trial seed prefix differs')
    for suite in [quick, detailed]:
        require(suite['status'] == 'draft' and suite['execution_ready'] is False, 'M0 suites must stay draft')
        require(not suite['selection_frozen'] and not suite['case_refs'], 'Unverified selection cannot be frozen')
        rows = suite['allocation']
        require(len({x['source_id'] for x in rows}) == len(rows), 'Duplicate allocation source')
        require(sum(x['cases'] for x in rows) == suite['cases_target'], 'Allocation does not match target count')
        require(all(x['source_id'] in ids and type(x['cases']) is int and x['cases'] >= 0 for x in rows), 'Unknown source or invalid case count')
        require(len(suite['trial_seeds']) == suite['trials_per_case'], 'Trial seed count mismatch')
    small = {x['source_id']: x['cases'] for x in quick['allocation']}
    large = {x['source_id']: x['cases'] for x in detailed['allocation']}
    require(all(large.get(key, 0) >= count for key, count in small.items()), 'Quick cannot be included in Detailed')
    for item in [quick, detailed, protocol]:
        require(set(item['limits']) == LIMIT_KEYS, 'Missing framework limit declaration')
        require(all(v is None for v in item['limits'].values()), 'Independent output/action/turn/deadline cap added')
    require(protocol['status'] == 'draft' and protocol['execution_ready'] is False, 'M0 protocol must stay draft')
    require(protocol['quality_regeneration_retries'] == 0, 'Quality regeneration is not allowed')
    require(protocol['native_benchmark_termination_rules'] == 'preserve-and-record', 'Native rules changed')


def validate_measurement_semantics(record) -> None:
    usage = record['usage']
    prompt, cached, completion, reasoning, total = [usage[k] for k in ['prompt_tokens', 'cached_prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'total_tokens']]
    if prompt is not None and cached is not None:
        require(cached <= prompt, 'Cached tokens exceed prompt tokens')
    if record['completion_includes_reasoning'] is True and completion is not None and reasoning is not None:
        require(reasoning <= completion, 'Reasoning tokens exceed inclusive completion tokens')
    if prompt is not None and completion is not None and total is not None:
        require(total == prompt + completion, 'Token total double-counts a component or is inconsistent')
    times = record['times']
    wall = times['request_wall_seconds']
    for key in ['ttft_seconds', 'ttfv_seconds']:
        if wall is not None and times[key] is not None:
            require(times[key] <= wall, 'First output is after request completion')


def local_links(root: Path, path: Path, text: str) -> int:
    count = 0
    for target in re.findall(r'\]\(([^)]+)\)', text):
        target = target.strip().split(' "', 1)[0].strip('<>')
        if not target or target.startswith('#') or urlsplit(target).scheme:
            continue
        name = unquote(target.split('#', 1)[0])
        dest = (path.parent / name).resolve()
        require(dest.is_relative_to(root.resolve()), 'Documentation link escapes repository: ' + target)
        require(dest.exists(), 'Broken local link: ' + str(path.relative_to(root)) + ' -> ' + target)
        count += 1
    return count


def main() -> None:
    paths = public_files(ROOT)
    denied_terms = private_terms(ROOT)
    json_count = link_count = 0
    for path in paths:
        raw = path.read_text(encoding='utf-8')
        check_no_private_content(path.relative_to(ROOT).as_posix(), raw, denied_terms)
        if path.suffix == '.json':
            read_json(path)
            json_count += 1
        elif path.suffix == '.md':
            link_count += local_links(ROOT, path, raw)
    catalog = read_json(ROOT / 'catalog/benchmarks.json')
    quick = read_json(ROOT / 'suites/quick.v0.draft.json')
    detailed = read_json(ROOT / 'suites/detailed.v0.draft.json')
    protocol = read_json(ROOT / 'protocols/common-ja.v0.draft.json')
    validate_blueprints(catalog, quick, detailed, protocol)
    fixtures = 0
    for name in ['case', 'run', 'measurement', 'grade', 'translation']:
        schema = read_json(ROOT / ('schemas/' + name + '.schema.json'))
        Draft202012Validator.check_schema(schema)
        example = read_json(ROOT / ('examples/' + name + '.fixture.json'))
        Draft202012Validator(schema).validate(example)
        require(example['fixture'] is True, 'Example is presented as a real measurement')
        fixtures += 1
    validate_measurement_semantics(read_json(ROOT / 'examples/measurement.fixture.json'))
    print(json.dumps({'status': 'passed', 'public_files': len(paths), 'json_files': json_count,
                      'local_links': link_count, 'schemas_and_fixtures': fixtures,
                      'quick_cases_proposed': quick['cases_target'], 'detailed_cases_proposed': detailed['cases_target'],
                      'execution_ready': False, 'model_calls': 0, 'downloads_in_check': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
