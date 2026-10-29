#!/usr/bin/env python3
"""Rebuild the AlgoGators issue tracker page from GitHub.
GitHub is the source: labels (P0-P3, 'project: x'), assignees, 'Blocked by' links and the latest
comment that starts with 'Status:'. base.json supplies the long descriptions and the order within a priority.
Usage: python3 sync.py [output.html] [--standalone]
Read-only: it only reads GitHub (gh api graphql) and writes one HTML file."""
import json,subprocess,re,sys,datetime,os
HERE=os.path.dirname(os.path.abspath(__file__)); ORG="AlgoGators"
REPOS=['trade-ngin','algogators','AlgoLens','algosystem','algogauge']
# data-ngin was archived on 2026-10-08; its open issues were migrated to the algogators mono repo,
# each body starting '> Migrated from .../data-ngin/issues/N'. Old numbers are mapped to the new ones.
OLD='data-ngin'; NEW='algogators'
NAMES={'Faybioo':'Fabio','ChronoInferna':'Oliver','SivanRP':'Sivan','gabbypolito':'Gaby','pranavb05':'Pranav','jrile018':'John Riley','dominickdupuy':'Dom','Gannon-Stoner':'Gannon','pranavrevuri':'Pranav R.','raohemdutt':'HD','hdgit8':'HD','dependabot':'Dependabot'}
PL={'equities':'equities','regime':'regime detection','data':'data','qt':'QT platform','monitoring':'run monitoring','ml':'ML models','spreads':'spreads','stats':'statistics and validation','audiences':'audiences and investor books','reporting':'reporting','testing':'unit testing','refactor':'refactor and benchmarking','hygiene':'CI and hygiene','futures':'futures strategy','website':'website'}
LP={v:k for k,v in PL.items()}
STATUSES=['In progress','PR open','Draft PR open','Fix written, no PR','Ready to start','Under review','Blocked','Parked','Reference','Done']
def gql(q):
    r=subprocess.run(['gh','api','graphql','-f','query='+q],capture_output=True,text=True)
    if r.returncode: sys.exit('GitHub query failed: '+r.stderr[:400])
    return json.loads(r.stdout)['data']
def unref(t): return re.sub(r'AlgoGators/([\w-]+)#(\d+)',r'\1 #\2',t or '')
def parse_status(comments):
    for c in reversed(comments):
        b=(c['body'] or '').strip(); by=(c['author'] or {}).get('login',''); at=c['createdAt'][:10]
        m=re.match(r'\*Originally posted by (\S+) on (\d{4}-\d\d-\d\d) in [^\n]*\*\s*',b)  # a comment copied over by the migration
        if m: by,at,b=m.group(1),m.group(2),b[m.end():].strip()
        if not re.match(r'(?i)status\s*:',b): continue
        f={'by':NAMES.get(by,by),'at':at}
        cur=None; extra=[]
        for line in b.split('\n'):
            if line.strip().lower().startswith('to update, add a new comment'): continue
            m=re.match(r'(?i)\s*(status|waiting on|next step|notes?)\s*:\s*(.*)$',line)
            if m: cur={'status':'status','waiting on':'waiting','next step':'next','note':'notes','notes':'notes'}[m.group(1).lower()]; f[cur]=m.group(2).strip()
            elif line.strip():
                if cur: f[cur]=(f[cur]+' '+line.strip()).strip()
                else: extra.append(line.strip())
        if extra: f['notes']=(f.get('notes','')+' '+' '.join(extra)).strip()
        return f
    return None
base=json.load(open(os.path.join(HERE,'base.json')))
B={(i['repo'],i['n']):i for i in base['issues']}
BP={(p['repo'],p['n']):p for p in base['prs']}
# order of work: the pinned issue trade-ngin #153 lists every issue, top first
ORDER_ISSUE=('trade-ngin',153)
def first_line(body):
    """The first line of an issue body that is not empty, without markdown markers, for the one-line summary."""
    for ln in (body or '').split('\n'):
        t=re.sub(r'^[#>*\-\s]+|\*\*','',ln).strip()
        if t: return t[:200]
    return ''
