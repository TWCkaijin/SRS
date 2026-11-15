import { useEffect, useState } from 'react';
import type { MonitorResult } from './types';

type Point = { time: number; ms: number | null };
export function PingSparkline({ ping, clock }: { ping: MonitorResult['ping']; clock: number }) {
  const [points, setPoints] = useState<Point[]>([]);
  useEffect(() => {
    if (!ping) return;
    const time = Date.parse(ping.checked_at);
    if (!Number.isFinite(time)) return;
    setPoints(previous => previous.some(point => point.time === time) ? previous : [...previous, {
      time, ms: ping.status === 'ok' && ping.ms != null && Number.isFinite(ping.ms) ? ping.ms : null,
    }].slice(-30));
  }, [ping?.checked_at, ping?.status, ping?.ms]);
  const stale = ping && clock - Date.parse(ping.checked_at) > 30000;
  const label = !ping ? 'Ping 測試中' : stale ? 'Ping 已過期' : ping.status === 'ok' && ping.ms != null ? `Ping ${ping.ms.toFixed(1)} ms` : ping.status === 'timeout' ? 'Ping 無回應' : 'Ping 不可用';
  const maximum = Math.max(1, ...points.map(point => point.ms ?? 0));
  const start = points[0]?.time ?? 0, duration = Math.max(1, (points.at(-1)?.time ?? start) - start);
  const runs: string[][] = [];
  let previous: Point | undefined;
  points.forEach(point => {
    if (point.ms === null) { previous = undefined; return; }
    if (!previous || point.time - previous.time > 30000) runs.push([]);
    runs.at(-1)!.push(`${3 + (point.time - start) / duration * 84},${25 - point.ms / maximum * 21}`);
    previous = point;
  });
  return <span className="host-ping" title={`監控伺服器至主機的 ICMP Ping；最近 ${points.length} 筆，上限 30 筆；縱軸 0–${maximum.toFixed(1)} ms，缺失資料不連線。`}>
    <svg className={`ping-sparkline${stale ? ' ping-stale' : ''}`} viewBox="0 0 90 30" role="img" aria-label={`Ping 延遲趨勢，${label}，最近 ${points.length} 筆，範圍 0 至 ${maximum.toFixed(1)} 毫秒`}>
      <line x1="3" y1="27" x2="87" y2="27" className="ping-baseline" />
      {runs.map((run, i) => <g key={i}>{run.length > 1 && <polyline points={run.join(' ')} />} {run.map((point, j) => { const [cx, cy] = point.split(','); return <circle key={j} cx={cx} cy={cy} r="1.5" />; })}</g>)}
    </svg><span>{label}</span>
  </span>;
}
