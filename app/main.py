import os, sys, sqlite3, threading, time, uuid, json, posixpath, secrets, hashlib
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response, abort
from werkzeug.security import generate_password_hash, check_password_hash
import requests

VERSION='0.4.0'
DATA=os.getenv('DATA_DIR','/data'); DB=os.path.join(DATA,'genrearr.db'); os.makedirs(DATA,exist_ok=True)
WEAK_SECRETS={'','genrearr-change-me','change-this-secret','replace-with-a-long-random-secret'}
WEAK_PASSWORDS={'','admin','password','changeme','replace-with-a-strong-password'}
USER_SETTINGS=['dry_run','auto_sort','poll_seconds','fallback_movie','fallback_series','discord_webhook','telegram_bot','telegram_chat','min_free_gb','retry_attempts','history_days']
MEDIA_TYPES={'movie','series'}; EXCL_MEDIA={'all','movie','series'}; EXCL_MATCH={'title','path','tag'}; MATCH_MODES={'all','any'}
WEBHOOK_DELAY=int(os.getenv('WEBHOOK_DELAY','20') or 20)
MOVE_VERIFY_SECONDS=int(os.getenv('MOVE_VERIFY_SECONDS','60') or 60)
# Radarr/Sonarr report original language by English name (e.g. "Japanese"); rules may use ISO 639-1 codes.
LANGUAGES={'en':'English','fr':'French','es':'Spanish','de':'German','it':'Italian','da':'Danish','nl':'Dutch','ja':'Japanese','is':'Icelandic',
    'zh':'Chinese','ru':'Russian','pl':'Polish','vi':'Vietnamese','sv':'Swedish','no':'Norwegian','nb':'Norwegian','fi':'Finnish','tr':'Turkish',
    'pt':'Portuguese','el':'Greek','ko':'Korean','hu':'Hungarian','he':'Hebrew','lt':'Lithuanian','cs':'Czech','hi':'Hindi','ro':'Romanian',
    'th':'Thai','bg':'Bulgarian','ar':'Arabic','uk':'Ukrainian','fa':'Persian','bn':'Bengali','sk':'Slovak','lv':'Latvian','ca':'Catalan',
    'hr':'Croatian','sr':'Serbian','bs':'Bosnian','et':'Estonian','ta':'Tamil','id':'Indonesian','te':'Telugu','mk':'Macedonian','sl':'Slovenian',
    'ml':'Malayalam','kn':'Kannada','sq':'Albanian','af':'Afrikaans','mr':'Marathi','tl':'Tagalog','ur':'Urdu','mn':'Mongolian','ka':'Georgian',
    'si':'Sinhala','ms':'Malay','pa':'Punjabi','gu':'Gujarati','ne':'Nepali','km':'Khmer','lo':'Lao','my':'Burmese','sw':'Swahili','ga':'Irish',
    'cy':'Welsh','eu':'Basque','gl':'Galician','hy':'Armenian','az':'Azerbaijani','kk':'Kazakh','uz':'Uzbek','be':'Belarusian','la':'Latin','yi':'Yiddish'}
LANGUAGE_ALIASES={'sinhalese':'sinhala','farsi':'persian','bokmal':'norwegian','flemish':'dutch','portuguese (brazil)':'portuguese','spanish (latino)':'spanish'}
MOVE_COMMANDS={'movie':('MoveMovie','movieId'),'series':('MoveSeries','seriesId')}
MOVE_LOCK=threading.Lock(); PENDING=set(); PENDING_LOCK=threading.Lock(); FAILS={}; FAIL_LOCK=threading.Lock()

def log(msg): print(f'[GenreArr] {msg}',file=sys.stderr,flush=True)
def to_int(v,default=None):
    try: return int(str(v).strip())
    except (TypeError,ValueError): return default
def to_float(v,default=None):
    try: return float(str(v).strip())
    except (TypeError,ValueError): return default
