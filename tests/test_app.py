import io, json, time
from conftest import post, login

def wait(pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if pred(): return True
        time.sleep(0.05)
    return False

def add_rule(client, **kw):
    data = {'media_type': 'movie', 'match_mode': 'all', 'priority': '10', **kw}
    return post(client, '/rules', data, follow_redirects=True).get_data(as_text=True)

def decisions(main):
    return {x['id']: main.decision(inst, media, x, ctx) for inst, media, x, ctx in main.load_library()}

def run(main, **kw):
    jid = main.start_job(**kw)
    assert wait(lambda: main.get_job(jid)['status'] not in ('queued', 'running'))
    return main.get_job(jid)

# --- auth & security -------------------------------------------------------
def test_csrf_required(client):
    assert client.post('/rules', data={'media_type': 'movie', 'target_root': '/movies', 'priority': '1'}).status_code == 400

def test_login_and_rate_limit(main, client):
    client.get('/logout')
    assert login(client, 'wrong').status_code == 200
    for _ in range(10): login(client, 'wrong')
    assert 'Too many failed attempts' in login(client, 's3cret-pass').get_data(as_text=True)
    main.FAILS.clear()
    assert login(client, 's3cret-pass').status_code == 302

def test_change_password(client):
    r = post(client, '/settings/password', {'current_password': 's3cret-pass', 'new_password': 'another-pass', 'confirm_password': 'another-pass'}, follow_redirects=True)
    assert 'Admin password changed' in r.get_data(as_text=True)
    client.get('/logout')
    assert login(client, 'another-pass').status_code == 302

def test_export_has_no_secrets(client):
    exp = json.dumps(client.get('/api/export').get_json())
    assert 'api_key' not in exp and 'webhook_secret' not in exp and 'admin_password_hash' not in exp

# --- rules & decisions -----------------------------------------------------
def test_rule_validation(client):
    assert 'Priority must be a whole number' in add_rule(client, genre='Horror', target_root='/movies/Horror', priority='abc')
    assert 'is not a root folder in Radarr' in add_rule(client, genre='Horror', target_root='/movies/Horor')
    assert 'Smart rule added' in add_rule(client, genre='Horror', target_root='/movies/Horror')

def test_nested_roots_and_queue(main, client):
    add_rule(client, genre='Animation + Family', target_root='/movies/Kids', priority='10')
    add_rule(client, genre='Horror', target_root='/movies/Horror', priority='20')
    main.set_setting('fallback_movie', '/movies')
    d = decisions(main)
    assert d[1]['action'] == 'correct'
    assert d[3]['action'] == 'move' and d[3]['new'] == '/movies/Drama C'
    assert d[2]['action'] == 'move' and d[2]['new'] == '/movies/Horror/Scary'   # 'imported' queue record does not block
    assert d[4]['action'] == 'skip' and 'queue' in d[4]['reason']

def test_language_codes(main, client):
    add_rule(client, genre='Animation', language='ja', target_root='/movies/Kids')
    assert decisions(main)[5]['action'] == 'move'
    r = main.db().execute('SELECT * FROM rules').fetchone()
    assert main.rule_matches(dict(r, language='Japanese'), client.arr.movies[5])
    assert main.rule_matches(dict(r, language='ko, ja'), client.arr.movies[5])
    assert not main.rule_matches(dict(r, language='en'), client.arr.movies[5])

def test_tag_exclusion_is_exact(main, client):
    add_rule(client, genre='Horror', target_root='/movies/Horror')
    post(client, '/exclusions', {'media_type': 'all', 'match_type': 'tag', 'pattern': '1'})
    assert decisions(main)[2]['action'] == 'move'
    post(client, '/exclusions', {'media_type': 'all', 'match_type': 'tag', 'pattern': '10'})
    assert decisions(main)[2]['action'] == 'skip'

def test_planner_is_cheap_and_paginated(main, client, monkeypatch):
    calls = []; real = main.api
    monkeypatch.setattr(main, 'api', lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    add_rule(client, genre='Horror', target_root='/movies/Horror')
    calls.clear()
    html = client.get('/planner?action=move').get_data(as_text=True)
    assert len(calls) <= 3
    assert 'Scary' in html and 'Kid A' not in html

# --- moves -----------------------------------------------------------------
def test_live_move_uses_query_flag_and_confirms(main, client):
    add_rule(client, genre='Horror', target_root='/movies/Horror')
    job = run(main, dry=False, selected={'1:movie:2'})
    assert job['counts']['moved'] == 1
    assert client.arr.moved_with_flag[2] is True and client.arr.movies[2]['path'] == '/movies/Horror/Scary'
    msg = main.db().execute("SELECT message FROM history WHERE status='moved'").fetchone()[0]
    assert 'confirmed by Arr' in msg

def test_failed_arr_move_is_reported_not_retried(main, client):
    add_rule(client, genre='Horror', target_root='/movies/Horror')
    client.arr.command_status = 'failed'
    job = run(main, dry=False, selected={'1:movie:2'})
    assert job['counts']['error'] == 1 and len(client.arr.commands) == 1
    assert 'disk full' in main.db().execute("SELECT message FROM history WHERE status='error'").fetchone()[0]

def test_free_space_tracked_during_bulk_move(main, client):
    main.set_setting('fallback_movie', '/movies/Horror')
    client.arr.free['/movies/Horror'] = 20 * 1024**3   # room for one 10 GB title above the 5 GB minimum
    client.arr.queue = []
    job = run(main, dry=False, selected={'1:movie:2', '1:movie:4'})
    assert job['counts']['moved'] == 1 and job['counts']['skipped'] == 1

def test_undo_only_latest(main, client):
    add_rule(client, genre='Horror', target_root='/movies/Horror')
    run(main, dry=False, selected={'1:movie:2'})
    first = main.db().execute("SELECT id FROM history WHERE status='moved'").fetchone()[0]
    post(client, f'/history/{first}/undo')
    assert client.arr.movies[2]['path'] == '/movies/Scary'
    run(main, dry=False, selected={'1:movie:2'})
    second = main.db().execute("SELECT max(id) FROM history WHERE status='moved'").fetchone()[0]
    html = client.get('/').get_data(as_text=True)
    assert f'/history/{second}/undo' in html and f'/history/{first}/undo' not in html

def test_repeated_scans_do_not_grow_history(main, client):
    for _ in range(3): run(main, dry=True)
    n = main.db().execute('SELECT count(*) FROM history').fetchone()[0]
    assert n == 5   # one row per title

def test_jobs_persist_and_stop(main, client):
    job = run(main, dry=True)
    assert main.get_job(job['id'])['status'] == 'complete'
    assert client.get(f"/jobs/{job['id']}").get_json()['status'] == 'complete'

def test_webhook(main, client):
    main.set_setting('dry_run', '0'); main.set_setting('fallback_movie', '/movies')
    secret = main.db().execute('SELECT webhook_secret FROM instances').fetchone()[0]
    iid = main.db().execute('SELECT id FROM instances').fetchone()[0]
    assert client.post(f'/webhook/{iid}/wrong', json={}).status_code == 403
    assert client.post(f'/webhook/{iid}/{secret}', json={'eventType': 'Download', 'movie': {'id': 3}}).status_code == 202
    assert wait(lambda: client.arr.movies[3]['path'] == '/movies/Drama C')

# --- backup ----------------------------------------------------------------
def test_import_validates_before_replacing(main, client):
    add_rule(client, genre='Horror', target_root='/movies/Horror')
    bad = {'rules': [{'media_type': 'movie', 'target_root': ''}]}
    post(client, '/api/import', {'file': (io.BytesIO(json.dumps(bad).encode()), 'x.json')}, content_type='multipart/form-data')
    assert main.db().execute('SELECT count(*) FROM rules').fetchone()[0] == 1
    exp = client.get('/api/export').get_json()
    post(client, '/api/import', {'file': (io.BytesIO(json.dumps(exp).encode()), 'x.json')}, content_type='multipart/form-data')
    assert main.db().execute('SELECT count(*) FROM rules').fetchone()[0] == 1 and main.setting('admin_password_hash')
