import { createPortal } from 'react-dom';
import { FieldHelp } from './FieldHelp';
import { Children, cloneElement, isValidElement, useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type ReactElement, type ReactNode } from 'react';
import { AlertCircle, ArrowRight, Check, ChevronDown, Clock3, Copy, FileCode2, LoaderCircle, LockKeyhole, ShieldCheck, Terminal, X } from 'lucide-react';
import type { Mode, PlanDocument, PlanStep } from './types';
import { json, shellPreview, statusLabels } from './utils';

export function Badge({ status, label }: { status: string; label?: string }) {
  const value = status.toLowerCase();
  const tone = ['running', 'active', 'enabled', 'online', 'succeeded', 'success', 'completed'].includes(value) ? 'green' : ['failed', 'rejected', 'error', 'offline', 'timeout', 'node_fail'].includes(value) ? 'red' : ['awaiting_approval', 'pending_approval', 'planned', 'pending', 'queued'].includes(value) ? 'amber' : 'neutral';
  return <span className={`badge badge-${tone}`}><span className="badge-dot" />{label || statusLabels[status] || status}</span>;
}

export function ModeBanner({ mode, compact = false }: { mode?: Mode; compact?: boolean }) {
  const title = mode === 'demo' ? '模擬環境' : mode === 'local-slurm' ? '本機 Slurm' : mode === 'remote-slurm' ? '實機 Slurm' : '環境待確認';
  return <div className={`mode-banner ${mode === 'demo' ? 'mode-demo' : ''} ${compact ? 'mode-compact' : ''}`} role="status">
    <span className="mode-indicator" />
    <strong>{title}</strong>
    {!compact && <span>{mode === 'demo' ? '工作執行為模擬；主機頁的「開始實際監控」會讀取真實主機。未開始時不會建立監控連線。' : mode ? '頁面讀取已儲存的觀測紀錄；遠端操作須先審查核准。' : '尚未取得伺服器模式，請檢查後端連線。'}</span>}
  </div>;
}

export function Button({ variant = 'secondary', busy = false, children, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; busy?: boolean }) {
  return <button {...props} className={`button button-${variant} ${className}`} disabled={props.disabled || busy} aria-busy={busy || undefined}>{busy && <LoaderCircle size={16} className="spin" />}{children}</button>;
}

export function PageIntro({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description: string; action?: ReactNode }) {
  return <div className="page-intro"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1><p className="page-description">{description}</p></div>{action && <div className="intro-action">{action}</div>}</div>;
}

export function Section({ title, aside, children, className = '' }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="section-heading"><h2>{title}</h2>{aside}</div>{children}</section>;
}

