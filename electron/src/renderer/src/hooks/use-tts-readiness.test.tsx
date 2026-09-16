import {renderHook,waitFor} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {beforeEach,expect,it,vi} from 'vitest';
import type {ReactNode} from 'react';
const state=vi.hoisted(()=>({stage:'ready',get:vi.fn()}));
vi.mock('@/lib/nimi-client',()=>({getNimiClient:()=>({aiConfig:{get:state.get}})}));
vi.mock('./use-backend-status',()=>({useBackendStatus:()=>({stage:state.stage})}));
import {useTtsReadiness} from './use-tts-readiness';
function wrapper(){const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});return ({children}:{children:ReactNode})=><QueryClientProvider client={client}>{children}</QueryClientProvider>}
beforeEach(()=>{state.stage='ready';state.get.mockReset()});
it('requires both speech and voice creation for cloning',async()=>{state.get.mockResolvedValue({effectiveSelections:[{capabilityContract:'audio.synthesize',state:'ready'}]});const {result}=renderHook(()=>useTtsReadiness('clone'),{wrapper:wrapper()});await waitFor(()=>expect(result.current).toBe('engine'))});
it('accepts Runtime-ready capabilities without inspecting old local engines',async()=>{state.get.mockResolvedValue({effectiveSelections:[{capabilityContract:'audio.synthesize',state:'ready'},{capabilityContract:'voice.create',state:'ready'}]});const {result}=renderHook(()=>useTtsReadiness('clone'),{wrapper:wrapper()});await waitFor(()=>expect(result.current).toBeNull())});
it('keeps a disconnected session blocked',async()=>{state.get.mockRejectedValue(Error('session revoked'));const {result}=renderHook(()=>useTtsReadiness(),{wrapper:wrapper()});await waitFor(()=>expect(result.current).toBe('engine'))});
it('waits for the real business worker',()=>{state.stage='starting';state.get.mockResolvedValue({effectiveSelections:[]});const {result}=renderHook(()=>useTtsReadiness(),{wrapper:wrapper()});expect(result.current).toBe('loading')});
