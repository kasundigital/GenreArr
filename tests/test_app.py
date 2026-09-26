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

# --- Sonarr -----------------------------------------------------------------
def add_sonarr(client, srv):
    srv.reset()
    post(client, '/instances', {'name': 'S', 'type': 'sonarr', 'url': srv.url, 'api_key': 'k'})

def sonarr_id(main):
    return main.db().execute("SELECT id FROM instances WHERE type='sonarr'").fetchone()[0]

def test_sonarr_decisions_and_move(main, client, sonarr_server):
    add_sonarr(client, sonarr_server); sid = sonarr_id(main)
    add_rule(client, media_type='series', genre='Animation + Children', target_root='/tv/Kids', priority='10')
    add_rule(client, media_type='series', genre='Animation', language='ja', target_root='/tv/Anime', priority='20')
    d = {x['id']: main.decision(i, m, x, c) for i, m, x, c in main.load_library() if m == 'series'}
    assert d[1]['action'] == 'move' and d[1]['new'] == '/tv/Kids/Cartoon Show'
    assert d[2]['action'] == 'move' and d[2]['new'] == '/tv/Anime/Shonen'
    assert d[3]['action'] == 'skip' and 'No imported media' in d[3]['reason']
    assert d[4]['action'] == 'skip' and 'queue' in d[4]['reason']
    job = run(main, dry=False, selected={f'{sid}:series:1'})
    assert job['counts']['moved'] == 1
    assert sonarr_server.moved_with_flag[1] is True and sonarr_server.items[1]['path'] == '/tv/Kids/Cartoon Show'
    assert 'confirmed by Arr' in main.db().execute("SELECT message FROM history WHERE status='moved'").fetchone()[0]

def test_sonarr_webhook_burst_processed_once(main, client, sonarr_server):
    add_sonarr(client, sonarr_server); sid = sonarr_id(main)
    add_rule(client, media_type='series', genre='Animation + Children', target_root='/tv/Kids')
    main.set_setting('dry_run', '0')
    secret = main.db().execute('SELECT webhook_secret FROM instances WHERE id=?', (sid,)).fetchone()[0]
    codes = [client.post(f'/webhook/{sid}/{secret}', json={'eventType': 'Download', 'series': {'id': 1}}).status_code for _ in range(3)]
    assert codes[0] == 202
    assert wait(lambda: sonarr_server.items[1]['path'] == '/tv/Kids/Cartoon Show')
    assert len(sonarr_server.commands) == 1

# --- per-instance rules -------------------------------------------------------
def test_rule_limited_to_instance(main, client):
    rid = main.db().execute("SELECT id FROM instances WHERE type='radarr'").fetchone()[0]
    post(client, '/instances', {'name': 'R2', 'type': 'radarr', 'url': client.arr.url, 'api_key': 'k'})
    r2 = main.db().execute("SELECT id FROM instances WHERE name='R2'").fetchone()[0]
    add_rule(client, genre='Horror', target_root='/movies/Horror', instance_id=str(r2))
    d = {(i['id'], x['id']): main.decision(i, m, x, c) for i, m, x, c in main.load_library()}
    assert d[(r2, 2)]['action'] == 'move' and d[(rid, 2)]['action'] == 'skip'
    post(client, f'/instances/{r2}/delete')
    assert main.db().execute('SELECT enabled FROM rules').fetchone()[0] == 0

def test_rule_instance_type_must_match(main, client, sonarr_server):
    add_sonarr(client, sonarr_server)
    html = add_rule(client, genre='Horror', target_root='/movies/Horror', instance_id=str(sonarr_id(main)))
    assert 'cannot be limited to a Sonarr instance' in html