export function EmptyState({ title, description, action, icon }: { title: string; description?: string; action?: ReactNode; icon?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon">{icon || <FileCode2 size={26} strokeWidth={1.4} />}</div><h3>{title}</h3>{description && <p>{description}</p>}{action}</div>;
}

export function Loading({ label = '正在讀取紀錄…' }: { label?: string }) { return <div className="loading-state" role="status"><LoaderCircle size={20} className="spin" /><span>{label}</span></div>; }
export function InlineError({ message }: { message?: string }) { return message ? <div className="inline-error" role="alert"><AlertCircle size={17} /><span>{message}</span></div> : null; }
export function Notice({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'amber' }) { return <div className={`notice notice-${tone}`}><AlertCircle size={17} /><div>{children}</div></div>; }
export function Field({ label, children, hint, help, className = '' }: { label: string; children: ReactNode; hint?: string; help?: string; className?: string }) {
  const id = useId();
  if (!help) return <label className={`field ${className}`}><span id={`${id}-label`} className="field-label">{label}</span>{Children.map(children, child => isValidElement(child) && typeof child.type === 'string' && ['input', 'select', 'textarea'].includes(child.type) ? cloneElement(child as ReactElement<{ 'aria-labelledby'?: string; 'aria-describedby'?: string }>, { 'aria-labelledby': `${id}-label`, 'aria-describedby': hint ? `${id}-hint` : undefined }) : child)}{hint && <span id={`${id}-hint`} className="field-hint">{hint}</span>}</label>;
  const description = [hint && `${id}-hint`, help && `${id}-help`].filter(Boolean).join(' ') || undefined;
  return <div className={`field ${className}`}><div className="field-label-row"><label htmlFor={`${id}-control-0`} id={`${id}-label`} className="field-label">{label}</label>{help && <FieldHelp label={label}>{help}</FieldHelp>}</div>{Children.map(children, (child, index) => isValidElement(child) && typeof child.type === 'string' && ['input', 'select', 'textarea'].includes(child.type) ? cloneElement(child as ReactElement<{ id?: string; 'aria-labelledby'?: string; 'aria-describedby'?: string }>, { id: `${id}-control-${index}`, 'aria-labelledby': `${id}-label`, 'aria-describedby': description }) : child)}{hint && <span id={`${id}-hint`} className="field-hint">{hint}</span>}{help && <span id={`${id}-help`} className="sr-only">{help}</span>}</div>;
}

export function Meter({ label, value, detail }: { label: string; value: number | null; detail?: string }) {
  const valid = value !== null && Number.isFinite(value);
  return <div className="meter"><div className="meter-caption"><span>{label}</span><strong>{valid ? `${value.toFixed(0)}%` : '未觀測'}</strong></div><div className={`meter-track ${!valid ? 'meter-unknown' : ''}`} role={valid ? 'meter' : undefined} aria-label={label} aria-valuenow={valid ? value : undefined} aria-valuemin={valid ? 0 : undefined} aria-valuemax={valid ? 100 : undefined}><div style={{ width: valid ? `${Math.max(0, Math.min(100, value))}%` : '0%' }} /></div>{detail && <span className="meter-detail">{detail}</span>}</div>;
}

export function KeyValue({ label, children }: { label: string; children: ReactNode }) { return <div className="key-value"><dt>{label}</dt><dd>{children}</dd></div>; }

export function CodeBlock({ value, label = '內容', copy = true }: { value: string; label?: string; copy?: boolean }) {
  const [copied, setCopied] = useState(false);
  return <div className="code-block"><div className="code-heading"><span>{label}</span>{copy && <button type="button" className="icon-button" aria-label={`複製${label}`} onClick={async () => { try { await navigator.clipboard.writeText(value); setCopied(true); window.setTimeout(() => setCopied(false), 1600); } catch { setCopied(false); } }}>{copied ? <Check size={14} /> : <Copy size={14} />}</button>}</div><pre tabIndex={0}>{value}</pre></div>;
}

function StepCard({ step, index }: { step: PlanStep; index: number }) {
  const fileChange = step.file_change as { diff?: string; unified_diff?: string } | undefined;
  return <article className="step-card">
    <header className="step-heading"><span className="step-number">{String(index + 1).padStart(2, '0')}</span><div className="step-title"><h3>{step.title}</h3><div className="step-meta"><span>{step.host_id || '控制節點'}</span><span>{step.execution_user || '尚未指定使用者'}</span><span><Clock3 size={12} />{step.timeout_seconds ?? '—'} 秒</span></div></div><span className="tag">{step.kind}</span>{step.requires_sudo && <span className="tag tag-amber"><LockKeyhole size={12} />管理權限</span>}</header>
    <div className="step-body">{step.reason && <p className="step-reason">{step.reason}</p>}
      {step.cwd && <div className="inline-meta">工作目錄 <code>{step.cwd}</code></div>}
      {!!step.argv?.length && <CodeBlock value={shellPreview(step.argv)} label="預計執行的命令" />}
      {step.script && <CodeBlock value={step.script} label="完整執行腳本" />}
      {step.request !== undefined && step.request !== null && <CodeBlock value={json(step.request)} label="API 請求" />}
      {step.file_change !== undefined && step.file_change !== null && <CodeBlock value={fileChange?.unified_diff || fileChange?.diff || json(step.file_change)} label="檔案變更／差異" />}
      {step.environment && Object.keys(step.environment).length > 0 && <CodeBlock value={json(step.environment)} label="環境變數與秘密參照" />}
      <div className="step-details-grid">{step.evidence !== undefined && <details className="disclosure"><summary>依據與觀測紀錄<ChevronDown size={15} /></summary><pre>{json(step.evidence)}</pre></details>}{step.preconditions !== undefined && <details className="disclosure"><summary>執行前提<ChevronDown size={15} /></summary><pre>{json(step.preconditions)}</pre></details>}{step.on_failure !== undefined && <details className="disclosure"><summary>失敗處理<ChevronDown size={15} /></summary><pre>{json(step.on_failure)}</pre></details>}</div>
    </div>
  </article>;
}

export function PlanDocumentView({ document }: { document: PlanDocument }) {
  return <div className="plan-document"><div className="section-heading"><h2>完整執行步驟</h2><span className="muted">{document.steps?.length || 0} 個步驟</span></div>
    {document.steps?.length ? document.steps.map((step, i) => <StepCard key={step.id || i} step={step} index={i} />) : <Notice>此計畫沒有可執行步驟。</Notice>}
    <div className="document-details">{([['影響範圍', document.impact], ['失敗與中斷處理', document.failure_policy], ['設備清單版本', document.inventory], ['計畫來源', document.source]] as [string, unknown][]).filter(([, value]) => value != null).map(([title, value]) => <details className="disclosure" key={title}><summary>{title}<ChevronDown size={15} /></summary><pre>{json(value)}</pre></details>)}<details className="disclosure"><summary>檢視完整計畫文件<ChevronDown size={15} /></summary><pre>{json(document)}</pre></details></div>
  </div>;
}

export function ReviewApproval({ contentHash, eligible, issues = [], onApprove, busy = false, kind = 'plan' }: { contentHash: string; eligible: boolean; issues?: unknown[]; onApprove: () => void; busy?: boolean; kind?: 'plan' | 'schedule' }) {
  const [reviewed, setReviewed] = useState(false);
  useEffect(() => setReviewed(false), [contentHash]);
  return <section className="approval-box" aria-label="內容核准"><div className="approval-title"><ShieldCheck size={20} /><h2>{kind === 'plan' ? '核准此版本的計畫' : '核准此版本的排程'}</h2></div><p>核准綁定下方內容雜湊。任何內容變更，都必須重新審查。</p><code className="hash-value" aria-label="計畫內容雜湊">{contentHash || '尚未產生雜湊'}</code>
    {issues.length > 0 && <div className="approval-issues" role="alert"><strong>以下項目尚未解決</strong><ul>{issues.map((issue, i) => <li key={i}>{typeof issue === 'string' ? issue : json(issue)}</li>)}</ul></div>}
    <label className="checkbox-row review-checkbox"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={!eligible || busy} /><span>我已審查完整步驟、影響範圍與執行依據，同意核准此內容。</span></label>
    <Button variant="primary" onClick={onApprove} disabled={!eligible || !reviewed || !contentHash || issues.length > 0} busy={busy}><ShieldCheck size={16} />{kind === 'plan' ? '核准並排入執行' : '核准排程內容'}<ArrowRight size={15} /></Button>
    {!eligible && !issues.length && <p className="field-hint">此版本目前無法核准，可能已處理、到期或缺少核准權限。</p>}
    {kind === 'schedule' && <p className="field-hint">核准後仍須啟用排程；到期後不再發起新觀測。</p>}
  </section>;
}

export function Modal({ title, children, onClose, closeOnEscape = true }: { title: string; children: ReactNode; onClose: () => void; closeOnEscape?: boolean }) {
  const dialog = useRef<HTMLElement>(null);
  const close = useRef(onClose); close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const focusable = () => Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]') || []);
    (dialog.current?.querySelector<HTMLElement>('input, select, textarea') || focusable()[0] || dialog.current)?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && closeOnEscape) { event.preventDefault(); close.current(); }
      if (!closeOnEscape && (event.target as HTMLElement)?.closest('.web-terminal')) return;
      if (event.key !== 'Tab') return;
      const items = focusable(), first = items[0], last = items.at(-1);
      if (!first) { event.preventDefault(); dialog.current?.focus(); }
      else if (event.shiftKey && (document.activeElement === first || !dialog.current?.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !dialog.current?.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', keyboard);
    return () => { document.removeEventListener('keydown', keyboard); document.body.style.overflow = overflow; if (previous?.isConnected) previous.focus(); };
  }, []);
  return createPortal(<div className="modal-backdrop" onClick={onClose}><section ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} className="modal" onClick={event => event.stopPropagation()}><div className="section-heading"><h2>{title}</h2><button className="icon-button" aria-label="關閉視窗" onClick={onClose}><X size={19} /></button></div>{children}</section></div>, document.body);
}

export function ConnectionMark({ connected }: { connected: boolean }) { return <span className={`connection-mark ${connected ? '' : 'connection-offline'}`}><span />{connected ? '已連線' : '重新連線中'}</span>; }
export function StreamIcon() { return <Terminal size={16} />; }
