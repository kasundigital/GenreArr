import os, sqlite3, threading, time, uuid, json
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import requests

VERSION='0.2.0'
DATA=os.getenv('DATA_DIR','/data'); DB=os.path.join(DATA,'genrearr.db'); os.makedirs(DATA,exist_ok=True)
app=Flask(__name__); app.secret_key=os.getenv('SECRET_KEY','genrearr-change-me')
JOBS={}; JOB_LOCK=threading.Lock()

def db():
    c=sqlite3.connect(DB, timeout=30); c.row_factory=sqlite3.Row; return c

def col(c,t,n,ddl):
    names={x['name'] for x in c.execute(f'PRAGMA table_info({t})').fetchall()}
    if n not in names: c.execute(f'ALTER TABLE {t} ADD COLUMN {n} {ddl}')

def init():
    c=db(); c.executescript('''
    CREATE TABLE IF NOT EXISTS instances(id INTEGER PRIMARY KEY,name TEXT,type TEXT,url TEXT,api_key TEXT,enabled INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY,media_type TEXT,genre TEXT,target_root TEXT,priority INTEGER DEFAULT 100,enabled INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT);
    CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,ts DATETIME DEFAULT CURRENT_TIMESTAMP,instance TEXT,title TEXT,genres TEXT,old_path TEXT,new_path TEXT,status TEXT,message TEXT);
    CREATE TABLE IF NOT EXISTS exclusions(id INTEGER PRIMARY KEY,media_type TEXT DEFAULT 'all',match_type TEXT DEFAULT 'title',pattern TEXT,enabled INTEGER DEFAULT 1);
    ''')
    col(c,'instances','webhook_secret','TEXT'); col(c,'rules','match_mode',"TEXT DEFAULT 'all'")
    col(c,'history','media_type','TEXT'); col(c,'history','media_id','INTEGER'); col(c,'history','rule_id','INTEGER')
    defaults={'dry_run':'1','auto_sort':'0','poll_seconds':os.getenv('POLL_SECONDS','60'),'fallback_movie':'','fallback_series':'','discord_webhook':'','telegram_bot':'','telegram_chat':''}
    for k,v in defaults.items(): c.execute('INSERT OR IGNORE INTO settings(k,v) VALUES(?,?)',(k,v))
    c.commit(); c.close()
init()

def setting(k,default=''):
    c=db(); r=c.execute('SELECT v FROM settings WHERE k=?',(k,)).fetchone(); c.close(); return r['v'] if r else default