def migration_note(comments,after):
    """The migration's own '**Status at migration (date):** ...' comment, when it is newer than the status comment."""
    for c in reversed(comments):
        m=re.match(r'\*\*Status at migration \((\d{4}-\d\d-\d\d)\):\*\*\s*(.*)',(c['body'] or '').strip(),re.S)
        if m and m.group(1)>=(after or ''): return m.group(1),' '.join(m.group(2).split())
    return None
ob=gql('{repository(owner:"%s",name:"%s"){issue(number:%d){body}}}'%(ORG,ORDER_ISSUE[0],ORDER_ISSUE[1]))['repository']['issue']['body'] or ''
RANK={}
for m in re.finditer(r'(?m)^\s*[-*]\s*(?:AlgoGators/([\w-]+))?#(\d+)',ob):
    RANK.setdefault((m.group(1) or ORDER_ISSUE[0],int(m.group(2))),len(RANK)+1)
issues=[]; prs=[]; seen=set(); DATA={}
for repo in REPOS:
    DATA[repo]=gql('''{repository(owner:"%s",name:"%s"){
      issues(states:OPEN,first:100){nodes{number title url createdAt updatedAt body assignees(first:10){nodes{login}} labels(first:30){nodes{name}}
        blockedBy(first:20){nodes{number state repository{name}}} comments(last:25){nodes{body createdAt author{login}}}}}
      pullRequests(states:OPEN,first:100){nodes{number title url createdAt updatedAt isDraft baseRefName headRefName mergeable additions deletions changedFiles author{login}
        reviews(states:APPROVED,first:10){nodes{author{login}}} commits(last:1){nodes{commit{statusCheckRollup{state}}}}}}}}'''%(ORG,repo))['repository']
MIG={}
# open and closed: a migrated issue that is now closed still renames its old references
allnew=gql('{repository(owner:"%s",name:"%s"){issues(first:100,states:[OPEN,CLOSED]){nodes{number body state}}}}'%(ORG,NEW))['repository']['issues']['nodes']
for it in allnew:
    m=re.search(r'Migrated from https://github\.com/AlgoGators/%s/issues/(\d+)'%OLD,it['body'] or '')
    if m: MIG[int(m.group(1))]=it['number']
RMIG={v:k for k,v in MIG.items()}
CLOSEDNEW={it['number'] for it in allnew if it['state']=='CLOSED'}
def mig(t):
    """Rewrite 'data-ngin #105, #106' as 'algogators #24, #25' for the migrated issues."""
    def one(m): return NEW+' '+re.sub(r'#(\d+)',lambda x:'#%d'%MIG.get(int(x.group(1)),int(x.group(1))),m.group(1)) if all(int(n) in MIG for n in re.findall(r'#(\d+)',m.group(1))) else m.group(0)
    return re.sub(r'%s ((?:#\d+(?:,\s*|;\s*|\s+and\s+|\s+to\s+)?)+)'%OLD,one,t or '')
