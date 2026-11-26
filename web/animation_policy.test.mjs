import test from "node:test";
import assert from "node:assert/strict";
import {AnimationPolicy, LOOP_SLOTS} from "./src/animation_policy.js";
test("greeting survives audio start and finishes into talking",()=>{
 const a=new AnimationPolicy();
 assert.equal(a.response("welcome","happy"),"welcome");
 assert.equal(a.state("talking"),"welcome");
 assert.equal(a.finished("welcome",false),"talking");
 assert.equal(a.state("idle"),"idle");
});
test("short or missing greeting audio does not interrupt wave",()=>{
 const a=new AnimationPolicy();
 a.response("welcome","happy");
 assert.equal(a.state("idle"),"welcome");
 assert.equal(a.finished("welcome",false),"idle");
});
test("explicit turn reset cancels pending gesture",()=>{
 const a=new AnimationPolicy();
 a.response("welcome","happy");
 assert.equal(a.reset(),"idle");
 assert.equal(a.state("listening"),"listening");
});
test("explanation and emotion gestures preserve speech state",()=>{
 const a=new AnimationPolicy();
 a.state("talking");
 assert.equal(a.response("explaining","neutral"),"explaining");
 assert.equal(a.state("talking"),"explaining");
 assert.equal(a.finished("explaining",false),"talking");
 assert.equal(a.response("talking","sad"),"sad");
 assert.equal(a.finished("sad",false),"talk_gentle");
});
test("preview not interrupted by audio",()=>{
 const a=new AnimationPolicy();
 a.beginPreview();
 assert.equal(a.state("talking"),null);
 assert.equal(a.finished("welcome",true),null);
 a.endPreview();
 assert.equal(a.current(),"talking");
});
test("missing optional clips fall back safely",()=>{
 const a=new AnimationPolicy();
 assert.equal(a.state("thinking"),"thinking");
 assert.equal(a.failed("thinking",false),"idle");
 assert.equal(a.failed("idle",false),null);
 a.state("talking");
 a.response("talking","excited");
 assert.equal(a.failed("excited",false),"talk_enthusiastic");
 assert.equal(a.failed("talk_enthusiastic",false),"talking");
 assert.equal(a.failed("talking",false),"idle");
});
test("idle variation is one-shot, never during speaking",()=>{
 const a=new AnimationPolicy();
 assert.equal(a.canIdleGesture(),true);
 assert.equal(a.gesture("idle_shift"),"idle_shift");
 assert.equal(a.canIdleGesture(),false);
 assert.equal(a.finished("idle_shift",false),"idle");
 a.state("talking");
 assert.equal(a.canIdleGesture(),false);
 assert.equal(LOOP_SLOTS.has("idle_shift"),false);
});

test("speaking immediately preempts idle variation",()=>{
 const a=new AnimationPolicy();
 a.gesture("idle_shift");
 assert.equal(a.state("talking"),"talking");
});
