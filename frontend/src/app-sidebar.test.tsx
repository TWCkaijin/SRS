import { createRef, type ComponentProps } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Activity, CalendarClock, Server } from 'lucide-react';
import { afterEach, expect, it, vi } from 'vitest';
import { AppSidebar } from './AppSidebar';

const items = [
  { key: 'nodes', label: '主機監控', icon: Server },
  { key: 'training', label: 'Slurm 叢集設定', icon: Activity },
  { key: 'schedules', label: '定時監控', icon: CalendarClock },
];

function mount(overrides: Partial<ComponentProps<typeof AppSidebar>> = {}) {
  const onPinnedChange = vi.fn();
  const onCloseMobile = vi.fn();
  const view = render(<AppSidebar
    activePage="training"
    healthVersion="0.1.0"
    mobileOpen={false}
    smallScreen={false}
    pinned={false}
    sidebarRef={createRef<HTMLElement>()}
    navItems={items}
    onPinnedChange={onPinnedChange}
    onCloseMobile={onCloseMobile}
    {...overrides}
  />);
  return { ...view, onPinnedChange, onCloseMobile };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it('shows a stable rail, previews on mouse hover, and closes after the exit delay', () => {
  vi.useFakeTimers();
  const { container } = mount();
  const sidebar = container.querySelector('aside')!;
  expect(sidebar).toHaveClass('sidebar-rail');
  fireEvent.pointerEnter(sidebar, { pointerType: 'mouse' });
  expect(sidebar).toHaveClass('sidebar-expanded', 'sidebar-preview');
  fireEvent.pointerLeave(sidebar, { pointerType: 'mouse' });
  act(() => vi.advanceTimersByTime(219));
  expect(sidebar).toHaveClass('sidebar-expanded');
  act(() => vi.advanceTimersByTime(1));
  expect(sidebar).toHaveClass('sidebar-rail');
});

it('temporarily expands for keyboard navigation and Escape returns focus to the pin control', async () => {
  const { container } = mount();
  const sidebar = container.querySelector('aside')!;
  const link = screen.getByRole('link', { name: '主機監控' });
  fireEvent.focus(link);
  expect(sidebar).toHaveClass('sidebar-preview');
  fireEvent.keyDown(sidebar, { key: 'Escape' });
  expect(sidebar).toHaveClass('sidebar-rail');
  expect(screen.getByRole('button', { name: '固定展開側欄' })).toHaveFocus();
});

it('pins explicitly, identifies the current route, and keeps all rail links named', async () => {
  const user = userEvent.setup();
  const { onPinnedChange } = mount();
  expect(screen.getByRole('link', { name: 'Slurm 叢集設定' })).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('link', { name: '主機監控' })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '定時監控' })).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '固定展開側欄' }));
  expect(onPinnedChange).toHaveBeenCalledWith(true);
});

it('acts as an inert mobile dialog while closed and closes after navigation', async () => {
  const user = userEvent.setup();
  const closed = mount({ smallScreen: true });
  expect(closed.container.querySelector('aside')).toHaveAttribute('inert');
  expect(closed.container.querySelector('aside')).toHaveAttribute('aria-hidden', 'true');
  closed.unmount();

  const open = mount({ smallScreen: true, mobileOpen: true });
  const dialog = screen.getByRole('dialog', { name: '主要導覽' });
  expect(dialog).toHaveAttribute('aria-modal', 'true');
  await user.click(screen.getByRole('link', { name: '定時監控' }));
  expect(open.onCloseMobile).toHaveBeenCalled();
});
