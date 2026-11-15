import { act, render, screen, cleanup } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ConsoleContext, type ConsoleContextValue } from './context';
import { LoginUsersPanel } from './pages/Observability';
import { api } from './api';
vi.mock('./api', () => ({ api: vi.fn(), errorMessage: (e: Error) => e.message }));
const context = { data: {nodes:[{id:'host',alias:'Lab'}]}, navigate:vi.fn() } as unknown as ConsoleContextValue;
const state = (active=true, users=['alice','alice','bob']) => ({active,host_ids:['host'],results:{host:{status:'online',snapshot:{collected_at:new Date().toISOString(),login_users:users.map(username=>({username}))}}}});
const mount = async () => { render(<ConsoleContext.Provider value={context}><LoginUsersPanel /></ConsoleContext.Provider>); await act(async()=>{}); };
afterEach(()=>{cleanup();vi.useRealTimers();vi.clearAllMocks();});
it('deduplicates accounts and refreshes every 30 seconds without starting monitoring',async()=>{
 vi.useFakeTimers();vi.mocked(api).mockResolvedValue(state());await mount();
 expect(screen.getByText('2 人')).toBeVisible();expect(screen.getByText('alice、bob')).toBeVisible();
 vi.mocked(api).mockResolvedValue(state(true,[]));
 await act(async()=>{await vi.advanceTimersByTimeAsync(29999);});expect(api).toHaveBeenCalledTimes(1);
 await act(async()=>{await vi.advanceTimersByTimeAsync(1);});expect(screen.getByText('0 人')).toBeVisible();
 expect(api).toHaveBeenNthCalledWith(2,'/monitor');
});
it('hides cached names and counts when monitoring stops',async()=>{
 vi.useFakeTimers();vi.mocked(api).mockResolvedValue(state());await mount();
 vi.mocked(api).mockResolvedValue(state(false));await act(async()=>{await vi.advanceTimersByTimeAsync(30000);});
 expect(screen.getByText('未開啟監控')).toBeVisible();expect(screen.queryByText('alice、bob')).not.toBeInTheDocument();
});
it('does not present stale or failed observations as zero people',async()=>{
 const stale=state();stale.results.host.snapshot.collected_at=new Date(Date.now()-120000).toISOString();
 vi.mocked(api).mockResolvedValue(stale);await mount();
 expect(screen.getByText(/資料已過期/)).toBeVisible();expect(screen.queryByText('0 人')).not.toBeInTheDocument();
});
it('clears current readings after a polling error and cleans up on unmount',async()=>{
 vi.useFakeTimers();vi.mocked(api).mockResolvedValue(state());await mount();
 vi.mocked(api).mockRejectedValue(new Error('Network unavailable'));
 await act(async()=>{await vi.advanceTimersByTimeAsync(30000);});
 expect(screen.queryByText('alice、bob')).not.toBeInTheDocument();expect(screen.getByText('更新失敗')).toBeVisible();
 cleanup();await vi.advanceTimersByTimeAsync(60000);expect(api).toHaveBeenCalledTimes(2);
});
