"""Score fixed helper controls; distinguish class accuracy from missing labels."""
from __future__ import annotations

from collections import Counter

from .util import BenchError, digest, file_sha, inside, read_json, utc, write_once


def evaluate_controls(expected: list[dict], predictions: list[dict], labels: list[str], minimum_macro_f1=.9):
    if type(minimum_macro_f1) not in (int, float) or not 0 <= minimum_macro_f1 <= 1 or len(set(labels)) != len(labels):
        raise BenchError('較正の閾値またはラベル定義が不正です。')
    truth = {row['id']: row for row in expected}
    actual = {row['id']: row for row in predictions}
    if len(truth) != len(expected) or len(actual) != len(predictions) or set(truth) != set(actual):
        raise BenchError('較正集合の欠落・追加・重複があります。')
    if not labels or set(labels) != {row['label'] for row in expected}:
        raise BenchError('宣言したすべてのクラスにcontrolが必要です。')
    confusion = Counter()
    critical = []
    unknown = 0
    for key, row in truth.items():
        predicted = actual[key].get('label')
        if predicted not in labels:
            predicted = 'unknown'; unknown += 1
        confusion[(row['label'], predicted)] += 1
        if row.get('critical') and predicted != row['label']:
            critical.append(key)
    per_class = {}
    for label in labels:
        tp = confusion[(label, label)]
        fp = sum(n for (a, b), n in confusion.items() if b == label and a != label)
        fn = sum(n for (a, b), n in confusion.items() if a == label and b != label)
        per_class[label] = {'tp': tp, 'fp': fp, 'fn': fn, 'f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0}
    macro = sum(row['f1'] for row in per_class.values()) / len(labels)
    return {'controls': len(truth), 'unknown': unknown, 'macro_f1': macro, 'per_class': per_class,
            'critical_errors': critical, 'minimum_macro_f1': minimum_macro_f1,
            'passed': macro >= minimum_macro_f1 and unknown == 0 and not critical,
            'meaning': 'development acceptance on the fixed control set; not population accuracy'}


def verify_instrument(root, reference, model, preset, policy, *, allow_fixture=False):
    path = inside(root, reference['path'])
    if file_sha(path) != reference['sha256']:
        raise BenchError('補助モデルの較正証拠が変更されています。')
    receipt = read_json(path)
    expected = digest({'model': model, 'preset': preset, 'policy': policy})
    if receipt.get('instrument_fingerprint') != expected or receipt.get('passed') is not True:
        raise BenchError('補助モデル・生成条件・採点方針に一致する較正がありません。')
    if receipt.get('fixture') and not allow_fixture:
        raise BenchError('fixtureの較正を本評価用の受入へ流用できません。')
    if not receipt.get('files'):
        raise BenchError('較正の入力・出力原本が不足しています。')
    for relative, expected_sha in receipt['files'].items():
        if file_sha(inside(root, relative)) != expected_sha:
            raise BenchError('補助モデルの較正原本のSHAが一致しません。')
    return receipt


def record_instrument(store, model_id, preset, policy, expected_path, predictions_path, *, fixture=False):
    """Import existing reviewed controls; this command never calls a model."""
    import uuid
    from .config import validate_preset
    expected, predictions = read_json(expected_path), read_json(predictions_path)
    if expected.get('reviewed') is not True or expected.get('language') != 'ja' or not expected.get('review_basis'):
        raise BenchError('較正の正解ラベルには日本語の内容点検と根拠が必要です。')
    if not fixture and (expected.get('fixture') or predictions.get('fixture')):
        raise BenchError('fixtureの予測を本評価用の較正にできません。')
    model = store.registry.get('models', model_id)
    preset = validate_preset(preset)
    fingerprint = digest({'model': model, 'preset': preset, 'policy': policy})
    if predictions.get('instrument_fingerprint') != fingerprint:
        raise BenchError('予測結果の測定器が登録したモデル・条件・方針に一致しません。')
    threshold = policy.get('control_acceptance', {}).get('minimum_macro_f1', .9)
    result = evaluate_controls(expected['cases'], predictions['cases'], expected['labels'], threshold)
    folder = store.root / 'instruments' / uuid.uuid4().hex
    write_once(folder / 'expected.json', expected); write_once(folder / 'predictions.json', predictions)
    receipt = {'utc': utc(), 'fixture': fixture, 'instrument_fingerprint': fingerprint,
               'passed': result['passed'], 'controls': result, 'files': {
                   path.relative_to(store.root).as_posix(): file_sha(path) for path in (folder / 'expected.json', folder / 'predictions.json')}}
    write_once(folder / 'receipt.json', receipt)
    return {'path': (folder / 'receipt.json').relative_to(store.root).as_posix(), 'sha256': file_sha(folder / 'receipt.json'),
            'passed': result['passed'], 'controls': result}
