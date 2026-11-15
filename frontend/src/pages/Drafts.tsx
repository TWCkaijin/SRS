import { useEffect, useState, type FormEvent } from 'react';
import { ArrowLeft, ArrowRight, FileCode2, FilePlus2, Plus, Save } from 'lucide-react';
import { api, errorMessage, patch, post } from '../api';
import { useConsole } from '../context';
import type { Draft, Operation, Plan } from '../types';
import { Button, EmptyState, Field, InlineError, Loading, Notice, PageIntro, Section } from '../ui';
import { formatDate, operationLabels, profileLabels, shortId } from '../utils';

export function DraftsPage({ id }: { id?: string }) {
  const { data, navigate, refresh, notify, can } = useConsole();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [title, setTitle] = useState('');
  const [operation, setOperation] = useState<Operation>('preflight');
  const [hosts, setHosts] = useState<string[]>([]);
  const [projectId, setProjectId] = useState('');
  const [profile, setProfile] = useState('cpu');
  const [cpus, setCpus] = useState('1');
  const [memory, setMemory] = useState('2048');
  const [minutes, setMinutes] = useState('5');
  const [args, setArgs] = useState('[]');
  const [jobId, setJobId] = useState('');
  const [filenames, setFilenames] = useState<string[]>(['rank-0.json']);
  const [component, setComponent] = useState('slurm');
  const [advanced, setAdvanced] = useState('{}');
  useEffect(() => {
    setError(''); setDraft(null);
    if (id && id !== 'new') {
      let active = true; setLoading(true);
      api<Draft>(`/drafts/${encodeURIComponent(id)}`).then(value => { if (!active) return; setDraft(value); setTitle(value.title); setOperation(value.operation); setHosts(value.host_ids); setProjectId(String(value.parameters.project_id || '')); setProfile(String(value.parameters.profile || 'cpu')); setCpus(String(value.parameters.cpus || 1)); setMemory(String(value.parameters.memory_mb || 2048)); setMinutes(String(value.parameters.time_limit_minutes || 5)); setArgs(JSON.stringify(value.parameters.args || [])); setJobId(String(value.parameters.job_id || '')); setFilenames(Array.isArray(value.parameters.filenames) ? value.parameters.filenames.filter((name): name is string => typeof name === 'string' && ['rank-0.json', 'rank-1.json'].includes(name)) : ['rank-0.json']); setComponent(String(value.parameters.component || 'slurm')); setAdvanced(JSON.stringify(value.parameters, null, 2)); }).catch(err => active && setError(errorMessage(err))).finally(() => active && setLoading(false));
      return () => { active = false; };
    }
    setTitle(''); setOperation('preflight'); setHosts([]); setProjectId(''); setProfile('cpu'); setCpus('1'); setMemory('2048'); setMinutes('5'); setArgs('[]'); setJobId(''); setFilenames(['rank-0.json']); setComponent('slurm'); setAdvanced('{}');
  }, [id]);
  useEffect(() => { if (!projectId && data.projects.length && (!id || id === 'new')) setProjectId(data.projects[0].id); }, [data.projects, projectId, id]);

  async function save(event: FormEvent<HTMLFormElement>, generate = false) {
    event.preventDefault(); setError(''); setBusy(true);
    try {
      let parameters: Record<string, unknown> = {};
      if (operation === 'submit_job') { const parsed = JSON.parse(args); if (!Array.isArray(parsed) || parsed.some(arg => typeof arg !== 'string')) throw new Error('工作參數請填入 JSON 字串陣列，例如 ["--epochs", "5"]。'); parameters = { project_id: projectId, profile, cpus: Number(cpus), memory_mb: Number(memory), time_limit_minutes: Number(minutes), args: parsed }; }
      else if (['cancel_job', 'collect_results'].includes(operation)) parameters = { job_id: jobId };
      else if (operation === 'cleanup') { if (!filenames.length) throw new Error('請選擇至少一個已收集的成果檔案。'); parameters = { job_id: jobId, filenames }; }
      else if (operation === 'deploy') parameters = { component };
      else if (operation === 'write_config') { const parsed = JSON.parse(advanced); if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('參數必須是 JSON 物件。'); parameters = parsed; }
      if (!hosts.length) throw new Error('請選擇至少一台試點主機。');
      const payload = { title, operation, host_ids: hosts, parameters };
      const saved = draft ? await patch<Draft>(`/drafts/${draft.id}`, payload) : await post<Draft>('/drafts', payload);
      if (generate) { const plan = await post<Plan>(`/drafts/${saved.id}/plan`); await refresh(); navigate(`/plans/${plan.id}`); notify('計畫已產生，請審查完整步驟與依據。'); }
      else { await refresh(); navigate(`/drafts/${saved.id}`); setDraft(saved); notify('草稿已儲存，尚未執行任何遠端操作。'); }
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }

  if (!id) return <><PageIntro eyebrow="WORKSPACE / DRAFTS" title="工作草稿" description="先描述你要做的事情。草稿不會執行，產生計畫後才能送交審查。" action={can('draft.manage') && <Button variant="primary" onClick={() => navigate('/drafts/new')}><Plus size={16} />建立草稿</Button>} /><Section title="所有草稿" aside={<span className="muted">{data.drafts.length} 份</span>}>{data.drafts.length ? <div className="table-scroll"><table><thead><tr><th>草稿</th><th>操作</th><th>目標主機</th><th>建立時間</th><th><span className="sr-only">開啟</span></th></tr></thead><tbody>{data.drafts.map(item => <tr key={item.id}><td><button className="table-title" onClick={() => navigate(`/drafts/${item.id}`)}>{item.title}</button><span className="table-subtitle mono">{shortId(item.id)}</span></td><td>{operationLabels[item.operation] || item.operation}</td><td>{item.host_ids.map(host => data.nodes.find(node => node.id === host)?.alias || host).join('、')}</td><td className="muted nowrap">{formatDate(item.created_at)}</td><td><button className="icon-button" aria-label={`開啟草稿：${item.title}`} onClick={() => navigate(`/drafts/${item.id}`)}><ArrowRight size={17} /></button></td></tr>)}</tbody></table></div> : <EmptyState title="還沒有工作草稿" description="從環境盤點開始，先取得可用於計畫審查的實際依據。" icon={<FilePlus2 size={27} />} action={can('draft.manage') && <Button onClick={() => navigate('/drafts/new')}><Plus size={15} />建立第一份草稿</Button>} />}</Section></>;
  if (loading) return <Loading />;
  return <><button className="back-link" onClick={() => navigate('/drafts')}><ArrowLeft size={15} />所有草稿</button><PageIntro eyebrow="DRAFT → REVIEW → APPROVE" title={draft ? '編輯工作草稿' : '建立工作草稿'} description="設定目標與資源需求。下一步會產生完整執行計畫，供你檢查後再核准。" /><Section title="工作設定" className="form-panel"><form onSubmit={event => save(event, (event.nativeEvent as SubmitEvent).submitter?.getAttribute('data-action') === 'plan')}><div className="form-grid"><Field label="草稿名稱" className="span-2"><input value={title} onChange={event => setTitle(event.target.value)} required placeholder="例如：225 環境盤點" maxLength={160} /></Field><Field label="操作類型" className="span-2"><select value={operation} onChange={event => { const next = event.target.value as Operation; setOperation(next); if (['collect_results', 'cleanup'].includes(next)) setHosts(['225']); }}>{Object.entries(operationLabels).filter(([key]) => ['preflight', 'submit_job', 'cancel_job', 'collect_results', 'deploy', 'write_config', 'cleanup'].includes(key)).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></Field>
      <fieldset className="field span-2"><legend className="field-label">目標主機</legend><div className="host-options">{data.nodes.map(node => <label key={node.id} className={`host-option ${hosts.includes(node.id) ? 'host-selected' : ''} ${!node.enabled ? 'host-disabled' : ''}`}><input type="checkbox" checked={hosts.includes(node.id)} disabled={!node.enabled || (['collect_results', 'cleanup'].includes(operation) && node.id !== '225')} onChange={event => setHosts(previous => event.target.checked ? [...previous, node.id] : previous.filter(host => host !== node.id))} /><div><strong>{node.alias}</strong><span>{node.enabled ? node.hardware?.cpu_model || node.display_name : '已排除，不可選取'}</span></div></label>)}</div></fieldset>
      {operation === 'submit_job' && <><Field label="研究專案"><select value={projectId} onChange={event => setProjectId(event.target.value)} required><option value="" disabled>請選擇專案</option>{data.projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></Field><Field label="執行模式"><select value={profile} onChange={event => setProfile(event.target.value)}>{Object.entries(profileLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field><div className="form-grid three-fields span-2"><Field label="CPU 數量"><input type="number" min="1" max="128" value={cpus} onChange={event => setCpus(event.target.value)} required /></Field><Field label="記憶體（MB）"><input type="number" min="128" step="128" value={memory} onChange={event => setMemory(event.target.value)} required /></Field><Field label="時間上限（分鐘）"><input type="number" min="1" max="1440" value={minutes} onChange={event => setMinutes(event.target.value)} required /></Field></div><Field label="程式參數" className="span-2" hint='JSON 字串陣列，例如 ["--epochs", "5"]。不填 shell 命令。'><textarea className="mono" rows={3} value={args} onChange={event => setArgs(event.target.value)} spellCheck={false} /></Field>{['ddp', 'fsdp2'].includes(profile) && <div className="span-2"><Notice tone="amber">跨機測試需要兩台已保留的 GPU 主機。計畫會檢查主機、資源與先前盤點依據。</Notice></div>}</>}
      {['cancel_job', 'collect_results', 'cleanup'].includes(operation) && <Field label={operation === 'cancel_job' ? '要取消的工作' : '工作成果來源'} className="span-2"><select value={jobId} onChange={event => setJobId(event.target.value)} required><option value="">選擇工作</option>{data.jobs.map(job => <option value={job.id} key={job.id}>{job.slurm_job_id} · {job.profile} · {job.owner}</option>)}</select></Field>}
      {operation === 'collect_results' && <div className="span-2"><Notice>從控制節點 225 讀取此工作固定的 rank-0.json 與 rank-1.json，每檔上限 64 KiB。核准後才會收集。</Notice></div>}
      {operation === 'cleanup' && <><div className="span-2"><Notice tone="amber">限已結束且已收集成果的工作。計畫會核對內容摘要，備份後才移除選定檔案。</Notice></div><fieldset className="field span-2"><legend className="field-label">要清理的已收集成果</legend><div className="checkbox-options">{['rank-0.json', 'rank-1.json'].map(name => <label className="checkbox-row" key={name}><input type="checkbox" checked={filenames.includes(name)} onChange={event => setFilenames(previous => event.target.checked ? [...previous, name] : previous.filter(item => item !== name))} />{name}</label>)}</div></fieldset></>}
      {operation === 'deploy' && <Field label="部署元件" className="span-2"><select value={component} onChange={event => setComponent(event.target.value)}><option value="slurm">Slurm 排程服務</option><option value="apptainer">Apptainer 容器環境</option><option value="platform">控制平台</option></select></Field>}
      {operation === 'write_config' && <><div className="span-2"><Notice tone="amber">設定變更必須有已儲存的驗證依據。缺少實際檔案內容或差異時，計畫不能核准。</Notice></div><Field label="操作參數" className="span-2" hint="參數將交由後端驗證並產生可審查的差異。"><textarea rows={8} className="mono" value={advanced} onChange={event => setAdvanced(event.target.value)} spellCheck={false} /></Field></>}
    </div><InlineError message={error} /><div className="form-actions"><Button type="button" onClick={() => navigate('/drafts')}>取消</Button><Button type="submit" busy={busy} disabled={!can('draft.manage')}><Save size={16} />儲存草稿</Button><Button type="submit" data-action="plan" variant="primary" busy={busy} disabled={!can('draft.manage')}><FileCode2 size={16} />產生審查計畫<ArrowRight size={15} /></Button></div></form></Section></>;
}
