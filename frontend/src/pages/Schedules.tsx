import { useEffect, useState, type FormEvent } from 'react';
import { ArrowLeft, ArrowRight, CalendarClock, Edit3, FileCheck2, Pause, Play, Plus, Save, Trash2 } from 'lucide-react';
import { api, errorMessage, patch, post } from '../api';
import { useConsole } from '../context';
import type { Schedule } from '../types';
import { Badge, Button, EmptyState, Field, InlineError, Loading, Notice, PageIntro, PlanDocumentView, ReviewApproval, Section } from '../ui';
import { canEnableSchedule, formatDate, localDateInput, operationLabels, scheduleExpired, shortId } from '../utils';

export function SchedulesPage({ id, editing = false }: { id?: string; editing?: boolean }) {
  const { data, navigate, refresh, notify, can } = useConsole();
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [title, setTitle] = useState('');
  const [operation, setOperation] = useState<Schedule['operation']>('host_snapshot');
  const [hostIds, setHostIds] = useState<string[]>([]);
  const [jobIds, setJobIds] = useState<string[]>([]);
  const [interval, setIntervalValue] = useState('30');
  const [starts, setStarts] = useState(localDateInput());
  const [expires, setExpires] = useState(localDateInput(new Date(Date.now() + 3600000)));
  useEffect(() => {
    setError(''); setSchedule(null); setLoading(false);
    if (!id || id === 'new') { setTitle(''); setOperation('host_snapshot'); setHostIds([]); setJobIds([]); setIntervalValue('30'); setStarts(localDateInput()); setExpires(localDateInput(new Date(Date.now() + 3600000))); return; }
    let active = true; setLoading(true);
    api<Schedule>(`/schedules/${encodeURIComponent(id)}`).then(value => { if (!active) return; setSchedule(value); setTitle(value.title); setOperation(value.operation); setHostIds(value.host_ids); setJobIds(value.scope?.job_ids || []); setIntervalValue(String(value.interval_seconds)); setStarts(localDateInput(new Date(value.starts_at))); setExpires(localDateInput(new Date(value.expires_at))); }).catch(err => active && setError(errorMessage(err))).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [id, editing]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      if (!hostIds.length) throw new Error('請選擇至少一台主機。');
      if (new Date(expires).getTime() <= Math.max(new Date(starts).getTime(), Date.now())) throw new Error('到期時間必須晚於開始時間與現在。');
      if (operation !== 'host_snapshot' && !jobIds.length) throw new Error('工作定時監控必須指定工作範圍。');
      const body = { title, operation, host_ids: hostIds, interval_seconds: Number(interval), starts_at: new Date(starts).toISOString(), expires_at: new Date(expires).toISOString(), scope: { job_ids: operation === 'host_snapshot' ? [] : jobIds } };
      const result = schedule ? await patch<Schedule>(`/schedules/${schedule.id}`, body) : await post<Schedule>('/schedules', body);
      await refresh(); navigate(`/schedules/${result.id}`); notify(schedule ? '排程已修改，舊授權已失效；請重新產生計畫並核准。' : '排程草稿已建立，請產生計畫並核准。');
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  async function remove(item: Schedule) {
    if (!window.confirm(`刪除排程「${item.title}」？將停止後續觀測，既有執行紀錄會保留。`)) return;
    setBusy(true); setError('');
    try {
      await api(`/schedules/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
      await refresh(); if (id) navigate('/schedules'); notify('排程已刪除。');
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  async function action(name: 'plan' | 'approve' | 'enable' | 'disable') {
    if (!schedule) return; setBusy(true); setError('');
    try { await post(`/schedules/${schedule.id}/${name}`, name === 'approve' ? { content_hash: schedule.content_hash } : {}); setSchedule(await api<Schedule>(`/schedules/${schedule.id}`)); await refresh(); notify({ plan: '排程計畫已產生，請審查內容。', approve: '排程已核准，可以在有效期間啟用。', enable: '唯讀排程已啟用。', disable: '排程已停用，不再派送新的觀測。' }[name]); } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  if (!id) return <><PageIntro title="定時監控" description="在指定時段自動取得主機資料，離開頁面後仍會執行。" action={can('schedule.manage') && <Button variant="primary" onClick={() => navigate('/schedules/new')}><Plus size={16} />建立唯讀排程</Button>} /><InlineError message={error} /><Section title="排程清單" aside={<span className="muted">{data.schedules.length} 筆</span>}>{data.schedules.length ? <div className="table-scroll"><table><thead><tr><th>排程</th><th>觀測內容</th><th>狀態</th><th>頻率</th><th>到期時間</th><th><span className="sr-only">開啟</span></th></tr></thead><tbody>{data.schedules.map(item => <tr key={item.id}><td><button className="table-title" onClick={() => navigate(`/schedules/${item.id}`)}>{item.title}</button><span className="table-subtitle">{item.host_ids.length} 台主機 · v{item.revision}</span></td><td>{operationLabels[item.operation]}</td><td><Badge status={scheduleExpired(item) ? 'expired' : item.enabled ? 'enabled' : item.status} /></td><td>每 {item.interval_seconds} 秒</td><td className="nowrap muted">{formatDate(item.expires_at)}</td><td><div className="table-row-actions">{can('schedule.manage') && <button className="icon-button text-red" disabled={busy} aria-label={`刪除排程：${item.title}`} onClick={() => void remove(item)}><Trash2 size={16} /></button>}<button className="icon-button" aria-label={`開啟排程：${item.title}`} onClick={() => navigate(`/schedules/${item.id}`)}><ArrowRight size={17} /></button></div></td></tr>)}</tbody></table></div> : <EmptyState title="目前沒有定時監控" description="建立有明確到期時間的觀測，持續追蹤主機狀態。" icon={<CalendarClock size={28} />} />}</Section></>;
  if (loading) return <Loading />;
  if (id === 'new' || editing) return <><button className="back-link" onClick={() => navigate(schedule ? `/schedules/${schedule.id}` : '/schedules')}><ArrowLeft size={15} />{schedule ? '返回排程' : '所有排程'}</button><PageIntro title={editing ? '修改定時監控' : '建立唯讀排程'} description="排程必須有明確的觀測範圍與到期時間；儲存後仍須審查並核准。" /><Section title="排程設定" className="form-panel"><form onSubmit={save}>{editing && <Notice tone="amber">儲存變更會停用舊授權。更新後需要重新產生計畫、核准並啟用。</Notice>}<div className="form-grid"><Field label="排程名稱" className="span-2"><input required value={title} onChange={event => setTitle(event.target.value)} placeholder="例如：晚間測試主機觀測" maxLength={160} /></Field><Field label="觀測內容"><select value={operation} onChange={event => setOperation(event.target.value as Schedule['operation'])}><option value="host_snapshot">主機與 GPU 快照</option><option value="job_status">指定工作的狀態</option><option value="job_logs">指定工作的日誌</option></select></Field><Field label="間隔（秒）" hint="觀測不重疊執行，中斷後不補跑漏掉的次數。"><input required type="number" min={5} step={5} max={86400} value={interval} onChange={event => setIntervalValue(event.target.value)} /></Field><Field label="開始時間（本機時區）"><input type="datetime-local" required value={starts} onChange={event => setStarts(event.target.value)} /></Field><Field label="到期時間（必填）"><input type="datetime-local" required value={expires} onChange={event => setExpires(event.target.value)} /></Field><fieldset className="field span-2"><legend className="field-label">觀測主機</legend><div className="host-options">{data.nodes.map(node => <label className={`host-option ${hostIds.includes(node.id) ? 'host-selected' : ''} ${!node.enabled ? 'host-disabled' : ''}`} key={node.id}><input type="checkbox" disabled={!node.enabled} checked={hostIds.includes(node.id)} onChange={event => setHostIds(previous => event.target.checked ? [...previous, node.id] : previous.filter(host => host !== node.id))} /><div><strong>{node.alias}</strong><span>{node.enabled ? node.display_name : '排除於試點'}</span></div></label>)}</div></fieldset>{operation !== 'host_snapshot' && <fieldset className="field span-2"><legend className="field-label">指定工作範圍</legend><div className="checkbox-options">{data.jobs.length ? data.jobs.map(job => <label className="checkbox-row" key={job.id}><input type="checkbox" checked={jobIds.includes(job.id)} onChange={event => setJobIds(previous => event.target.checked ? [...previous, job.id] : previous.filter(value => value !== job.id))} />{job.slurm_job_id} · {job.profile} · {job.owner}</label>) : <span className="muted">尚無可觀測的工作。</span>}</div></fieldset>}</div><InlineError message={error} /><div className="form-actions"><Button type="button" onClick={() => navigate('/schedules')}>取消</Button><Button type="submit" variant="primary" busy={busy} disabled={!can('schedule.manage')}><Save size={16} />儲存排程草稿</Button></div></form></Section></>;
  if (!schedule) return <><InlineError message={error || '找不到排程。'} /><Button onClick={() => navigate('/schedules')}>返回排程清單</Button></>;
  const expired = scheduleExpired(schedule);
  const enabled = schedule.enabled === true || ['enabled', 'active'].includes(schedule.status);
  const issues = schedule.issues || [];
  const awaiting = schedule.status === 'awaiting_approval' && !expired && !issues.length;
  return <><button className="back-link" onClick={() => navigate('/schedules')}><ArrowLeft size={15} />所有排程</button><PageIntro eyebrow={`SCHEDULE ${shortId(schedule.id)} / REVISION ${schedule.revision}`} title={schedule.title} description={`${operationLabels[schedule.operation]} · 每 ${schedule.interval_seconds} 秒 · 到期後停止新觀測`} action={<Badge status={expired ? 'expired' : enabled ? 'enabled' : schedule.status} />} /><InlineError message={error} /><div className="review-metadata"><div><span>開始時間</span><strong>{formatDate(schedule.starts_at)}</strong></div><div><span>到期時間</span><strong>{formatDate(schedule.expires_at)}</strong></div><div><span>上次觀測</span><strong>{formatDate(schedule.last_run_at)}</strong></div></div><div className="execution-toolbar"><div>{can('schedule.manage') && <Button onClick={() => navigate(`/schedules/${schedule.id}/edit`)}><Edit3 size={15} />修改排程</Button>}{can('schedule.manage') && <Button variant="danger" busy={busy} onClick={() => void remove(schedule)}><Trash2 size={15} />刪除排程</Button>}{can('schedule.manage') && !expired && <Button onClick={() => action('plan')} busy={busy}><FileCheck2 size={15} />{schedule.plan_document ? '重新產生計畫' : '產生審查計畫'}</Button>}</div><div>{can('schedule.enable') && (enabled ? <Button onClick={() => action('disable')} busy={busy}><Pause size={15} />停用排程</Button> : <Button variant="primary" onClick={() => action('enable')} busy={busy} disabled={!canEnableSchedule(schedule)}><Play size={15} />啟用排程</Button>)}</div></div>{expired && <Notice tone="amber">這份排程已到期。延長時間會建立新版本，必須重新審查與核准。</Notice>}{schedule.plan_document ? <div className="review-layout"><PlanDocumentView document={schedule.plan_document} /><aside className="review-aside"><ReviewApproval kind="schedule" contentHash={schedule.content_hash || ''} eligible={awaiting && can('schedule.approve')} issues={issues} onApprove={() => action('approve')} busy={busy} /><Notice>只執行核准範圍內的唯讀操作。停用排程會停止後續觀測，不終止已開始的訓練工作。</Notice></aside></div> : <Section title="排程審查"><EmptyState title="先產生可審查的計畫" description="完整觀測步驟、目標主機與授權有效期限會一併列出。" icon={<FileCheck2 size={27} />} /></Section>}</>;
}
