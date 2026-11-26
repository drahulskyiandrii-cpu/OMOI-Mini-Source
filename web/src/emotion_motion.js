// Facial expression weights are independent of body clips and respect active speech.
const profiles=Object.freeze({
 neutral:{}, happy:{happy:.65}, amused:{happy:.8}, excited:{happy:.85,surprised:.25},
 sad:{sad:.7}, annoyed:{angry:.48}, shy:{happy:.24}, surprised:{surprised:.8}
});
const roles=Object.freeze({welcome:"happy",happy:"happy",laugh:"amused",sad:"sad",excited:"excited",annoyed:"annoyed",shy:"shy",surprised:"surprised"});
const keys=["happy","sad","angry","surprised","oh"];
const clamp=v=>Math.max(0,Math.min(1,v));
export function faceProfile(emotion,slot,preview,speech) {
 const role=roles[String(slot||"").replace(/_alt[12]$/,"")];
 const selected=role||(preview?"neutral":emotion);
 const out={...(profiles[selected]||profiles.neutral)};
 // The uploaded avatar has only smile and vowels. A short rounded mouth
 // conveys surprise even without a dedicated surprise morph.
 if(selected==="surprised"&&!speech)out.oh=.32;
 return out;
}
export class EmotionMotion {
 constructor(){this.reset();}
 reset(){this.values=Object.fromEntries(keys.map(k=>[k,0]));}
 update(manager,dt,emotion,slot,preview,speech,lip){
  const target=faceProfile(emotion,slot,preview,speech);
  const alpha=1-Math.exp(-Math.max(0,dt)*9);
  for(const k of keys){
   const next=k==="oh"&&speech?0:(target[k]||0);
   this.values[k]=k==="oh"&&speech?0:this.values[k]+(next-this.values[k])*alpha;
   if(Math.abs(this.values[k]-next)<.001)this.values[k]=next;
   const expr=manager?.getExpression(k);
   if(expr&&expr.binds?.length!==0)manager.setValue(k,clamp(this.values[k]));
  }
  if(manager?.getExpression("aa"))manager.setValue("aa",clamp(lip));
 }
 diagnostics(){return {...this.values};}
}


// A missing body clip may still express a short reaction on the face.
export class FaceReaction {
  constructor(){this.reset();}
  trigger(slot,now) {
    if (slot==="laugh" || slot==="surprised") {
      this.slot=slot; this.until=now+1400;
    }
  }
  current(now){return now<this.until?this.slot:null;}
  reset(){this.slot=null;this.until=0;}
}
