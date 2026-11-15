import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import * as api from './api';
import { ConsoleContext } from './context';
import { SlurmSetupPage } from './pages/SlurmSetup';
import type { Node } from './types';
const node={id:'n1',alias:'lab',hostname:'192.0.2.1',hardware:{},ssh_connection:{enabled:true,display_name:'Lab',hostname:'192.0.2.1',username:'researcher',host_key_sha256:'SHA256:fixture'}} as Node;
const row={host_id:'n1',facts:{hostname:'lab',cpus:8,memory_mb:16384,gpus:[{index:0,uuid:'GPU-test',name:'GPU'}]},issues:[]};
const plan={token:'fixture-token',id:'plan-1',ready:true,content_hash:'a'.repeat(64),rows:[row],document:{actions:['設定 Slurm'],files:{n1:{'/etc/slurm/slurm.conf':'ClusterName=research'}},packages:{n1:[]},nodes:[{host_id:'n1'}]}};
function mount(){return render(<ConsoleContext.Provider value={{session:{username:'admin',csrf_token:'c',permissions:['*'],mode:'demo'},data:{nodes:[node],projects:[],drafts:[],plans:[],executions:[],schedules:[],jobs:[]},can:()=>true,refresh:vi.fn(),navigate:vi.fn(),notify:vi.fn()}}><SlurmSetupPage/></ConsoleContext.Provider>);}
afterEach(()=>vi.restoreAllMocks());
it('leaves GPUs unassigned, invalidates edited plans and clears sudo input after apply',async()=>{
 const send=vi.spyOn(api,'post').mockImplementation(async path=>path==='/slurm/inspect'?{rows:[row]}:path==='/slurm/preview'?plan:{status:'running'});
 vi.spyOn(api,'api').mockResolvedValue({status:'configured',results:{},verification:null});
 mount();await userEvent.click(screen.getByRole('checkbox',{name:/Lab/}));
 await userEvent.click(screen.getByRole('button',{name:'檢查 1 台主機'}));
 const gpu=await screen.findByRole('checkbox',{name:/GPU 0/});expect(gpu).not.toBeChecked();
 await userEvent.click(screen.getByRole('button',{name:'產生批次設定預覽'}));
 const apply=await screen.findByRole('button',{name:'批次安裝並啟用 Slurm'});expect(apply).toBeDisabled();
 await userEvent.type(screen.getByLabelText('Lab sudo 密碼'),'private-sudo');
 await userEvent.click(screen.getByRole('checkbox',{name:/我已審查/}));expect(apply).toBeEnabled();
 await userEvent.type(screen.getByLabelText('Partition 名稱'),'2');expect(screen.queryByRole('button',{name:'批次安裝並啟用 Slurm'})).not.toBeInTheDocument();
 await userEvent.click(screen.getByRole('button',{name:'產生批次設定預覽'}));await screen.findByRole('button',{name:'批次安裝並啟用 Slurm'});
 expect(screen.getByLabelText('Lab sudo 密碼')).toHaveValue('');
 await userEvent.type(screen.getByLabelText('Lab sudo 密碼'),'private-sudo');await userEvent.click(screen.getByRole('checkbox',{name:/我已審查/}));
 await userEvent.click(screen.getByRole('button',{name:'批次安裝並啟用 Slurm'}));
 await waitFor(()=>expect(send).toHaveBeenCalledWith('/slurm/apply',{token:plan.token,content_hash:plan.content_hash,sudo_passwords:{n1:'private-sudo'}}));
 expect(screen.queryByDisplayValue('private-sudo')).not.toBeInTheDocument();
});
it('keeps apply disabled for existing Slurm hosts',async()=>{
 vi.spyOn(api,'post').mockImplementation(async path=>path==='/slurm/inspect'?{rows:[row]}:{...plan,ready:false,rows:[{...row,issues:['既有 Slurm 設定不可覆寫']}]});
 mount();await userEvent.click(screen.getByRole('checkbox',{name:/Lab/}));await userEvent.click(screen.getByRole('button',{name:'檢查 1 台主機'}));await screen.findByRole('checkbox',{name:/GPU 0/});await userEvent.click(screen.getByRole('button',{name:'產生批次設定預覽'}));
 expect(await screen.findByRole('button',{name:'批次安裝並啟用 Slurm'})).toBeDisabled();expect(screen.getByRole('checkbox',{name:/我已審查/})).toBeDisabled();
});
