import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import {gazeTarget,BlinkMotion,FaceMotion} from "./src/face_motion.js";
const rect={left:100,top:50,width:400,height:600};
test("bounded stage-relative cursor mapping",()=>{
 assert.deepEqual(gazeTarget([300,230],rect),[0,0]);
 assert.deepEqual(gazeTarget([10000,-10000],rect),[1,1]);
 assert.deepEqual(gazeTarget(null,rect),[0,0]);
});
test("blink closes and reopens within bounded interval",()=>{
 const b=new BlinkMotion(()=>0);
 let peak=0;
 for(let i=0;i<230;i++){const v=b.step(.01);assert.ok(v>=0&&v<=1);peak=Math.max(peak,v);}
 assert.ok(peak>.9);assert.equal(b.phase,-1);
});
test("head offsets do not accumulate and restore VRM0 orientation",()=>{
 const root=new THREE.Group();root.rotation.y=Math.PI;
 const head=new THREE.Object3D();root.add(head);root.updateMatrixWorld(true);
 const initial=head.quaternion.clone();
 const lookAt={target:null,autoUpdate:true,reset(){}};
 const vrm={humanoid:{getNormalizedBoneNode:()=>head},lookAt};
 const camera=new THREE.PerspectiveCamera();camera.position.z=4;
 const motion=new FaceMotion();
 for(let i=0;i<300;i++){
  motion.restoreHead();motion.update(vrm,camera,rect,[10000,230],.016,true,false);
 }
 assert.ok(head.quaternion.angleTo(initial)<.22);
 assert.ok(motion.diagnostics().yaw>11);
 assert.equal(lookAt.target,motion.target);
 motion.reset();assert.ok(head.quaternion.angleTo(initial)<1e-7);
 assert.equal(lookAt.target,null);
});
test("disabled follow returns to neutral without replacing lip expressions",()=>{
 const values={aa:.6};const vrm={expressionManager:{getExpression:n=>n==="blink",setValue:(n,v)=>values[n]=v}};
 const camera=new THREE.PerspectiveCamera(),motion=new FaceMotion();
 for(let i=0;i<100;i++)motion.update(vrm,camera,rect,[500,230],.016,true,true);
 for(let i=0;i<200;i++)motion.update(vrm,camera,rect,null,.016,false,true);
 assert.equal(motion.diagnostics().yaw,0);
 assert.equal(values.aa,.6);
});
