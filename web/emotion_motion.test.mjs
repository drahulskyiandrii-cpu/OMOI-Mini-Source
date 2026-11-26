import test from "node:test";
import assert from "node:assert/strict";
import {EmotionMotion,faceProfile,FaceReaction} from "./src/emotion_motion.js";
test("preview emotions use the actual selected gesture, not previous speech",()=>{
 assert.deepEqual(faceProfile("sad","shy_alt2",true,false),{happy:.24});
 assert.deepEqual(faceProfile("happy","idle",true,false),{});
 assert.equal(faceProfile("neutral","surprised",true,false).oh,.32);
 assert.equal(faceProfile("neutral","surprised",true,true).oh,undefined);
});
test("smooth face transition, missing morphs, and speech priority",()=>{
 const writes={};
 const manager={getExpression:k=>k==="sad"?{binds:[]}:k==="surprised"?null:{binds:[1]},setValue:(k,v)=>writes[k]=v};
 const m=new EmotionMotion();
 m.update(manager,.016,"happy",null,false,false,0);
 assert.ok(writes.happy>0&&writes.happy<.65);
 for(let i=0;i<100;i++)m.update(manager,.016,"happy",null,false,false,0);
 assert.equal(writes.happy,.65);
 m.update(manager,.016,"sad",null,false,false,0);
 assert.ok(writes.happy>0&&writes.happy<.65);
 assert.equal(writes.sad,undefined);
 for(let i=0;i<80;i++)m.update(manager,.016,"neutral","surprised",true,false,0);
 assert.ok(writes.oh>.3);
 m.update(manager,.016,"neutral","surprised",true,true,.7);
 assert.equal(writes.oh,0);assert.equal(writes.aa,.7);
 m.reset();assert.equal(m.diagnostics().happy,0);
});


test("missing reaction clips still allow a bounded facial response",()=>{
 const r=new FaceReaction();
 r.trigger("surprised",100);
 assert.equal(r.current(1499),"surprised");
 assert.equal(r.current(1500),null);
 r.trigger("laugh",2000);
 assert.equal(r.current(2100),"laugh");
 r.reset();assert.equal(r.current(2100),null);
});
