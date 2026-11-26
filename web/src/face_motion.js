import * as THREE from "three";

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const radians = THREE.MathUtils.degToRad;

// Pointer is in logical WebView pixels, never a global desktop coordinate.
export function gazeTarget(point, rect) {
  if (!point || rect.width <= 0 || rect.height <= 0) return [0, 0];
  return [
    clamp((point[0]-rect.left-rect.width/2)/(rect.width/2),-1,1),
    clamp((rect.top+rect.height*.3-point[1])/(rect.height/2),-1,1),
  ];
}

export class BlinkMotion {
  constructor(random = Math.random) {
    this.random = random;
    this.remaining = 2 + random()*3;
    this.phase = -1;
  }
  step(dt) {
    if (this.phase < 0) {
      this.remaining -= dt;
      if (this.remaining > 0) return 0;
      this.phase = 0;
    }
    this.phase += dt;
    if (this.phase >= .19) {
      this.phase = -1;
      this.remaining = 2.5 + this.random()*3.5;
      return 0;
    }
    return this.phase < .075 ? this.phase/.075 : 1-(this.phase-.075)/.115;
  }
}

export class FaceMotion {
  constructor() {
    this.x=0; this.y=0; this.blink=0;
    this.blinks=new BlinkMotion();
    this.savedHead=null;
    this.headPose=new THREE.Quaternion();
    this.parentRotation=new THREE.Quaternion();
    this.delta=new THREE.Quaternion();
    this.localDelta=new THREE.Quaternion();
    this.euler=new THREE.Euler(0,0,0,"YXZ");
    this.target=new THREE.Object3D();
    this.eyeOrigin=new THREE.Vector3();
    this.blinkApplied=false;
    this.lookAt=null;
    this.previousTarget=null;
    this.previousAutoUpdate=true;
  }
  // Restore the animated pose BEFORE every mixer evaluation. This prevents
  // additive head rotation from accumulating when a clip has no head track.
  restoreHead() {
    if (this.savedHead) this.savedHead.quaternion.copy(this.headPose);
    this.savedHead=null;
  }
  releaseLook() {
    if (!this.lookAt) return;
    this.lookAt.target=this.previousTarget;
    this.lookAt.autoUpdate=this.previousAutoUpdate;
    if (!this.previousTarget) this.lookAt.reset();
    this.lookAt=null;
    this.previousTarget=null;
  }
  reset() {
    this.restoreHead();
    this.releaseLook();
    this.x=0; this.y=0; this.blink=0;
    this.blinks=new BlinkMotion();
    this.blinkApplied=false;
  }
  update(vrm,camera,rect,point,dt,follow,blinkEnabled) {
    const [x,y]=gazeTarget(follow?point:null,rect);
    const blend=1-Math.exp(-Math.max(0,dt)*7);
    this.x+=(x-this.x)*blend;
    this.y+=(y-this.y)*blend;
    if (Math.abs(this.x)<.0001) this.x=0;
    if (Math.abs(this.y)<.0001) this.y=0;
    const head=vrm.humanoid?.getNormalizedBoneNode("head");
    if (head?.parent && (this.x || this.y)) {
      this.savedHead=head;
      this.headPose.copy(head.quaternion);
      // World-space offset works for both VRM 0.x rotated scenes and VRM 1.0.
      // +Y yaw follows screen right, negative X pitch follows screen up.
      head.parent.getWorldQuaternion(this.parentRotation);
      this.delta.setFromEuler(this.euler.set(radians(-7*this.y),radians(12*this.x),0));
      this.localDelta.copy(this.parentRotation).invert().multiply(this.delta).multiply(this.parentRotation);
      head.quaternion.premultiply(this.localDelta).normalize();
    }
    if (follow && point && vrm.lookAt) {
      if (this.lookAt!==vrm.lookAt) {
        this.releaseLook();
        this.lookAt=vrm.lookAt;
        this.previousTarget=this.lookAt.target;
        this.previousAutoUpdate=this.lookAt.autoUpdate;
      }
      this.target.position.copy(camera.position);
      if (head) {
        head.getWorldPosition(this.eyeOrigin);
        this.target.position.x=this.eyeOrigin.x;
        this.target.position.y=this.eyeOrigin.y;
      }
      this.target.position.x+=this.x*1.6;
      this.target.position.y+=this.y*1.0;
      this.target.updateMatrixWorld(true);
      this.lookAt.target=this.target;
      this.lookAt.autoUpdate=true;
    } else this.releaseLook();
    this.blink=blinkEnabled?this.blinks.step(dt):0;
    const expressions=vrm.expressionManager;
    if (expressions && (blinkEnabled || this.blinkApplied)) {
      if (expressions.getExpression("blink")) expressions.setValue("blink",this.blink);
      else for (const name of ["blinkLeft","blinkRight"]) {
        if (expressions.getExpression(name)) expressions.setValue(name,this.blink);
      }
    }
    this.blinkApplied=blinkEnabled;
  }
  diagnostics() {
    return {yaw:12*this.x,pitch:-7*this.y,blink:this.blink,eyes:!!this.lookAt};
  }
}
