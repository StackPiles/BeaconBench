"""Context accounting and accepted-call replay must fail closed."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from beaconbench.context_budget import fit, tokenizer_payload
from beaconbench.recovery import replay_model_call
from beaconbench.util import BenchError, file_sha, read_json, write_once
from beaconbench import transport


class ContextBudget(unittest.TestCase):
    def test_rejection_keeps_numeric_context_evidence_without_error_echo(self):
        secret = 'provider-echo-that-must-not-be-stored'
        message = "This model's maximum context length is 262144 tokens. However, you requested 131072 output tokens and your prompt contains at least 131073 input tokens. " + secret
        def client():
            return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={'error': {'message': message}})))
        model = {'engine': {}, 'adapter': 'vllm', 'base_url': 'http://localhost/v1', 'api_key_env': ''}
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'call'
            with patch.object(transport, 'client', client):
                with self.assertRaises(transport.TransportFailure):
                    asyncio.run(transport.chat(model, {'model': 'fixed', 'messages': [], 'max_tokens': 131072}, folder, 'target', lambda: False, lambda p: None))
            evidence = read_json(folder / 'http-error.json')
            self.assertEqual(evidence['prompt_tokens_at_least'], 131073)
            self.assertEqual(read_json(folder / 'measurement.json')['termination'], 'request_rejected')
            for p in folder.iterdir():
                if p.is_file(): self.assertNotIn(secret.encode(), p.read_bytes())

    def test_bound_reduces_only_output_and_keeps_thinking_in_tokenizer_input(self):
        requests = []
        def responder(request):
            requests.append(request)
            return httpx.Response(200, json={'count': 180, 'max_model_len': 256})
        model = {'adapter': 'vllm', 'base_url': 'http://localhost/v1', 'api_model': 'fixed', 'context_tokens': 256,
                 'engine': {'context_policy': 'llama-swap-qwen-vllm-context-fit-v1'}}
        body = {'model': 'fixed', 'max_tokens': 128, 'temperature': 1, 'messages': [
            {'role': 'user', 'content': 'keep every observation'},
            {'role': 'assistant', 'content': '', 'reasoning_content': 'keep this previous thought'}]}
        original = copy.deepcopy(body)
        with tempfile.TemporaryDirectory() as directory:
            def client(): return httpx.AsyncClient(transport=httpx.MockTransport(responder))
            actual, receipt = asyncio.run(fit(model, body, Path(directory), client, {'Authorization': 'Bearer test-key'}))
            self.assertEqual(actual['max_tokens'], 76)
            self.assertEqual(actual['messages'], original['messages'])
            self.assertEqual(body, original)
            counted = json.loads(requests[0].content)
            self.assertIn('keep this previous thought', counted['messages'][1]['content'])
            self.assertEqual(str(requests[0].url), 'http://localhost/upstream/fixed/tokenize')
            self.assertFalse(receipt['input_history_removed'])
            self.assertNotIn('test-key', (Path(directory) / 'context-budget.json').read_text())

    def test_disabled_policy_makes_no_tokenizer_request(self):
        def forbidden(): raise AssertionError('no API call expected')
        body = {'max_tokens': 128, 'messages': []}
        result, proof = asyncio.run(fit({'engine': {}}, body, Path('.'), forbidden, {}))
        self.assertEqual(result, body); self.assertIsNone(proof)

    def test_exhausted_or_wrong_capacity_does_not_start_generation(self):
        model = {'adapter': 'vllm', 'base_url': 'http://localhost/v1', 'api_model': 'fixed', 'context_tokens': 256,
                 'engine': {'context_policy': 'qwen-vllm-context-fit-v1'}}
        for response in ({'count': 256, 'max_model_len': 256}, {'count': 100, 'max_model_len': 512}):
            with tempfile.TemporaryDirectory() as directory:
                def client(): return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response)))
                with self.assertRaises(BenchError):
                    asyncio.run(fit(model, {'model': 'fixed', 'messages': [], 'max_tokens': 128}, Path(directory), client, {}))


class Replay(unittest.TestCase):
    def test_only_a_complete_matching_immutable_call_can_be_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'old'; source.mkdir()
            parameters = {'model': 'fixed', 'messages': [{'role': 'user', 'content': 'same input'}], 'temperature': 1, 'max_tokens': 128}
            request = {'role': 'target', 'body': {**parameters, 'stream': True, 'stream_options': {'include_usage': True}}}
            write_once(source / 'request.json', request)
            write_once(source / 'measurement.json', {'completed': True, 'usage': {'prompt_tokens': 9}})
            write_once(source / 'message.json', {'role': 'assistant', 'content': 'saved response'})
            ref = {'folder': str(source), 'files': {p.name: file_sha(p) for p in source.glob('*.json')}}
            actual = replay_model_call(ref, parameters, 'target', root / 'replayed')
            self.assertEqual(actual['message']['content'], 'saved response')
            self.assertEqual(actual['measurement']['reused_from'], str(source))
            self.assertFalse(read_json(root / 'replayed/replay.json')['new_model_request'])
            with self.assertRaises(BenchError): replay_model_call(ref, {**parameters, 'temperature': 0}, 'target', root / 'changed-input')
            (source / 'message.json').write_text('{}')
            with self.assertRaises(BenchError): replay_model_call(ref, parameters, 'target', root / 'tampered')
            self.assertFalse((root / 'tampered').exists())


if __name__ == '__main__': unittest.main()
