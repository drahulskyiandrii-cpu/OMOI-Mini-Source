// One body animation at a time; lip-sync is independent of gestures.
export const LOOP_SLOTS = new Set(["idle","listening","thinking","talking","talk_gentle","talk_enthusiastic"]);
export class AnimationPolicy {
  constructor(){ this.reset(); }
  reset(){ this.base="idle"; this.protectedGesture=null; this.preview=false; this.speech="talking"; return "idle"; }
  current(){ return this.protectedGesture || (this.base==="talking"?this.speech:this.base); }
  state(next){
    if (!["idle","listening","thinking","talking"].includes(next)) return null;
    this.base=next;
    if(next!=="idle" && this.protectedGesture==="idle_shift") this.protectedGesture=null;
    if(next==="listening" || next==="thinking") {this.protectedGesture=null; this.speech="talking";}
    return this.preview?null:this.current();
  }
  gesture(slot){
    if(this.preview || this.protectedGesture) return null;
    this.protectedGesture=slot;
    return slot;
  }
  response(slot,emotion){
    if(this.preview) return null;
    if(slot!=="talking" && slot!=="idle") {
      // A reply-specific greeting or explanation must complete even if audio begins.
      this.protectedGesture=slot;
      return slot;
    }
    this.speech=emotion==="sad"?"talk_gentle":emotion==="excited"?"talk_enthusiastic":"talking";
    const gesture={sad:"sad",excited:"excited",amused:"laugh",happy:"happy"}[emotion];
    return gesture?this.gesture(gesture):this.current();
  }
  canIdleGesture(){return !this.preview && this.base==="idle" && !this.protectedGesture;}
  beginPreview(){this.preview=true;this.protectedGesture=null;}
  endPreview(){this.preview=false;}
  finished(slot,preview){
    if(preview)return null; // Keep final preview pose until explicit exit.
    if(this.protectedGesture===slot)this.protectedGesture=null;
    return this.current();
  }
  failed(slot,preview){
    if(preview)return null;
    if(this.protectedGesture===slot)this.protectedGesture=null;
    if(slot===this.speech)this.speech="talking";
    if(slot==="thinking" || slot==="listening" || slot==="talking")this.base="idle";
    const next=this.current();
    return next===slot?null:next;
  }
}
