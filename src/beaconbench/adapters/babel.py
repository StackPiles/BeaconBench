"""Build one-case native recipes from the pinned BabelArena CLI contract.

Recipes are preparation artifacts, not evidence that data, retries or native
controls passed. No CLI, model, container or download is started here.
"""
from __future__ import annotations

from ..util import BenchError


REVISION = '36960fbb7ee1aac5082fa7307264abc612a180f4'


def shared_models():
    return {'default': {'temperature': 1.0, 'headers': {'Authorization': 'Bearer ${PROXY_KEY}', 'Content-Type': 'application/json'}},
            'models': [{'name': role, 'base_url': '${' + role.upper() + '_BASE_URL}/chat/completions',
                        'max_input_tokens': '${' + role.upper() + '_CONTEXT_TOKENS}',
                        'max_tokens': '${' + role.upper() + '_OUTPUT_TOKENS}'} for role in ('target', 'customer', 'judge')]}


def recipe(family, domain):
    common = {'family': family, 'source_revision': REVISION, 'source_repository': 'QwenLM/BabelArena',
              'platform': 'linux', 'execution_ready': False,
              'required_acceptance': ['Japanese data lock', 'native positive/negative controls', 'role call coverage',
                                      'SDK retries/timeouts audit', 'owned environment reset and cleanup', 'task-completion time boundary'],
              'inline_files': {}, 'environment': {}, 'required_roles': ['target'], 'variables': {}}
    if family in {'babel-tau3', 'babel-vitabench'}:
        domains = {'retail', 'banking_knowledge', 'telecom', 'airline'} if family == 'babel-tau3' else {'cross', 'delivery', 'instore', 'ota'}
        if domain not in domains: raise BenchError('Babelの公式ドメインではありません。')
        tau = family == 'babel-tau3'
        command = ['${NATIVE_BIN}/' + ('tau2' if tau else 'vita'), 'run', '--domain', 'delivery,instore,ota' if domain == 'cross' else domain,
                   '--agent-llm', 'target', '--user-llm', 'customer', '--evaluator-llm', 'judge',
                   '--task-ids', '${NATIVE_ID}', '--num-trials', '1', '--max-concurrency', '1', '--seed', '${SEED}',
                   '--save-to', '${WORK_ROOT}/' + ('simulation' if tau else 'simulation.json')]
        if tau: command += ['--auto-resume', '--max-retries', '0']
        else: command += ['--language', 'japanese']
        prefix = 'TAU2' if tau else 'VITA'
        return {**common, 'native_command': command, 'native_cwd': '${SOURCE_ROOT}/benchmarks/' + ('tau3' if tau else 'vitabench'),
                'required_roles': ['target', 'customer', 'judge'], 'inline_files': {'models.json': shared_models()},
                'environment': {prefix + '_MODEL_CONFIG_PATH': '${WORK_ROOT}/models.json', prefix + '_DATA_DIR': '${DATA_ROOT}',
                                **({'TAU2_LANGUAGE': 'japanese'} if tau else {'VITA_AUTO_RESUME': '0'})},
                'result': {'path': 'simulation/results.json' if tau else 'simulation.json', 'pointer': ['simulations'], 'id_field': 'task_id'},
                'artifact_globs': ['simulation/**/*'] if tau else ['simulation.json'],
                'task_time': {'pointer': ['duration']},
                'native_rules': {'agent_steps': 200 if tau else 100, 'trial_count_per_invocation': 1, 'concurrency': 1, 'framework_caps': None},
                'known_blockers': [] if tau else ['Pinned vita/run.py hardcodes max_retries=3 in run_task; a hashed no-retry patch is required before qualification.']}
    if family == 'babel-deepplanning-travel':
        models = {'models': {role: {'model_name': role, 'model_type': 'openai', 'base_url': '${' + role.upper() + '_BASE_URL}',
                                  'api_key_env': 'BEACON_NATIVE_PROXY_KEY', 'temperature': 1.0,
                                  'max_tokens': '${' + role.upper() + '_OUTPUT_TOKENS}'} for role in ('target', 'converter')}}
        return {**common, 'native_command': ['${NATIVE_PYTHON}', 'run.py', '--model', 'target', '--conversion-model', 'converter',
                 '--language', 'japanese', '--workers', '1', '--rerun-ids', '${NATIVE_ID}', '--output-dir', '${WORK_ROOT}/travel', '--save-intermediate'],
                'native_cwd': '${SOURCE_ROOT}/benchmarks/deepplanning/travelplanning', 'required_roles': ['target', 'converter'],
                'inline_files': {'models.json': models},
                'environment': {'BEACON_NATIVE_PROXY_KEY': '${PROXY_KEY}', 'DEEPPLANNING_MODELS_CONFIG': '${WORK_ROOT}/models.json',
                                'DEEPPLANNING_DATA_DIR': '${DATA_ROOT}'},
                'result': {'glob': 'travel/*/evaluation/evaluation_summary.json', 'pointer': ['results'], 'id_field': 'sample_id'},
                'artifact_globs': ['travel/**/*'],
                'native_rules': {'max_llm_calls': 150, 'source': 'travelplanning/run.py', 'framework_caps': None}}
    if family == 'babel-claw':
        if domain != 'general': raise BenchError('初版Clawはgeneralの候補だけを準備します。')
        model = {'provider': 'openai_compat', 'model_id': 'target', 'api_key': '${PROXY_KEY}', 'base_url': '${TARGET_BASE_URL}',
                 'context_window': '${TARGET_CONTEXT_TOKENS}'}
        config = {'model': model, 'judge': {'enabled': True, 'model_id': 'judge', 'api_key': '${PROXY_KEY}', 'base_url': '${JUDGE_BASE_URL}'},
                  'defaults': {'trace_dir': '${WORK_ROOT}/traces', 'tasks_dir': '${DATA_ROOT}'},
                  'sandbox': {'enabled': True, 'image': '${SANDBOX_IMAGE}'}}
        return {**common, 'required_roles': ['target', 'judge'], 'inline_files': {'claw.json': config},
                'native_command': ['${NATIVE_BIN}/claw-eval', 'run', '--task', '${DATA_ROOT}/${NATIVE_ID}', '--language', 'ja',
                                   '--config', '${WORK_ROOT}/claw.json', '--trials', '1', '--trace-dir', '${WORK_ROOT}/traces', '--sandbox'],
                'native_cwd': '${SOURCE_ROOT}/benchmarks/claw',
                'result': {'glob': 'traces/**/*.jsonl', 'format': 'jsonl', 'filter': {'type': 'grading_result'}, 'id_field': 'task_id'},
                'artifact_globs': ['traces/**/*'],
                'task_time': {'artifact': {'glob': 'traces/**/*.jsonl', 'format': 'jsonl', 'filter': {'type': 'trace_end'}, 'single': True}, 'pointer': ['wall_time_s']},
                'native_rules': {'source': 'claw_eval CLI and task YAML', 'framework_caps': None},
                'known_blockers': ['Inspect the selected Japanese task YAML for real-web, image, user-simulator and dependent graders. Add required helper roles before qualification.']}
    raise BenchError('この系列は汎用RPC接続と得点正規化を使用し、専用CLIレシピの受入は未完了です。')