# --- instance management ------------------------------------------------------
def test_edit_toggle_and_regenerate_instance(main, client):
    iid, secret, key = main.db().execute('SELECT id,webhook_secret,api_key FROM instances').fetchone()
    post(client, f'/instances/{iid}/edit', {'name': 'Radarr HD', 'url': client.arr.url + '/', 'api_key': ''})
    row = main.db().execute('SELECT name,url,api_key,webhook_secret FROM instances WHERE id=?', (iid,)).fetchone()
    assert (row[0], row[1], row[2], row[3]) == ('Radarr HD', client.arr.url, key, secret)
    post(client, f'/instances/{iid}/toggle')
    assert main.db().execute('SELECT enabled FROM instances WHERE id=?', (iid,)).fetchone()[0] == 0
    assert main.load_library() == []
    post(client, f'/instances/{iid}/toggle')
    post(client, f'/instances/{iid}/webhook')
    new = main.db().execute('SELECT webhook_secret FROM instances WHERE id=?', (iid,)).fetchone()[0]
    assert new != secret
    assert client.post(f'/webhook/{iid}/{secret}', json={}).status_code == 403

# --- path mappings -------------------------------------------------------------
def test_path_mapping_detects_folder_on_disk(main, client, tmp_path):
    (tmp_path / 'Horror' / 'Scary').mkdir(parents=True)
    add_rule(client, genre='Horror', target_root='/movies/Horror')
    r = post(client, '/settings', {'dry_run': '1', 'poll_seconds': '3600', 'min_free_gb': '5', 'retry_attempts': '2', 'history_days': '30', 'path_mappings': 'bad line'}, follow_redirects=True)
    assert 'Path mappings not changed' in r.get_data(as_text=True)
    main.set_setting('path_mappings', f'/movies={tmp_path}')
    d = decisions(main)[2]
    assert d['action'] == 'skip' and 'not already on disk' in d['reason']
    (tmp_path / 'Horror' / 'Scary').rmdir()
    assert decisions(main)[2]['action'] == 'move'
    main.set_setting('path_mappings', '/movies=/does/not/exist')
    assert 'visible on disk' in decisions(main)[2]['reason']

def test_local_path_longest_prefix(main):
    m = main.parse_mappings('/movies=/a\n/movies/Kids=/b\n# comment\n')
    assert main.local_path('/movies/Kids/X', m) == '/b/X'
    assert main.local_path('/movies/Y', m) == '/a/Y'
    assert main.local_path('/moviesX/Y', m) is None and main.local_path('/tv/Y', m) is None

# --- notifications -------------------------------------------------------------
def test_notification_test_button(main, client, monkeypatch):
    sent = []
    class Resp:
        def raise_for_status(self): pass
    monkeypatch.setattr(main.requests, 'post', lambda url, **kw: sent.append(url) or Resp())
    assert 'No notification channel is configured' in post(client, '/settings/notify-test', follow_redirects=True).get_data(as_text=True)
    main.set_setting('discord_webhook', 'https://discord.example/hook')
    assert 'Discord: test sent' in post(client, '/settings/notify-test', follow_redirects=True).get_data(as_text=True)
    assert sent == ['https://discord.example/hook']
    main.set_setting('discord_webhook', '')

def test_export_import_keeps_rule_scope(main, client):
    iid = main.db().execute('SELECT id FROM instances').fetchone()[0]
    add_rule(client, genre='Horror', target_root='/movies/Horror', instance_id=str(iid))
    exp = client.get('/api/export').get_json()
    exp['rules'].append(dict(exp['rules'][0], instance_id=999))
    r = post(client, '/api/import', {'file': (io.BytesIO(json.dumps(exp).encode()), 'x.json')}, content_type='multipart/form-data', follow_redirects=True)
    assert '1 rule(s) were limited to an instance that does not exist here' in r.get_data(as_text=True)
    rows = main.db().execute('SELECT instance_id,enabled FROM rules ORDER BY id').fetchall()
    assert [tuple(x) for x in rows] == [(iid, 1), (999, 0)]

def test_every_page_renders(main, client):
    add_rule(client, genre='Horror', target_root='/movies/Horror')
    iid = main.db().execute('SELECT id FROM instances').fetchone()[0]
    rid = main.db().execute('SELECT id FROM rules').fetchone()[0]
    for url in ['/', '/planner', '/instances', f'/instances/{iid}/edit', '/rules', f'/rules/{rid}/edit', '/exclusions', '/health-ui', '/settings', '/health']:
        assert client.get(url).status_code == 200, url
