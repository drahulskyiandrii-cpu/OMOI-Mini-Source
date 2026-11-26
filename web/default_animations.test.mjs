import fs from "node:fs/promises";
import crypto from "node:crypto";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRMAnimationLoaderPlugin } from "@pixiv/three-vrm-animation";
import assert from "node:assert/strict";
const folder = new URL("../assets/default-animations/", import.meta.url);
const names = (await fs.readdir(folder)).filter(n => n.endsWith(".vrma"));
const manifest=JSON.parse(await fs.readFile(new URL("LICENSES.json",folder),"utf8"));
assert.equal(names.length,27);
assert.deepEqual(new Set(names),new Set(manifest.files.map(e=>e.name)));
assert.equal(new Set(manifest.files.map(e=>e.sha256)).size,names.length);
const loader = new GLTFLoader();
loader.register(parser => new VRMAnimationLoaderPlugin(parser));
for (const name of names) {
  const bytes = await fs.readFile(new URL(name, folder));
  assert.equal(crypto.createHash("sha256").update(bytes).digest("hex"),manifest.files.find(e=>e.name===name).sha256,name);
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  const result = await loader.parseAsync(buffer, "");
  const clip = result.userData.vrmAnimations?.[0];
  assert.ok(clip, name);
  assert.ok(Number.isFinite(clip.duration) && clip.duration > 1, name);
  const rotations = clip.humanoidTracks.rotation;
  assert.ok(rotations.size>=19, name);
  for (const track of rotations.values()) {
    assert.ok(track.values.every(Number.isFinite), name);
    for(let i=0; i<track.values.length; i+=4) {
      assert.ok(Math.abs(Math.hypot(...track.values.slice(i,i+4))-1)<1e-5, name);
    }

  }
}
console.log("VRMA loader accepted all "+names.length+" clips, with finite unit rotations.");