def load_secret():
    k=os.getenv('SECRET_KEY','')
    if k not in WEAK_SECRETS: return k
    p=os.path.join(DATA,'.secret_key')
    try:
        with open(p) as f: k=f.read().strip()
        if k: return k
    except OSError: pass
    k=secrets.token_hex(32)
    with open(os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as f: f.write(k)
    log('SECRET_KEY not set (or a public default) — generated a private key in the data directory.')
    return k

def startup_lock():
    # Serialise first-run setup (secret key, schema, generated password) across processes.
    try:
        import fcntl
        fh=open(os.path.join(DATA,'.init.lock'),'w'); fcntl.flock(fh,fcntl.LOCK_EX); return fh
    except (OSError,ImportError): return None
_init_lock=startup_lock()
app=Flask(__name__); app.secret_key=load_secret()
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE','0')=='1')
if to_int(os.getenv('TRUST_PROXY'),0):
    # Behind a reverse proxy: take client IP/scheme from X-Forwarded-* so login rate limiting is per real client.
    from werkzeug.middleware.proxy_fix import ProxyFix
    n=to_int(os.getenv('TRUST_PROXY')); app.wsgi_app=ProxyFix(app.wsgi_app,x_for=n,x_proto=n,x_host=n,x_prefix=n)

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
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,started DATETIME DEFAULT CURRENT_TIMESTAMP,status TEXT,done INTEGER DEFAULT 0,total INTEGER DEFAULT 0,counts TEXT DEFAULT '{}',dry INTEGER,stop INTEGER DEFAULT 0,error TEXT,source TEXT);
    ''')
    for t,n,d in [('instances','webhook_secret','TEXT'),('instances','last_webhook','TEXT'),('rules','match_mode',"TEXT DEFAULT 'all'"),('rules','language','TEXT'),('rules','year_before','INTEGER'),('rules','tag','TEXT'),('history','media_type','TEXT'),('history','media_id','INTEGER'),('history','rule_id','INTEGER'),('history','instance_id','INTEGER')]: col(c,t,n,d)
    c.execute('CREATE INDEX IF NOT EXISTS history_media ON history(instance_id,media_id,id)')
    # Jobs that were running when the container stopped can never finish; mark them so the dashboard is honest.
    c.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('queued','running')")
    defaults={'dry_run':'1','auto_sort':'0','poll_seconds':'3600','fallback_movie':'','fallback_series':'','discord_webhook':'','telegram_bot':'','telegram_chat':'','min_free_gb':'5','retry_attempts':'2','history_days':'30'}
    for k,v in defaults.items(): c.execute('INSERT OR IGNORE INTO settings(k,v) VALUES(?,?)',(k,v))
    c.commit(); c.close()
init()
def setting(k,d=''):
    c=db(); r=c.execute('SELECT v FROM settings WHERE k=?',(k,)).fetchone(); c.close(); return r['v'] if r else d
def set_setting(k,v):
    c=db(); c.execute('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(k,str(v))); c.commit(); c.close()
def set_password(pw): set_setting('admin_password_hash',generate_password_hash(pw))
def init_auth():
    # ADMIN_PASSWORD from the environment wins whenever it is set to a new (non-default) value;
    # otherwise the password stored in the database (changeable in Settings) is used.
    env=os.getenv('ADMIN_PASSWORD','')
    seed=hashlib.sha256(env.encode()).hexdigest() if env not in WEAK_PASSWORDS else ''
    if seed and setting('admin_env_seed')!=seed: set_password(env); set_setting('admin_env_seed',seed)
    elif not setting('admin_password_hash'):
        pw=secrets.token_urlsafe(12); set_password(pw)
        log(f'ADMIN_PASSWORD not set (or a public default). Generated admin password: {pw}  — change it in Settings.')
init_auth()
if _init_lock: _init_lock.close()

def csrf_token():
    if 'csrf' not in session: session['csrf']=secrets.token_hex(16)
    return session['csrf']
app.jinja_env.globals['csrf_token']=csrf_token
def same(a,b): return secrets.compare_digest(str(a).encode(),str(b).encode())
@app.before_request
def csrf_protect():
    if request.method=='POST' and request.endpoint!='webhook':
        if not session.get('csrf') or not same(request.form.get('csrf_token',''),session['csrf']):
            return 'Invalid or expired form token — go back, reload the page and try again.',400
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
def size_of(media,x): return (x.get('sizeOnDisk') if media=='movie' else (x.get('statistics') or {}).get('sizeOnDisk')) or 0
def media_of(inst): return 'movie' if inst['type']=='radarr' else 'series'
def imported(media,x): return x.get('hasFile',False) if media=='movie' else (x.get('statistics') or {}).get('episodeFileCount',0)>0
def norm(p): return (p or '').rstrip('/')
def queue_ids(inst,media):
    key='movieId' if media=='movie' else 'seriesId'; ids=set(); page=1
    while page<=50:
        q=api(inst,f'queue?page={page}&pageSize=500')
        rec=q.get('records',[]) if isinstance(q,dict) else q
        # Finished imports can linger in the queue briefly; they no longer block a move.
        ids|={z.get(key) for z in rec if z.get(key) is not None and (z.get('trackedDownloadState') or '').lower()!='imported'}
        if not isinstance(q,dict) or page*500>=(q.get('totalRecords') or 0): break
        page+=1
    return ids

class Ctx:
    '''Per-instance cache of roots, queue and library so a scan costs a few API calls, not several per title.'''
    def __init__(self,inst,media,library=None):
        self.inst=inst; self.media=media; self._roots=None; self._queue=None; self._paths=None; self.used={}
        if library is not None: self._index(library)
    def _index(self,library): self._paths={norm(z.get('path')):z.get('id') for z in library if z.get('path')}
    def roots(self):
        if self._roots is None:
            try: self._roots=roots(self.inst)
            except Exception: self._roots=[]
        return self._roots
    def queue(self,refresh=False):
        if self._queue is None or refresh:
            try: self._queue=queue_ids(self.inst,self.media)
            except Exception: self._queue=False
        return self._queue
    def busy(self,x,refresh=False):
        q=self.queue(refresh); return True if q is False else x.get('id') in q
    def paths(self):
        if self._paths is None:
            try: self._index(api(self.inst,media_endpoint(self.media)))
            except Exception: return None
        return self._paths
    def free(self,root_info):
        '''Free space reported by the Arr minus what this run has already moved onto that root.'''
        f=root_info.get('freeSpace')
        return None if f is None else f-self.used.get(norm(root_info.get('path')),0)
    def moved(self,media_id,old,new,root=None,size=0):
        if root: self.used[norm(root)]=self.used.get(norm(root),0)+(size or 0)
        p=self.paths()
        if p is not None:
            if p.get(norm(old))==media_id: p.pop(norm(old),None)
            p[norm(new)]=media_id

def excluded(media,x):
    c=db(); rows=c.execute("SELECT * FROM exclusions WHERE enabled=1 AND (media_type='all' OR media_type=?)",(media,)).fetchall(); c.close()
    tags={str(t) for t in x.get('tags') or []}
    for r in rows:
        pat=(r['pattern'] or '').strip()
        if not pat: continue
        if r['match_type']=='tag':
            if pat in tags: return r
        elif pat.lower() in str(x.get(r['match_type']) or '').lower(): return r
def lang_key(v):
    '''Normalise a language code or name ("ja", "Japanese", "Sinhalese") to one comparable lowercase name.'''
    v=str(v or '').strip().lower(); v=LANGUAGES.get(v,v).lower(); return LANGUAGE_ALIASES.get(v,v)
def language_matches(wanted,orig):
    name,lid=(orig.get('name'),orig.get('id')) if isinstance(orig,dict) else (orig,None)
    have={lang_key(name)} | ({str(lid)} if lid is not None else set())
    return any(lang_key(w) in have for w in str(wanted).split(',') if w.strip())
def rule_matches(r,x):
    genres={str(g).strip().lower() for g in x.get('genres',[]) or []}; wanted={g.strip().lower() for g in (r['genre'] or '').replace('+',',').split(',') if g.strip()}
    if wanted and not ((wanted<=genres) if (r['match_mode'] or 'all')=='all' else bool(wanted&genres)): return False
    if r['language'] and not language_matches(r['language'],x.get('originalLanguage')): return False
    year=to_int(x.get('year'),0); yb=to_int(r['year_before'])
    if yb and (not year or year>=yb): return False
    if r['tag'] and str(r['tag']).strip() not in {str(t) for t in x.get('tags',[]) or []}: return False
    return True
def choose(media,x):
    c=db(); rows=c.execute('SELECT * FROM rules WHERE enabled=1 AND media_type=? ORDER BY priority,id',(media,)).fetchall(); c.close()
    for r in rows:
        if rule_matches(r,x): return r['target_root'],r
    f=setting('fallback_'+media,''); return (f,None) if f else (None,None)
def safety(ctx,x,target,new):
    checks=[]
    ri=next((r for r in ctx.roots() if norm(r.get('path'))==norm(target)),None); checks.append({'label':'Destination is an Arr root','ok':bool(ri)})
    free=ctx.free(ri) if ri else None; minimum=(to_float(setting('min_free_gb','5'),5) or 0)*1024**3
    # The title itself must fit on top of the configured minimum (worst case: a move across filesystems).
    checks.append({'label':'Enough destination free space','ok':free is None or free-size_of(ctx.media,x)>=minimum})
    checks.append({'label':'Not active in queue','ok':not ctx.busy(x)})
    paths=ctx.paths(); owner=paths.get(norm(new)) if paths is not None else None
    checks.append({'label':'No destination collision','ok':paths is not None and owner in (None,x.get('id'))})
    return checks
def decision(inst,media,x,ctx=None,check_safety=True):
    ctx=ctx or Ctx(inst,media)
    genres=x.get('genres') or []; old=x.get('path',''); ex=excluded(media,x)
    base={'old':old,'new':old,'genres':genres,'rule':None,'safety':[]}
    if ex: return {**base,'action':'skip','reason':f"Excluded by {ex['match_type']}: {ex['pattern']}"}
    if not imported(media,x): return {**base,'action':'skip','reason':'No imported media files yet'}
    root,rule=choose(media,x)
    if not root: return {**base,'action':'skip','reason':'No matching smart rule or fallback'}
    folder=posixpath.basename(norm(old)) or x.get('title','Media'); new=norm(root)+'/'+folder
    # Compare the title's parent folder, not a path prefix, so nested roots (/movies vs /movies/Kids) are handled.
    if old and posixpath.dirname(norm(old))==norm(root): return {**base,'action':'correct','reason':'Already in correct root','rule':rule}
    checks=safety(ctx,x,root,new) if check_safety else []
    reason='Fallback folder' if not rule else f"Rule #{rule['id']} priority {rule['priority']}"
    d={**base,'new':new,'rule':rule,'root':root,'safety':checks}
    if checks and not all(c['ok'] for c in checks): return {**d,'action':'skip','reason':reason+' — safety validation failed: '+', '.join(c['label'] for c in checks if not c['ok'])}
    return {**d,'action':'move','reason':reason}
def record(inst,title,genres,old,new,status,msg='',media_type=None,media_id=None,rule_id=None):
    genres=', '.join(map(str,genres or [])); c=db()
    try:
        if status in ('skipped','dry-run') and media_id is not None:
            # Repeat scans produce the same outcome for most titles: refresh the timestamp instead of adding a row.
            last=c.execute('SELECT id,status,message,old_path,new_path FROM history WHERE instance_id=? AND media_id=? ORDER BY id DESC LIMIT 1',(inst['id'],media_id)).fetchone()
            if last and (last['status'],last['message'],last['old_path'],last['new_path'])==(status,msg,old,new):
                c.execute('UPDATE history SET ts=CURRENT_TIMESTAMP,title=?,genres=? WHERE id=?',(title,genres,last['id'])); c.commit(); return last['id']
        cur=c.execute('INSERT INTO history(instance,instance_id,title,genres,old_path,new_path,status,message,media_type,media_id,rule_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(inst['name'],inst['id'],title,genres,old,new,status,msg,media_type,media_id,rule_id)); c.commit(); return cur.lastrowid
    finally: c.close()
def prune_history():
    '''Drop old preview/skip/error rows. Moves and undos are kept so the audit trail and Undo stay intact.'''
    days=to_int(setting('history_days','30'),30)
    if not days or days<1: return 0
    c=db(); n=c.execute("DELETE FROM history WHERE status NOT IN ('moved','undone','undo') AND ts<datetime('now',?)",(f'-{days} days',)).rowcount
    c.execute("DELETE FROM jobs WHERE status NOT IN ('queued','running') AND started<datetime('now',?)",(f'-{days} days',)); c.commit(); c.close(); return n
def notify(text):
    try:
        if setting('discord_webhook'): requests.post(setting('discord_webhook'),json={'content':text},timeout=10)
    except Exception: pass
    try:
        if setting('telegram_bot') and setting('telegram_chat'): requests.post(f"https://api.telegram.org/bot{setting('telegram_bot')}/sendMessage",json={'chat_id':setting('telegram_chat'),'text':text},timeout=10)
    except Exception: pass
class MoveFailed(RuntimeError):
    '''The Arr accepted the new path but reported that moving the files failed; retrying the PUT would not help.'''
def command_ids(inst):
    try: return {c.get('id') for c in api(inst,'command') or []}
    except Exception: return None
def wait_for_move(inst,media,media_id,before):
    '''Follow the MoveMovie/MoveSeries task the Arr queues for a moveFiles update. Returns a note for the history.'''
    if before is None: return 'file move not confirmed (Arr task list unavailable)'
    name,key=MOVE_COMMANDS[media]; start=time.time()
    while True:
        try: cmds=api(inst,'command') or []
        except Exception: return 'file move not confirmed (Arr task list unavailable)'
        mine=[c for c in cmds if c.get('id') not in before and (c.get('name') or '').replace(' ','')==name and (c.get('body') or {}).get(key)==media_id]
        waited=time.time()-start
        if not mine:
            if waited>=min(10,MOVE_VERIFY_SECONDS): return 'file move not confirmed (no move task reported by Arr)'
        else:
            c=mine[-1]; st=(c.get('status') or '').lower()
            if st=='completed': return 'files moved (confirmed by Arr)'
            if st in ('failed','aborted','cancelled','orphaned'):
                raise MoveFailed(f"Arr reported the file move as {st}: {c.get('exception') or c.get('message') or 'no details'} — check Radarr/Sonarr → System → Tasks/Logs")
            if waited>=MOVE_VERIFY_SECONDS: return f'file move still {st or "running"} in Arr after {MOVE_VERIFY_SECONDS}s'
        time.sleep(2)
def arr_move(inst,media,media_id,root,path):
    '''Ask Radarr/Sonarr to move the title. moveFiles must be a query parameter; in the body it is ignored.'''
    ep=f"{media_endpoint(media)}/{media_id}"; body=dict(api(inst,ep)); body['rootFolderPath']=root; body['path']=path
    before=command_ids(inst)
    api(inst,ep+'?moveFiles=true','PUT',body)
    if norm(api(inst,ep).get('path'))!=norm(path): raise RuntimeError('Arr path verification did not match destination')
    return wait_for_move(inst,media,media_id,before)
def do_move(inst,media,x,dry=True,ctx=None):
    ctx=ctx or Ctx(inst,media); title=x.get('title','?')
    d=decision(inst,media,x,ctx); rid=d['rule']['id'] if d.get('rule') else None
    if d['action']=='move' and not dry:
        with MOVE_LOCK:
            # Re-read the title and the queue right before a live move so concurrent jobs/webhooks and fresh downloads are respected.
            x=api(inst,f"{media_endpoint(media)}/{x['id']}"); ctx.queue(refresh=True)
            d=decision(inst,media,x,ctx); rid=d['rule']['id'] if d.get('rule') else None
            if d['action']=='move': return live_move(inst,media,x,d,rid,ctx)
    if d['action']!='move':
        if d['action']=='skip': record(inst,title,d['genres'],d['old'],d['new'],'skipped',d['reason'],media,x.get('id'),rid); return 'skipped'
        return d['action']
    record(inst,title,d['genres'],d['old'],d['new'],'dry-run',d['reason'],media,x.get('id'),rid); return 'dry-run'
def live_move(inst,media,x,d,rid,ctx):
    title=x.get('title','?'); attempts=max(1,min(10,to_int(setting('retry_attempts','2'),2))+1); last=None
    for n in range(attempts):
        try:
            note=arr_move(inst,media,x['id'],d['root'],d['new']); ctx.moved(x['id'],d['old'],d['new'],d['root'],size_of(media,x))
            record(inst,title,d['genres'],d['old'],d['new'],'moved',f"{d['reason']} — {note}",media,x.get('id'),rid); notify(f"GenreArr ✓ {title} → {d['new']} ({note})"); return 'moved'
        except MoveFailed as e: last=e; break
        except Exception as e:
            last=e
            if n<attempts-1: time.sleep(min(15,2**n))
    record(inst,title,d['genres'],d['old'],d['new'],'error',str(last),media,x.get('id'),rid); notify(f"GenreArr ✗ {title}: {last}"); return 'error'
def load_library():
    c=db(); ins=[dict(r) for r in c.execute('SELECT * FROM instances WHERE enabled=1 ORDER BY name').fetchall()]; c.close(); out=[]
    for inst in ins:
        media=media_of(inst)
        try: lib=api(inst,media_endpoint(media))
        except Exception as e: record(inst,'(library)',[],'','','error',str(e),media); continue
        ctx=Ctx(inst,media,lib); out+=[(inst,media,x,ctx) for x in lib]
    return out
def job_update(jid,**kw):
    if 'counts' in kw: kw['counts']=json.dumps(kw['counts'])
    c=db(); c.execute(f"UPDATE jobs SET {','.join(k+'=?' for k in kw)} WHERE id=?",(*kw.values(),jid)); c.commit(); c.close()
def get_job(jid):
    c=db(); r=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone(); c.close()
    if not r: return None
    j=dict(r); j['counts']=json.loads(j['counts'] or '{}'); return j
def recent_jobs(n=5):
    c=db(); rows=c.execute('SELECT id FROM jobs ORDER BY started DESC,rowid DESC LIMIT ?',(n,)).fetchall(); c.close(); return [get_job(r['id']) for r in rows]
def job_stopped(jid):
    c=db(); r=c.execute('SELECT stop FROM jobs WHERE id=?',(jid,)).fetchone(); c.close(); return bool(r and r['stop'])
def run_job(jid,instance_id=None,dry=True,selected=None):
    counts={'moved':0,'dry-run':0,'skipped':0,'correct':0,'error':0}; status='complete'
    try:
        items=load_library()
        if instance_id: items=[z for z in items if z[0]['id']==instance_id]
        if selected: items=[z for z in items if f"{z[0]['id']}:{z[1]}:{z[2].get('id')}" in selected]
        job_update(jid,total=len(items),status='running')
        for n,(inst,media,x,ctx) in enumerate(items,1):
            if job_stopped(jid): status='stopped'; break
            try: r=do_move(inst,media,x,dry,ctx)
            except Exception as e:
                r='error'
                try: record(inst,x.get('title','?'),x.get('genres'),x.get('path',''),'','error',str(e),media,x.get('id'))
                except Exception: pass
            counts[r]=counts.get(r,0)+1; job_update(jid,done=n,counts=counts)
    except Exception as e:
        status='failed'; job_update(jid,error=str(e)); log(f'Job {jid} failed: {e}')
    finally:
        job_update(jid,status=status,counts=counts)
        try: prune_history()
        except Exception as e: log(f'History pruning failed: {e}')
def start_job(instance_id=None,dry=True,selected=None,source='manual'):
    jid=uuid.uuid4().hex[:10]
    c=db(); c.execute("INSERT INTO jobs(id,status,dry,source) VALUES(?,'queued',?,?)",(jid,1 if dry else 0,source)); c.commit(); c.close()
    threading.Thread(target=run_job,args=(jid,instance_id,dry,selected),daemon=True).start(); return jid
def job_running():
    c=db(); n=c.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]; c.close(); return n>0
def worker():
    while True:
        time.sleep(max(300,to_int(setting('poll_seconds','3600'),3600) or 3600))
        try:
            if setting('auto_sort')=='1' and setting('dry_run')!='1' and not job_running(): start_job(None,False,source='scheduled')
            else: prune_history()
        except Exception as e: log(f'Scheduled reconciliation failed to start: {e}')
def start_scheduler():
    # Only one process may run the scheduler, even if more gunicorn workers are configured.
    try:
        import fcntl
        fh=open(os.path.join(DATA,'.scheduler.lock'),'w'); fcntl.flock(fh,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except (OSError,ImportError): return
    app.config['_scheduler_lock']=fh; threading.Thread(target=worker,daemon=True).start()
start_scheduler()

def rate_limited(ip):
    now=time.time()
    with FAIL_LOCK:
        FAILS[ip]=[t for t in FAILS.get(ip,[]) if now-t<900]; return len(FAILS[ip])>=10
def failed_login(ip):
    with FAIL_LOCK: FAILS.setdefault(ip,[]).append(time.time())
@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        ip=request.remote_addr or '?'
        if rate_limited(ip): flash('Too many failed attempts. Try again in 15 minutes.')
        elif check_password_hash(setting('admin_password_hash'),request.form.get('password','')):
            session.clear(); session['ok']=True
            with FAIL_LOCK: FAILS.pop(ip,None)
            return redirect('/')
        else: failed_login(ip); flash('Incorrect password')
    return render_template('login.html',version=VERSION)
@app.route('/logout')
def logout(): session.clear(); return redirect('/login')
@app.route('/')
@auth
def home():
    c=db(); stats={k:c.execute(q).fetchone()[0] for k,q in {'instances':'SELECT count(*) FROM instances','rules':'SELECT count(*) FROM rules','moves':"SELECT count(*) FROM history WHERE status='moved'",'errors':"SELECT count(*) FROM history WHERE status='error'",'previews':"SELECT count(*) FROM history WHERE status='dry-run'"}.items()}; hist=[dict(r) for r in c.execute('SELECT * FROM history ORDER BY ts DESC,id DESC LIMIT 40').fetchall()]
    # Only the most recent move of a title can be undone; older ones would send it to an outdated folder.
    latest={(r[0],r[1]):r[2] for r in c.execute("SELECT instance_id,media_id,max(id) FROM history WHERE status IN ('moved','undo') GROUP BY instance_id,media_id")}
    for r in hist: r['undoable']=r['status']=='moved' and latest.get((r['instance_id'],r['media_id']))==r['id']
    c.close(); jobs=recent_jobs()
    return render_template('index.html',stats=stats,hist=hist,jobs=jobs,version=VERSION,dry=setting('dry_run','1'))
@app.route('/planner')
@auth
def planner():
    items=[]; q=request.args.get('q','').strip().lower(); action=request.args.get('action',''); per=100; page=max(1,to_int(request.args.get('page'),1) or 1)
    for inst,media,x,ctx in load_library():
        try: d=decision(inst,media,x,ctx)
        except Exception as e: d={'action':'skip','reason':f'Error: {e}','old':x.get('path',''),'new':x.get('path',''),'genres':x.get('genres') or [],'safety':[]}
        items.append({'instance_id':inst['id'],'instance':inst['name'],'media':media,'media_id':x.get('id'),'title':x.get('title','?'),'genres':', '.join(map(str,d['genres'])),'old':d['old'],'new':d['new'],'action':d['action'],'reason':d['reason'],'safety':d.get('safety',[])})
    counts={a:sum(1 for x in items if x['action']==a) for a in ('move','correct','skip')}
    if q: items=[x for x in items if q in ' '.join([x['title'],x['genres'],x['old'],x['new']]).lower()]
    if action: items=[x for x in items if x['action']==action]
    pages=max(1,-(-len(items)//per)); page=min(page,pages)
    return render_template('planner.html',items=items[(page-1)*per:page*per],matched=len(items),counts=counts,q=request.args.get('q',''),action=action,page=page,pages=pages,version=VERSION)
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
        name=request.form.get('name','').strip(); typ=request.form.get('type'); url=request.form.get('url','').strip().rstrip('/'); key=request.form.get('api_key','').strip()
        if typ not in ('radarr','sonarr') or not name or not url.startswith(('http://','https://')) or not key: flash('Please enter a name, type, http(s) URL and API key')
        else: c.execute('INSERT INTO instances(name,type,url,api_key,webhook_secret) VALUES(?,?,?,?,?)',(name,typ,url,key,uuid.uuid4().hex)); c.commit(); flash('Instance added')
    rows=c.execute('SELECT * FROM instances ORDER BY id').fetchall(); c.close(); return render_template('instances.html',rows=rows,version=VERSION)
@app.post('/instances/<int:i>/delete')
@auth
def del_instance(i):
    c=db(); c.execute('DELETE FROM instances WHERE id=?',(i,)); c.commit(); c.close(); return redirect('/instances')
def get_instance(i):
    c=db(); inst=c.execute('SELECT * FROM instances WHERE id=?',(i,)).fetchone(); c.close()
    if not inst: abort(404)
    return inst
@app.get('/instances/<int:i>/test')
@auth
def test_instance(i):
    inst=get_instance(i)
    try: flash(f"Connected successfully — {len(roots(inst))} root folder(s) found")
    except Exception as e: flash(f'Connection failed: {e}')
    return redirect('/instances')
@app.get('/instances/<int:i>/roots')
@auth
def instance_roots(i):
    inst=get_instance(i)
    try: return jsonify([{'id':r.get('id'),'path':r.get('path'),'freeSpace':r.get('freeSpace')} for r in roots(inst)])
    except Exception as e: return jsonify(error=str(e)),400
def rule_form(f):
    mt=f.get('media_type'); target=f.get('target_root','').strip(); prio=to_int(f.get('priority'))
    yb=f.get('year_before','').strip(); yb_i=to_int(yb)
    if mt not in MEDIA_TYPES: raise ValueError('Media type must be movie or series')
    if not target: raise ValueError('Target root is required')
    if prio is None: raise ValueError('Priority must be a whole number')
    if yb and yb_i is None: raise ValueError('Year before must be a whole number')
    mode=f.get('match_mode','all'); mode=mode if mode in MATCH_MODES else 'all'
    return (mt,f.get('genre','').strip(),target,prio,mode,f.get('language','').strip() or None,yb_i,f.get('tag','').strip() or None)
def arr_roots(media):
    '''All root folders of enabled instances for this media type, or None when none could be reached.'''
    c=db(); ins=c.execute('SELECT * FROM instances WHERE enabled=1 AND type=?',('radarr' if media=='movie' else 'sonarr',)).fetchall(); c.close(); found=set(); ok=False
    for i in ins:
        try: found|={norm(r.get('path')) for r in roots(i)}; ok=True
        except Exception: pass
    return found if ok else None
def check_target(media,target):
    '''Returns (error, warning). An unknown root is an error only when Radarr/Sonarr could actually be asked.'''
    known=arr_roots(media); app_name='Radarr' if media=='movie' else 'Sonarr'
    if known is None: return None,f'Saved, but the target could not be checked because no {app_name} instance is reachable — verify it on the Health page.'
    if norm(target) not in known: return f"'{target}' is not a root folder in {app_name}. Known roots: {', '.join(sorted(known)) or 'none'}. Add it in {app_name} → Settings → Media Management first.",None
    return None,None
@app.route('/rules',methods=['GET','POST'])
@auth
def rules_page():
    c=db()
    if request.method=='POST':
        try:
            vals=rule_form(request.form); err,warn=check_target(vals[0],vals[2])
            if err: raise ValueError(err)
            c.execute('INSERT INTO rules(media_type,genre,target_root,priority,match_mode,language,year_before,tag) VALUES(?,?,?,?,?,?,?,?)',vals); c.commit(); flash(warn or 'Smart rule added')
        except ValueError as e: flash(f'Rule not saved: {e}')
    rows=c.execute('SELECT * FROM rules ORDER BY media_type,priority,id').fetchall(); ins=c.execute('SELECT * FROM instances WHERE enabled=1 ORDER BY name').fetchall(); c.close(); return render_template('rules.html',rows=rows,instances=ins,version=VERSION)
@app.route('/rules/<int:i>/edit',methods=['GET','POST'])
@auth
def edit_rule(i):
    c=db(); r=c.execute('SELECT * FROM rules WHERE id=?',(i,)).fetchone()
    if not r: c.close(); abort(404)
    if request.method=='POST':
        try:
            vals=rule_form(request.form); err,warn=check_target(vals[0],vals[2])
            if err: raise ValueError(err)
            c.execute('UPDATE rules SET media_type=?,genre=?,target_root=?,priority=?,match_mode=?,language=?,year_before=?,tag=?,enabled=? WHERE id=?',(*vals,1 if request.form.get('enabled') else 0,i)); c.commit(); c.close()
            if warn: flash(warn)
            return redirect('/rules')
        except ValueError as e: flash(f'Rule not saved: {e}')
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
    if request.method=='POST':
        mt=request.form.get('media_type'); mm=request.form.get('match_type'); pat=request.form.get('pattern','').strip()
        if mt not in EXCL_MEDIA or mm not in EXCL_MATCH or not pat: flash('Exclusion not saved: pattern is required')
        else: c.execute('INSERT INTO exclusions(media_type,match_type,pattern) VALUES(?,?,?)',(mt,mm,pat)); c.commit()
    rows=c.execute('SELECT * FROM exclusions ORDER BY id DESC').fetchall(); c.close(); return render_template('exclusions.html',rows=rows,version=VERSION)
@app.post('/exclusions/<int:i>/delete')
@auth
def del_exclusion(i):
    c=db(); c.execute('DELETE FROM exclusions WHERE id=?',(i,)); c.commit(); c.close(); return redirect('/exclusions')
@app.route('/settings',methods=['GET','POST'])
@auth
def settings_page():
    if request.method=='POST':
        f=request.form; errors=[]
        numeric={'poll_seconds':(lambda v:to_int(v) is not None and to_int(v)>=300,'Reconciliation interval must be a whole number ≥ 300'),
                 'min_free_gb':(lambda v:to_float(v) is not None and to_float(v)>=0,'Minimum free space must be a number ≥ 0'),
                 'retry_attempts':(lambda v:to_int(v) is not None and 0<=to_int(v)<=10,'Retry attempts must be between 0 and 10'),
                 'history_days':(lambda v:to_int(v) is not None and 0<=to_int(v)<=3650,'History retention must be between 0 and 3650 days')}
        for k in USER_SETTINGS:
            v=f.get(k,'0' if k in ['dry_run','auto_sort'] else '').strip()
            if k in numeric and not numeric[k][0](v): errors.append(numeric[k][1]); continue
            if k in ('fallback_movie','fallback_series') and v and v!=setting(k):
                err,_=check_target('movie' if k=='fallback_movie' else 'series',v)
                if err: errors.append('Fallback not changed: '+err); continue
            set_setting(k,v)
        flash('Settings saved' if not errors else 'Settings saved, except: '+'; '.join(errors))
    return render_template('settings.html',s={k:setting(k) for k in USER_SETTINGS},version=VERSION)
@app.post('/settings/password')
@auth
def change_password():
    f=request.form; new=f.get('new_password','')
    if not check_password_hash(setting('admin_password_hash'),f.get('current_password','')): flash('Password not changed: current password is incorrect')
    elif len(new)<8: flash('Password not changed: use at least 8 characters')
    elif new!=f.get('confirm_password'): flash('Password not changed: new passwords do not match')
    else: set_password(new); flash('Admin password changed')
    return redirect('/settings')
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
def job(jid): return jsonify(get_job(jid) or {'status':'unknown'})
@app.post('/jobs/<jid>/stop')
@auth
def stop_job(jid):
    job_update(jid,stop=1); return redirect('/')
@app.post('/history/<int:i>/undo')
@auth
def undo(i):
    c=db(); h=c.execute("SELECT * FROM history WHERE id=? AND status='moved'",(i,)).fetchone(); inst=None
    if h: inst=(c.execute('SELECT * FROM instances WHERE id=?',(h['instance_id'],)).fetchone() if h['instance_id'] else None) or c.execute('SELECT * FROM instances WHERE name=?',(h['instance'],)).fetchone()
    c.close()
    if not h or not inst or not h['media_id'] or not h['old_path']: flash('Undo unavailable'); return redirect('/')
    c=db(); newer=c.execute("SELECT count(*) FROM history WHERE instance_id=? AND media_id=? AND id>? AND status IN ('moved','undo')",(inst['id'],h['media_id'],i)).fetchone()[0]; c.close()
    if newer: flash('Undo unavailable: this title was moved again later. Undo the most recent move instead.'); return redirect('/')
    media=h['media_type'] or media_of(inst)
    try:
        with MOVE_LOCK:
            current=norm(api(inst,f"{media_endpoint(media)}/{h['media_id']}").get('path'))
            if current!=norm(h['new_path']): raise RuntimeError(f"title is now at {current or 'an unknown path'}, not {h['new_path']} — it was changed outside GenreArr")
            note=arr_move(inst,media,h['media_id'],posixpath.dirname(norm(h['old_path'])),h['old_path'])
        record(inst,h['title'],[g for g in (h['genres'] or '').split(', ') if g],h['new_path'],h['old_path'],'undo',f'Undo of history #{i} — {note}',media,h['media_id'],h['rule_id'])
        c=db(); c.execute("UPDATE history SET status='undone' WHERE id=?",(i,)); c.commit(); c.close(); flash(f'Undo completed: {note}')
    except Exception as e: flash(f'Undo failed: {e}')
    return redirect('/')
def webhook_worker(inst,media,media_id,key):
    try:
        time.sleep(WEBHOOK_DELAY)  # let Radarr/Sonarr finish the import and clear the queue entry first
        do_move(inst,media,api(inst,f"{media_endpoint(media)}/{media_id}"),setting('dry_run','1')=='1')
    except Exception as e:
        log(f'Webhook processing failed for {inst["name"]} #{media_id}: {e}')
        try: record(inst,f'(webhook #{media_id})',[],'','','error',str(e),media,media_id)
        except Exception: pass
    finally:
        with PENDING_LOCK: PENDING.discard(key)
@app.post('/webhook/<int:i>/<secret>')
def webhook(i,secret):
    c=db(); inst=c.execute('SELECT * FROM instances WHERE id=? AND enabled=1',(i,)).fetchone()
    if not inst or not inst['webhook_secret'] or not same(secret,inst['webhook_secret']): c.close(); return jsonify(error='unauthorized'),403
    c.execute("UPDATE instances SET last_webhook=datetime('now') WHERE id=?",(i,)); c.commit(); c.close()
    data=request.get_json(silent=True) or {}; event=(data.get('eventType') or '').lower()
    if event not in ['download','rename']: return jsonify(status='ignored',event=event)
    media=media_of(inst); obj=data.get('movie') if media=='movie' else data.get('series')
    if not obj or not obj.get('id'): return jsonify(status='ignored',reason='no media id')
    key=(i,obj['id'])
    with PENDING_LOCK:
        # Sonarr sends one event per episode; process each series only once per burst.
        if key in PENDING: return jsonify(status='already-queued')
        PENDING.add(key)
    threading.Thread(target=webhook_worker,args=(dict(inst),media,obj['id'],key),daemon=True).start()
    return jsonify(status='queued'),202
@app.get('/api/export')
@auth
def export_config():
    c=db(); out={'version':VERSION}
    # API keys and webhook secrets are never exported; instances are listed for reference only.
    out['instances']=[{k:x[k] for k in ('id','name','type','url','enabled')} for x in c.execute('SELECT * FROM instances').fetchall()]
    out['rules']=[dict(x) for x in c.execute('SELECT * FROM rules').fetchall()]
    out['exclusions']=[dict(x) for x in c.execute('SELECT * FROM exclusions').fetchall()]
    out['settings']=[{'k':k,'v':setting(k)} for k in USER_SETTINGS]
    c.close(); return Response(json.dumps(out,indent=2),mimetype='application/json',headers={'Content-Disposition':'attachment; filename=genrearr-config.json'})
def parse_import(data):
    if not isinstance(data,dict): raise ValueError('not a GenreArr export')
    rules=[]; excl=[]; sets=[]
    for r in data.get('rules',[]) or []:
        mt=r.get('media_type'); target=str(r.get('target_root') or '').strip(); prio=to_int(r.get('priority',100))
        if mt not in MEDIA_TYPES or not target or prio is None: raise ValueError(f'invalid rule: {r}')
        mode=r.get('match_mode') if r.get('match_mode') in MATCH_MODES else 'all'; tag=r.get('tag')
        rules.append((mt,r.get('genre') or '',target,prio,1 if r.get('enabled',1) else 0,mode,r.get('language') or None,to_int(r.get('year_before')),str(tag).strip() if tag not in (None,'') else None))
    for r in data.get('exclusions',[]) or []:
        mt=r.get('media_type','all'); mm=r.get('match_type','title'); pat=str(r.get('pattern') or '').strip()
        if mt not in EXCL_MEDIA or mm not in EXCL_MATCH: raise ValueError(f'invalid exclusion: {r}')
        if pat: excl.append((mt,mm,pat,1 if r.get('enabled',1) else 0))
    for r in data.get('settings',[]) or []:
        if r.get('k') in USER_SETTINGS: sets.append((r['k'],'' if r.get('v') is None else str(r['v'])))
    return rules,excl,sets
@app.post('/api/import')
@auth
def import_config():
    try:
        f=request.files.get('file')
        if not f: raise ValueError('no file uploaded')
        rules,excl,sets=parse_import(json.load(f))
        c=db()
        try:
            with c:
                c.execute('DELETE FROM rules'); c.execute('DELETE FROM exclusions')
                c.executemany('INSERT INTO rules(media_type,genre,target_root,priority,enabled,match_mode,language,year_before,tag) VALUES(?,?,?,?,?,?,?,?,?)',rules)
                c.executemany('INSERT INTO exclusions(media_type,match_type,pattern,enabled) VALUES(?,?,?,?)',excl)
                c.executemany('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',sets)
        finally: c.close()
        flash(f'Configuration restored: {len(rules)} rule(s), {len(excl)} exclusion(s), {len(sets)} setting(s)')
    except Exception as e: flash(f'Restore failed: {e}')
    return redirect('/settings')
@app.get('/health')
def health(): return jsonify(status='ok',version=VERSION,dry_run=setting('dry_run')=='1')
