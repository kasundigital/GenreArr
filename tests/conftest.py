import os, re, sys, tempfile, threading
import pytest
from flask import Flask, request, jsonify
from werkzeug.serving import make_server

DATA = tempfile.mkdtemp(prefix='genrearr-test-')
os.environ.update(DATA_DIR=DATA, ADMIN_PASSWORD='s3cret-pass', WEBHOOK_DELAY='0', MOVE_VERIFY_SECONDS='3')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class FakeArr:
    '''Minimal Radarr: moves only happen when moveFiles=true is a query parameter, like the real API.'''
    def __init__(self):
        self.app = Flask('fake-arr'); self.reset()
        a = self.app
        a.get('/api/v3/rootfolder', endpoint='roots')(lambda: jsonify([{'id': i, 'path': p, 'freeSpace': self.free.get(p, 10**12)} for i, p in enumerate(['/movies', '/movies/Kids', '/movies/Horror'])]))
        a.get('/api/v3/queue', endpoint='queue')(lambda: jsonify({'page': 1, 'pageSize': 500, 'totalRecords': len(self.queue), 'records': self.queue}))
        a.get('/api/v3/movie', endpoint='movies')(lambda: jsonify(list(self.movies.values())))
        a.get('/api/v3/movie/<int:i>', endpoint='movie')(lambda i: jsonify(self.movies[i]))
        a.get('/api/v3/command', endpoint='commands')(lambda: jsonify(self.commands))
        @a.put('/api/v3/movie/<int:i>')
        def put(i):
            m = self.movies[i]; body = request.get_json(); move = request.args.get('moveFiles') == 'true'
            self.moved_with_flag[i] = move
            if move:
                self.commands.append({'id': len(self.commands) + 1, 'name': 'MoveMovie', 'status': self.command_status,
                                      'body': {'movieId': i, 'sourcePath': m['path'], 'destinationPath': body['path']}, 'exception': 'disk full' if self.command_status == 'failed' else None})
            m['path'] = body['path']; return jsonify(m)
        self.server = make_server('127.0.0.1', 0, a, threaded=True)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
    def reset(self):
        movie = lambda i, title, path, genres, **kw: {'id': i, 'title': title, 'path': path, 'genres': genres, 'hasFile': True, 'year': 2010, 'tags': [], 'sizeOnDisk': 10 * 1024**3, 'originalLanguage': {'id': 1, 'name': 'English'}, **kw}
        self.movies = {1: movie(1, 'Kid A', '/movies/Kids/Kid A', ['Animation', 'Family']),
                       2: movie(2, 'Scary', '/movies/Scary', ['Horror'], tags=[10]),
                       3: movie(3, 'Drama C', '/movies/Kids/Drama C', ['Drama']),
                       4: movie(4, 'Busy', '/movies/Busy', ['Horror']),
                       5: movie(5, 'Anime E', '/movies/Anime E', ['Animation'], originalLanguage={'id': 8, 'name': 'Japanese'})}
        self.queue = [{'movieId': 4, 'trackedDownloadState': 'downloading'}, {'movieId': 2, 'trackedDownloadState': 'imported'}]
        self.commands = []; self.command_status = 'completed'; self.moved_with_flag = {}; self.free = {}

@pytest.fixture(scope='session')
def arr():
    return FakeArr()

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
    main.set_setting('dry_run', '1'); main.set_setting('fallback_movie', ''); main.set_setting('min_free_gb', '5')
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
