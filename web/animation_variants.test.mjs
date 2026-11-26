import test from "node:test";
import assert from "node:assert/strict";
import {AnimationVariants} from "./src/animation_variants.js";
test("rotate only available alternatives, primary first",()=>{
 const a=new AnimationVariants();
 a.update(["idle_alt1","idle_alt2","talking_alt2"]);
 assert.deepEqual(Array.from({length:5},()=>a.next("idle")),["idle","idle_alt1","idle_alt2","idle","idle_alt1"]);
 assert.deepEqual(Array.from({length:3},()=>a.next("talking")),["talking","talking_alt2","talking"]);
 assert.equal(a.next("welcome"),"welcome");
});
test("missing alternative rejected and avatar change resets rotation",()=>{
 const a=new AnimationVariants();
 a.update(["idle_alt1","idle_alt2"]);
 a.next("idle"); a.next("idle");
 a.reject("idle_alt1");
 assert.deepEqual(a.choices("idle"),["idle","idle_alt2"]);
 a.update(["idle_alt2"],true);
 assert.equal(a.next("idle"),"idle");
 assert.equal(a.next("idle"),"idle_alt2");
});
