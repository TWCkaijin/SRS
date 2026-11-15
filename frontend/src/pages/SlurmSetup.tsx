import { FieldHelp } from '../FieldHelp';
import { useEffect, useRef, useState } from 'react';
import { Network, SearchCheck, Play, ArrowRight } from 'lucide-react';
import { api, errorMessage, post } from '../api';
import { useConsole } from '../context';
import { Button, Field, InlineError, Notice, PageIntro } from '../ui';
type Resource = { host_id:string; name:string; address:string; cpus:number; memory_mb:number; gpu_indices:number[] };
type Package = {name:string;version:string;sha256:string;size:number;uri:string};
type Facts = {hostname?:string;cpus?:number;memory_mb?:number;slurm_version?:string;gpus?:{index:number;uuid:string;name:string}[];packages?:Package[]};
type Check = {host_id:string;facts:Facts;issues:string[]};
type Plan = {token:string;id:string;content_hash:string;ready:boolean;rows:Check[];document:{actions:string[];files:Record<string,Record<string,string>>;packages:Record<string,Package[]>;nodes:Resource[]}};
type Status = {status:string;results:Record<string,{phase:string;status:string;error?:string}>;verification:{ready:boolean;nodes:Record<string,string>;note:string}|null};
const phaseLabels:Record<string,string>={precheck:'檢查管理員權限',install:'安裝套件',installed:'套件已安裝',configure:'寫入叢集設定',configured:'設定已寫入',activate:'啟動服務',activated:'服務已啟動',verify:'驗證節點註冊'};
export function SlurmSetupPage(){
 const {data,can,navigate}=useConsole();
 const hosts=data.nodes.filter(n=>n.ssh_connection?.enabled);
 const [selected,setSelected]=useState<string[]>([]);const [controller,setController]=useState('');
 const [cluster,setCluster]=useState('research');const [partition,setPartition]=useState('research');
 const [user,setUser]=useState('');const [minutes,setMinutes]=useState(1440);
 const [checks,setChecks]=useState<Check[]>([]);const [resources,setResources]=useState<Record<string,Resource>>({});
 const [plan,setPlan]=useState<Plan|null>(null);const [status,setStatus]=useState<Status|null>(null);
 const [passwords,setPasswords]=useState<Record<string,string>>({});const [confirmed,setConfirmed]=useState(false);
 const [busy,setBusy]=useState('');const [error,setError]=useState('');const mounted=useRef(true);
 const name=(id:string)=>hosts.find(h=>h.id===id)?.ssh_connection?.display_name||id;
 const running=status?.status==='running';
 const signature=JSON.stringify([selected,controller,cluster,partition,user,minutes,resources]);
 useEffect(()=>{setPlan(null);setStatus(null);setConfirmed(false);setPasswords({});},[signature]);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 useEffect(()=>{
  if(!plan||!running)return;
  let active=true,pending=false;
  const poll=async()=>{if(pending)return;pending=true;try{const next=await api<Status>(`/slurm/status/${plan.token}`);if(active){setStatus(next);setError('');}}catch(e){if(active)setError(errorMessage(e));}finally{pending=false;}};
  void poll();const timer=window.setInterval(()=>void poll(),2000);
  return()=>{active=false;window.clearInterval(timer);};
 },[plan,running]);
 function select(id:string,checked:boolean){
  const next=checked?[...selected,id]:selected.filter(x=>x!==id);setSelected(next);setChecks([]);
  if(!next.includes(controller))setController(next[0]||'');
  if(!user&&next.length)setUser(hosts.find(n=>n.id===next[0])?.ssh_connection?.username||'');
 }
 async function inspect(){setBusy('正在檢查主機與套件來源');setError('');setPlan(null);try{
  const result=await post<{rows:Check[]}>('/slurm/inspect',{host_ids:selected,training_user:user});
  setChecks(result.rows);setResources(Object.fromEntries(result.rows.map(row=>[row.host_id,{host_id:row.host_id,name:row.facts.hostname?.replaceAll('.','-')||`node-${selected.indexOf(row.host_id)+1}`,address:hosts.find(h=>h.id===row.host_id)!.ssh_connection!.hostname,cpus:row.facts.cpus||1,memory_mb:Math.max(256,(row.facts.memory_mb||1280)-1024),gpu_indices:[]}])));
 }catch(e){setError(errorMessage(e));}finally{setBusy('');}}
 function resource(id:string,patch:Partial<Resource>){setResources(prev=>({...prev,[id]:{...prev[id],...patch}}));}
 async function preview(){setBusy('重新核對環境並產生 Slurm 計畫');setError('');setPlan(null);setConfirmed(false);try{
  const result=await post<Plan>('/slurm/preview',{cluster,partition,controller_id:controller,nodes:selected.map(id=>resources[id]),training_user:user,max_minutes:minutes});
  setPlan(result);setChecks(result.rows);
 }catch(e){setError(errorMessage(e));}finally{setBusy('');}}
 async function apply(){if(!plan)return;setBusy('正在送出確認的計畫');setError('');try{
  await post('/slurm/apply',{token:plan.token,content_hash:plan.content_hash,sudo_passwords:passwords});
  if(mounted.current)setStatus({status:'running',results:{},verification:null});
 }catch(e){setError(errorMessage(e));}finally{setPasswords({});setConfirmed(false);setBusy('');}}
 return <><PageIntro title="Slurm 叢集設定" description="一次選取多台主機，建立由 Slurm 統一排程的研究運算叢集。" action={<Button disabled={!!busy||running} onClick={()=>navigate('/torchrun')}>進階：PyTorch 環境<ArrowRight size={15}/></Button>}/>
 <Notice>新叢集流程支援 Ubuntu 24.04。選擇一台控制節點，其餘節點由 Slurm 分配 CPU、RAM 與你勾選的 GPU；控制節點也可執行工作。</Notice>
 <fieldset className="training-form" disabled={!!busy||running||!can('hosts.terminal')}><legend><Network size={18}/>1. 選擇主機與控制節點<FieldHelp label="主機選取">勾選這次要安裝與設定新 Slurm 叢集的主機，未勾選的主機不會納入。要做跨主機訓練，需至少兩台；單台也可使用 Slurm 排隊與分配 GPU。</FieldHelp></legend>
 <div className="training-hosts">{hosts.map(host=><label className="import-host checkbox-row" key={host.id}><input type="checkbox" checked={selected.includes(host.id)} disabled={!host.ssh_connection?.host_key_sha256||!!host.ssh_connection?.pending_host_key_sha256} onChange={e=>select(host.id,e.target.checked)}/><span><strong>{name(host.id)}</strong><small>{host.ssh_connection?.hostname}{!host.ssh_connection?.host_key_sha256?' · 先確認 SSH 指紋':''}</small></span></label>)}</div>
 {!hosts.length&&<p>請先在主機監控頁新增 SSH 主機。</p>}
 <div className="form-grid"><Field label="控制節點" help="選擇負責接收工作、管理佇列與分配資源的主機。此頁的控制節點也會執行訓練；只選一台主機時，就是單節點叢集。"><select value={controller} onChange={e=>setController(e.target.value)}><option value="" disabled>選擇控制節點</option>{selected.map(id=><option key={id} value={id}>{name(id)}</option>)}</select></Field><Field label="訓練使用者" help="填 Linux 帳號，例如 researcher，不是 SRS 網頁登入帳號。此帳號須在每台所選主機存在，且 UID／GID（使用者與群組的數字 ID）一致。此欄位做一致性檢查，不會建立帳號或限制只有這個帳號能提交工作。" hint="此帳號須在各主機存在，且 UID／GID 一致。"><input value={user} onChange={e=>{setUser(e.target.value);setChecks([]);}} placeholder="researcher"/></Field>
 <Field label="叢集名稱" help="替整個 Slurm 叢集命名，例如 research_250。使用小寫英文字母開頭，可包含小寫字母、數字、底線與連字號，最多 32 字元；不會修改電腦名稱。"><input value={cluster} onChange={e=>setCluster(e.target.value)}/></Field><Field label="Partition 名稱" help="Slurm 的資源分組／工作佇列，不是硬碟分割區。例如 research；此頁會將所選節點加入同一分組。使用小寫字母開頭，最多 32 字元，可含數字、底線與連字號。"><input value={partition} onChange={e=>setPartition(e.target.value)}/></Field><Field label="工作時間上限（分鐘）" help="設定這個 Partition 允許單一工作執行的最長時間，範圍 1～10080 分鐘。1440 分鐘是 24 小時；工作可以申請更短時間，排隊時間不計入。達到工作時間限制會被終止，Slurm 不會自動儲存模型 checkpoint。"><input type="number" min={1} max={10080} value={minutes} onChange={e=>setMinutes(Number(e.target.value))}/></Field></div>
 <Button variant="primary" disabled={!selected.length||!controller||!user} onClick={()=>void inspect()}><SearchCheck size={16}/>檢查 {selected.length} 台主機</Button><FieldHelp label="檢查主機">透過 SSH 讀取環境、硬體、帳號與套件資訊。此步驟不會安裝 Slurm；後續還要配置資源、預覽並確認套用。</FieldHelp>
 </fieldset>
 {checks.length>0&&<fieldset className="training-form" disabled={!!busy||running}><legend>2. 配置各節點資源</legend><p className="muted small">RAM 預留 1 GiB 給系統。GPU 預設不納入，請只勾選本次可交由 Slurm 管理的顯示卡。節點 IP 需能由其他節點互相連線。</p>
 {checks.map(row=>{const node=resources[row.host_id];if(!node)return null;return <section className="import-host" key={row.host_id}><h3>{name(row.host_id)}{controller===row.host_id?' · 控制＋運算節點':' · 運算節點'}</h3><p>{row.facts.slurm_version||'尚未安裝 Slurm'} · {row.facts.cpus??'—'} CPU · {row.facts.memory_mb??'—'} MiB RAM</p>
 {row.issues.map((issue,i)=><InlineError key={i} message={issue}/>)}<div className="form-grid"><Field label={`${name(row.host_id)} 節點名稱`} help="填 Slurm 用來識別此節點的唯一名稱，例如 gpu-node-1。英文字母開頭，可含數字、底線與連字號，最多 63 字元。這不會改掉主機的作業系統 hostname。"><input value={node.name} onChange={e=>resource(row.host_id,{name:e.target.value})}/></Field><Field label={`${name(row.host_id)} 節點 IP`} help="填所有節點都能互相連線的 IP 或可解析主機名稱，不含 http://、帳號或連接埠。若 SSH 使用跳板或外部轉送位址，請改填節點間實際互通的位址。"><input value={node.address} onChange={e=>resource(row.host_id,{address:e.target.value})}/></Field><Field label="CPU 數" help="填這台節點要交由 Slurm 排程的邏輯 CPU 數，不能超過檢查出的數量。這是節點提供的總量，不是每個工作都會取得的數量。"><input type="number" min={1} max={row.facts.cpus||1} value={node.cpus} onChange={e=>resource(row.host_id,{cpus:Number(e.target.value)})}/></Field><Field label="RAM（MiB）" help="填交由 Slurm 排程的記憶體容量。1024 MiB＝1 GiB，預設保留 1 GiB 給系統，至少須保留 512 MiB。這是整台節點可分配的容量，不是單一工作的申請量。"><input type="number" min={256} value={node.memory_mb} onChange={e=>resource(row.host_id,{memory_mb:Number(e.target.value)})}/></Field></div>
 <div className="slurm-gpu-options"><div className="field-label-row"><span className="field-label">納管 GPU</span><FieldHelp label="納管 GPU">只勾選允許此 Slurm 叢集使用的顯示卡。未勾選的 GPU 不納入排程；全部不選時為 CPU 節點。多張 GPU 的顯示記憶體不會自動合併。</FieldHelp></div>{(row.facts.gpus||[]).map(gpu=><label className="checkbox-row" key={gpu.uuid}><input type="checkbox" checked={node.gpu_indices.includes(gpu.index)} onChange={e=>resource(row.host_id,{gpu_indices:e.target.checked?[...node.gpu_indices,gpu.index]:node.gpu_indices.filter(i=>i!==gpu.index)})}/><span>GPU {gpu.index} · {gpu.name}<small className="muted"> {gpu.uuid}</small></span></label>)}{!row.facts.gpus?.length&&<p className="muted small">沒有可選 GPU，此節點使用 CPU。</p>}</div></section>;})}
 <Button variant="primary" disabled={checks.some(r=>!r.facts.hostname)||checks.length!==selected.length} onClick={()=>void preview()}>產生批次設定預覽</Button></fieldset>}
 <InlineError message={error}/>{busy&&<p role="status" className="notice">{busy}…</p>}
 {plan&&<section className="training-plan"><h2>3. 審查並套用</h2><p>{plan.ready?'環境檢查通過，可審查變更後套用。':'尚有前置條件未滿足，套用已停用；修正後請重新檢查。'}</p><ol className="slurm-actions">{plan.document.actions.map(action=><li key={action}>{action}</li>)}</ol>
 <Notice>此次將安裝系統套件、建立服務帳號及共用 MUNGE 金鑰，並開機自動啟動 Slurm。節點需允許彼此使用 TCP 6817／6818 與工作通訊連接埠；此流程不修改防火牆、不同步訓練資料，也不自動提交工作。</Notice>
 {plan.rows.map(row=><details className="import-host" key={row.host_id}><summary>{name(row.host_id)} · 設定檔與 {plan.document.packages[row.host_id]?.length||0} 個新增套件</summary>{Object.entries(plan.document.files[row.host_id]||{}).map(([file,content])=><div key={file}><h4>{file}</h4><pre className="training-code">{content}</pre></div>)}<div className="table-scroll"><table><thead><tr><th>套件</th><th>版本</th><th>SHA256</th></tr></thead><tbody>{plan.document.packages[row.host_id]?.map(pkg=><tr key={pkg.name}><td>{pkg.name}</td><td>{pkg.version}</td><td><code className="break-all">{pkg.sha256}</code></td></tr>)}</tbody></table></div></details>)}
 {!status&&<><div className="form-grid">{selected.map(id=><Field key={id} label={`${name(id)} sudo 密碼`} help="填這台主機目前 SSH 登入帳號執行 sudo 時使用的密碼，不是 SRS 管理員密碼。root 或免密碼 sudo 可留白。只用於本次安裝與服務設定，送出後會清除欄位，不儲存密碼。" hint="已有免密碼 sudo 或 root 可留白。只用於本次作業，不儲存。"><input type="password" autoComplete="new-password" disabled={!plan.ready||!!busy} value={passwords[id]||''} onChange={e=>setPasswords(prev=>({...prev,[id]:e.target.value}))}/></Field>)}</div><label className="checkbox-row"><input type="checkbox" disabled={!plan.ready||!!busy} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已審查各節點設定與套件，確認可在這些主機安裝並啟用新 Slurm 叢集</label><Button variant="primary" disabled={!plan.ready||!confirmed||!!busy||!can('plan.approve')} onClick={()=>void apply()}><Play size={16}/>批次安裝並啟用 Slurm</Button>{!can('plan.approve')&&<p>需要計畫核准權限。</p>}</>}
 {status&&<div className="slurm-progress" aria-live="polite"><h3>{running?'執行中':status.status==='ready'?'Slurm 節點已註冊':status.status==='configured'?'服務已設定，節點仍需檢查':'部分步驟未確認完成'}</h3>{plan.document.nodes.map(node=><div className="import-host" key={node.host_id}><strong>{name(node.host_id)}</strong><p>{phaseLabels[status.results[node.host_id]?.phase]||'等待執行'} · {status.results[node.host_id]?.status==='success'?'完成':status.results[node.host_id]?.status==='running'?'進行中':'待確認'}</p><InlineError message={status.results[node.host_id]?.error||''}/></div>)}{status.verification&&<><p>{status.verification.note}</p><pre className="training-code">{Object.entries(status.verification.nodes).map(([node,state])=>`${node}: ${state}`).join('\n')}</pre></>}{status.status==='ready'&&<Notice>可在控制節點以 sbatch 提交工作、squeue 查看佇列、scancel 取消工作。PyTorch DDP／torchrun 可在 Slurm 工作內執行。</Notice>}</div>}
 <p className="muted small">預覽有效 30 分鐘。執行紀錄 ID：{plan.id}。部分失敗時保留已完成的變更；不會自動回滾或重跑。此版進度由目前 API 程序管理，請保持頁面開啟。</p></section>}
 </>;
}
