import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { NodeCard } from './pages/Overview';
import type { Node } from './types';
const gpu = { index: 0, uuid: 'GPU-new', name: 'NVIDIA GeForce RTX 3080 Ti', memory_total_mb: 12288, memory_used_mb: 2340, utilization_percent: 8 };
const node = { id: 'wsl', alias: 'twcworker18', enabled: true, hardware: { gpus: [] }, snapshot: { status: 'online', source: 'remote', collected_at: '2026-11-15T13:46:49Z', gpu_error: null, gpus: [gpu] } } as unknown as Node;
it('shows current GPU readings even when initial hardware inventory is empty', () => {
 render(<NodeCard node={node} />);
 expect(screen.getByText('GeForce RTX 3080 Ti')).toBeVisible();
 expect(screen.getByText('8%')).toBeVisible();
 expect(screen.getByText('GPU 0 · 12 GB')).toBeVisible();
});
it('uses observed device identity instead of stale inventory', () => {
 render(<NodeCard node={{ ...node, hardware: { gpus: [{index:0, uuid:'GPU-old', name:'Old GPU',memory_mb:1024,in_scope:true}] } }} />);
 expect(screen.queryByText('Old GPU')).not.toBeInTheDocument();
 expect(screen.getByText('GeForce RTX 3080 Ti')).toBeVisible();
});
it('keeps known inventory visible if a GPU query fails without fabricating utilization', () => {
 render(<NodeCard node={{ ...node, hardware: { gpus: [{index:0,uuid:'GPU-old',name:'Known GPU',memory_mb:1024,in_scope:true}] }, snapshot:{...node.snapshot!,gpus:[],gpu_error:'query failed'} }} />);
 expect(screen.getByText('Known GPU')).toBeVisible();
 expect(screen.getByText('query failed')).toBeVisible();
 expect(screen.queryByText('8%')).not.toBeInTheDocument();
});