RANK={((NEW,MIG[n]) if r==OLD and n in MIG else (r,n)):v for (r,n),v in RANK.items()}
for repo in REPOS:
    d=DATA[repo]
    for p in d['pullRequests']['nodes']:
        au=(p['author'] or {}).get('login','?'); b=BP.get((repo,p['number']))
        roll=((p['commits']['nodes'] or [{}])[0].get('commit') or {}).get('statusCheckRollup') or {}
        ci={'SUCCESS':'passing','FAILURE':'failing','ERROR':'failing','PENDING':'running'}.get(roll.get('state'),'no checks ran')
        dep=au=='dependabot'
        rec=dict(id=f"pr-{repo}-{p['number']}",repo=repo,n=p['number'],title=p['title'],url=p['url'],author=NAMES.get(au,au),draft=p['isDraft'],base=p['baseRefName'],head=p['headRefName'],
            created=p['createdAt'][:10],updated=p['updatedAt'][:10],mergeable={'MERGEABLE':'no conflicts','CONFLICTING':'conflicts with base'}.get(p['mergeable'],'not computed'),ci=ci,
            approved=sorted({NAMES.get(r['author']['login'],r['author']['login']) for r in p['reviews']['nodes'] if r['author']}),size=f"+{p['additions']} / -{p['deletions']}, {p['changedFiles']} files",
            disp=(b or {}).get('disp') or ('Dependabot: needs sorting' if dep else 'New: needs a disposition'),note=(b or {}).get('note',''),lane=(b or {}).get('lane') or ('dep' if dep else 'main'))
        if dep and rec['mergeable']=='conflicts with base': rec['disp']='Let Dependabot rebase'
        elif dep and ci=='failing': rec['disp']='Batch after CI fix'
        elif dep and ci=='passing' and rec['mergeable']=='no conflicts' and not b: rec['disp']='Merge in a batch'
        prs.append(rec)
    for it in d['issues']['nodes']:
        k=(repo,it['number']); seen.add(k)
        if repo==NEW and it['number'] in RMIG: seen.add((OLD,RMIG[it['number']]))
        b=B.get(k) or (B.get((OLD,RMIG[it['number']])) if repo==NEW and it['number'] in RMIG else None) or {}
        labels=[l['name'] for l in it['labels']['nodes']]
        if 'tracker' in labels: continue
        prio=next((l for l in labels if re.fullmatch(r'P[0-3]',l)),None)
        proj=next((LP.get(l[9:].strip()) for l in labels if l.startswith('project:')),None)
        sc=parse_status(it['comments']['nodes'])
        bl=[((NEW,MIG[x['number']]) if x['repository']['name']==OLD and x['number'] in MIG else (x['repository']['name'],x['number'])) for x in it['blockedBy']['nodes'] if x['state']=='OPEN']
        bl=list(dict.fromkeys(b for b in bl if not (b[0]==NEW and b[1] in CLOSEDNEW)))  # an archived data-ngin link and its mono-repo replacement map to the same issue
        openb=[f"{r} #{n}" if r!=repo else f"#{n}" for r,n in bl]
        hadb=len(it['blockedBy']['nodes'])>0
        status=None; waiting=''; nxt=''; notes=''
        if sc:
            st=sc.get('status','')
            status=next((s for s in STATUSES if s.lower()==st.lower().rstrip('.')),None)
            if not status and st: notes=st
            waiting=unref(sc.get('waiting','')); nxt=unref(sc.get('next','')); notes=(notes+' '+unref(sc.get('notes',''))).strip()
        mn=migration_note(it['comments']['nodes'],(sc or {}).get('at'))
        if mn: notes=(notes+f' At the move to the mono repo ({mn[0]}): '+unref(mn[1])).strip()
        if not status: status='Blocked' if openb else 'Ready to start'
        if not waiting: waiting=', '.join(openb) or 'Nothing'
        if status=='Blocked' and hadb and not openb: notes=(notes+' All linked blockers on GitHub are now closed.').strip()
        if not prio: status='Needs triage'
        issues.append(dict(id=f'{repo}-{it["number"]}',repo=repo,n=it['number'],title=it['title'],url=it['url'],
            assignees=[NAMES.get(a['login'],a['login']) for a in it['assignees']['nodes']],labels=[l for l in labels if not re.fullmatch(r'P[0-3]',l) and not l.startswith('project:')],
            created=it['createdAt'][:10],updated=it['updatedAt'][:10],prio=prio or 'P2',rank=RANK.get(k,9000+it['number']),lane='qtjr' if proj=='qt' else 'main',
            what=b.get('what','') or first_line(it['body']),tags=[],proposed='',cross=b.get('cross',''),status=status,waiting=waiting,next=nxt,notes=notes,
            noteby=(sc or {}).get('by',''),noteat=(sc or {}).get('at',''),blockers=openb,project=proj or 'unsorted',
            long=b.get('long') or {'description':(it['body'] or '')[:1500]}))
