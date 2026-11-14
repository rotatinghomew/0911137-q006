import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from pay_equity.application import Application, ROOT, server


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.application = Application(ROOT / 'fixtures/data.json', Path(self.directory.name) / 'state.sqlite3', ROOT / 'web')
        self.httpd = server(self.application)
        self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.worker.start()
        self.base = f'http://127.0.0.1:{self.httpd.server_port}'

    def tearDown(self):
        self.httpd.shutdown()
        self.worker.join()
        self.httpd.server_close()
        self.directory.cleanup()

    def get(self, path):
        with urlopen(self.base + path) as response:
            return json.load(response)

    def test_catalog_and_snapshot_roundtrip(self):
        catalog = self.get('/api/catalog')
        view = self.get('/api/view')
        self.assertEqual(catalog['digest'], view['digest'])
        self.assertTrue(view['result']['rows'])
        payload = {'title': '业务核对', 'options': view['options']}
        request = Request(self.base + '/api/analyses', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(request) as response:
            self.assertEqual(response.status, 201)
            saved = json.load(response)
        restored = self.get('/api/analyses/' + saved['id'])
        self.assertEqual(restored['result'], view['result'])
        self.assertEqual(restored['options'], view['options'])
        self.assertEqual(len(self.get('/api/analyses')), 1)

    def test_real_assets(self):
        for path, marker in [('/', '<html lang="zh-CN">'), ('/app.js', 'fetch('), ('/style.css', ':root')]:
            with urlopen(self.base + path) as response:
                self.assertIn(marker, response.read().decode())

    def test_bad_filter_and_unknown_resource(self):
        for path, code in [('/api/view?cutoff=1.5', 400), ('/api/view?extra=1', 400), ('/api/view?group=missing', 400), ('/api/analyses/missing', 404), ('/api/source?table=missing', 404)]:
            with self.assertRaises(HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code, code)

    def test_source_is_same_generation(self):
        catalog = self.get('/api/catalog')
        for table, count in catalog['tables'].items():
            source = self.get('/api/source?table=' + table)
            self.assertEqual(source['digest'], catalog['digest'])
            self.assertEqual(len(source['rows']), count)

    def post(self, path, payload, raw=False):
        request = Request(self.base + path, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(request) as response:
            body = response.read()
            value = body if raw else json.loads(body)
            return {'status': response.status, 'value': value, 'headers': dict(response.headers)}

    def test_disparity_defaults_and_explicit_options(self):
        view = self.get('/api/view')
        self.assertEqual(view['options']['dimension'], 'group')
        self.assertEqual(view['options']['metric'], 'fixed_fulltime')
        self.assertEqual(view['options']['baseline'], 'A')
        disparity = view['result']['disparity']
        self.assertEqual(disparity['values'], ['A', 'B', 'C'])
        self.assertEqual([item['group'] for item in disparity['comparisons']], ['B', 'C'])
        by_group = {item['group']: item for item in disparity['comparisons']}
        self.assertTrue(by_group['B']['primary']['comparable'])
        self.assertFalse(by_group['C']['primary']['comparable'])
        self.assertIsNone(by_group['C']['primary']['within_difference'])

    def test_regional_dimension_has_no_comparable_pairs(self):
        view = self.get('/api/view?dimension=region&baseline=' + quote('上海'))
        disparity = view['result']['disparity']
        self.assertEqual(disparity['values'], ['上海', '成都'])
        self.assertEqual(disparity['incomparable_count'], 1)
        self.assertIsNone(disparity['comparisons'][0]['primary']['within_difference'])

    def test_invalid_analysis_options(self):
        for path in [
            '/api/view?dimension=bad',
            '/api/view?metric=bad',
            '/api/view?tenure_width=0',
            '/api/view?tenure_width=x',
            '/api/view?baseline=Z',
            '/api/view?baseline=',
        ]:
            expected = 400 if path != '/api/view?baseline=' else 200
            try:
                response = self.get(path)
            except HTTPError as error:
                self.assertEqual(error.code, expected)
            else:
                self.assertEqual(expected, 200)
                self.assertIn('disparity', response['result'])

    def test_simulation_endpoint_and_validation(self):
        response = self.post('/api/simulate', {'cutoff': 2026, 'scenario': {'rule': 'uniform_percent', 'budget': 500000, 'percent': 3, 'floor': 42000}}, raw=True)
        self.assertEqual(response['status'], 200)
        value = json.loads(response['value'])
        self.assertAlmostEqual(value['result']['budget']['cost'], sum(row['fixed'] * .03 for row in self.get('/api/source?table=compensation')['rows'] if row['year'] == 2026), places=2)
        for payload in [
            {'scenario': {'rule': 'bad'}},
            {'scenario': {'rule': 'uniform_percent', 'budget': -1}},
            {'scenario': 'bad'},
            {'cutoff': 2026, 'unexpected': 1},
            {'cutoff': 2026, 'scenario': {'rule': 'uniform_percent', 'percent': 1000}},
        ]:
            with self.assertRaises(HTTPError) as caught:
                self.post('/api/simulate', payload)
            self.assertEqual(caught.exception.code, 400)

    def test_export_requires_saved_version_and_is_aggregate_only(self):
        view = self.get('/api/view')
        saved = self.post('/api/analyses', {'title': '评审汇总', 'options': view['options']})['value']
        response = self.post('/api/exports', {'id': saved['id'], 'simulation': {'rule': 'uniform_percent', 'budget': 500000, 'percent': 3, 'floor': 42000}}, raw=True)
        self.assertEqual(response['status'], 200)
        body = response['value']
        material = json.loads(body)
        self.assertNotIn('rows', material)
        self.assertIsNotNone(material['disparity'])
        self.assertIsNotNone(material['simulation'])
        self.assertNotIn('"people"', body.decode())
        response = self.post('/api/exports?format=csv', {'id': saved['id']}, raw=True)
        self.assertEqual(response['status'], 200)
        raw = response['value']
        self.assertIn('text/csv', response['headers']['Content-Type'])
        self.assertIn('可比岗位内差异', raw.decode('utf-8-sig'))
        self.assertNotIn(b'e000', raw)
        for payload in [{}, {'id': 'missing'}, {'id': saved['id'], 'extra': 1}]:
            with self.assertRaises(HTTPError) as caught:
                self.post('/api/exports', payload)
            self.assertIn(caught.exception.code, (400, 404))

    def test_export_cannot_access_employee_table(self):
        view = self.get('/api/view')
        saved = self.post('/api/analyses', {'title': '评审汇总', 'options': view['options']})['value']
        material = self.post('/api/exports', {'id': saved['id']})['value']
        self.assertNotIn('employees', json.dumps(material, ensure_ascii=False))

    def test_saved_version_does_not_recompute_after_dataset_change(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'data.json'
            payload = json.loads((ROOT / 'fixtures/data.json').read_text())
            source.write_text(json.dumps(payload))
            application = Application(source, Path(directory) / 'state.sqlite3', ROOT / 'web')
            first = application.analyze({'cutoff': 2026, 'metric': 'fixed_fulltime'})
            saved = application.store.save('固定版本', first['digest'], first['options'], first['result'])
            payload['tables']['compensation'][0]['fixed'] += 1000
            source.write_text(json.dumps(payload))
            restored = application.store.get(saved['id'])
            self.assertEqual(restored['result'], first['result'])
            self.assertNotEqual(application.analyze({'cutoff': 2026})['digest'], first['digest'])

    def test_database_persists_and_saved_result_is_frozen(self):
        value = self.application.analyze({})
        saved = self.application.store.save('保留版本', value['digest'], value['options'], value['result'])
        reloaded = Application(self.application.dataset, self.application.store.path, ROOT / 'web')
        changed = dict(value['result'])
        changed['notes'] = ['后续操作']
        reloaded.store.save('另一次分析', value['digest'], value['options'], changed)
        self.assertEqual(reloaded.store.get(saved['id'])['result'], value['result'])
        self.assertEqual(len(reloaded.store.list()), 2)

    def test_invalid_save_does_not_create_record(self):
        for payload in [{'title': ''}, {'title': '有效', 'options': {'cutoff': False}}, {'title': '有效', 'unexpected': 1}]:
            request = Request(self.base + '/api/analyses', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request)
            self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.get('/api/analyses'), [])
