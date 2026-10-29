"""Source-only customer checklists and evidence-bound, per-condition audits."""
from __future__ import annotations

import copy
import re
from pathlib import Path

from .util import BenchError, digest, file_sha

VERSION = 'vita-fixed-goals-v1'
REMINDER = '''固定条件の項目別確認：下の一覧は、最初から与えられた<instructions>を文単位でそのまま抜き出したものです。追加の要望や正解例ではありません。
返答前に、各項目と直近の提案・操作結果を照合してください。種類、数量、人数、金額の上下限、日時、対象、条件分岐を別々に確認し、一部を満たしていても他の条件を変更して了承しないでください。
条件外の提案には、元の条件を明示して訂正を依頼してください。相手が完了と言っても、条件外の内容に「すべて満たした」と同意しないでください。元の指示が認める選択や柔軟性は保持してください。
「予定」「希望」「考えている」という文体だけを、種類や数量の変更許可と解釈しないでください。在庫不足や予算の余裕を理由に、指示にない優先順位や代替許可を作らないでください。「中心に」「一部だけ別でもよい」などへ固定要望を読み替えません。代替が元の指示で明示されている場合は、その許可を保持してください。
未確認の内容を確認済みとせず、元の要望に必要な確認を依頼してください。固定情報を相手の計算や説明で置き換えないでください。条件を満たす作業が終わるまでは終了しません。ただし、元の規則が許す技術的に完了不能な場合の終了は妨げません。
条件分岐が成立せず作業不要で、ほかの要望もない場合は終了してください。全要望の完了が確認できた場合も、再確認だけのために対話を延ばさず終了してください。元の要望にない作業や支払い確認を追加しないでください。支払い確認が必要なのは、元の指示または実際の注文で必要なものだけです。
要望は元の規則どおり段階的に伝え、一覧やこの確認手順をそのままエージェントへ開示する必要はありません。返答は顧客の発言だけにしてください。'''
AUDIT = '''各条件を独立に照合してください。conditionsはreference内の元の<instructions>から機械的に抜き出した文で、追加規則ではありません。全項目をchecksに1回ずつ含めてください。
preservedは顧客が元条件を保持したこと、waivedは顧客が条件外の提案を了承したり条件を放棄・変更したこと、unknownは根拠不足、not_applicableは純粋な背景説明または成立しない条件分岐です。
数量・種類・予算・日時などが同じ文にある場合もすべて照合します。全体の成功宣言で個別の矛盾を帳消しにせず、種類の相違と金額の適合などを混同しません。
対象エージェントの失敗だけではwaivedにしません。顧客が訂正を求め元条件を保持した場合はpreservedです。提案の引用、疑問形の訂正、元条件が認めた選択を放棄と誤認しないでください。
messagesのassistantはエージェント、userは顧客です。抜粋対話がassistantで始まることやuserで終わること自体は異常ではありません。作業がまだ未完了でも、顧客が完了を待ち条件を要求し続けていればpreservedです。依頼の完了を確認できないという理由だけで、顧客の条件保持をunknownにしないでください。
preservedとwaivedには保存発言からの引用証拠が必要です。waivedには顧客の発言を引用するfindingも必要です。unknownが1件でもありwaivedがなければ全体はunknown、waivedがあればviolation、それ以外はconsistentにしてください。'''
OUTPUT = '''追加の必須キーchecksは配列です。各要素のキーはcondition_index（conditionsのindex）、verdict（preserved / waived / unknown / not_applicable）、explanation、evidence（配列）です。
evidenceの各要素はmessage_indexとmessage_quoteだけを持ち、保存発言の完全一致の引用にします。waivedのfinding.rule_quoteは該当する条件文から引用し、findingの顧客発言とchecksの証拠を対応させてください。'''