def renum(i):  # a migrated data-ngin issue that is closed shows under its mono-repo number
    if i['repo']==OLD and i['n'] in MIG: return dict(i,repo=NEW,n=MIG[i['n']],id=f"{NEW}-{MIG[i['n']]}",url=f"https://github.com/{ORG}/{NEW}/issues/{MIG[i['n']]}")
    return i
closed=[renum(dict(i,status='Closed',tags=['close'],waiting='Nothing',next='None',order=[])) for i in base['issues'] if (i['repo'],i['n']) not in seen]
# work order per person
PO={'P0':0,'P1':1,'P2':2,'P3':3}; G={'In progress':0,'PR open':0,'Draft PR open':0,'Fix written, no PR':0,'Ready to start':0,'Needs triage':0,'Under review':1,'Blocked':1,'Parked':2}
q={}
for i in issues:
    i['order']=[]
    if i['status'] in ('Reference','Done') or i['project']=='testing': continue
    for a in i['assignees']: q.setdefault(a,[]).append(i)
queues={}
for p in ['Fabio','Oliver','Sivan','Gaby','Pranav','John Riley','Dom']+sorted(set(q)-{'Fabio','Oliver','Sivan','Gaby','Pranav','John Riley','Dom'}):
    lst=sorted(q.get(p,[]),key=lambda i:(PO[i['prio']],G.get(i['status'],1),i['rank']))
    first=next((k for k,i in enumerate(lst) if G.get(i['status'],1)==0),None)
    queues[p]=[dict(id=i['id'],proposed=False,start=(k==first)) for k,i in enumerate(lst)]
    for k,i in enumerate(lst,1): i['order'].append(dict(name=p,n=k,of=len(lst),proposed=False))
projects=base['projects']+([dict(key='unsorted',name='Not yet sorted into a project',desc="New issues without a 'project:' label.")] if any(i['project']=='unsorted' for i in issues) else [])
out=dict(issues=issues+closed,prs=prs,rotation=base['rotation'],people=[],queues=queues,projects=projects,updated=datetime.datetime.now().strftime('%-d %B %Y, %H:%M'))
s=mig(json.dumps(out,separators=(',',':'))).replace('</','<\\/')
t=open(os.path.join(HERE,'template.html')).read()
args=[a for a in sys.argv[1:] if not a.startswith('--')]
dest=args[0] if args else os.path.join(HERE,'algogators-issue-tracker.html')
page=t.replace('__DATA__',s)
if '--standalone' in sys.argv:  # a full web page, for hosting as a file or on Pages
    page='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}[hidden]{display:none!important}</style></head><body>\n'+page+'\n</body></html>'
os.makedirs(os.path.dirname(os.path.abspath(dest)),exist_ok=True)
open(dest,'w').write(page)
from collections import Counter
print(f"Open issues: {len(issues)}. Open PRs: {len(prs)} ({sum(1 for p in prs if p['lane']=='dep')} Dependabot). Status: {dict(Counter(i['status'] for i in issues))}")
def show(title,rows):
    if rows: print(title+':'); [print('  '+r) for r in rows]
ref=lambda i:f"{i['repo']} #{i['n']} {i['title'][:70]}"
show('No priority label (shown as Needs triage)',[ref(i) for i in issues if i['status']=='Needs triage'])
show("No 'project:' label",[ref(i) for i in issues if i['project']=='unsorted'])
show('No assignee',[ref(i) for i in issues if not i['assignees']])
show("No 'Status:' comment yet",[ref(i) for i in issues if not i['noteat']])
show('Not on the order list (trade-ngin #153)',[ref(i) for i in issues if i['rank']>=9000])
show('Marked Blocked but every linked blocker is closed',[ref(i) for i in issues if 'All linked blockers on GitHub are now closed.' in i['notes']])
show('Closed since the last full read',[ref(i) for i in closed if (i['repo'],i['n']) not in {(c['repo'],c['n']) for c in base['issues'] if c['status']=='Closed'}])
show('New pull requests with no disposition',[f"{p['repo']} #{p['n']} {p['title'][:70]} ({p['author']})" for p in prs if p['disp'].startswith('New:')])
print('Wrote '+dest)
