import {fireEvent,render,screen} from '@testing-library/react';import {expect,it,vi} from 'vitest';import type {ReactNode} from 'react';
vi.mock('@tanstack/react-router',()=>({Link:({to,children}:{to:string;children:ReactNode})=><a href={to}>{children}</a>}));
vi.mock('react-i18next',()=>({useTranslation:()=>({t:(key:string)=>key})}));
import {DictationSetup} from './dictation-setup';
it('configures speech in Nimi and refreshes after the user returns',()=>{const ready=vi.fn();render(<DictationSetup onReady={ready}/>);expect(screen.getByRole('link')).toHaveAttribute('href','/settings/models');fireEvent.click(screen.getByRole('button',{name:'common.retry'}));expect(ready).toHaveBeenCalledOnce()});
