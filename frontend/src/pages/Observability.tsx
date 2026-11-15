import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../api';
import { useConsole } from '../context';
import type { MonitorState, Node } from '../types';
import { Badge, Button, EmptyState, Field, InlineError, Loading, Modal, PageIntro, Section } from '../ui';
import { formatDate } from '../utils';
import { HostAccounts } from './HostAccess';

export type ActivitySnapshot = NonNullable<Node['snapshot']> & {
  login_users?: { username: string; terminal: string; login_time: string; origin: string }[];
  login_users_error?: string | null;
  gpu_processes?: { gpu_uuid: string; pid: number; name: string; username: string | null; memory_mb: number | null }[];
  gpu_process_error?: string | null;
};
type Sample = { id: string; host_id: string; collected_at: string; observer: string; result: { status: string; error: string | null; snapshot: ActivitySnapshot | null }; cpu_peak?: number; memory_peak?: number; sample_count?: number };
type Alert = { id: string; host_id: string; rule: string; message: string; opened_at: string; last_seen_at: string; resolved_at: string | null };
type Overview = { hosts: { host_id: string; alias: string; latest: Sample | null }[]; alerts: Alert[]; web_sessions: { username: string; expires_at: string }[]; events: { id: number; actor: string; action: string; created_at: string }[]; retention_days: number };
function useOverview() {
  const [value, setValue] = useState<Overview | null>(null), [error, setError] = useState('');
  useEffect(() => { let active = true, fetching = false; const load = async () => { if (fetching) return; fetching = true; try { const result = await api<Overview>('/observability'); if (active) { setValue(result); setError(''); } } catch (e) { if (active) setError(errorMessage(e)); } finally { fetching = false; } }; void load(); const timer = window.setInterval(load, 15000); return () => { active = false; clearInterval(timer); }; }, []);
  return { value, error };
}
const recent = (sample: Sample | null) => sample && Date.now() - Date.parse(sample.collected_at) < 60000;
export function HostActivity({ snapshot, defaultOpen = false }: { snapshot: ActivitySnapshot | null; defaultOpen?: boolean }) {
  return <details className="host-activity" open={defaultOpen}><summary>GPU 程序</summary>
    {!snapshot ? <p>啟動監控後顯示，尚無觀測資料。</p> : <>
      <h3>GPU 計算程序</h3><InlineError message={snapshot.gpu_process_error || ''} />
      {snapshot.gpu_processes === undefined ? <p>這筆舊觀測尚未包含程序，請重新開始監控。</p> : snapshot.gpu_processes.length ? <div className="table-scroll"><table><thead><tr><th>GPU</th><th>PID / 使用者</th><th>程序</th><th>VRAM</th></tr></thead><tbody>{snapshot.gpu_processes.map((p, i) => <tr key={i}><td>{snapshot.gpus?.find(g => g.uuid === p.gpu_uuid)?.index ?? p.gpu_uuid}</td><td>{p.pid} / {p.username || '無法讀取'}</td><td>{p.name}</td><td>{p.memory_mb == null ? '不支援回報' : `${p.memory_mb} MiB`}</td></tr>)}</tbody></table></div> : !snapshot.gpu_process_error && <p>未回報 GPU 計算程序。</p>}
      <p className="muted">唯讀顯示 NVIDIA 計算程序，不含圖形程序與完整命令參數。</p>
    </>}
  </details>;
}
export function DashboardPage() {
  const { value, error } = useOverview(); const { data, navigate } = useConsole();
  if (!value) return <><InlineError message={error} />{!error && <Loading />}</>;
  const fresh = value.hosts.filter(h => recent(h.latest) && h.latest?.result.status === 'online');
  const gpus = fresh.flatMap(h => h.latest?.result.snapshot?.gpus || []);
  const knownVram = gpus.filter(g => g.memory_total_mb != null && g.memory_used_mb != null);
  return <><PageIntro eyebrow="LAB OVERVIEW" title="實驗室總覽" description="彙整已授權監控的觀測紀錄，不會自動連線至主機。" action={<Button onClick={() => navigate('/nodes')}>前往主機監控</Button>} /><InlineError message={error} />
    <div className="overview-metrics">{[['近期回報主機', `${fresh.length} / ${value.hosts.length}`, '最近 60 秒內觀測成功'], ['已觀測 GPU', String(gpus.length), '僅計入近期成功觀測'], ['可用 VRAM', knownVram.length ? `${(knownVram.reduce((sum, g) => sum + Math.max(0, g.memory_total_mb! - g.memory_used_mb!), 0) / 1024).toFixed(1)} GiB` : '未觀測', `${knownVram.length} 張 GPU 有完整記憶體回報`], ['未解除告警', String(value.alerts.filter(a => !a.resolved_at).length), '需後續成功觀測確認恢復']].map(([label, number, detail]) => <section className="overview-metric" key={label}><span>{label}</span><strong>{number}</strong><small>{detail}</small></section>)}</div>
    <Section className="observability-panel" title="主機觀測摘要"><div className="table-scroll"><table><thead><tr><th>主機</th><th>觀測狀態</th><th>CPU / RAM</th><th>最後觀測</th><th>資料</th></tr></thead><tbody>{value.hosts.map(h => { const s = h.latest?.result.snapshot; return <tr key={h.host_id}><td><strong>{h.alias}</strong></td><td><Badge status={recent(h.latest) ? h.latest!.result.status : 'unknown'} label={!h.latest ? '尚未觀測' : !recent(h.latest) ? '舊紀錄' : h.latest.result.status === 'online' ? '近期成功' : '觀測失敗'} /></td><td>{s ? `${s.cpu_percent ?? '—'}% / ${s.memory_total_mb ? ((s.memory_used_mb || 0) / s.memory_total_mb * 100).toFixed(0) : '—'}%` : '—'}</td><td>{h.latest ? formatDate(h.latest.collected_at) : '—'}</td><td><Button onClick={() => navigate(`/history/${h.host_id}`)}>查看歷史</Button></td></tr>; })}</tbody></table></div>{!value.hosts.length && <EmptyState title="尚無主機" />}</Section>
    <div className="overview-columns"><Section className="observability-panel" title="網站登入工作階段"><p>目前網站使用環境設定中的管理者帳號；此處與 Linux 主機帳號分開顯示。</p>{value.web_sessions.map((s, i) => <p key={i}><strong>{s.username}</strong> · 到期 {formatDate(s.expires_at)}</p>)}</Section><Section className="observability-panel" title="已記錄工作狀態"><p>{data.jobs.filter(j => ['pending', 'queued'].includes(j.status.toLowerCase())).length} 個待執行 · {data.jobs.filter(j => j.status.toLowerCase() === 'running').length} 個執行中</p><p className="muted">平台已記錄的工作，非即時 Slurm 全部佇列。</p><Button onClick={() => navigate('/history')}>查看告警與趨勢</Button></Section></div>
    <Section className="observability-panel" title="最近登入與操作紀錄"><div className="table-scroll"><table><thead><tr><th>時間</th><th>網站使用者</th><th>事件</th></tr></thead><tbody>{value.events.map(e => <tr key={e.id}><td>{formatDate(e.created_at)}</td><td>{e.actor}</td><td>{e.action}</td></tr>)}</tbody></table></div>{!value.events.length && <p>尚無事件紀錄。</p>}</Section>
  </>;
}
export function HistoryPage({ id }: { id?: string }) {
  const { data } = useConsole(); const { value, error: overviewError } = useOverview();
  const [host, setHost] = useState(id || data.nodes[0]?.id || ''), [hours, setHours] = useState(24), [points, setPoints] = useState<Sample[]>([]), [count, setCount] = useState(0), [error, setError] = useState(''), [loading, setLoading] = useState(true);
  useEffect(() => { if (id) setHost(id); }, [id]);
  const lastSelection = useRef('');
  useEffect(() => { let active = true; if (lastSelection.current !== `${host}/${hours}`) { setLoading(true); setPoints([]); setCount(0); lastSelection.current = `${host}/${hours}`; } setError(''); if (!host) { setLoading(false); return; }
    api<{ items: Sample[]; sample_count: number }>(`/monitor/history/${encodeURIComponent(host)}?hours=${hours}`).then(result => { if (active) { setPoints(result.items); setCount(result.sample_count); } }).catch(e => { if (active) setError(errorMessage(e)); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; };
  }, [host, hours, value]);
  const alerts = value?.alerts.filter(a => a.host_id === host) || [];
  const latest = value?.hosts.find(h => h.host_id === host)?.latest;
  const end = Date.now(), start = end - hours * 3600000;
  // Never draw across failures or unobserved intervals.
  const series = (key: 'cpu_peak' | 'memory_peak') => {
    const runs: string[][] = []; let previous: Sample | null = null;
    for (const point of points) {
      if (!point.result.snapshot) { previous = null; continue; }
      const gap = previous ? Date.parse(point.collected_at) - Date.parse(previous.collected_at) : Infinity;
      if (!previous || gap > Math.max(90000, hours * 3600000 / 480 * 2)) runs.push([]);
      runs[runs.length - 1].push(`${Math.max(0, Math.min(1000, (Date.parse(point.collected_at) - start) / (end - start) * 1000))},${180 - (point[key] || 0) * 1.6}`);
      previous = point;
    }
    return runs.map(run => run.join(' '));
  };
  return <><PageIntro eyebrow="HISTORY & ALERTS" title="歷史監控與告警" description="保留最近 7 天；監控期間約每 30 秒保存一筆，告警轉變另行保存。未監控的時間沒有資料。" />
    <div className="history-filters"><Field label="主機"><select value={host} onChange={e => setHost(e.target.value)}>{data.nodes.map(n => <option key={n.id} value={n.id}>{n.alias}</option>)}</select></Field><Field label="時間範圍"><select value={hours} onChange={e => setHours(Number(e.target.value))}><option value={1}>最近 1 小時</option><option value={24}>最近 24 小時</option><option value={168}>最近 7 天</option></select></Field></div>
    <InlineError message={error || overviewError} /><Section className="observability-panel" title="CPU 與 RAM 趨勢"><p>藍色實線：CPU；紫色虛線：RAM。各時間桶顯示峰值，共 {count} 筆樣本。</p>{loading ? <Loading /> : !points.length ? <EmptyState title="此時段尚無監控紀錄" description="請在主機監控頁明確啟動觀測，歷史資料才會累積。" /> : <><svg className="history-chart" viewBox="0 0 1000 210" role="img" aria-label={`最近 ${hours} 小時 CPU 與 RAM 使用率趨勢，詳細數值見下方資料表`}><text x="0" y="14">100%</text><text x="0" y="202">0%</text><line x1="0" y1="180" x2="1000" y2="180" className="chart-axis" /><>{series('cpu_peak').map((line, i) => <polyline key={i} points={line} className="chart-cpu" />)}{series('memory_peak').map((line, i) => <polyline key={i} points={line} className="chart-memory" />)}</>{points.filter(p => p.result.snapshot).map(p => <circle key={p.id} cx={Math.max(0, Math.min(1000, (Date.parse(p.collected_at) - start) / (end - start) * 1000))} cy={180 - (p.cpu_peak || 0) * 1.6} r="3" className="chart-dot" />)}</svg><div className="chart-range"><span>{formatDate(new Date(start).toISOString())}</span><span>{formatDate(new Date(end).toISOString())}</span></div><details><summary>檢視歷史數值與採樣使用者</summary><div className="table-scroll history-values"><table><thead><tr><th>時間</th><th>CPU 峰值</th><th>RAM 峰值</th><th>樣本</th><th>觀測者</th><th>狀態</th></tr></thead><tbody>{points.map(p => <tr key={p.id}><td>{formatDate(p.collected_at)}</td><td>{p.result.snapshot ? `${p.cpu_peak?.toFixed(1)}%` : '—'}</td><td>{p.result.snapshot ? `${p.memory_peak?.toFixed(1)}%` : '—'}</td><td>{p.sample_count}</td><td>{p.observer}</td><td>{p.result.status}</td></tr>)}</tbody></table></div></details></>}</Section>
    <Section className="observability-panel" title="告警紀錄"><p className="muted">CPU ≥95%、RAM ≥90%、VRAM ≥95%、磁碟可用 ≤10% 或觀測失敗。站內顯示，不發送外部通知；停止監控不代表告警解除。</p>{alerts.length ? <div className="table-scroll"><table><thead><tr><th>狀態</th><th>告警</th><th>首次 / 最近觀測</th><th>恢復時間</th></tr></thead><tbody>{alerts.map(a => <tr key={a.id}><td><Badge status={a.resolved_at ? 'success' : 'error'} label={a.resolved_at ? '已恢復' : '待確認'} /></td><td>{a.message}</td><td>{formatDate(a.opened_at)}<br />{formatDate(a.last_seen_at)}</td><td>{a.resolved_at ? formatDate(a.resolved_at) : '尚未觀測到恢復'}</td></tr>)}</tbody></table></div> : <EmptyState title="尚無告警紀錄" />}</Section>
    <Section className="observability-panel" title="最後觀測的 GPU 程序"><p>{latest ? `觀測時間：${formatDate(latest.collected_at)} · 網站觀測者：${latest.observer}` : '尚無觀測'}</p><HostActivity snapshot={latest?.result.snapshot || null} /></Section>
  </>;
}
export function LoginUsersPanel() {
  const { data, navigate } = useConsole();
  const [monitor, setMonitor] = useState<MonitorState | null>(null);
  const [error, setError] = useState('');
  const [clock, setClock] = useState(Date.now());
  useEffect(() => {
    let alive = true, fetching = false;
    const load = async () => {
      if (fetching) return;
      fetching = true;
      try {
        // Read existing monitoring only; never start or extend a monitoring lease.
        const result = await api<MonitorState>('/monitor');
        if (alive) { setMonitor(result); setError(''); setClock(Date.now()); }
      } catch (e) {
        if (alive) { setMonitor(null); setError(errorMessage(e)); }
      } finally { fetching = false; }
    };
    void load();
    const timer = window.setInterval(load, 30000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);
  const active = monitor?.active && (!monitor.expires_at || Date.parse(monitor.expires_at) > clock);
  return <Section className="observability-panel" title="目前登入使用者">
    <p>每 30 秒更新已開啟監控的主機。人數以不重複帳號計算；僅包含 who 回報的終端登入，不包含所有背景服務或無終端的 SSH 連線。</p>
    <InlineError message={error} />
    {!monitor && !error ? <Loading /> : <div className="table-scroll"><table><thead><tr><th>主機</th><th>登入人數</th><th>已登入帳號</th><th>觀測狀態 / 時間</th></tr></thead><tbody>{data.nodes.map(node => {
      const watching = active && monitor?.host_ids.includes(node.id);
      const result = monitor?.results[node.id];
      const snapshot = result?.snapshot as ActivitySnapshot | null | undefined;
      const stale = !snapshot?.collected_at || clock - Date.parse(snapshot.collected_at) > 60000;
      const valid = watching && result?.status === 'online' && !result.error && !snapshot?.login_users_error && !stale && snapshot?.login_users !== undefined;
      const users = valid ? [...new Set(snapshot!.login_users!.map(user => user.username))].sort() : [];
      const status = error ? '更新失敗' : !watching ? '未開啟監控' : result?.error || snapshot?.login_users_error ? '讀取失敗' : !snapshot ? '等待觀測' : stale ? '資料已過期' : !valid ? '登入資料無法取得' : '監控中';
      return <tr key={node.id}><td><strong>{node.ssh_connection?.display_name || node.alias}</strong></td><td>{valid ? `${users.length} 人` : '—'}</td><td>{valid ? users.join('、') || '目前無終端登入帳號' : '—'}</td><td>{status}{watching && snapshot && <><br />{formatDate(snapshot.collected_at)}</>}</td></tr>;
    })}</tbody></table></div>}
    {!active && <p><Button onClick={() => navigate('/nodes')}>前往開啟主機監控</Button></p>}
  </Section>;
}
export function AccountsPage() {
  const { data } = useConsole(); const [selected, setSelected] = useState<Node | null>(null), [busy, setBusy] = useState(false);
  const onBusy = useCallback((value: boolean) => setBusy(value), []);
  return <><PageIntro eyebrow="ACCOUNTS" title="帳號管理" description="依主機查看一般 Linux 使用者帳號。讀取清單不修改主機；新增、修改、鎖定與刪除皆需命令計畫審核。" />
    <LoginUsersPanel />
    <Section className="observability-panel" title="選擇主機"><div className="table-scroll"><table><thead><tr><th>主機</th><th>SSH 帳號</th><th>帳號清單</th></tr></thead><tbody>{data.nodes.map(n => <tr key={n.id}><td><strong>{n.alias}</strong><br />{n.hostname}</td><td>{n.ssh_connection?.username || '尚未設定'}</td><td><Button disabled={!n.ssh_connection?.enabled || !n.ssh_connection.host_key_sha256} onClick={() => setSelected(n)}>查看 {n.alias} 帳號</Button></td></tr>)}</tbody></table></div><p className="muted">需已啟用的 SSH 連線與確認過的主機指紋。</p></Section>
    {selected && <Modal title={`${selected.alias} · Linux 帳號管理`} onClose={() => { if (!busy) setSelected(null); }}><HostAccounts key={selected.id} node={selected} onBusy={onBusy} /></Modal>}
  </>;
}
