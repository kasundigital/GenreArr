import os, sqlite3, threading, time, uuid, json, posixpath
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
import requests

VERSION='0.3.0'
DATA=os.getenv('DATA_DIR','/data'); DB=os.path.join(DATA,'genrearr.db'); os.makedirs(DATA,exist_ok=True)
app=Flask(__name__); app.secret_key=os.getenv('SECRET_KEY','genrearr-change-me')
JOBS={}; JOB_LOCK=threading.Lock(); RETRIES=[]

def db():
    c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; return c
def col(c,t,n,ddl):
    if n not in {x['name'] for x in c.execute(f'PRAGMA table_info({t})')}: c.execute(f'ALTER TABLE {t} ADD COLUMN {n} {ddl}')
def init():
    c=db(); c.executescript('''
    CREATE TABLE IF NOT EXISTS instances(id INTEGER PRIMARY KEY,name TEXT,type TEXT,url TEXT,api_key TEXT,enabled INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY,media_type TEXT,genre TEXT,target_root TEXT,priority INTEGER DEFAULT 100,enabled INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT);
    CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,ts DATETIME DEFAULT CURRENT_TIMESTAMP,instance TEXT,title TEXT,genres TEXT,old_path TEXT,new_path TEXT,status TEXT,message TEXT);
    CREATE TABLE IF NOT EXISTS exclusions(id INTEGER PRIMARY KEY,media_type TEXT DEFAULT 'all',match_type TEXT DEFAULT 'title',pattern TEXT,enabled INTEGER DEFAULT 1);
    ''')
    for t,n,d in [('instances','webhook_secret','TEXT'),('instances','last_webhook','TEXT'),('rules','match_mode',"TEXT DEFAULT 'all'"),('rules','language','TEXT'),('rules','year_before','INTEGER'),('rules','tag','TEXT'),('history','media_type','TEXT'),('history','media_id','INTEGER'),('history','rule_id','INTEGER')]: col(c,t,n,d)
    defaults={'dry_run':'1','auto_sort':'0','poll_seconds':'3600','fallback_movie':'','fallback_series':'','discord_webhook':'','telegram_bot':'','telegram_chat':'','min_free_gb':'5','retry_attempts':'2'}
    for k,v in defaults.items(): c.execute('INSERT OR IGNORE INTO settings(k,v) VALUES(?,?)',(k,v))
    c.commit(); c.close()
init()
def setting(k,d=''):
    c=db(); r=c.execute('SELECT v FROM settings WHERE k=?',(k,)).fetchone(); c.close(); return r['v'] if r else d
