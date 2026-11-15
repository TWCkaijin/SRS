import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Download, FileBox, FileSearch, Square } from 'lucide-react';
import { API_BASE, api, errorMessage, list, post } from '../api';
import { useConsole } from '../context';
import type { Artifact, Draft, Job } from '../types';
import { Badge, Button, CodeBlock, EmptyState, InlineError, KeyValue, Loading, Notice, PageIntro, Section } from '../ui';
import { formatDate, json, profileLabels, shortId } from '../utils';

export function JobsPage({ id }: { id?: string }) {
  const { data, navigate, refresh, notify, can } = useConsole();
  const [job, setJob] = useState<Job | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!id) return; let active = true; setLoading(true); setError('');
    Promise.all([api<Job>(`/jobs/${encodeURIComponent(id)}`), list<Artifact>(`/jobs/${encodeURIComponent(id)}/artifacts`)]).then(([value, files]) => { if (active) { setJob(value); setArtifacts(files); } }).catch(err => active && setError(errorMessage(err))).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [id, data.jobs]);
  async function collectionDraft() {
    if (!job) return; setBusy(true); setError('');
    try { const draft = await post<Draft>('/drafts', { title: `收集工作 ${job.slurm_job_id} 成果`, operation: 'collect_results', host_ids: ['225'], parameters: { job_id: job.id } }); await refresh(); navigate(`/drafts/${draft.id}`); notify('成果收集草稿已建立。請審查計畫並核准。'); } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  async function cancellationDraft() {
    if (!job) return; setBusy(true); setError('');
    try { const hostIds = Array.isArray(job.host_ids) ? job.host_ids : []; const draft = await post<Draft>('/drafts', { title: `取消工作 ${job.slurm_job_id}`, operation: 'cancel_job', host_ids: hostIds, parameters: { job_id: job.id } }); await refresh(); navigate(`/drafts/${draft.id}`); notify('取消工作草稿已建立。請確認主機並產生計畫，核准後才會取消。'); } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  if (!id) return <><PageIntro eyebrow="WORKLOADS & ARTIFACTS" title="工作與結果" description="查看 Slurm 工作的快取狀態、執行紀錄與已登錄的成果檔案。" /><Section title="運算工作" aside={<span className="muted">{data.jobs.length} 筆</span>}>{data.jobs.length ? <div className="table-scroll"><table><thead><tr><th>Slurm 工作</th><th>專案</th><th>執行模式</th><th>擁有者</th><th>狀態</th><th>建立時間</th><th><span className="sr-only">開啟</span></th></tr></thead><tbody>{data.jobs.map(item => <tr key={item.id}><td><button className="table-title mono" onClick={() => navigate(`/jobs/${item.id}`)}>#{item.slurm_job_id}</button><span className="table-subtitle mono">{shortId(item.id)}</span></td><td>{data.projects.find(project => project.id === item.project_id)?.name || item.project_id}</td><td>{profileLabels[item.profile] || item.profile}</td><td>{item.owner}</td><td><Badge status={item.status} /></td><td className="nowrap muted">{formatDate(item.created_at)}</td><td><button className="icon-button" aria-label={`開啟工作 ${item.slurm_job_id}`} onClick={() => navigate(`/jobs/${item.id}`)}><ArrowRight size={17} /></button></td></tr>)}</tbody></table></div> : <EmptyState title="尚無運算工作" description="提交工作計畫經核准執行後，工作與成果會在這裡呈現。" icon={<FileBox size={27} />} />}</Section></>;
  if (loading && !job) return <Loading />;
  if (!job) return <><InlineError message={error || '找不到工作。'} /><Button onClick={() => navigate('/jobs')}>返回工作清單</Button></>;
  return <><button className="back-link" onClick={() => navigate('/jobs')}><ArrowLeft size={15} />所有工作</button><PageIntro eyebrow={`SLURM JOB ${job.slurm_job_id}`} title={data.projects.find(project => project.id === job.project_id)?.name || `工作 #${job.slurm_job_id}`} description={`${profileLabels[job.profile] || job.profile} · ${job.owner} · 建立於 ${formatDate(job.created_at)}`} action={<Badge status={job.status} />} /><InlineError message={error} /><div className="execution-toolbar"><div>{job.execution_id && <Button onClick={() => navigate(`/executions/${job.execution_id}`)}><FileSearch size={15} />執行與日誌</Button>}</div><div>{can('draft.manage') && <Button onClick={collectionDraft} busy={busy}><FileBox size={15} />建立收集成果草稿</Button>}{can('draft.manage') && !['COMPLETED', 'FAILED', 'CANCELLED', 'completed', 'failed', 'cancelled', 'canceled'].includes(job.status) && <Button onClick={cancellationDraft} busy={busy}><Square size={14} />建立取消計畫草稿</Button>}</div></div><Section title="成果與報告">{artifacts.length ? <div className="artifact-list">{artifacts.map(artifact => <div className="artifact-row" key={artifact.id}><div className="artifact-icon"><FileBox size={21} /></div><div><strong>{artifact.name || artifact.filename || artifact.id}</strong><span className="muted small">{artifact.content_type || '已登錄成果檔案'}{typeof (artifact.size_bytes ?? artifact.size) === 'number' ? ` · ${((artifact.size_bytes ?? artifact.size ?? 0) / 1024).toFixed(1)} KB` : ''}</span></div><a className="button button-secondary" href={`${API_BASE}/artifacts/${encodeURIComponent(artifact.id)}/download`} download><Download size={15} />下載</a></div>)}</div> : <EmptyState title="尚無已登錄的成果" description="工作完成後，由後端登錄可下載的結果與報告。" />}</Section><details className="disclosure"><summary>檢視完整工作紀錄</summary><CodeBlock value={json(job)} label="快取工作資料" /></details></>;
}