def set_setting(k,v):
    c=db(); c.execute('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(k,str(v))); c.commit(); c.close()

def auth(f):
    @wraps(f)
    def w(*a,**k):
        if not session.get('ok'): return redirect(url_for('login'))
        return f(*a,**k)
    return w

def api(inst,path,method='GET',payload=None):
    u=inst['url'].rstrip('/')+'/api/v3/'+path.lstrip('/')
    r=requests.request(method,u,headers={'X-Api-Key':inst['api_key']},json=payload,timeout=45); r.raise_for_status()
    return r.json() if r.content else {}

def roots(inst): return api(inst,'rootfolder')

def imported(media,x):
    return x.get('hasFile',False) if media=='movie' else (x.get('statistics',{}).get('episodeFileCount',0)>0)

def busy(inst,media,x):
    try:
        q=api(inst,'queue?page=1&pageSize=200&includeUnknownMovieItems=true&includeMovie=true&includeSeries=true')
        records=q.get('records',[]) if isinstance(q,dict) else q
        key='movieId' if media=='movie' else 'seriesId'
        return any(z.get(key)==x.get('id') for z in records)
    except Exception:
        return True

def excluded(media,x):
    c=db(); rows=c.execute("SELECT * FROM exclusions WHERE enabled=1 AND (media_type='all' OR media_type=?)",(media,)).fetchall(); c.close()
    vals={'title':x.get('title',''),'path':x.get('path',''),'tag':','.join(map(str,x.get('tags',[])))}
    for r in rows:
        if r['pattern'].lower() in vals.get(r['match_type'],'').lower(): return r
    return None

def choose(media,genres):
    c=db(); rows=c.execute('SELECT * FROM rules WHERE enabled=1 AND media_type=? ORDER BY priority,id',(media,)).fetchall(); c.close()
    gs={str(g).strip().lower() for g in genres or []}
    for r in rows:
        wanted={g.strip().lower() for g in r['genre'].replace('+',',').split(',') if g.strip()}
        ok=(wanted <= gs) if (r['match_mode'] or 'all')=='all' else bool(wanted & gs)
        if ok: return r['target_root'],r
    fallback=setting('fallback_'+media,'')
    return (fallback,None) if fallback else (None,None)

def record(inst,title,genres,old,new,status,msg='',media_type=None,media_id=None,rule_id=None):
    c=db(); cur=c.execute('INSERT INTO history(instance,title,genres,old_path,new_path,status,message,media_type,media_id,rule_id) VALUES(?,?,?,?,?,?,?,?,?,?)',(inst,title,', '.join(genres or []),old,new,status,msg,media_type,media_id,rule_id)); c.commit(); i=cur.lastrowid; c.close(); return i

def notify(text):
    d=setting('discord_webhook'); tb=setting('telegram_bot'); tc=setting('telegram_chat')
    try:
        if d: requests.post(d,json={'content':text},timeout=10)
    except Exception: pass
    try:
        if tb and tc: requests.post(f'https://api.telegram.org/bot{tb}/sendMessage',json={'chat_id':tc,'text':text},timeout=10)
    except Exception: pass

def decision(inst,media,x,check_busy=True):
    genres=x.get('genres') or []; old=x.get('path',''); ex=excluded(media,x)
    if ex: return {'action':'skip','reason':f"Excluded by {ex['match_type']}: {ex['pattern']}",'old':old,'new':old,'genres':genres,'rule':None}
    if not imported(media,x): return {'action':'skip','reason':'No imported media files yet','old':old,'new':old,'genres':genres,'rule':None}
    if check_busy and busy(inst,media,x): return {'action':'skip','reason':'Active in download/import queue','old':old,'new':old,'genres':genres,'rule':None}
    root,rule=choose(media,genres)
    if not root: return {'action':'skip','reason':'No matching rule or fallback','old':old,'new':old,'genres':genres,'rule':None}
    folder=os.path.basename(old.rstrip('/')) or x.get('title','Media'); new=root.rstrip('/')+'/'+folder
    if old.rstrip('/')==new.rstrip('/') or old.rstrip('/').startswith(root.rstrip('/')+'/'):
        return {'action':'correct','reason':'Already in correct root','old':old,'new':old,'genres':genres,'rule':rule}
    return {'action':'move','reason':('Fallback folder' if not rule else f"Rule #{rule['id']}: {rule['genre']} ({rule['match_mode']})"),'old':old,'new':new,'genres':genres,'rule':rule,'root':root}

def do_move(inst,media,x,dry=True):
    d=decision(inst,media,x)
    rid=d['rule']['id'] if d.get('rule') else None
    if d['action']!='move':
        if d['action']=='skip': record(inst['name'],x.get('title','?'),d['genres'],d['old'],d['new'],'skipped',d['reason'],media,x.get('id'),rid)
        return d['action']
    if dry:
        record(inst['name'],x.get('title','?'),d['genres'],d['old'],d['new'],'dry-run',d['reason'],media,x.get('id'),rid); return 'dry-run'
    try:
        body=dict(x); body['rootFolderPath']=d['root']; body['path']=d['new']; body['moveFiles']=True
        api(inst,f"{media}/{x['id']}",'PUT',body)
        after=api(inst,f"{media}/{x['id']}")
        if after.get('path','').rstrip('/') != d['new'].rstrip('/'):
            raise RuntimeError('Move request returned but Arr path verification did not match destination')
        record(inst['name'],x.get('title','?'),d['genres'],d['old'],d['new'],'moved',d['reason'],media,x.get('id'),rid)
        notify(f"GenreArr ✓ {x.get('title')} → {d['new']}")
        return 'moved'
    except Exception as e:
        record(inst['name'],x.get('title','?'),d['genres'],d['old'],d['new'],'error',str(e),media,x.get('id'),rid); notify(f"GenreArr ✗ {x.get('title')}: {e}"); return 'error'

def run_job(job_id,instance_id=None,dry=True):
    c=db(); ins=c.execute('SELECT * FROM instances WHERE enabled=1'+(' AND id=?' if instance_id else ''),((instance_id,) if instance_id else ())).fetchall(); c.close()
    all_items=[]
    for inst in ins:
        media='movie' if inst['type']=='radarr' else 'series'
        try:
            for x in api(inst,'movie' if media=='movie' else 'series'): all_items.append((inst,media,x))
        except Exception as e: record(inst['name'],'(scan)',[],'','','error',str(e),media)
    with JOB_LOCK: JOBS[job_id].update(total=len(all_items),status='running')
    counts={'moved':0,'dry-run':0,'skipped':0,'correct':0,'error':0}
    for idx,(inst,media,x) in enumerate(all_items,1):
        with JOB_LOCK:
            if JOBS[job_id].get('stop'): JOBS[job_id]['status']='stopped'; break
        res=do_move(inst,media,x,dry); counts[res]=counts.get(res,0)+1
        with JOB_LOCK: JOBS[job_id].update(done=idx,counts=counts)
    else:
        with JOB_LOCK: JOBS[job_id]['status']='complete'

def start_job(instance_id=None,dry=True):
    jid=uuid.uuid4().hex[:10]; JOBS[jid]={'id':jid,'status':'queued','done':0,'total':0,'counts':{},'stop':False,'dry':dry}
    threading.Thread(target=run_job,args=(jid,instance_id,dry),daemon=True).start(); return jid

def auto_worker():
    while True:
        time.sleep(max(30,int(setting('poll_seconds','60') or 60)))
        if setting('auto_sort','0')=='1' and setting('dry_run','1')!='1': start_job(None,False)
threading.Thread(target=auto_worker,daemon=True).start()

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST' and request.form.get('password')==os.getenv('ADMIN_PASSWORD','admin'): session['ok']=True; return redirect('/')
    return render_template('login.html',version=VERSION)
@app.route('/logout')
def logout(): session.clear(); return redirect('/login')

@app.route('/')
@auth
def home():
    c=db(); stats={k:c.execute(q).fetchone()[0] for k,q in {'instances':'SELECT count(*) FROM instances','rules':'SELECT count(*) FROM rules','moves':"SELECT count(*) FROM history WHERE status='moved'",'errors':"SELECT count(*) FROM history WHERE status='error'",'previews':"SELECT count(*) FROM history WHERE status='dry-run'"}.items()}; hist=c.execute('SELECT * FROM history ORDER BY id DESC LIMIT 40').fetchall(); c.close()
    jobs=list(JOBS.values())[-5:][::-1]; return render_template('index.html',stats=stats,hist=hist,jobs=jobs,version=VERSION,dry=setting('dry_run','1'))

@app.route('/instances',methods=['GET','POST'])
@auth
def instances():
    c=db()
    if request.method=='POST':
        secret=uuid.uuid4().hex
        c.execute('INSERT INTO instances(name,type,url,api_key,webhook_secret) VALUES(?,?,?,?,?)',(request.form['name'],request.form['type'],request.form['url'].rstrip('/'),request.form['api_key'],secret)); c.commit(); flash('Instance added')
    rows=c.execute('SELECT * FROM instances ORDER BY id').fetchall(); c.close(); return render_template('instances.html',rows=rows,version=VERSION)
@app.post('/instances/<int:i>/delete')
@auth
def del_instance(i):
    c=db(); c.execute('DELETE FROM instances WHERE id=?',(i,)); c.commit(); c.close(); return redirect('/instances')
@app.get('/instances/<int:i>/test')
@auth
def test_instance(i):
    c=db(); inst=c.execute('SELECT * FROM instances WHERE id=?',(i,)).fetchone(); c.close()
    try: rs=roots(inst); flash(f"Connected successfully — {len(rs)} root folder(s) found")
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
        c.execute('INSERT INTO rules(media_type,genre,target_root,priority,match_mode) VALUES(?,?,?,?,?)',(request.form['media_type'],request.form['genre'],request.form['target_root'],int(request.form['priority']),request.form.get('match_mode','all'))); c.commit(); flash('Rule added')
    rows=c.execute('SELECT * FROM rules ORDER BY media_type,priority,id').fetchall(); inst=c.execute('SELECT * FROM instances WHERE enabled=1 ORDER BY name').fetchall(); c.close()
    return render_template('rules.html',rows=rows,instances=inst,version=VERSION)
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
    if request.method=='POST':
        for k in ['dry_run','auto_sort','poll_seconds','fallback_movie','fallback_series','discord_webhook','telegram_bot','telegram_chat']:
            set_setting(k, request.form.get(k,'0' if k in ['dry_run','auto_sort'] else ''))
        flash('Settings saved')
    vals={k:setting(k) for k in ['dry_run','auto_sort','poll_seconds','fallback_movie','fallback_series','discord_webhook','telegram_bot','telegram_chat']}
    return render_template('settings.html',s=vals,version=VERSION)

@app.post('/scan')
@auth
def scan():
    dry=request.form.get('dry','1')=='1'; jid=start_job(request.form.get('instance_id',type=int),dry); flash(f"Scan started: {jid}"); return redirect('/')
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
        media=h['media_type']; x=api(inst,f"{media}/{h['media_id']}"); oldroot=os.path.dirname(h['old_path'].rstrip('/')); body=dict(x); body['rootFolderPath']=oldroot; body['path']=h['old_path']; body['moveFiles']=True; api(inst,f"{media}/{h['media_id']}",'PUT',body)
        record(inst['name'],h['title'],(h['genres'] or '').split(', '),h['new_path'],h['old_path'],'undo','Reverted previous GenreArr move',media,h['media_id']); flash('Undo requested successfully')
    except Exception as e: flash(f'Undo failed: {e}')
    return redirect('/')

@app.post('/webhook/<int:i>/<secret>')
def webhook(i,secret):
    c=db(); inst=c.execute('SELECT * FROM instances WHERE id=? AND enabled=1',(i,)).fetchone(); c.close()
    if not inst or secret!=inst['webhook_secret']: return jsonify(error='unauthorized'),403
    data=request.get_json(silent=True) or {}; event=(data.get('eventType') or '').lower()
    if event not in ['download','moviefiledelete','episodefiledelete','rename']: return jsonify(status='ignored',event=event)
    media='movie' if inst['type']=='radarr' else 'series'; obj=data.get('movie') if media=='movie' else data.get('series')
    if not obj or not obj.get('id'): return jsonify(status='ignored',reason='no media id')
    try:
        x=api(inst,f"{media}/{obj['id']}"); dry=setting('dry_run','1')=='1'; res=do_move(inst,media,x,dry); return jsonify(status=res,dry_run=dry)
    except Exception as e: return jsonify(error=str(e)),500

@app.get('/api/export')
@auth
def export_config():
    c=db(); out={t:[dict(x) for x in c.execute(f'SELECT * FROM {t}').fetchall()] for t in ['instances','rules','settings','exclusions']}; c.close(); return jsonify(out)
@app.get('/health')
def health(): return jsonify(status='ok',version=VERSION,dry_run=setting('dry_run','1')=='1')
