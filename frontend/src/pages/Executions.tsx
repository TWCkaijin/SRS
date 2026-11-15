import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Download, FileSearch, Pause, RefreshCw, Terminal } from 'lucide-react';
import { API_BASE, api, errorMessage, post } from '../api';
import { useConsole } from '../context';
import type { Execution, LogLine, LogResponse } from '../types';
import { Badge, Button, EmptyState, InlineError, Loading, Notice, PageIntro, Section } from '../ui';
import { formatDate, json, shortId } from '../utils';

type Stream = 'stdout' | 'stderr' | 'audit';
const emptyLogs: Record<Stream, LogLine[]> = { stdout: [], stderr: [], audit: [] };

function LogPanel({ title, stream, lines, autoScroll }: { title: string; stream: Stream; lines: LogLine[]; autoScroll: boolean }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => { if (autoScroll && container.current) container.current.scrollTop = container.current.scrollHeight; }, [lines, autoScroll]);
  return <section className={`log-panel log-${stream}`}><header><span><Terminal size={15} />{title}</span><span>{lines.length} 筆事件</span></header><div className="log-content" ref={container} tabIndex={0} aria-label={`${title}日誌`}>
    {lines.length ? lines.map(line => <div className="log-line" key={line.id || line.sequence}><span className="log-time" title={line.created_at}>{formatDate(line.created_at).split(' ').at(-1)}</span><pre><span className="log-host">{line.host_id ? `[${line.host_id}] ` : ''}</span>{line.text}</pre></div>) : <p className="log-empty">尚無{title}內容</p>}
  </div></section>;
}

