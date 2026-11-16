import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { Field } from './ui';
it('opens on hover, allows hovering the content, and dismisses with Escape', async()=>{
 const user=userEvent.setup();render(<Field label="Partition" help="資源分組，不是硬碟分割區。"><input/></Field>);
 const trigger=screen.getByRole('button',{name:'Partition說明'});await user.hover(trigger);
 const tip=screen.getByRole('tooltip');expect(tip).toHaveTextContent('不是硬碟分割區');await user.hover(tip);expect(tip).toBeInTheDocument();
 await user.keyboard('{Escape}');expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
 expect(screen.getByLabelText('Partition',{exact:true})).toHaveAccessibleDescription('資源分組，不是硬碟分割區。');
});
it('supports keyboard focus and touch-style clicks with outside dismissal',async()=>{
 const user=userEvent.setup();render(<><Field label="叢集名稱" help="例如 research_103"><input/></Field><button>其他位置</button></>);
 await user.tab();expect(screen.getByRole('button',{name:'叢集名稱說明'})).toHaveFocus();expect(screen.getByRole('tooltip')).toBeInTheDocument();
 await user.keyboard('{Escape}');expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'叢集名稱說明'}));expect(screen.getByRole('tooltip')).toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'其他位置'}));await waitFor(()=>expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());
});
