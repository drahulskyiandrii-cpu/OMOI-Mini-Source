// A transition starts at the current blend, not at the previous clip's full weight.
// This keeps total weight at one even when a new gesture interrupts a crossfade.
export class MotionBlend {
  constructor(duration=.28) {
    this.duration=duration;
    this.weights=new Map();
    this.start=new Map();
    this.target=null;
    this.elapsed=0;
  }
  switchTo(action) {
    if (action===this.target) return;
    const total=[...this.weights.values()].reduce((a,b)=>a+b,0);
    this.start=new Map();
    if(total>0) for(const [a,w] of this.weights)this.start.set(a,w/total);
    this.target=action;
    this.elapsed=total>0?0:this.duration;
    this.weights=new Map(this.start);
    this.weights.set(action, this.weights.get(action)||0);
    action.reset().setEffectiveWeight(0).play();
    this.step(0);
  }
  step(dt) {
    if(!this.target)return [];
    this.elapsed=Math.min(this.duration,this.elapsed+Math.max(0,dt));
    const t=this.duration>0?this.elapsed/this.duration:1;
    const s=t*t*(3-2*t);
    const retired=[];
    for(const action of this.weights.keys()) {
      const weight=(this.start.get(action)||0)*(1-s)+(action===this.target?s:0);
      action.setEffectiveWeight(weight);
      this.weights.set(action,weight);
      if(t===1&&action!==this.target){this.weights.delete(action);retired.push(action);}
    }
    if(t===1)this.start=new Map([[this.target,1]]);
    return retired;
  }
  reset() {
    this.weights.clear();this.start.clear();this.target=null;this.elapsed=0;
  }
}
