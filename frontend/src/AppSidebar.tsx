import { useEffect, useRef, useState, type FocusEvent, type KeyboardEvent, type PointerEvent, type RefObject } from 'react';
import { PanelLeftClose, PanelLeftOpen, X, type LucideIcon } from 'lucide-react';

type NavItem = { key: string; label: string; icon: LucideIcon };

export function AppSidebar({
  activePage,
  healthVersion,
  mobileOpen,
  smallScreen,
  pinned,
  sidebarRef,
  navItems,
  onCloseMobile,
  onPinnedChange,
}: {
  activePage: string;
  healthVersion?: string;
  mobileOpen: boolean;
  smallScreen: boolean;
  pinned: boolean;
  sidebarRef: RefObject<HTMLElement | null>;
  navItems: NavItem[];
  onCloseMobile: () => void;
  onPinnedChange: (value: boolean) => void;
}) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const closeTimer = useRef<number | undefined>(undefined);
  const pinButton = useRef<HTMLButtonElement>(null);
  const expanded = pinned || previewOpen || mobileOpen;

  function cancelClose() {
    window.clearTimeout(closeTimer.current);
  }

  function openPreview(event?: PointerEvent<HTMLElement>) {
    cancelClose();
    if (!pinned && (!event || event.pointerType !== 'touch')) setPreviewOpen(true);
  }

  function scheduleClose(delay = 220) {
    cancelClose();
    if (!pinned) closeTimer.current = window.setTimeout(() => setPreviewOpen(false), delay);
  }

  function focusEntered(event: FocusEvent<HTMLElement>) {
    cancelClose();
    if (!pinned && event.target !== pinButton.current) setPreviewOpen(true);
  }

  function focusLeft(event: FocusEvent<HTMLElement>) {
    const next = event.relatedTarget;
    if (!(next instanceof Node) || !event.currentTarget.contains(next)) scheduleClose(80);
  }

  function keyboard(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== 'Escape' || pinned || !previewOpen) return;
    event.preventDefault();
    event.stopPropagation();
    cancelClose();
    setPreviewOpen(false);
    pinButton.current?.focus();
  }

  useEffect(() => {
    if (pinned) setPreviewOpen(false);
  }, [pinned]);

  useEffect(() => () => window.clearTimeout(closeTimer.current), []);

  return <aside
    ref={sidebarRef}
    className={`sidebar app-sidebar ${expanded ? 'sidebar-expanded' : 'sidebar-rail'} ${previewOpen && !pinned ? 'sidebar-preview' : ''} ${mobileOpen ? 'sidebar-open' : ''}`}
    inert={smallScreen && !mobileOpen}
    aria-hidden={smallScreen && !mobileOpen ? true : undefined}
    role={smallScreen ? 'dialog' : undefined}
    aria-modal={smallScreen && mobileOpen ? true : undefined}
    onPointerEnter={smallScreen ? undefined : openPreview}
    onPointerLeave={() => scheduleClose()}
    onFocusCapture={smallScreen ? undefined : focusEntered}
    onBlurCapture={focusLeft}
    onKeyDown={keyboard}
    aria-label="主要導覽"
  >
    <div className="sidebar-header">
      <a href="#/overview" className="brand-lockup sidebar-brand" aria-label="SRS 總覽首頁">
        <div className="brand-mark"><span /><span /><span /></div>
        <span className="sidebar-label sidebar-brand-copy">SRS<span className="brand-small">RESEARCH COMPUTE</span></span>
      </a>
      <button className="icon-button sidebar-mobile-close" aria-label="關閉導覽選單" onClick={onCloseMobile}><X size={20} /></button>
      <button
        ref={pinButton}
        type="button"
        className="sidebar-pin"
        aria-label={pinned ? '收合側欄，只顯示圖示' : '固定展開側欄'}
        aria-pressed={pinned}
        title={pinned ? '收合側欄' : '固定展開側欄'}
        onClick={() => { cancelClose(); setPreviewOpen(false); onPinnedChange(!pinned); }}
      >
        {pinned ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
        <span className="sidebar-label">{pinned ? '收合側欄' : '固定展開'}</span>
      </button>
    </div>

    <div className="sidebar-section-label sidebar-label">工作空間</div>
    <nav aria-label="主要功能">
      {navItems.map(item => {
        const Icon = item.icon;
        const active = activePage === item.key;
        return <a
          key={item.key}
          href={`#/${item.key}`}
          className={`nav-item ${active ? 'nav-active' : ''}`}
          aria-current={active ? 'page' : undefined}
          aria-label={item.label}
          data-rail-label={item.label}
          onClick={onCloseMobile}
        >
          <span className="nav-icon"><Icon size={20} strokeWidth={1.7} /></span>
          <span className="sidebar-label nav-label">{item.label}</span>
        </a>;
      })}
    </nav>

    <div className="sidebar-spacer" />
    <div className="sidebar-status" aria-label={`SRS ${healthVersion || '0.1.0'}，研究實驗室`}>
      <span className="sidebar-status-mark" aria-hidden="true" />
      <span className="sidebar-label"><strong>SRS / {healthVersion || '0.1.0'}</strong><small>RESEARCH LAB</small></span>
    </div>
  </aside>;
}
