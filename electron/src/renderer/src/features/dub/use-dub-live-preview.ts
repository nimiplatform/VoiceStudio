import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiFetch } from '@/lib/api/client';
import { acquireSynthesis } from '@/lib/synthesis-lock';
import { beginAppActivity } from '@/lib/app-activity';
import { claimPlayback } from '@/lib/audio/playback';
import { segmentGenInputs } from '../../../../../../frontend/src/utils/segments';
import type { DubSegment } from './dub-session';
export const LIVE_DUB_PREVIEW_DELAY_MS=400;
type Active={id:string;controller:AbortController;audio?:HTMLAudioElement;url?:string;release:()=>void;finish:()=>void;releasePlayback?:()=>void};
export function useDubLivePreview({enabled,language,jobId}:{enabled:boolean;language:string;jobId?:string|null}){
 const { t } = useTranslation();
 const [liveSegmentId,setLiveSegmentId]=useState<string|null>(null);
 const active=useRef<Active|null>(null),timer=useRef<ReturnType<typeof setTimeout>|null>(null);
 const stop=useCallback(()=>{
  if(timer.current)clearTimeout(timer.current);timer.current=null;
  const task=active.current;active.current=null;
  if(task){task.controller.abort();task.audio?.pause();task.releasePlayback?.();task.release();task.finish();if(task.url)URL.revokeObjectURL(task.url);}
  setLiveSegmentId(null);
 },[]);
 const start=useCallback(async(segment:DubSegment,text:string)=>{
  stop();if(!enabled||!jobId||!text.trim())return;
  const release=acquireSynthesis();if(!release)return;
  const task:Active={id:segment.id,controller:new AbortController(),release,finish:beginAppActivity('synthesis')};
  active.current=task;setLiveSegmentId(segment.id);
  try{
   const input=segmentGenInputs(segment);
   const response=await apiFetch('/dub/preview-segment/'+encodeURIComponent(jobId),{method:'POST',headers:{'Content-Type':'application/json'},signal:task.controller.signal,body:JSON.stringify({segment_id:segment.id,text,language:input.target_lang||language,profile_id:input.profile_id,speed:input.speed||1,instruct:input.instruct})});
   const blob=await response.blob();if(active.current!==task)return;
   task.url=URL.createObjectURL(blob);task.audio=new Audio(task.url);
   task.releasePlayback=claimPlayback(stop,'dub-live-'+segment.id);
   task.audio.addEventListener('ended',stop,{once:true});task.audio.addEventListener('error',stop,{once:true});
   await task.audio.play();
  }catch(error){if(active.current===task){if(!task.controller.signal.aborted)toast.error(t('tts_errors.error_prefix',{message:error instanceof Error?error.message:String(error)}));stop();}}
 },[enabled,language,jobId,stop,t]);
 const onEdit=useCallback((segment:DubSegment,text:string)=>{stop();if(enabled)timer.current=setTimeout(()=>{timer.current=null;void start(segment,text);},LIVE_DUB_PREVIEW_DELAY_MS);},[enabled,start,stop]);
 const onToggle=useCallback((segment:DubSegment)=>{if(active.current?.id===segment.id)stop();else void start(segment,segment.text);},[start,stop]);
 useEffect(()=>{if(!enabled)stop();return stop;},[enabled,stop]);
 return {liveSegmentId,onEdit,onToggle,stop};
}
