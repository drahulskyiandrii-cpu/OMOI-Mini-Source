// Only explicitly available, fixed-index variants; no arbitrary paths.
export class AnimationVariants {
  constructor(){this.available=new Set();this.last=new Map();}
  update(slots,reset=false){
    this.available=new Set(Array.isArray(slots)?slots:[]);
    if(reset)this.last.clear();
  }
  choices(role){return [role,...[1,2].map(i=>role+"_alt"+i).filter(s=>this.available.has(s))];}
  next(role){
    const choices=this.choices(role),previous=this.last.get(role);
    const chosen=choices[(choices.indexOf(previous)+1)%choices.length];
    this.last.set(role,chosen);
    return chosen;
  }
  reject(slot){this.available.delete(slot);}
}
