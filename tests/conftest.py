import os, re, sys, tempfile, threading
import pytest
from flask import Flask, request, jsonify
from werkzeug.serving import make_server

DATA = tempfile.mkdtemp(prefix='genrearr-test-')
os.environ.update(DATA_DIR=DATA, ADMIN_PASSWORD='s3cret-pass', WEBHOOK_DELAY='0', MOVE_VERIFY_SECONDS='3')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class FakeArr:
    '''Minimal Radarr/Sonarr: files only move when moveFiles=true is a query parameter, like the real API.'''
    def __init__(self, kind='radarr'):
        self.kind = kind; self.ep = 'movie' if kind == 'radarr' else 'series'; self.key = 'movieId' if kind == 'radarr' else 'seriesId'
        self.base = '/movies' if kind == 'radarr' else '/tv'
        self.app = Flask('fake-' + kind); self.reset()
        a = self.app; ep = self.ep
        a.get('/api/v3/rootfolder', endpoint='roots')(lambda: jsonify([{'id': i, 'path': p, 'freeSpace': self.free.get(p, 10**12)} for i, p in enumerate(self.roots)]))
        a.get('/api/v3/queue', endpoint='queue')(lambda: jsonify({'page': 1, 'pageSize': 500, 'totalRecords': len(self.queue), 'records': self.queue}))
        a.get(f'/api/v3/{ep}', endpoint='items')(lambda: jsonify(list(self.items.values())))
        a.get(f'/api/v3/{ep}/<int:i>', endpoint='item')(lambda i: jsonify(self.items[i]))
        a.get('/api/v3/command', endpoint='commands')(lambda: jsonify(self.commands))
        @a.put(f'/api/v3/{ep}/<int:i>', endpoint='put')
        def put(i):
            m = self.items[i]; body = request.get_json(); move = request.args.get('moveFiles') == 'true'
            self.moved_with_flag[i] = move
            if move:
                self.commands.append({'id': len(self.commands) + 1, 'name': 'MoveMovie' if self.kind == 'radarr' else 'MoveSeries', 'status': self.command_status,
                                      'body': {self.key: i, 'sourcePath': m['path'], 'destinationPath': body['path']}, 'exception': 'disk full' if self.command_status == 'failed' else None})
            m['path'] = body['path']; return jsonify(m)
        self.server = make_server('127.0.0.1', 0, a, threaded=True)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
    @property
    def movies(self): return self.items
    def reset(self):
        self.commands = []; self.command_status = 'completed'; self.moved_with_flag = {}; self.free = {}
        if self.kind == 'radarr':
            movie = lambda i, title, path, genres, **kw: {'id': i, 'title': title, 'path': path, 'genres': genres, 'hasFile': True, 'year': 2010, 'tags': [], 'sizeOnDisk': 10 * 1024**3, 'originalLanguage': {'id': 1, 'name': 'English'}, **kw}
            self.roots = ['/movies', '/movies/Kids', '/movies/Horror']
            self.items = {1: movie(1, 'Kid A', '/movies/Kids/Kid A', ['Animation', 'Family']),
                          2: movie(2, 'Scary', '/movies/Scary', ['Horror'], tags=[10]),
                          3: movie(3, 'Drama C', '/movies/Kids/Drama C', ['Drama']),
                          4: movie(4, 'Busy', '/movies/Busy', ['Horror']),
                          5: movie(5, 'Anime E', '/movies/Anime E', ['Animation'], originalLanguage={'id': 8, 'name': 'Japanese'})}
            self.queue = [{'movieId': 4, 'trackedDownloadState': 'downloading'}, {'movieId': 2, 'trackedDownloadState': 'imported'}]
        else:
            series = lambda i, title, path, genres, files=3, **kw: {'id': i, 'title': title, 'path': path, 'genres': genres, 'year': 2015, 'tags': [], 'statistics': {'episodeFileCount': files, 'sizeOnDisk': 30 * 1024**3}, 'originalLanguage': {'id': 1, 'name': 'English'}, **kw}
            self.roots = ['/tv', '/tv/Kids', '/tv/Anime']
            self.items = {1: series(1, 'Cartoon Show', '/tv/Cartoon Show', ['Animation', 'Children']),
                          2: series(2, 'Shonen', '/tv/Shonen', ['Animation', 'Action'], originalLanguage={'id': 8, 'name': 'Japanese'}),
                          3: series(3, 'Announced', '/tv/Announced', ['Animation', 'Children'], files=0),
                          4: series(4, 'Airing', '/tv/Airing', ['Animation', 'Children'])}
            self.queue = [{'seriesId': 4, 'episodeId': 99, 'trackedDownloadState': 'downloading'}]

@pytest.fixture(scope='session')
def arr():
    return FakeArr('radarr')

@pytest.fixture(scope='session')
def sonarr_server():
    return FakeArr('sonarr')

@pytest.fixture(scope='session')
def main():
    from app import main
    return main

@pytest.fixture
def client(main, arr):
    arr.reset()
    c = main.db()
    for t in ('rules', 'exclusions', 'history', 'jobs', 'instances'): c.execute(f'DELETE FROM {t}')
    c.commit(); c.close()
    for k, v in {'dry_run': '1', 'fallback_movie': '', 'fallback_series': '', 'min_free_gb': '5', 'path_mappings': ''}.items(): main.set_setting(k, v)
    main.set_password('s3cret-pass'); main.FAILS.clear()
    cl = main.app.test_client(); cl.arr = arr
    login(cl, 's3cret-pass')
    post(cl, '/instances', {'name': 'R', 'type': 'radarr', 'url': arr.url, 'api_key': 'k'})
    return cl

def token(cl, page='/login'):
    return re.search(r'name="csrf_token" value="([^"]+)"', cl.get(page).get_data(as_text=True)).group(1)

def post(cl, url, data=None, **kw):
    d = dict(data or {}); d['csrf_token'] = token(cl, '/settings' if url != '/login' else '/login')
    return cl.post(url, data=d, **kw)

def login(cl, pw):
    return post(cl, '/login', {'password': pw})