def set_setting(k,v):
    c=db(); c.execute('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(k,str(v))); c.commit(); c.close()
def auth(f):
    @wraps(f)
    def w(*a,**k):
        if not session.get('ok'): return redirect(url_for('login'))
        return f(*a,**k)
    return w
def api(inst,path,method='GET',payload=None):
    r=requests.request(method,inst['url'].rstrip('/')+'/api/v3/'+path.lstrip('/'),headers={'X-Api-Key':inst['api_key']},json=payload,timeout=45); r.raise_for_status(); return r.json() if r.content else {}
def roots(inst): return api(inst,'rootfolder')
def media_endpoint(media): return 'movie' if media=='movie' else 'series'
def imported(media,x): return x.get('hasFile',False) if media=='movie' else x.get('statistics',{}).get('episodeFileCount',0)>0
def busy(inst,media,x):
    try:
        q=api(inst,'queue?page=1&pageSize=500&includeUnknownMovieItems=true&includeMovie=true&includeSeries=true'); rec=q.get('records',[]) if isinstance(q,dict) else q; key='movieId' if media=='movie' else 'seriesId'; return any(z.get(key)==x.get('id') for z in rec)
    except Exception: return True
def excluded(media,x):
    c=db(); rows=c.execute("SELECT * FROM exclusions WHERE enabled=1 AND (media_type='all' OR media_type=?)",(media,)).fetchall(); c.close()
    vals={'title':x.get('title',''),'path':x.get('path',''),'tag':','.join(map(str,x.get('tags',[])))}
    for r in rows:
        if r['pattern'].lower() in vals.get(r['match_type'],'').lower(): return r
def rule_matches(r,x):
    genres={str(g).strip().lower() for g in x.get('genres',[]) or []}; wanted={g.strip().lower() for g in (r['genre'] or '').replace('+',',').split(',') if g.strip()}
    if wanted and not ((wanted<=genres) if (r['match_mode'] or 'all')=='all' else bool(wanted&genres)): return False
    lang=(x.get('originalLanguage') or {}).get('name') if isinstance(x.get('originalLanguage'),dict) else x.get('originalLanguage')
    lang_id=(x.get('originalLanguage') or {}).get('id') if isinstance(x.get('originalLanguage'),dict) else None
    if r['language'] and r['language'].lower() not in {str(lang or '').lower(),str(lang_id or '').lower()}: return False
    year=x.get('year') or 0
    if r['year_before'] and (not year or int(year)>=int(r['year_before'])): return False
    if r['tag'] and str(r['tag']) not in {str(t) for t in x.get('tags',[])}: return False
    return True
def choose(media,x):
    c=db(); rows=c.execute('SELECT * FROM rules WHERE enabled=1 AND media_type=? ORDER BY priority,id',(media,)).fetchall(); c.close()
    for r in rows:
        if rule_matches(r,x): return r['target_root'],r
    f=setting('fallback_'+media,''); return (f,None) if f else (None,None)
def root_info(inst,target):
    try:
        for r in roots(inst):
            if r.get('path','').rstrip('/')==target.rstrip('/'): return r
    except Exception: pass
def safety(inst,media,x,target,new):
    checks=[]
    ri=root_info(inst,target); checks.append({'label':'Destination is an Arr root','ok':bool(ri)})
    free=ri.get('freeSpace') if ri else None; minimum=float(setting('min_free_gb','5') or 0)*1024**3
    checks.append({'label':'Enough destination free space','ok':free is None or free>=minimum})
    checks.append({'label':'Not active in queue','ok':not busy(inst,media,x)})
    collision=False
    try:
        for z in api(inst,media_endpoint(media)):
            if z.get('id')!=x.get('id') and z.get('path','').rstrip('/')==new.rstrip('/'): collision=True; break
    except Exception: collision=True
    checks.append({'label':'No destination collision','ok':not collision})
    return checks
def decision(inst,media,x,check_safety=True):
    genres=x.get('genres') or []; old=x.get('path',''); ex=excluded(media,x)
    if ex: return {'action':'skip','reason':f"Excluded by {ex['match_type']}: {ex['pattern']}",'old':old,'new':old,'genres':genres,'rule':None,'safety':[]}
    if not imported(media,x): return {'action':'skip','reason':'No imported media files yet','old':old,'new':old,'genres':genres,'rule':None,'safety':[]}
    root,rule=choose(media,x)
    if not root: return {'action':'skip','reason':'No matching smart rule or fallback','old':old,'new':old,'genres':genres,'rule':None,'safety':[]}
    folder=posixpath.basename(old.rstrip('/')) or x.get('title','Media'); new=root.rstrip('/')+'/'+folder
    if old.rstrip('/').startswith(root.rstrip('/')+'/'): return {'action':'correct','reason':'Already in correct root','old':old,'new':old,'genres':genres,'rule':rule,'safety':[]}
    checks=safety(inst,media,x,root,new) if check_safety else []
    reason='Fallback folder' if not rule else f"Rule #{rule['id']} priority {rule['priority']}"
    if checks and not all(c['ok'] for c in checks): return {'action':'skip','reason':reason+' — safety validation failed','old':old,'new':new,'genres':genres,'rule':rule,'root':root,'safety':checks}
    return {'action':'move','reason':reason,'old':old,'new':new,'genres':genres,'rule':rule,'root':root,'safety':checks}
def record(inst,title,genres,old,new,status,msg='',media_type=None,media_id=None,rule_id=None):
    c=db(); cur=c.execute('INSERT INTO history(instance,title,genres,old_path,new_path,status,message,media_type,media_id,rule_id) VALUES(?,?,?,?,?,?,?,?,?,?)',(inst,title,', '.join(genres or []),old,new,status,msg,media_type,media_id,rule_id)); c.commit(); i=cur.lastrowid; c.close(); return i
def notify(text):
    try:
        if setting('discord_webhook'): requests.post(setting('discord_webhook'),json={'content':text},timeout=10)
    except Exception: pass
    try:
        if setting('telegram_bot') and setting('telegram_chat'): requests.post(f"https://api.telegram.org/bot{setting('telegram_bot')}/sendMessage",json={'chat_id':setting('telegram_chat'),'text':text},timeout=10)
    except Exception: pass
def move_once(inst,media,x,d):
    body=dict(x); body['rootFolderPath']=d['root']; body['path']=d['new']; body['moveFiles']=True
    api(inst,f"{media_endpoint(media)}/{x['id']}",'PUT',body)
    after=api(inst,f"{media_endpoint(media)}/{x['id']}")
    if after.get('path','').rstrip('/')!=d['new'].rstrip('/'): raise RuntimeError('Arr path verification did not match destination')
def do_move(inst,media,x,dry=True):
    d=decision(inst,media,x); rid=d['rule']['id'] if d.get('rule') else None
    if d['action']!='move':
        if d['action']=='skip': record(inst['name'],x.get('title','?'),d['genres'],d['old'],d['new'],'skipped',d['reason'],media,x.get('id'),rid)
        return d['action']
    if dry: record(inst['name'],x.get('title','?'),d['genres'],d['old'],d['new'],'dry-run',d['reason'],media,x.get('id'),rid); return 'dry-run'
    attempts=max(1,int(setting('retry_attempts','2') or 2)+1); last=None
    for n in range(attempts):
        try:
            move_once(inst,media,x,d); record(inst['name'],x.get('title','?'),d['genres'],d['old'],d['new'],'moved',d['reason'],media,x.get('id'),rid); notify(f"GenreArr ✓ {x.get('title')} → {d['new']}"); return 'moved'
        except Exception as e:
            last=e
            if n<attempts-1: time.sleep(min(15,2**n))
    record(inst['name'],x.get('title','?'),d['genres'],d['old'],d['new'],'error',str(last),media,x.get('id'),rid); notify(f"GenreArr ✗ {x.get('title')}: {last}"); return 'error'
def load_library():
    c=db(); ins=c.execute('SELECT * FROM instances WHERE enabled=1 ORDER BY name').fetchall(); c.close(); out=[]
    for inst in ins:
        media='movie' if inst['type']=='radarr' else 'series'
        try:
            for x in api(inst,media_endpoint(media)): out.append((inst,media,x))
        except Exception as e: record(inst['name'],'(library)',[],'','','error',str(e),media)
    return out
def run_job(jid,instance_id=None,dry=True,selected=None):
    items=load_library()
    if instance_id: items=[z for z in items if z[0]['id']==instance_id]
    if selected: items=[z for z in items if f"{z[0]['id']}:{z[1]}:{z[2]['id']}" in selected]
    with JOB_LOCK: JOBS[jid].update(total=len(items),status='running')
    counts={'moved':0,'dry-run':0,'skipped':0,'correct':0,'error':0}
    for n,(inst,media,x) in enumerate(items,1):
        if JOBS[jid].get('stop'): JOBS[jid]['status']='stopped'; break
        r=do_move(inst,media,x,dry); counts[r]=counts.get(r,0)+1
        with JOB_LOCK: JOBS[jid].update(done=n,counts=counts)
    else: JOBS[jid]['status']='complete'
def start_job(instance_id=None,dry=True,selected=None):
    jid=uuid.uuid4().hex[:10]; JOBS[jid]={'id':jid,'status':'queued','done':0,'total':0,'counts':{},'stop':False,'dry':dry}; threading.Thread(target=run_job,args=(jid,instance_id,dry,selected),daemon=True).start(); return jid
def worker():
    while True:
        time.sleep(max(300,int(setting('poll_seconds','3600') or 3600)))
        if setting('auto_sort')=='1' and setting('dry_run')!='1': start_job(None,False)
threading.Thread(target=worker,daemon=True).start()

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST' and request.form.get('password')==os.getenv('ADMIN_PASSWORD','admin'): session['ok']=True; return redirect('/')
    return render_template('login.html',version=VERSION)
@app.route('/logout')
def logout(): session.clear(); return redirect('/login')
@app.route('/')
@auth
def home():
    c=db(); stats={k:c.execute(q).fetchone()[0] for k,q in {'instances':'SELECT count(*) FROM instances','rules':'SELECT count(*) FROM rules','moves':"SELECT count(*) FROM history WHERE status='moved'",'errors':"SELECT count(*) FROM history WHERE status='error'",'previews':"SELECT count(*) FROM history WHERE status='dry-run'"}.items()}; hist=c.execute('SELECT * FROM history ORDER BY id DESC LIMIT 40').fetchall(); c.close(); return render_template('index.html',stats=stats,hist=hist,jobs=list(JOBS.values())[-5:][::-1],version=VERSION,dry=setting('dry_run','1'))
@app.route('/planner')
@auth
def planner():
    items=[]
    for inst,media,x in load_library():
        d=decision(inst,media,x)
        items.append({'instance_id':inst['id'],'instance':inst['name'],'media':media,'media_id':x['id'],'title':x.get('title','?'),'genres':', '.join(d['genres']),'old':d['old'],'new':d['new'],'action':d['action'],'reason':d['reason'],'safety':d.get('safety',[])})
    return render_template('planner.html',items=items,version=VERSION)
@app.post('/planner/apply')
@auth
def planner_apply():
    picks=set(request.form.getlist('pick')); dry=request.form.get('dry','1')=='1'
    if not picks: flash('No titles selected'); return redirect('/planner')
    jid=start_job(None,dry,picks); flash(f"Selected job started: {jid}"); return redirect('/')
@app.route('/instances',methods=['GET','POST'])
@auth
def instances():
    c=db()
    if request.method=='POST':
        c.execute('INSERT INTO instances(name,type,url,api_key,webhook_secret) VALUES(?,?,?,?,?)',(request.form['name'],request.form['type'],request.form['url'].rstrip('/'),request.form['api_key'],uuid.uuid4().hex)); c.commit(); flash('Instance added')
    rows=c.execute('SELECT * FROM instances ORDER BY id').fetchall(); c.close(); return render_template('instances.html',rows=rows,version=VERSION)
@app.post('/instances/<int:i>/delete')
@auth
def del_instance(i):
    c=db(); c.execute('DELETE FROM instances WHERE id=?',(i,)); c.commit(); c.close(); return redirect('/instances')
@app.get('/instances/<int:i>/test')
@auth
def test_instance(i):
    c=db(); inst=c.execute('SELECT * FROM instances WHERE id=?',(i,)).fetchone(); c.close()
    try: flash(f"Connected successfully — {len(roots(inst))} root folder(s) found")
    except Exception as e: flash(f'Connection failed: {e}')
    return redirect('/instances')
@app.get('/instances/<int:i>/roots')
@auth
def instance_roots(i):
    c=db(); inst=c.execute('SELECT * FROM instances WHERE id=?',(i,)).fetchone(); c.close()
    try: return jsonify([{'id':r.get('id'),'path':r.get('path'),'freeSpace':r.get('freeSpace')} for r in roots(inst)])
    except Exception as e: return jsonify(error=str(e)),400
@app.route('/rules',methods=['GET','POST'])
@auth
def rules_page():
    c=db()
    if request.method=='POST':
        c.execute('INSERT INTO rules(media_type,genre,target_root,priority,match_mode,language,year_before,tag) VALUES(?,?,?,?,?,?,?,?)',(request.form['media_type'],request.form.get('genre',''),request.form['target_root'],int(request.form['priority']),request.form.get('match_mode','all'),request.form.get('language') or None,request.form.get('year_before') or None,request.form.get('tag') or None)); c.commit(); flash('Smart rule added')
    rows=c.execute('SELECT * FROM rules ORDER BY media_type,priority,id').fetchall(); ins=c.execute('SELECT * FROM instances WHERE enabled=1 ORDER BY name').fetchall(); c.close(); return render_template('rules.html',rows=rows,instances=ins,version=VERSION)
@app.route('/rules/<int:i>/edit',methods=['GET','POST'])
@auth
def edit_rule(i):
    c=db(); r=c.execute('SELECT * FROM rules WHERE id=?',(i,)).fetchone()
    if request.method=='POST':
        c.execute('UPDATE rules SET media_type=?,genre=?,target_root=?,priority=?,match_mode=?,language=?,year_before=?,tag=?,enabled=? WHERE id=?',(request.form['media_type'],request.form.get('genre',''),request.form['target_root'],int(request.form['priority']),request.form.get('match_mode','all'),request.form.get('language') or None,request.form.get('year_before') or None,request.form.get('tag') or None,1 if request.form.get('enabled') else 0,i)); c.commit(); c.close(); return redirect('/rules')
    c.close(); return render_template('rule_edit.html',r=r,version=VERSION)
@app.post('/rules/<int:i>/toggle')
@auth
def toggle_rule(i):
    c=db(); c.execute('UPDATE rules SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id=?',(i,)); c.commit(); c.close(); return redirect('/rules')
@app.post('/rules/<int:i>/delete')
@auth
def del_rule(i):
    c=db(); c.execute('DELETE FROM rules WHERE id=?',(i,)); c.commit(); c.close(); return redirect('/rules')
@app.route('/exclusions',methods=['GET','POST'])
@auth
def exclusions():
    c=db()
    if request.method=='POST': c.execute('INSERT INTO exclusions(media_type,match_type,pattern) VALUES(?,?,?)',(request.form['media_type'],request.form['match_type'],request.form['pattern'])); c.commit()
    rows=c.execute('SELECT * FROM exclusions ORDER BY id DESC').fetchall(); c.close(); return render_template('exclusions.html',rows=rows,version=VERSION)
@app.post('/exclusions/<int:i>/delete')
@auth
def del_exclusion(i):
    c=db(); c.execute('DELETE FROM exclusions WHERE id=?',(i,)); c.commit(); c.close(); return redirect('/exclusions')
@app.route('/settings',methods=['GET','POST'])
@auth
def settings_page():
    keys=['dry_run','auto_sort','poll_seconds','fallback_movie','fallback_series','discord_webhook','telegram_bot','telegram_chat','min_free_gb','retry_attempts']
    if request.method=='POST':
        for k in keys: set_setting(k,request.form.get(k,'0' if k in ['dry_run','auto_sort'] else ''))
        flash('Settings saved')
    return render_template('settings.html',s={k:setting(k) for k in keys},version=VERSION)
@app.route('/health-ui')
@auth
def health_ui():
    c=db(); ins=c.execute('SELECT * FROM instances ORDER BY name').fetchall(); c.close(); checks=[]
    for i in ins:
        x={'name':i['name'],'url':i['url'],'api':'Failed','roots':'—','space':'—','webhook':i['last_webhook'] or 'Never','ok':False}
        try:
            rs=roots(i); x['api']='Connected'; x['roots']=str(len(rs)); free=[r.get('freeSpace') for r in rs if r.get('freeSpace') is not None]; x['space']=f"{min(free)/1024**3:.1f} GB minimum" if free else 'Unknown'; x['ok']=True
        except Exception as e: x['api']=str(e)[:90]
        checks.append(x)
    return render_template('health.html',checks=checks,version=VERSION)
@app.post('/scan')
@auth
def scan():
    dry=request.form.get('dry','1')=='1'; flash(f"Scan started: {start_job(request.form.get('instance_id',type=int),dry)}"); return redirect('/')
@app.get('/jobs/<jid>')
@auth
def job(jid): return jsonify(JOBS.get(jid,{'status':'unknown'}))
@app.post('/jobs/<jid>/stop')
@auth
def stop_job(jid):
    if jid in JOBS: JOBS[jid]['stop']=True
    return redirect('/')
@app.post('/history/<int:i>/undo')
@auth
def undo(i):
    c=db(); h=c.execute("SELECT * FROM history WHERE id=? AND status='moved'",(i,)).fetchone(); inst=c.execute('SELECT * FROM instances WHERE name=?',(h['instance'],)).fetchone() if h else None; c.close()
    if not h or not inst: flash('Undo unavailable'); return redirect('/')
    try:
        media=h['media_type']; x=api(inst,f"{media_endpoint(media)}/{h['media_id']}"); oldroot=posixpath.dirname(h['old_path'].rstrip('/')); body=dict(x); body['rootFolderPath']=oldroot; body['path']=h['old_path']; body['moveFiles']=True; api(inst,f"{media_endpoint(media)}/{h['media_id']}",'PUT',body); flash('Undo requested')
    except Exception as e: flash(f'Undo failed: {e}')
    return redirect('/')
@app.post('/webhook/<int:i>/<secret>')
def webhook(i,secret):
    c=db(); inst=c.execute('SELECT * FROM instances WHERE id=? AND enabled=1',(i,)).fetchone()
    if not inst or secret!=inst['webhook_secret']: c.close(); return jsonify(error='unauthorized'),403
    c.execute("UPDATE instances SET last_webhook=datetime('now') WHERE id=?",(i,)); c.commit(); c.close()
    data=request.get_json(silent=True) or {}; event=(data.get('eventType') or '').lower()
    if event not in ['download','rename']: return jsonify(status='ignored',event=event)
    media='movie' if inst['type']=='radarr' else 'series'; obj=data.get('movie') if media=='movie' else data.get('series')
    if not obj or not obj.get('id'): return jsonify(status='ignored',reason='no media id')
    try: return jsonify(status=do_move(inst,media,api(inst,f"{media_endpoint(media)}/{obj['id']}"),setting('dry_run','1')=='1'))
    except Exception as e: return jsonify(error=str(e)),500
@app.get('/api/export')
@auth
def export_config():
    c=db(); out={'version':VERSION}
    for t in ['instances','rules','settings','exclusions']: out[t]=[dict(x) for x in c.execute(f'SELECT * FROM {t}').fetchall()]
    c.close(); return Response(json.dumps(out,indent=2),mimetype='application/json',headers={'Content-Disposition':'attachment; filename=genrearr-config.json'})
@app.post('/api/import')
@auth
def import_config():
    try:
        data=json.load(request.files['file']); c=db()
        for t in ['rules','exclusions','settings']: c.execute(f'DELETE FROM {t}')
        for r in data.get('rules',[]): c.execute('INSERT INTO rules(media_type,genre,target_root,priority,enabled,match_mode,language,year_before,tag) VALUES(?,?,?,?,?,?,?,?,?)',(r.get('media_type'),r.get('genre'),r.get('target_root'),r.get('priority',100),r.get('enabled',1),r.get('match_mode','all'),r.get('language'),r.get('year_before'),r.get('tag')))
        for r in data.get('exclusions',[]): c.execute('INSERT INTO exclusions(media_type,match_type,pattern,enabled) VALUES(?,?,?,?)',(r.get('media_type','all'),r.get('match_type','title'),r.get('pattern'),r.get('enabled',1)))
        for r in data.get('settings',[]): c.execute('INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)',(r.get('k'),r.get('v')))
        c.commit(); c.close(); flash('Configuration restored')
    except Exception as e: flash(f'Restore failed: {e}')
    return redirect('/settings')
@app.get('/health')
def health(): return jsonify(status='ok',version=VERSION,dry_run=setting('dry_run')=='1')
