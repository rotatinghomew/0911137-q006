import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from .disparity import DIMENSIONS, METRICS
from .domain import resolve, run
from .export import build_export, csv_rows
from .simulation import simulate
from .storage import AnalysisStore, read_dataset


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_FIELDS = {'cutoff', 'group', 'dimension', 'baseline', 'metric', 'tenure_width'}


def _csv_cell(value):
    text = '' if value is None else str(value)
    if any(character in text for character in ',"\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_FIELDS = {'cutoff', 'group', 'dimension', 'baseline', 'metric', 'tenure_width'}


class Application:
    def __init__(self, dataset, database, assets):
        self.dataset = Path(dataset)
        self.store = AnalysisStore(database)
        self.assets = Path(assets)

    def catalog(self):
        data, digest = read_dataset(self.dataset)
        return {**data['meta'], 'digest': digest, 'tables': {name: len(rows) for name, rows in data['tables'].items()}}

    def _validated(self, supplied, allowed):
        if not isinstance(supplied, dict) or set(supplied) - allowed:
            raise ValueError('筛选项无效：' + '、'.join(sorted(set(supplied) - allowed)))
        data, digest = read_dataset(self.dataset)
        cutoff = supplied.get('cutoff', data['meta']['default_cutoff'])
        try:
            if isinstance(cutoff, bool) or float(cutoff) != int(cutoff):
                raise ValueError()
            cutoff = int(cutoff)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('分析时点须为整数') from None
        group = supplied.get('group', '')
        if not isinstance(group, str) or group not in ['', *data['meta']['groups']]:
            raise ValueError('未知分析范围')
        if not data['meta']['min_cutoff'] <= cutoff <= data['meta']['max_cutoff']:
            raise ValueError('分析时点超出资料范围')
        options = {'cutoff': cutoff, 'group': group}
        for key, default, choices in [
            ('dimension', 'group', tuple(DIMENSIONS)),
            ('metric', 'fixed_fulltime', tuple(METRICS)),
        ]:
            value = supplied.get(key, default)
            if not isinstance(value, str) or value not in choices:
                raise ValueError('未知' + ('比较维度' if key == 'dimension' else '薪酬口径'))
            options[key] = value
        baseline = supplied.get('baseline', '')
        if not isinstance(baseline, str):
            raise ValueError('参照群体须为文本')
        options['baseline'] = baseline
        width = supplied.get('tenure_width', 10)
        if isinstance(width, bool):
            raise ValueError('任职时间带宽度须为整数')
        try:
            width = int(width)
        except (TypeError, ValueError):
            raise ValueError('任职时间带宽度须为整数') from None
        if not 1 <= width <= 50:
            raise ValueError('任职时间带宽度须为一到五十的整数')
        options['tenure_width'] = width
        return data, digest, options

    def analyze(self, supplied):
        data, digest, options = self._validated(supplied, ANALYSIS_FIELDS)
        resolved, _, _ = resolve(data['tables'], options)
        options = {**options, 'baseline': resolved['baseline']}
        result = run(data['tables'], options)
        return {'digest': digest, 'options': options, 'result': result}

    def simulate(self, supplied):
        if not isinstance(supplied, dict):
            raise ValueError('试算请求须为对象')
        allowed = ANALYSIS_FIELDS | {'scenario'}
        data, _, options = self._validated({key: value for key, value in supplied.items() if key != 'scenario'}, ANALYSIS_FIELDS)
        scenario = supplied.get('scenario')
        if scenario is None:
            scenario = {}
        resolved, rows, _ = resolve(data['tables'], options)
        options = {**options, 'baseline': resolved['baseline']}
        result = simulate(rows, scenario)
        return {'options': options, 'scenario': result['scenario'], 'result': result}

    def export(self, supplied):
        """Build an aggregate review export from a saved version plus a scenario."""
        if not isinstance(supplied, dict) or set(supplied) - {'id', 'simulation'}:
            raise ValueError('导出请求字段无效')
        identifier = supplied.get('id')
        if not isinstance(identifier, str) or not identifier:
            raise ValueError('导出须指定已保存的分析版本')
        analysis = self.store.get(identifier)
        scenario = supplied.get('simulation')
        simulation = None
        if scenario is not None:
            data, _ = read_dataset(self.dataset)
            resolved, rows, _ = resolve(data['tables'], analysis['options'])
            simulation = simulate(rows, scenario)
        return build_export(analysis, simulation)


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, application, **kwargs):
        self.application = application
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        return

    def send_json(self, status, value):
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        request = urlsplit(self.path)
        query = {key: values[-1] for key, values in parse_qs(request.query, keep_blank_values=True).items()}
        try:
            if request.path == '/api/catalog':
                value = self.application.catalog()
            elif request.path == '/api/view':
                value = self.application.analyze(query)
            elif request.path == '/api/analyses':
                value = self.application.store.list()
            elif request.path.startswith('/api/analyses/'):
                value = self.application.store.get(request.path.rsplit('/', 1)[-1])
            elif request.path == '/api/source':
                data, digest = read_dataset(self.application.dataset)
                table = query.get('table', '')
                if table not in data['tables']:
                    raise KeyError('资料表不存在')
                value = {'digest': digest, 'rows': data['tables'][table]}
            elif request.path in {'/', '/app.js', '/style.css'}:
                name = 'index.html' if request.path == '/' else request.path[1:]
                payload = (self.application.assets / name).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', {'index.html': 'text/html', 'app.js': 'text/javascript', 'style.css': 'text/css'}[name] + '; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            else:
                raise KeyError('路径不存在')
            self.send_json(200, value)
        except KeyError as error:
            self.send_json(404, {'error': str(error)})
        except (ValueError, TypeError) as error:
            self.send_json(400, {'error': str(error)})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in {'/api/analyses', '/api/simulate', '/api/exports'}:
            self.send_json(404, {'error': '路径不存在'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise ValueError('请求大小不合适')
            payload = json.loads(self.rfile.read(length))
            if path == '/api/analyses':
                if not isinstance(payload, dict) or set(payload) - {'title', 'options'}:
                    raise ValueError('保存请求字段无效')
                analyzed = self.application.analyze(payload.get('options', {}))
                value = self.application.store.save(payload.get('title'), analyzed['digest'], analyzed['options'], analyzed['result'])
                self.send_json(201, value)
                return
            if path == '/api/simulate':
                if not isinstance(payload, dict) or set(payload) - (ANALYSIS_FIELDS | {'scenario'}):
                    raise ValueError('试算请求字段无效')
                value = self.application.simulate(payload)
                self.send_json(200, value)
                return
            material = self.application.export(payload)
            query = parse_qs(urlsplit(self.path).query)
            if query.get('format', [''])[-1] == 'csv':
                rows = csv_rows(material)
                payload = '\n'.join(','.join(_csv_cell(cell) for cell in row) for row in rows).encode('utf-8-sig')
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename="pay-review.csv"')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_json(200, material)
        except KeyError as error:
            self.send_json(404, {'error': str(error)})
        except (ValueError, TypeError) as error:
            self.send_json(400, {'error': str(error)})


def server(application, host='127.0.0.1', port=0):
    return ThreadingHTTPServer((host, port), partial(Handler, application=application))


def main():
    parser = argparse.ArgumentParser(description='启动本地分析工作台')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8080, type=int)
    parser.add_argument('--data', type=Path, default=ROOT / 'fixtures/data.json')
    parser.add_argument('--state', type=Path, default=Path(os.environ.get('APP_STATE', '.state/analyses.sqlite3')))
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    application = Application(args.data, args.state, ROOT / 'web')
    httpd = server(application, args.host, 0 if args.demo else args.port)
    if args.demo:
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        base = f'http://{args.host}:{httpd.server_port}'
        try:
            with urlopen(base + '/api/view') as response:
                value = json.load(response)
            request = Request(base + '/api/analyses', data=json.dumps({'title': '当前资料摘要', 'options': value['options']}).encode(), headers={'Content-Type': 'application/json'})
            with urlopen(request) as response:
                saved = json.load(response)
            with urlopen(base + '/api/analyses/' + saved['id']) as response:
                restored = json.load(response)
            print(json.dumps({'当前摘要': value['result']['summary'], '资料指纹': value['digest'], '保存后相同': restored['result'] == value['result'], '说明': value['result']['notes']}, ensure_ascii=False, indent=2))
        finally:
            httpd.shutdown()
            worker.join()
            httpd.server_close()
    else:
        print(f'本地工作台：http://{args.host}:{httpd.server_port}', flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
