import { useEffect, useState } from 'react';
import { Network, Play, SearchCheck } from 'lucide-react';
import { errorMessage, post } from '../api';
import { useConsole } from '../context';
import { Button, Field, InlineError, Notice, PageIntro } from '../ui';
type Row = {host_id:string; ready:boolean; error:string|null; facts:{python?:string;torch?:string;gpus?:number}; config:{rank:number;nodes:number;master_addr:string;master_port:number;processes:number}};
type Plan = {token:string;rows:Row[];ready:boolean;directory:string;launcher:string};
type Result = {status:string;directory?:string;error?:string;torch?:string};
export function TrainingSetupPage() {
 const {data,can} = useConsole();
 const [selected,setSelected]=useState<string[]>([]);
 const [master,setMaster]=useState(''); const [port,setPort]=useState(29500);
 const [processes,setProcesses]=useState(1); const [device,setDevice]=useState('cuda');
 const [python,setPython]=useState('/usr/bin/python3');const [iface,setIface]=useState('');
 const [install,setInstall]=useState(false);const [version,setVersion]=useState('');const [wheel,setWheel]=useState('cu128');
 const [plan,setPlan]=useState<Plan|null>(null);const [confirmed,setConfirmed]=useState(false);
 const [results,setResults]=useState<Record<string,Result>>({});const [busy,setBusy]=useState('');const [error,setError]=useState('');
 const hosts=data.nodes.filter(n=>n.ssh_connection?.enabled);
 const name=(id:string)=>hosts.find(n=>n.id===id)?.ssh_connection?.display_name||id;
 const signature=JSON.stringify([selected,master,port,processes,device,python,iface,install,version,wheel]);
 useEffect(()=>{setPlan(null);setConfirmed(false);setResults({});},[signature]);
 useEffect(()=>{if(!busy)return;const guard=(e:BeforeUnloadEvent)=>{e.preventDefault();};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard);},[busy]);
 async function preview(){setBusy('正在檢查所選主機');setError('');setPlan(null);setResults({});setConfirmed(false);try{setPlan(await post<Plan>('/training/preview',{host_ids:selected,master_addr:master,master_port:port,processes,device,python,interface:iface,install_version:install?version:'',wheel}));}catch(e){setError(errorMessage(e));}finally{setBusy('');}}
 async function apply(){if(!plan)return;setError('');try{for(const row of plan.rows){if(results[row.host_id])continue;setBusy(`正在套用 ${name(row.host_id)}${install?'，套件安裝可能需要數分鐘':''}`);const result=await post<Result>('/training/apply',{token:plan.token,host_id:row.host_id});setResults(prev=>({...prev,[row.host_id]:result}));if(result.status!=='success')break;}}catch(e){setError(errorMessage(e));}finally{setBusy('');setConfirmed(false);}}
 return <><PageIntro title="分散式訓練設定" description="選取多台主機，一次建立一致的 PyTorch DDP／torchrun 設定與各節點啟動器。"/>
 <Notice>設定完成後，在每台節點執行啟動器並指定相同訓練程式。此頁不會啟動訓練、同步資料或變更防火牆；訓練程式需支援 DDP，各節點須能互相連線。</Notice>
 {!can('hosts.terminal')&&<Notice>需要主機終端操作權限才能套用訓練設定。</Notice>}
 <fieldset className="training-form" disabled={!!busy || !can('hosts.terminal')}><legend><Network size={18}/>節點與訓練環境</legend>
 <div className="training-hosts">{hosts.map(n=><label className="import-host checkbox-row" key={n.id}><input type="checkbox" disabled={!n.ssh_connection?.host_key_sha256 || !!n.ssh_connection?.pending_host_key_sha256} checked={selected.includes(n.id)} onChange={e=>{setSelected(prev=>e.target.checked?[...prev,n.id]:prev.filter(x=>x!==n.id));if(e.target.checked&&!selected.length)setMaster(n.ssh_connection!.hostname);}}/><span><strong>{name(n.id)}</strong><small>{n.ssh_connection?.hostname}{selected.includes(n.id)?` · Rank ${selected.indexOf(n.id)}`:''}{!n.ssh_connection?.host_key_sha256?' · 請先確認 SSH 指紋':''}</small></span></label>)}</div>
 {!hosts.length&&<p>請先在主機監控頁新增 SSH 主機。</p>}
 <p className="muted small">第一台勾選的主機為 Rank 0。主節點位址需填 Rank 0 可供其他節點連線的內網 IP。</p>
 <div className="form-grid"><Field label="主節點 IP／名稱"><input value={master} onChange={e=>setMaster(e.target.value)} required/></Field><Field label="主節點連接埠"><input type="number" min={1024} max={65535} value={port} onChange={e=>setPort(Number(e.target.value))}/></Field>
 <Field label="訓練裝置"><select value={device} onChange={e=>setDevice(e.target.value)}><option value="cuda">NVIDIA GPU（CUDA）</option><option value="cpu">CPU</option></select></Field><Field label="每台主機程序數" hint="GPU 模式不可超過任一節點可用 GPU 數。"><input type="number" min={1} max={128} value={processes} onChange={e=>setProcesses(Number(e.target.value))}/></Field>
 <Field label="Python 絕對路徑" hint="可填既有 conda／venv 的 Python 路徑，所有節點需一致。"><input value={python} onChange={e=>setPython(e.target.value)}/></Field><Field label="網路介面（可留白）" hint="例如 eth0；留白由通訊框架自動選擇。"><input value={iface} onChange={e=>setIface(e.target.value)}/></Field></div>
 <label className="checkbox-row"><input type="checkbox" checked={install} onChange={e=>setInstall(e.target.checked)}/>為每台主機建立獨立 venv 並安裝相同 PyTorch 版本</label>
 {install&&<div className="form-grid"><Field label="PyTorch 版本" hint="填入所選套件來源提供的版本，例如 2.9.0；主機需有 venv、網路與足夠磁碟空間。"><input value={version} placeholder="x.y.z" onChange={e=>setVersion(e.target.value)}/></Field><Field label="官方套件來源"><select value={wheel} onChange={e=>setWheel(e.target.value)}><option value="cpu">CPU</option><option value="cu128">CUDA 12.8</option><option value="cu130">CUDA 13.0</option></select></Field></div>}
 <Button variant="primary" disabled={!selected.length||!master||(install&&!/^\d+\.\d+\.\d+$/.test(version))} onClick={()=>void preview()}><SearchCheck size={16}/>檢查並預覽 {selected.length} 台主機</Button></fieldset>
 <InlineError message={error}/>{busy&&<p role="status" className="notice">{busy}… 請保持此頁開啟。</p>}
 {plan&&<section className="training-plan"><h2>套用預覽</h2><p>每台主機新增：<code className="break-all">{plan.directory}</code></p><p>內含 config.json、launch.py{install?'，以及獨立 venv（下載並安裝指定 PyTorch）':''}。不需 sudo；既有 Python 環境保持原樣。</p>
 <div className="training-hosts">{plan.rows.map(row=><div className="import-host" key={row.host_id}><strong>{name(row.host_id)} · Rank {row.config.rank}</strong><p>{row.ready?'檢查通過':'需處理'} · Python {row.facts.python||'—'} · PyTorch {row.facts.torch||'未安裝'} · GPU {row.facts.gpus??'—'}</p><p>{row.config.nodes} 節點 × {row.config.processes} 程序／節點 → {row.config.master_addr}:{row.config.master_port}</p>{row.error&&<InlineError message={row.error}/>}<p role="status">{results[row.host_id]?.status==='success'?`已套用 · PyTorch ${results[row.host_id].torch}`:results[row.host_id]?.error||'尚未套用'}</p>{results[row.host_id]?.directory&&<code className="break-all">python3 {results[row.host_id].directory}/launch.py /path/to/train.py</code>}</div>)}</div>
 <details><summary>查看啟動器內容</summary><pre className="training-code">{plan.launcher}</pre></details>
 <label className="checkbox-row"><input type="checkbox" checked={confirmed} disabled={!!busy||!plan.ready} onChange={e=>setConfirmed(e.target.checked)}/>確認在所選主機建立上述檔案{install?'並下載安裝套件':''}</label><Button variant="primary" disabled={!!busy||!plan.ready||!confirmed||Object.keys(results).length>0} onClick={()=>void apply()}><Play size={16}/>套用至所選主機</Button><p className="muted small">預覽有效 10 分鐘。部分失敗時保留逐台結果，重新檢查會建立新的設定目錄。</p></section>}
 </>;
}
