import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import {MotionBlend} from "./src/motion_blend.js";
test("first motion full weight, interruptions preserve the current pose",()=>{
 const root=new THREE.Object3D(), mixer=new THREE.AnimationMixer(root);
 const make=v=>mixer.clipAction(new THREE.AnimationClip("clip",2,[new THREE.NumberKeyframeTrack(".position[x]",[0,2],[v,v])]));
 const a=make(1),b=make(2),c=make(3);
 const blend=new MotionBlend(.28);
 blend.switchTo(a);mixer.update(0);
 assert.equal(root.position.x,1);
 blend.switchTo(b);blend.step(.10);mixer.update(0);
 const before=root.position.x;
 blend.switchTo(c);mixer.update(0);
 assert.ok(Math.abs(root.position.x-before)<1e-7);
 for(let i=0;i<20;i++){
  const retired=blend.step(.02);mixer.update(.02);
  for(const action of retired){action.stop();mixer.uncacheAction(action.getClip());}
  assert.ok(Math.abs([...blend.weights.values()].reduce((x,y)=>x+y,0)-1)<1e-9);
 }
 assert.equal(blend.weights.size,1);assert.ok(Math.abs(root.position.x-3)<1e-7);
});
test("reset releases references to an old avatar",()=>{
 const blend=new MotionBlend();
 blend.reset();assert.equal(blend.target,null);assert.deepEqual(blend.step(.1),[]);
});