export function ExecutionsPage({ id }: { id?: string }) {
  const { data, navigate, refresh, notify, can } = useConsole();
  const [execution, setExecution] = useState<Execution | null>(null);
  const [logs, setLogs] = useState<Record<Stream, LogLine[]>>(emptyLogs);
  const [error, setError] = useState('');
  const [logError, setLogError] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filter, setFilter] = useState('all');
  useEffect(() => {
    if (!id) return; let active = true; let inFlight = false; const cursors: Record<Stream, number> = { stdout: 0, stderr: 0, audit: 0 }; const controller = new AbortController();
    setExecution(null); setLogs({ stdout: [], stderr: [], audit: [] }); setError(''); setLogError(''); setLoading(true);
    async function load() {
      if (inFlight || !active) return; inFlight = true;
      try { const current = await api<Execution>(`/executions/${encodeURIComponent(id!)}`, { signal: controller.signal }); if (!active) return; setExecution(current); setError('');
        const results = await Promise.allSettled((['stdout', 'stderr', 'audit'] as Stream[]).map(async stream => {
          const response = await api<LogResponse>(`/executions/${encodeURIComponent(id!)}/logs?after=${cursors[stream]}&limit=200&stream=${stream}`, { signal: controller.signal });
          if (!active) return; cursors[stream] = response.next_cursor;
          setLogs(previous => { const seen = new Set(previous[stream].map(line => line.id || String(line.sequence))); return { ...previous, [stream]: [...previous[stream], ...response.items.filter(line => !seen.has(line.id || String(line.sequence)))].slice(-2000) }; });
        }));
        const failed = results.find(result => result.status === 'rejected');
        if (active) setLogError(failed?.status === 'rejected' ? `日誌讀取中斷，將從上次位置重試：${errorMessage(failed.reason)}` : '');
      } catch (err) { if (active && !(err instanceof DOMException && err.name === 'AbortError')) setError(errorMessage(err)); }
      finally { inFlight = false; if (active) setLoading(false); }
    }
    void load(); const timer = window.setInterval(() => void load(), 2000);
    return () => { active = false; controller.abort(); window.clearInterval(timer); };
  }, [id]);
  async function stop() {
    if (!execution) return; setBusy(true); setError('');
    try { await post(`/executions/${execution.id}/stop`); await refresh(); setExecution(await api<Execution>(`/executions/${execution.id}`)); notify('已要求停止後續派送。已開始的遠端工作不會因此被終止。'); } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  async function reconcile() {
    if (!execution) return; setBusy(true); setError('');
    try { setExecution(await post<Execution>(`/executions/${execution.id}/reconcile`)); await refresh(); notify('已排入重新對帳，將核對原始執行紀錄與日誌。'); } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  if (!id) {
    const items = data.executions.filter(item => filter === 'all' || (filter === 'active' ? ['queued', 'running'].includes(item.status) : ['failed', 'unknown'].includes(item.status)));
    return <><PageIntro eyebrow="EXECUTION JOURNAL" title="執行與日誌" description="追蹤已核准計畫的派送、每一步結果，以及標準輸出與錯誤輸出。" /><div className="filter-tabs" role="group" aria-label="執行狀態篩選">{[['all', '全部紀錄'], ['active', '執行中'], ['attention', '需要關注']].map(([key, label]) => <button key={key} className={filter === key ? 'selected' : ''} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>)}</div><Section title="執行紀錄" aside={<span className="muted">{items.length} 筆</span>}>{items.length ? <div className="table-scroll"><table><thead><tr><th>執行紀錄</th><th>來源</th><th>狀態</th><th>日誌完整性</th><th>建立時間</th><th><span className="sr-only">開啟</span></th></tr></thead><tbody>{items.map(item => <tr key={item.id}><td><button className="table-title mono" onClick={() => navigate(`/executions/${item.id}`)}>{shortId(item.id)}</button><span className="table-subtitle">{item.steps?.length || 0} 個步驟</span></td><td>{item.schedule_id ? '唯讀排程' : data.plans.find(plan => plan.id === item.plan_id)?.title || '單次計畫'}</td><td><Badge status={item.status} /></td><td><span className={`small ${item.log_complete ? 'muted' : 'text-amber'}`}>{item.log_complete ? '已收齊' : '尚待確認'}</span></td><td className="muted nowrap">{formatDate(item.created_at)}</td><td><button className="icon-button" aria-label={`開啟執行紀錄 ${shortId(item.id)}`} onClick={() => navigate(`/executions/${item.id}`)}><ArrowRight size={17} /></button></td></tr>)}</tbody></table></div> : <EmptyState title="尚無執行紀錄" description="核准計畫後，執行紀錄與日誌會顯示在這裡。" icon={<FileSearch size={26} />} />}</Section></>;
  }
  if (loading && !execution) return <Loading />;
  if (!execution) return <><InlineError message={error || '找不到執行紀錄。'} /><Button onClick={() => navigate('/executions')}>返回執行清單</Button></>;
  return <><button className="back-link" onClick={() => navigate('/executions')}><ArrowLeft size={15} />所有執行紀錄</button><PageIntro eyebrow={`EXECUTION ${shortId(execution.id)}`} title={data.plans.find(plan => plan.id === execution.plan_id)?.title || (execution.schedule_id ? '排程觀測紀錄' : '執行詳情')} description={`開始 ${formatDate(execution.started_at)} · ${execution.finished_at ? `結束 ${formatDate(execution.finished_at)}` : '尚未結束'}`} action={<Badge status={execution.status} />} />
    <div className="execution-toolbar"><div>{execution.plan_id && <Button onClick={() => navigate(`/plans/${execution.plan_id}`)}><FileSearch size={15} />原始核准計畫</Button>}{execution.schedule_id && <Button onClick={() => navigate(`/schedules/${execution.schedule_id}`)}>來源排程</Button>}</div><div><a className="button button-secondary" href={`${API_BASE}/executions/${encodeURIComponent(execution.id)}/download`} download><Download size={15} />下載完整日誌</a>{can('execution.reconcile') && execution.status === 'unknown' && !!execution.plan_id && <Button onClick={reconcile} busy={busy} disabled={execution.reconciliation_supported !== true}><RefreshCw size={15} />重新對帳</Button>}{can('execution.stop') && ['queued', 'running'].includes(execution.status) && <Button variant="danger" busy={busy} onClick={stop}><Pause size={15} />停止後續派送</Button>}</div></div>
    <InlineError message={error || execution.error || undefined} />{execution.status === 'unknown' && <Notice tone="amber">{execution.reconciliation_supported === true ? '此操作的結果尚未確認。重新對帳會核對原核准內容，嘗試從既有執行紀錄恢復狀態與日誌。' : '此唯讀探測沒有可供對帳的持久紀錄，請另建預檢計畫取得新的觀測。'}</Notice>}{!execution.log_complete && <Notice tone="amber">日誌尚未確認收齊。遠端中斷時會保留目前紀錄，不會把未確認的結果顯示為成功。</Notice>}
    <Section title="步驟進度" className="execution-steps"><div className="execution-step-list">{execution.steps?.map((step, index) => <div key={step.id || step.step_id} className="execution-step"><span className="step-number">{String(index + 1).padStart(2, '0')}</span><div className="execution-step-main"><strong>{step.title || step.step_id}</strong><span>{step.host_id} · {formatDate(step.started_at)}{step.exit_code != null ? ` · exit ${step.exit_code}` : ''}</span>{step.error && <p className="text-red">{step.error}</p>}</div><Badge status={step.status} /></div>)}{!execution.steps?.length && <p className="muted">等待工作程序建立步驟紀錄。</p>}</div></Section>
    <div className="section-heading standalone-heading"><h2>即時日誌</h2><label className="checkbox-row small"><input type="checkbox" checked={autoScroll} onChange={event => setAutoScroll(event.target.checked)} />自動捲動</label></div><InlineError message={logError} /><div className="log-grid"><LogPanel title="標準輸出" stream="stdout" lines={logs.stdout} autoScroll={autoScroll} /><LogPanel title="錯誤輸出" stream="stderr" lines={logs.stderr} autoScroll={autoScroll} /></div><details className="audit-details"><summary>派送與稽核紀錄 <span>{logs.audit.length} 筆</span></summary><LogPanel title="稽核紀錄" stream="audit" lines={logs.audit} autoScroll={autoScroll} /></details><p className="footnote">頁面每個輸出串流保留最近 2,000 筆事件。完整紀錄請使用「下載完整日誌」。停止後續派送不會終止已執行的遠端程序；終止工作需另建取消計畫。</p>
  </>;
}
