"""Read-only presentation of typed stops, including immutable older runs."""
from __future__ import annotations

import json

from .util import read_json

PHASE = 'instrument_review_required'


def presentation(store, identity, state):
    value = dict(state)
    if state.get('phase') not in {'infrastructure_error', PHASE}:
        return value
    source_id = state.get('measurement_run_id', identity)
    root = store.run_path(source_id)
    source_state = state if source_id == identity else read_json(root / 'status.json')
    recognized = source_state.get('phase') == PHASE
    events = root / 'events.jsonl'
    if events.is_file() and not recognized:
        with events.open('rb') as handle:
            handle.seek(max(0, events.stat().st_size - 65536))
            for line in handle.read().splitlines():
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if event.get('kind') == 'run_error' and event.get('error_type') == 'InvalidCustomerTermination':
                    recognized = True
    if not recognized:
        return value
    reason = {'code': 'customer_termination', 'category': 'instrument',
              'title': '終了判定の確認が必要', 'can_resume': False,
              'message': source_state.get('message') or '模擬顧客の終了条件を確認できず停止しました。',
              'measurement_run_id': source_id, 'raw_phase': source_state['phase']}
    value.update(display_phase=PHASE, stop_reason=reason)
    # Reviews live outside the immutable run. A new record never replaces the
    # old status, candidate, official score, or source transcript.
    from .termination_reviews import latest
    review = latest(store, source_id)
    if review:
        value['termination_review'] = {k: review[k] for k in ('id', 'name', 'version', 'verdict', 'confirmed', 'explanation', 'budget_totals', 'official_grade_changed', 'original_verdict', 'condition_checks')}
    return value
