import { render } from '@testing-library/react';
import { expect, it } from 'vitest';
import { PingSparkline } from './PingSparkline';
it('deduplicates poll responses and breaks the line at failed samples', () => {
 const time=Date.now();
 const sample=(step:number,status:'ok'|'timeout'='ok')=>({status,ms:status==='ok'?step+1:null,checked_at:new Date(time+step*2000).toISOString()});
 const view=render(<PingSparkline ping={sample(0)} clock={time} />);
 view.rerender(<PingSparkline ping={sample(0)} clock={time} />);
 expect(view.container.querySelectorAll('circle')).toHaveLength(1);
 view.rerender(<PingSparkline ping={sample(1)} clock={time} />);
 expect(view.container.querySelectorAll('polyline')).toHaveLength(1);
 view.rerender(<PingSparkline ping={sample(2,'timeout')} clock={time} />);
 view.rerender(<PingSparkline ping={sample(3)} clock={time} />);
 expect(view.container.querySelectorAll('polyline')).toHaveLength(1);
 expect(view.container.querySelectorAll('circle')).toHaveLength(3);
 for(let i=4;i<40;i++)view.rerender(<PingSparkline ping={sample(i)} clock={time} />);
 expect(view.container.querySelectorAll('circle')).toHaveLength(30);
});
