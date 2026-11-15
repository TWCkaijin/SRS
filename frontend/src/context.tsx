import { createContext, useContext } from 'react';
import type { OverviewData, Session } from './types';

export interface ConsoleContextValue {
  session: Session;
  data: OverviewData;
  refresh: () => Promise<void>;
  navigate: (path: string) => void;
  notify: (message: string, kind?: 'success' | 'error') => void;
  can: (permission: string) => boolean;
}
export const ConsoleContext = createContext<ConsoleContextValue | null>(null);
export function useConsole() { const value = useContext(ConsoleContext); if (!value) throw new Error('ConsoleContext missing'); return value; }
