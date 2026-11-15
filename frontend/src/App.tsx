import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { LayoutDashboard, History, Users, Activity, ArrowRight, CalendarClock, Check, ChevronRight, CircleHelp, LogOut, Menu, RefreshCw, Server, ShieldCheck, UserRound, X } from 'lucide-react';
import { API_BASE, api, errorMessage, getSession, list, post, setCsrfToken } from './api';
import { ThemeControl } from './theme';
import { ConsoleContext } from './context';
import type { Health, OverviewData, Session } from './types';
import { Button, ConnectionMark, InlineError, Loading } from './ui';
import { SlurmSetupPage } from './pages/SlurmSetup';
import { TrainingSetupPage } from './pages/TrainingSetup';
import { NodesPage } from './pages/Nodes';
import { SchedulesPage } from './pages/Schedules';
import { DashboardPage, HistoryPage, AccountsPage } from './pages/Observability';
import { AppSidebar } from './AppSidebar';

const emptyData: OverviewData = { nodes: [], projects: [], drafts: [], plans: [], executions: [], schedules: [], jobs: [] };
const navItems = [
  { key: 'overview', label: '總覽 Dashboard', icon: LayoutDashboard },
  { key: 'history', label: '歷史監控與告警', icon: History },
  { key: 'accounts', label: '帳號管理', icon: Users },
  { key: 'nodes', label: '主機監控', icon: Server },
  { key: 'training', label: 'Slurm 叢集設定', icon: Activity },
  { key: 'schedules', label: '定時監控', icon: CalendarClock },
];
function currentPath() { return window.location.hash.replace(/^#/, '') || '/overview'; }

function Login({ health, onLogin }: { health: Health | null; onLogin: (session: Session) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await post('/session', { username, password }); const session = await getSession(); setPassword(''); onLogin(session); } catch (err) { setPassword(''); setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <div className="login-layout"><section className="login-brand"><div className="brand-lockup"><div className="brand-mark"><span /><span /><span /></div><span>SRS<span className="brand-small">RESEARCH COMPUTE</span></span></div><div className="login-story"><p className="eyebrow">SERVER REMOTE SYSTEM</p><h1>把研究的時間，<br />留給研究。</h1><p>讓每一台主機的狀態，清楚呈現在同一個地方。</p><div className="login-grid-art" aria-hidden="true"><div className="art-node art-node-main"><Server size={28} /><span>CONTROL</span></div><div className="art-line" /><div className="art-workers"><div className="art-node"><Activity size={22} /><span>CPU / RAM</span></div><div className="art-node"><Activity size={22} /><span>GPU</span></div></div></div></div><footer>LAB INFRASTRUCTURE</footer></section><main className="login-main"><div className="login-theme"><ThemeControl /></div><div className="login-card"><p className="eyebrow">WELCOME TO YOUR WORKSPACE</p><h2>登入主機管理控制台</h2><p className="login-description">管理 SSH 主機，掌握 CPU、記憶體與 GPU 使用狀態。</p><form onSubmit={submit}><label className="field"><span className="field-label">帳號</span><input autoComplete="username" autoFocus value={username} onChange={event => setUsername(event.target.value)} required placeholder="輸入管理者建立的帳號" /></label><label className="field"><span className="field-label">密碼</span><input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required placeholder="輸入密碼" /></label><InlineError message={error} /><Button variant="primary" type="submit" busy={busy} className="login-submit">登入工作空間<ArrowRight size={17} /></Button></form><div className="login-footnote"><ShieldCheck size={17} /><span>使用管理者提供的帳號登入。</span></div>{health && !health.configured && <p className="setup-warning">伺服器尚未完成帳號設定。請管理者先完成初始化。</p>}</div><div className="login-version">SRS CONTROL PLANE <span>{health?.version ? `v${health.version}` : '後端連線待確認'}</span></div></main></div>;
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [initializing, setInitializing] = useState(true);
  const [data, setData] = useState<OverviewData>(emptyData);
  const [path, setPath] = useState(currentPath());
  const [monitorVisited, setMonitorVisited] = useState(currentPath().startsWith('/nodes'));
  useEffect(() => { if (path.startsWith('/nodes')) setMonitorVisited(true); }, [path]);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [connected, setConnected] = useState(false);
  const [sidebarPinned, setSidebarPinned] = useState(() => localStorage.getItem('srs-sidebar-collapsed') !== 'true');
  useEffect(() => { localStorage.setItem('srs-sidebar-collapsed', String(!sidebarPinned)); }, [sidebarPinned]);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [smallScreen, setSmallScreen] = useState(() => window.matchMedia('(max-width:760px)').matches);
  const sidebarRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' } | null>(null);
  const inFlight = useRef(false);
  const toastTimer = useRef<number | undefined>(undefined);
  const mounted = useRef(true);
  const sessionRef = useRef(session);
  sessionRef.current = session;
  const navigate = useCallback((next: string) => { window.location.hash = next; setPath(next); setMobileOpen(false); window.scrollTo({ top: 0 }); }, []);
  const notify = useCallback((message: string, kind: 'success' | 'error' = 'success') => { setToast({ message, kind }); window.clearTimeout(toastTimer.current); toastTimer.current = window.setTimeout(() => setToast(null), 6500); }, []);
  const refresh = useCallback(async () => {
    if (inFlight.current || !sessionRef.current) return; inFlight.current = true; setRefreshing(true);
    const keys: (keyof OverviewData)[] = ['nodes', 'schedules', 'jobs'];
    try {
      const results = await Promise.allSettled(keys.map(key => list(`/` + key)));
      if (!mounted.current || !sessionRef.current) return;
      const updates: Partial<OverviewData> = {}; const errors: string[] = [];
      results.forEach((result, index) => { if (result.status === 'fulfilled') Object.assign(updates, { [keys[index]]: result.value }); else errors.push(errorMessage(result.reason)); });
      setData(previous => ({ ...previous, ...updates })); setLoaded(true);
      setLoadError(errors.length ? `部分資料暫時無法更新，保留上次紀錄。${errors[0]}` : '');
    } finally { inFlight.current = false; if (mounted.current) setRefreshing(false); }
  }, []);
  useEffect(() => {
    mounted.current = true; let active = true;
    void Promise.allSettled([api<Health>('/health').then(value => active && setHealth(value)), getSession().then(value => active && setSession(value))]).finally(() => active && setInitializing(false));
    const route = () => { setPath(currentPath()); setMobileOpen(false); window.scrollTo({ top: 0 }); };
    const unauthorized = () => { setSession(null); setMonitorVisited(false); setData(emptyData); setLoaded(false); setCsrfToken(''); };
    window.addEventListener('hashchange', route); window.addEventListener('srs:unauthorized', unauthorized);
    return () => { active = false; mounted.current = false; window.removeEventListener('hashchange', route); window.removeEventListener('srs:unauthorized', unauthorized); window.clearTimeout(toastTimer.current); };
  }, []);
  useEffect(() => {
    if (!session) return; void refresh();
    const source = new EventSource(`${API_BASE}/events`, { withCredentials: true });
    source.onopen = () => { setConnected(true); void refresh(); };
    source.onerror = () => setConnected(false);
    source.onmessage = () => void refresh();
    source.addEventListener('refresh', () => void refresh());
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => { source.close(); setConnected(false); window.clearInterval(timer); };
  }, [session, refresh]);
  useEffect(() => {
    const media = window.matchMedia('(max-width:760px)');
    const changed = () => { setSmallScreen(media.matches); if (!media.matches) setMobileOpen(false); };
    media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  }, []);
  useEffect(() => {
    if (!smallScreen || !mobileOpen) return;
    const panel = sidebarRef.current;
    const focusable = () => Array.from(panel?.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), [tabindex="0"]') || []);
    focusable()[0]?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setMobileOpen(false); }
      if (event.key !== 'Tab') return;
      const items = focusable(), first = items[0], last = items.at(-1);
      if (event.shiftKey && (document.activeElement === first || !panel?.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !panel?.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', keyboard);
    return () => { document.removeEventListener('keydown', keyboard); menuRef.current?.focus(); };
  }, [smallScreen, mobileOpen]);
  async function logout() { try { await api('/session', { method: 'DELETE' }); setCsrfToken(''); setSession(null); setMonitorVisited(false); setData(emptyData); setLoaded(false); } catch (err) { notify(errorMessage(err), 'error'); } }
  if (initializing) return <div className="boot-screen"><div className="brand-mark"><span /><span /><span /></div><Loading label="正在連線至控制平台…" /></div>;
  if (!session) return <Login health={health} onLogin={value => { setSession(value); navigate('/overview'); }} />;
  const segments = path.split('/').filter(Boolean); const page = ['overview', 'history', 'accounts'].includes(segments[0]) ? segments[0] : segments[0] === 'schedules' ? 'schedules' : segments[0] === 'training' ? 'training' : segments[0] === 'torchrun' ? 'torchrun' : 'nodes'; const id = segments[1];
  const activeNav = navItems.find(item => item.key === (page === 'torchrun' ? 'training' : page)) || navItems[0];
  const can = (permission: string) => session.permissions.includes(permission) || session.permissions.includes('*');
  return <ConsoleContext.Provider value={{ session, data, refresh, navigate, notify, can }}><div className={`app-shell ${sidebarPinned ? 'sidebar-pinned' : 'sidebar-unpinned'}`}>
    {mobileOpen && <button className="sidebar-scrim" aria-label="關閉導覽" onClick={() => setMobileOpen(false)} />}
    <AppSidebar activePage={page === 'torchrun' ? 'training' : page} healthVersion={health?.version} mobileOpen={mobileOpen} smallScreen={smallScreen} pinned={sidebarPinned} sidebarRef={sidebarRef} navItems={navItems} onCloseMobile={() => setMobileOpen(false)} onPinnedChange={setSidebarPinned} />
    <div className="main-shell" inert={smallScreen && mobileOpen}><header className="topbar"><div className="breadcrumb"><button ref={menuRef} className="icon-button mobile-menu" aria-label="開啟導覽" aria-expanded={mobileOpen} onClick={() => setMobileOpen(!mobileOpen)}><Menu size={21} /></button><span className="breadcrumb-root">主機管理控制台</span><ChevronRight size={14} /><strong>{activeNav.label}</strong></div><div className="topbar-actions"><ThemeControl /><ConnectionMark connected={connected} /><button className="icon-button refresh-button" aria-label="重新整理" title="重新整理主機與排程清單" onClick={() => void refresh()} disabled={refreshing}><RefreshCw size={17} className={refreshing ? 'spin' : ''} /></button><div className="user-chip"><UserRound size={16} /><span>{session.username}</span></div><button className="icon-button" aria-label="登出" title="登出" onClick={logout}><LogOut size={17} /></button></div></header><div className="content-shell">{page === 'schedules' && session.mode === 'demo' && <div className="mode-banner" role="status">模擬排程 · 這裡的排程不會連線至實體主機。主機監控頁可讀取真實資料。</div>}<InlineError message={loadError} /><main id="main-content" className="page-content">
      {loaded && <div hidden={page !== 'nodes'}>{(monitorVisited || page === 'nodes') && <NodesPage />}</div>}
      {!loaded ? <Loading label="正在讀取主機與排程…" /> : page === 'overview' ? <DashboardPage /> : page === 'history' ? <HistoryPage id={id} /> : page === 'accounts' ? <AccountsPage /> : page === 'schedules' ? <SchedulesPage id={id} editing={segments[2] === 'edit'} /> : page === 'training' ? <SlurmSetupPage /> : page === 'torchrun' ? <TrainingSetupPage /> : null}
    </main><footer className="page-footer"><span>SERVER REMOTE SYSTEM</span><span>時間顯示為台北時間</span></footer></div></div>
    {toast && <div className={`toast toast-${toast.kind}`} role={toast.kind === 'error' ? 'alert' : 'status'}>{toast.kind === 'success' ? <Check size={18} /> : <CircleHelp size={18} />}<span>{toast.message}</span><button className="icon-button" aria-label="關閉通知" onClick={() => setToast(null)}><X size={16} /></button></div>}
  </div></ConsoleContext.Provider>;
}
