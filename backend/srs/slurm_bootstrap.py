"""Fixed Ubuntu bootstrap programs. Only executed after a Slurm plan is confirmed."""

PACKAGE_PLAN = r"""
def package_plan():
 import apt,re
 cache=apt.Cache()
 required=['slurmctld','slurmd','slurm-client','munge']
 for name in required:
  if name not in cache or not cache[name].candidate:raise RuntimeError('apt metadata missing')
  cache[name].mark_install(auto_fix=True,auto_inst=True,from_user=True)
 manifest=[]
 for pkg in cache.get_changes():
  if pkg.is_installed or pkg.marked_delete:raise RuntimeError('existing package changes required')
  v=pkg.candidate
  if not v or not v.origins or not all(o.trusted and o.origin=='Ubuntu' for o in v.origins):raise RuntimeError('untrusted package source')
  sha=v.record.get('SHA256','')
  if not re.fullmatch('[a-f0-9]{64}',sha):raise RuntimeError('package digest missing')
  if re.match(r'(nvidia|cuda|libnvidia|libcuda|linux-|libpam|openssh|ufw|iptables|firewalld)',pkg.name):raise RuntimeError('out of scope package')
  manifest.append({'name':pkg.name,'version':v.version,'sha256':sha,'size':v.size,'uri':v.uri})
 return sorted(manifest,key=lambda p:p['name']),cache
"""
EXTRA_PROBE = r"""
import sys,time,grp
r['observed_time']=time.time()
r['os']=platform.freedesktop_os_release()
r['cgroup2']=pathlib.Path('/sys/fs/cgroup/cgroup.controllers').exists()
r['systemd']=pathlib.Path('/run/systemd/system').exists()
r['munge_key_exists']=pathlib.Path('/etc/munge/munge.key').exists()
r['policy_exists']=os.path.lexists('/usr/sbin/policy-rc.d')
r['accounts']={}
for name in ('slurm','munge',sys.argv[1]):
 try:
  entry=pwd.getpwnam(name);r['accounts'][name]={'uid':entry.pw_uid,'gid':entry.pw_gid}
 except KeyError:r['accounts'][name]=None
r['reserved_ids']={}
for number in (64030,64031):
 try:r['reserved_ids'][str(number)]=pwd.getpwuid(number).pw_name
 except KeyError:pass
 try:r['reserved_ids']['g'+str(number)]=grp.getgrgid(number).gr_name
 except KeyError:pass
try:r['packages'],_=package_plan();r['package_error']=None
except Exception:r['packages']=[];r['package_error']='無法產生只新增套件的 Ubuntu 簽章套件清單。請檢查 python3-apt、APT 索引、來源與相依套件。'
print(json.dumps(r))
"""
BOOTSTRAP = (
    PACKAGE_PLAN
    + r"""
import base64,hashlib,json,os,pathlib,pwd,grp,subprocess,sys,stat,fcntl
p=json.loads(sys.stdin.read().splitlines()[-1])
if os.geteuid()!=0:raise RuntimeError('root required')
import re
if not re.fullmatch('[a-f0-9-]{36}',p['id']):raise RuntimeError('invalid plan id')
if set(p['files'])!={'/etc/slurm/slurm.conf','/etc/slurm/gres.conf','/etc/slurm/cgroup.conf','/etc/systemd/system/slurmd.service.d/srs.conf'}:raise RuntimeError('invalid destination')
lock=open('/run/lock/srs-slurm-setup.lock','a')
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
root=pathlib.Path('/var/lib/srs-slurm')
if root.exists() and (root.is_symlink() or root.stat().st_uid!=0 or root.stat().st_mode&0o022):raise RuntimeError('unsafe state directory')
root.mkdir(mode=0o700,exist_ok=True)
marker=root/(p['id']+'.json')
def run(args,timeout=60):
 return subprocess.run(args,check=True,capture_output=True,text=True,timeout=timeout,env={**os.environ,'DEBIAN_FRONTEND':'noninteractive','LC_ALL':'C'})
def safe_dir(path,mode=0o755,owner_uid=0):
 path=pathlib.Path(path)
 for parent in [path,*path.parents]:
  if parent.exists() and (parent.is_symlink() or parent.stat().st_uid not in (0,owner_uid) or parent.stat().st_mode&0o022):raise RuntimeError('unsafe directory')
 path.mkdir(parents=True,exist_ok=True,mode=mode)
 return path
def fresh_write(path,data,mode=0o644,owner=None):
 path=pathlib.Path(path)
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
 with os.fdopen(fd,'wb') as stream:
  stream.write(data);stream.flush();os.fsync(stream.fileno())
 if owner:os.chown(path,*owner)
if p['phase']=='install':
 if marker.exists():raise RuntimeError('already attempted')
 if any(os.path.lexists(x) for x in ['/etc/slurm/slurm.conf','/etc/slurm-llnl/slurm.conf','/usr/local/etc/slurm.conf','/etc/munge/munge.key','/usr/sbin/policy-rc.d']):raise RuntimeError('existing setup')
 for service in ('slurmctld','slurmd','munge'):
  if subprocess.run(['systemctl','is-active','--quiet',service]).returncode==0:raise RuntimeError('service active')
 account=pwd.getpwnam(p['training_user'])
 if {'uid':account.pw_uid,'gid':account.pw_gid}!=p['expected_facts']['accounts'][p['training_user']]:raise RuntimeError('training identity changed')
 import platform,csv,io
 selected=[int(x) for x in re.findall(r'/dev/nvidia(\d+)',p['files']['/etc/slurm/gres.conf'])]
 if selected:
  reading=run(['nvidia-smi','--query-gpu=index,uuid','--format=csv,noheader,nounits'])
  current={int(a[0]):a[1] for a in csv.reader(io.StringIO(reading.stdout),skipinitialspace=True)}
  expected={a['index']:a['uuid'] for a in p['expected_facts']['gpus']}
  if any(current.get(i)!=expected.get(i) or not pathlib.Path('/dev/nvidia'+str(i)).is_char_device() for i in selected):raise RuntimeError('GPU identity changed')
 if platform.freedesktop_os_release().get('VERSION_ID')!='24.04' or platform.freedesktop_os_release().get('ID')!='ubuntu' or not pathlib.Path('/sys/fs/cgroup/cgroup.controllers').exists():raise RuntimeError('platform changed')
 if platform.node().split('.')[0]!=p['expected_facts']['hostname']:raise RuntimeError('hostname changed')
 manifest,cache=package_plan()
 if manifest!=p['packages']:raise RuntimeError('package plan changed')
 for name,number in [('slurm',64030),('munge',64031)]:
  try:
   entry=pwd.getpwnam(name)
   if (entry.pw_uid,entry.pw_gid)!=(number,number):raise RuntimeError('service identity mismatch')
  except KeyError:
   try:pwd.getpwuid(number);raise RuntimeError('UID occupied')
   except KeyError:pass
   try:
    group=grp.getgrnam(name)
    if group.gr_gid!=number:raise RuntimeError('GID mismatch')
   except KeyError:run(['groupadd','--system','--gid',str(number),name])
   run(['useradd','--system','--uid',str(number),'--gid',name,'--home-dir','/var/lib/'+name,'--shell','/usr/sbin/nologin',name])
 fresh_write(marker,json.dumps({'phase':'install_requested','files':p['files']}).encode(),0o600)
 stage=safe_dir(root/p['id'],0o700)
 paths=[]
 for pkg in manifest:
  version=cache[pkg['name']].candidate
  filename=pathlib.Path(version.fetch_binary(destdir=str(stage)))
  if filename.parent!=stage or hashlib.sha256(filename.read_bytes()).hexdigest()!=pkg['sha256']:raise RuntimeError('digest mismatch')
  paths.append(str(filename))
 policy=pathlib.Path('/usr/sbin/policy-rc.d')
 fresh_write(policy,b'#!/bin/sh\nexit 101\n',0o755)
 try:
  if paths:run(['dpkg','--install',*paths],timeout=720)
 finally:
  if policy.read_bytes()==b'#!/bin/sh\nexit 101\n':policy.unlink()
 marker.write_text(json.dumps({'phase':'installed','files':p['files']}))
 print(json.dumps({'phase':'installed'}))
elif p['phase']=='configure':
 state=json.loads(marker.read_text())
 if state['phase']!='installed' or state['files']!=p['files']:raise RuntimeError('phase mismatch')
 for destination,content in p['files'].items():
  safe_dir(pathlib.Path(destination).parent)
  fresh_write(destination,content.encode())
 munge=pwd.getpwnam('munge');slurm=pwd.getpwnam('slurm')
 keydir=pathlib.Path('/etc/munge')
 if keydir.is_symlink() or keydir.stat().st_uid not in (0,munge.pw_uid):raise RuntimeError('unsafe munge directory')
 key=keydir/'munge.key'
 # The package-created key belongs to this new installation; never replace a pre-existing cluster key.
 if key.exists():
  if key.is_symlink() or not stat.S_ISREG(key.stat().st_mode):raise RuntimeError('unsafe key')
  backup=root/(p['id']+'.package-munge-key')
  fresh_write(backup,key.read_bytes(),0o600)
  key.unlink()
 fresh_write(key,base64.b64decode(p['munge_key']),0o400,(munge.pw_uid,munge.pw_gid))
 for path in ('/var/spool/slurmctld','/var/spool/slurmd','/var/log/slurm'):
  folder=safe_dir(path,owner_uid=slurm.pw_uid);os.chown(folder,slurm.pw_uid,slurm.pw_gid)
 marker.write_text(json.dumps({'phase':'configured','files':p['files']}))
 print(json.dumps({'phase':'configured'}))
elif p['phase']=='activate':
 state=json.loads(marker.read_text())
 if state['phase']!='configured' or state['files']!=p['files']:raise RuntimeError('phase mismatch')
 for name,content in p['files'].items():
  if pathlib.Path(name).read_text()!=content:raise RuntimeError('configuration changed')
 run(['systemctl','daemon-reload'])
 run(['systemctl','enable','--now','munge'])
 if p['controller']:run(['systemctl','enable','--now','slurmctld'])
 run(['systemctl','enable','--now','slurmd'])
 marker.write_text(json.dumps({'phase':'activated','files':p['files']}))
 print(json.dumps({'phase':'activated'}))
else:raise RuntimeError('unknown phase')
"""
)
