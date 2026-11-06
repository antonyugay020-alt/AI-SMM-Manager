"""Regressions for the transport dependency graph and provider error ABI."""
import ast
import io
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import urllib.error


class ProviderBoundaryRegressions(TestCase):
    def test_one_error_identity_works_for_both_transport_callers(self):
        from provider_errors import OllamaStageError, ProviderHTTPError
        from ollama_resilience import OllamaStageError as OllamaError, ProviderHTTPError as OllamaHTTPError
        from text_providers import ProviderHTTPError as CloudHTTPError
        self.assertIs(OllamaStageError, OllamaError)
        self.assertIs(ProviderHTTPError, OllamaHTTPError)
        self.assertIs(ProviderHTTPError, CloudHTTPError)
        self.assertTrue(issubclass(ProviderHTTPError, OllamaStageError))

    def test_cloud_http_error_is_caught_by_legacy_ollama_exception_import(self):
        from ollama_resilience import ProviderHTTPError
        from text_providers import call_text
        response = io.BytesIO(json.dumps({'error': {'code': 'rate_limit_exceeded'}}).encode())
        failure = urllib.error.HTTPError('https://api.openai.com', 429, 'Too Many Requests', {}, response)
        with patch('urllib.request.urlopen', side_effect=failure):
            with self.assertRaises(ProviderHTTPError) as raised:
                call_text('openai/example', [{'role': 'user', 'content': 'ping'}],
                          environ={'OPENAI_API_KEY': 'fixture_only'})
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.provider_error_code, 'rate_limit_exceeded')

    def test_source_dependency_graph_has_no_cycles(self):
        file_path = Path(__file__).resolve()
        candidates = (file_path.parents[1],)
        source = next((path for path in candidates if (path / 'app.py').is_file()), candidates[0])
        self.assertTrue((source / 'app.py').is_file(), 'Source graph cannot silently be empty')
        modules = {p.stem: p for p in source.glob('*.py') if not p.name.startswith('test_')}
        self.assertGreaterEqual(len(modules), 70, 'Source graph is incomplete or vacuous')
        from text_providers import __file__ as provider_source
        loaded=Path(provider_source).resolve()
        self.assertIn(loaded.parent,(source.resolve(), Path(__file__).resolve().parent),
                      'Provider imported from an unrelated source tree')
        self.assertEqual(loaded.read_bytes(),(source/'text_providers.py').read_bytes(),
                         'Tests and source tree diverged')
        for module,path in modules.items():
            mirror=Path(__file__).resolve().parent / (module+'.py')
            self.assertTrue(mirror.is_file(), 'Missing developer mirror: '+module)
            self.assertEqual(mirror.read_bytes(),path.read_bytes(),
                             'Developer mirror diverges: '+module)
        graph = {}
        for name, path in modules.items():
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            deps = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    head = node.module.split('.')[0]
                    if head in modules:
                        deps.add(head)
                if isinstance(node, ast.Import):
                    deps.update(a.name.split('.')[0] for a in node.names if a.name.split('.')[0] in modules)
            graph[name] = deps
        visiting, visited = [], set()
        def traverse(module):
            if module in visiting:
                self.fail('Import dependency cycle: ' + ' -> '.join(visiting + [module]))
            if module in visited:
                return
            visiting.append(module)
            for dependency in graph[module]:
                traverse(dependency)
            visiting.pop()
            visited.add(module)
        for module in graph:
            traverse(module)