def conditions(reference):
    # Upstream Japanese instructions can contain a literal opening tag in their
    # introduction. Choose the innermost, actual task block, never persona/gold.
    end = reference.find('</instructions>')
    start = reference.rfind('<instructions>', 0, end) if end >= 0 else -1
    if start < 0 or end < 0:
        raise BenchError('顧客の元の固定条件を抽出できません。')
    text = reference[start + len('<instructions>'):end].strip()
    rows = [part.strip() for part in re.split(r'(?<=[。！？])|\n+', text) if part.strip()]
    if not rows:
        raise BenchError('顧客の固定条件が空です。')
    return [{'index': index, 'quote': quote} for index, quote in enumerate(rows)]


def contract():
    return {'version': VERSION, 'reminder_sha256': digest(REMINDER),
            'implementation_sha256': file_sha(Path(__file__)), 'source': 'actual-customer-instructions-only'}


def validate(generation):
    version = generation.get('policy', {}).get('vita_fixed_goals')
    if not version:
        return False
    if (version != VERSION or generation.get('family') != 'babel-vitabench'
            or generation.get('helpers', {}).get('customer', {}).get('policy', {}).get('fixed_goal_contract') != contract()):
        raise BenchError('固定条件確認の版・対象・顧客役の受入条件が一致しません。')
    return True


def decorate(body, role):
    if role != 'customer':
        return body
    value = copy.deepcopy(body)
    system = next((m for m in value['messages'] if m.get('role') == 'system'), None)
    if not system or not isinstance(system.get('content'), str):
        raise BenchError('顧客の元の固定指示がありません。')
    rows = conditions(system['content'])
    system['content'] += '\n\n' + REMINDER + '\n' + '\n'.join(f"[{row['index']}] {row['quote']}" for row in rows)
    return value


def audit_material(material):
    value = copy.deepcopy(material)
    value['conditions'] = conditions(value['reference'])
    return value


def validate_checks(value, material):
    rows = material.get('conditions')
    if rows != conditions(material['reference']):
        raise BenchError('点検条件が実際の固定指示と一致しません。')
    checks = value.get('checks')
    if not isinstance(checks, list) or len(checks) != len(rows):
        raise BenchError('固定条件の項目別点検が不足しています。')
    seen, waived = set(), []
    for check in checks:
        if (not isinstance(check, dict) or set(check) != {'condition_index', 'verdict', 'explanation', 'evidence'}
                or type(check['condition_index']) is not int or not 0 <= check['condition_index'] < len(rows)
                or check['condition_index'] in seen or not isinstance(check['verdict'], str)
                or check['verdict'] not in {'preserved', 'waived', 'unknown', 'not_applicable'}
                or not isinstance(check['explanation'], str) or not check['explanation'].strip()
                or not isinstance(check['evidence'], list)):
            raise BenchError('固定条件の項目別点検の形式が不正です。')
        seen.add(check['condition_index'])
        for evidence in check['evidence']:
            if not isinstance(evidence, dict) or set(evidence) != {'message_index', 'message_quote'}:
                raise BenchError('項目別点検の引用形式が不正です。')
            index, quote = evidence['message_index'], evidence['message_quote']
            if (type(index) is not int or not 0 <= index < len(material['messages']) or not isinstance(quote, str)
                    or not quote.strip() or quote not in (material['messages'][index].get('content') or '')):
                raise BenchError('項目別点検の引用が保存発言と一致しません。')
        if check['verdict'] in {'preserved', 'waived'} and not check['evidence']:
            raise BenchError('条件保持・放棄の判定に引用証拠がありません。')
        if check['verdict'] == 'waived':
            quote = rows[check['condition_index']]['quote']
            matches = [f for f in value['findings'] if f['rule_quote'] in quote and
                       any(e['message_index'] == f['message_index'] and e['message_quote'] == f['message_quote'] for e in check['evidence'])]
            if not matches:
                raise BenchError('条件放棄の項目と顧客の引用証拠が対応していません。')
            waived.extend(matches)
    if any(f not in waived for f in value['findings']):
        raise BenchError('違反の引用に対応する条件項目がありません。')
    expected = 'violation' if waived else 'unknown' if any(c['verdict'] == 'unknown' for c in checks) else 'consistent'
    if value['verdict'] != expected:
        raise BenchError('項目別点検と全体判定が一致しません。')
