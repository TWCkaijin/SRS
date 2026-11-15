import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, FileCheck2, Plus, RotateCcw } from 'lucide-react';
import { api, errorMessage, post } from '../api';
import { useConsole } from '../context';
import type { Execution, Plan } from '../types';
import { Badge, Button, EmptyState, Field, InlineError, KeyValue, Loading, Notice, PageIntro, PlanDocumentView, ReviewApproval, Section } from '../ui';
import { canApprovePlan, formatDate, shortId } from '../utils';

export function PlansPage({ id }: { id?: string }) {
  const { data, navigate, refresh, notify, can } = useConsole();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [rejectReason, setRejectReason] = useState('');
  const [filter, setFilter] = useState('all');
  useEffect(() => {
    if (!id) return; let active = true; setLoading(true); setError('');
    api<Plan>(`/plans/${encodeURIComponent(id)}`).then(value => active && setPlan(value)).catch(err => active && setError(errorMessage(err))).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [id, data.plans]);
  async function approve() {
    if (!plan) return; setBusy(true); setError('');
    try { const result = await post<{ plan: Plan; execution: Execution }>(`/plans/${plan.id}/approve`, { content_hash: plan.content_hash }); setPlan(result.plan); await refresh(); notify('此版本已核准並排入執行。'); navigate(`/executions/${result.execution.id}`); } catch (err) { setError(errorMessage(err)); await refresh(); } finally { setBusy(false); }
  }
  async function reject() {
    if (!plan || !rejectReason.trim()) return; setBusy(true); setError('');
    try { await post(`/plans/${plan.id}/reject`, { reason: rejectReason.trim() }); await refresh(); notify('計畫已退回，尚未核准的內容不會執行。'); } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  if (!id) {
    const items = data.plans.filter(value => filter === 'all' || (filter === 'review' ? ['awaiting_approval', 'pending_approval', 'planned'].includes(value.status) : value.status === filter));
    return <><PageIntro eyebrow="REVIEW DESK" title="計畫審查" description="查看完整步驟、執行依據與檔案差異。每一次核准只適用於你眼前的內容版本。" action={can('draft.manage') && <Button onClick={() => navigate('/drafts/new')}><Plus size={16} />建立草稿</Button>} /><div className="filter-tabs" role="group" aria-label="計畫狀態篩選">{[['all', '全部計畫'], ['review', '待核准'], ['approved', '已核准'], ['rejected', '已退回']].map(([key, label]) => <button key={key} className={filter === key ? 'selected' : ''} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>)}</div><Section title="計畫清單" aside={<span className="muted">{items.length} 份</span>}>{items.length ? <div className="table-scroll"><table><thead><tr><th>計畫／內容版本</th><th>狀態</th><th>步驟</th><th>建立時間</th><th><span className="sr-only">審查</span></th></tr></thead><tbody>{items.map(item => <tr key={item.id}><td><button className="table-title" onClick={() => navigate(`/plans/${item.id}`)}>{item.title}</button><span className="table-subtitle mono">v{item.revision} · {shortId(item.content_hash)}</span></td><td><Badge status={item.status} /></td><td>{item.document.steps?.length || 0} 個{item.issues?.length > 0 && <span className="table-subtitle text-red">{item.issues.length} 項待解決</span>}</td><td className="muted nowrap">{formatDate(item.created_at)}</td><td><button className="icon-button" aria-label={`審查計畫：${item.title}`} onClick={() => navigate(`/plans/${item.id}`)}><ArrowRight size={17} /></button></td></tr>)}</tbody></table></div> : <EmptyState title="這裡還沒有計畫" description="在工作草稿中產生計畫後，便能進行完整審查。" icon={<FileCheck2 size={27} />} />}</Section></>;
  }
  if (loading && !plan) return <Loading />;
  if (!plan) return <><InlineError message={error || '找不到這份計畫。'} /><Button onClick={() => navigate('/plans')}>返回計畫清單</Button></>;
  const eligible = can('plan.approve') && canApprovePlan(plan);
  return <><button className="back-link" onClick={() => navigate('/plans')}><ArrowLeft size={15} />所有計畫</button><PageIntro eyebrow={`PLAN ${shortId(plan.id)} / REVISION ${plan.revision}`} title={plan.title} description="逐步確認要執行的命令、使用者與依據。核准前不會派送這份計畫。" action={<Badge status={plan.status} />} /><div className="review-metadata"><div><span>建立時間</span><strong>{formatDate(plan.created_at)}</strong></div><div><span>核准有效期限</span><strong>{plan.approval_expires_at ? formatDate(plan.approval_expires_at) : '依伺服器核准政策'}</strong></div><div><span>授權種類</span><strong>{plan.document.authorization_kind === 'one_shot' ? '單次計畫' : plan.document.authorization_kind || '單次計畫'}</strong></div></div><InlineError message={error} /><div className="review-layout"><PlanDocumentView document={plan.document} /><aside className="review-aside"><ReviewApproval key={plan.id} contentHash={plan.content_hash} eligible={eligible} issues={plan.issues || []} onApprove={approve} busy={busy} />{['awaiting_approval', 'pending_approval', 'planned'].includes(plan.status) && can('plan.approve') && <section className="panel reject-panel"><h3>需要調整內容？</h3><Field label="退回原因"><textarea rows={3} value={rejectReason} onChange={event => setRejectReason(event.target.value)} placeholder="說明哪些步驟或依據需要補充" /></Field><Button onClick={reject} busy={busy} disabled={!rejectReason.trim()}><RotateCcw size={15} />退回計畫</Button></section>}<Notice>重複點擊或過期的核准不會建立新的授權。後端會再次驗證內容雜湊與目前狀態。</Notice></aside></div></>;
}
