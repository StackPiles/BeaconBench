"""Validate explicit case selections independently from trials and readiness."""
from collections import Counter

from .util import BenchError


COUNTS = {'babel-tau3': (2, 10), 'babel-deepplanning': (2, 10), 'babel-vitabench': (2, 8),
          'babel-claw': (3, 10), 'livecodebench': (4, 18), 'scicode-verified': (2, 10),
          'frontier-algorithmic': (1, 6), 'salesllm-ja': (2, 12),
          'crmarena-pro-sales-ja': (2, 8), 'swebench-pro-v2-ja': (0, 8)}


def validate_selection(selection, quick, detailed):
    cases = selection['cases']
    by_id = {row['id']: row for row in cases}
    if selection['case_count'] != 100 or len(cases) != 100 or len(by_id) != 100:
        raise BenchError('Detailedの100ケースに欠落または重複があります。')
    identities = {(row['source_id'], row['domain'], row['native_id']) for row in cases}
    if len(identities) != 100:
        raise BenchError('元の同じ問題を重複して数えています。')
    if selection['selection_policy']['target_results_used'] is not False:
        raise BenchError('選定が対象モデルの結果から独立していません。')
    for manifest, index, count in [(quick, 0, 20), (detailed, 1, 100)]:
        if manifest['cases_target'] != count or manifest['selection_ref'] != 'catalog/selection.v1.candidate.json':
            raise BenchError('セットの件数または参照する選定版が異なります。')
        if {'trials_per_case', 'seed_policy', 'trial_seeds'} & set(manifest):
            raise BenchError('評価セットへ試行回数や試行seedを埋め込めません。')
        refs = manifest['case_refs']
        if len(refs) != count or len(set(refs)) != count or not set(refs) <= set(by_id):
            raise BenchError('選定したケースIDが一致しません。')
        counts = Counter(by_id[key]['source_id'] for key in refs)
        if counts != Counter({key: value[index] for key, value in COUNTS.items() if value[index]}):
            raise BenchError('分野別の選定数が合意した配分と異なります。')
        if set(manifest['limits']) != {'total_output_tokens', 'tool_calls', 'agent_turns', 'framework_deadline_seconds'} or any(value is not None for value in manifest['limits'].values()):
            raise BenchError('選定セットへ独自の出力・操作・時間上限は追加しません。')
        ready = all(by_id[key]['execution_ready'] for key in refs)
        if manifest['execution_ready'] and not ready:
            raise BenchError('未準備のケースを含むセットを実行可能にできません。')
    if not set(quick['case_refs']) <= set(detailed['case_refs']):
        raise BenchError('QuickはDetailedの同じケースの部分集合である必要があります。')
    if set(quick['case_refs']) != {row['id'] for row in cases if row['quick']} or quick['subset_of'] != detailed['suite_id']:
        raise BenchError('Quickの選択表示と参照するケースが一致しません。')
    if selection['execution_ready'] and not detailed['execution_ready']:
        raise BenchError('選定一覧とDetailedの準備状態が一致しません。')
    for row in cases:
        if not row['native_id'] or not row['source']['revision'] or not row['source']['path']:
            raise BenchError('元の問題ID・出典・版が不足しています。')
        if row['readiness_gates'] and row['execution_ready']:
            raise BenchError('未解決の受入条件を成功扱いにできません。')
    return {'cases_selected': 100, 'quick_cases_selected': 20, 'execution_ready': bool(detailed['execution_ready']),
            'source_counts': dict(Counter(row['source_id'] for row in cases))}
