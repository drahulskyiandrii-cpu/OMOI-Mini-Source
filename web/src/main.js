import "./style.css";
import { MotionBlend } from "./motion_blend.js";
import { EmotionMotion, FaceReaction } from "./emotion_motion.js";
import { FaceMotion } from "./face_motion.js";
import { AnimationVariants } from "./animation_variants.js";
import { AnimationPolicy, LOOP_SLOTS } from "./animation_policy.js";
import { t, captureStatic, setLocale } from "./i18n.js";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from "@pixiv/three-vrm-animation";

const $ = (id) => document.getElementById(id);
let bridge = null, vrm = null, mixer = null;
window.__omoiDiagnostics = {bridgeReady:false, avatarLoaded:false, animationLoaded:false, avatarError:false};
let turnEmotion = "neutral", targetLip = 0, lip = 0, recording = false;
let settings = { llm: "cloud", stt: "cloud", tts: "cartesia", language: "de", speak: true, greeting: true, mouse_tracking: true, prewarm_local_models: false };
let activeAnimationToken = 0;
const animationPolicy = new AnimationPolicy();
let requestedSlot = null, requestedPreview = false, idleSince = performance.now();
const motionBlend = new MotionBlend();
let avatarLoadToken = 0;
let activeFinishListener = null;
let previewPendingSlot = null;
let otherAvatarAssignments = 0;
let activeScreen = null, activeCategory = "general", activeStreamMessage = null;
let reviewTurn = null, reviewFloor = 0, dockOpen = false;
let expandedToolbar = false;
let voiceWorking = false, audioPlaying = false, voiceRevision = -1;
let llmStatus = {}, lastStatus = "Bereit", lastMicrophones = null, lastRuntimeSources = {}, lastRuntimeReport = null;
const roles = {
  welcome: "Begrüßung", idle: "Ruhe", listening: "Zuhören", talking: "Sprechen",
  explaining: "Erklären", happy: "Freude", laugh: "Lachen", nod: "Nicken",
  surprised: "Überraschung",
  thinking: "Nachdenken", idle_shift: "Gewichtsverlagerung",
  sad: "Traurigkeit", excited: "Begeisterung", shy: "Schüchtern",
  annoyed: "Genervt", shake_head: "Kopfschütteln",
  talk_gentle: "Sanft sprechen", talk_enthusiastic: "Lebhaft sprechen",
};
const animationRows = {};
const variants = new AnimationVariants();
const baseRoles = Object.keys(roles);
for (const role of baseRoles) for (const i of [1,2]) roles[role+"_alt"+i] = "Alternative "+i;
let idleVariation = false;
let availablePrimary = null;
function updateAlternatives(slots,reset=false,primary) {
  if(Array.isArray(primary))availablePrimary=new Set(primary);
  variants.update(slots,reset);
  for (const role of baseRoles) {
    const row=animationRows[role];
    if(row && availablePrimary) {
      row.preview.disabled=!availablePrimary.has(role) && row.confirm.hidden;
      if(!row.current && row.confirm.hidden)row.label.textContent=t(availablePrimary.has(role)?"Standardanimation":"Nicht belegt");
    }
    if(row?.alternatives)row.alternatives.textContent=t("Alternativen")+" ("+(variants.choices(role).length-1)+"/2)";
    for(const i of [1,2]) {
      const key=role+"_alt"+i, alt=animationRows[key];
      if(alt) {
        alt.preview.disabled=!variants.available.has(key) && alt.confirm.hidden;
        if(!alt.current && alt.confirm.hidden)alt.label.textContent=t(variants.available.has(key)?"Standardanimation":"Nicht belegt");
      }
    }
  }
}

function updateAnimationLabel(slot, filename, pending = false) {
  const row = animationRows[slot];
  if (!row) return;
  if (!pending) row.current = filename || null;
  row.label.textContent = pending ? (settings.ui_language==="ru" ? "Предпросмотр: " : "Vorschau: ") + filename : t(row.current || "Standardanimation");
  row.confirm.hidden = !pending;
  row.cancel.hidden = !pending;
  row.confirm.disabled = true;
}

function updateAnimationSummary() {
  const count = Object.values(animationRows).filter((row) => !!row.current).length;
  $("avatar-animation-summary").textContent = settings.ui_language==="ru"
    ? "Сохранено анимаций: " + count + ". ✓ — сохранить."
    : count + " gespeicherte Animationen. Vorschau mit ✓ speichern.";
}

function closeAnimationPreview() {
  animationPolicy.endPreview();
  requestedSlot=null;
  requestAnimation(animationPolicy.current());
  $("animation-preview-actions").hidden = true;
  $("preview-save").disabled = true;
  $("preview-save").hidden = true;
  setExpanded(true);
  showCategory("avatar");
  setScreen("settings");
}

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 40);
// Weniger Überbelichtung bei hellen MToon-/PBR-Materialien.
camera.position.set(0, 1.07, 4.15);
camera.lookAt(0, 1.04, 0);
scene.add(new THREE.HemisphereLight(0xe9f0ff, 0x6b7890, 0.85));
const light = new THREE.DirectionalLight(0xfff4e9, 1.15);
light.position.set(1.7, 3.5, 2.5);
scene.add(light);
let renderer = null;
try {
  renderer = new THREE.WebGLRenderer({canvas: $("scene"), alpha: true, antialias: true, premultipliedAlpha: false});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
} catch (error) {
  renderer = null;
  window.__omoiDiagnostics.rendererUnavailable = true;
  console.warn("3D renderer unavailable; controls remain usable", error.message);
}

function resize() {
  const stage = $("stage");
  const w = Math.max(1, stage.clientWidth), h = Math.max(1, stage.clientHeight);
  renderer?.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
const stageObserver = new ResizeObserver(resize);
stageObserver.observe($("stage"));
resize();

const modelLoader = new GLTFLoader();
modelLoader.register((parser) => new VRMLoaderPlugin(parser));
const animationLoader = new GLTFLoader();
animationLoader.register((parser) => new VRMAnimationLoaderPlugin(parser));
const clock = new THREE.Clock();
const faceMotion = new FaceMotion();
const emotionMotion = new EmotionMotion();
const faceReaction = new FaceReaction();
let visibleMotion = null;
let pointer = null, cursorPending = false, cursorAt = -Infinity;
function pollCursor() {
  if (!settings.mouse_tracking || !renderer || !vrm || document.hidden || animationPolicy.preview || animationPolicy.protectedGesture) {
    pointer = null;
    return;
  }
  const now = performance.now();
  if (cursorPending || now-cursorAt<80 || !bridge?.cursorPosition) return;
  cursorAt=now;
  cursorPending=true;
  bridge.cursorPosition((point) => {
    cursorPending=false;
    pointer=Array.isArray(point) && point.length===2 && point.every(Number.isFinite) ? point : null;
  });
}

function renderLoop() {
  requestAnimationFrame(renderLoop);
  const dt = Math.min(clock.getDelta(), 0.06);
  // Fit the avatar vertically and horizontally in its actual reserved stage.
  const targetZ = 4.15 * Math.max(1, 0.56 / Math.max(0.1, camera.aspect));
  camera.position.z += (targetZ - camera.position.z) * Math.min(1, dt * 5);
  faceMotion.restoreHead();
  if (mixer) {
    const retired = motionBlend.step(dt);
    mixer.update(dt);
    for (const action of retired) {
      action.stop();
      mixer.uncacheAction(action.getClip());
    }
  }
  if (vrm && animationPolicy.canIdleGesture() && performance.now()-idleSince>18000) {
    idleSince=performance.now();
    idleVariation=!idleVariation;
    if(idleVariation && variants.choices("idle").length>1) {
      playAnimation("idle",false,false,true);
    } else requestAnimation(animationPolicy.gesture("idle_shift"));
  }
  if (vrm) {
    // Die Mundform hat Vorrang vor der Körperanimation.
    lip += (targetLip - lip) * Math.min(1, dt * 14);
    emotionMotion.update(vrm.expressionManager,dt,turnEmotion,
      (!animationPolicy.preview && faceReaction.current(performance.now())) || visibleMotion,
      animationPolicy.preview, audioPlaying || voiceWorking || recording || lip>.02 || targetLip>.02,lip);
    window.__omoiDiagnostics.face = emotionMotion.diagnostics();
    pollCursor();
    faceMotion.update(vrm, camera, $("stage").getBoundingClientRect(), pointer, dt,
      !!settings.mouse_tracking && !animationPolicy.preview && !animationPolicy.protectedGesture,
      !animationPolicy.preview);
    vrm.update(dt);
    window.__omoiDiagnostics.gaze = faceMotion.diagnostics();
  }
  renderer?.render(scene, camera);
}
renderLoop();

function send(method, ...args) {
  if (bridge && typeof bridge[method] === "function") bridge[method](...args);
}
function syncDock() {
  const visible = reviewTurn !== null ? "stt-review-panel" : activeScreen;
  for (const id of ["settings", "chat", "stt-review-panel"]) $(id).hidden = id !== visible;
  const open = visible !== null;
  $("dock").hidden = !open;
  $("workspace").classList.toggle("dock-open", open);
  $("settings-toggle").setAttribute("aria-pressed", String(visible === "settings"));
  $("chat-toggle").setAttribute("aria-pressed", String(visible === "chat"));
  if (open !== dockOpen) {
    dockOpen = open;
    send("setDockOpen", open);
  }
  resize();
  updateVoiceButton();
}
function setScreen(screen) {
  activeScreen = screen;
  syncDock();
}
function showCategory(category) {
  const tabs = [...document.querySelectorAll("[data-settings-tab]")];
  if (!tabs.some(tab => tab.dataset.settingsTab === category)) return;
  activeCategory = category;
  for (const tab of tabs) tab.setAttribute("aria-pressed", String(tab.dataset.settingsTab === category));
  for (const page of document.querySelectorAll("[data-settings-page]")) page.hidden = page.dataset.settingsPage !== category;
  $("settings-content").scrollTop = 0;
}
function status(text) {
  lastStatus = text || "Bereit";
  $("state").textContent = t(lastStatus);
}
function bubble(text, important = false) {
  const el = $("bubble");
  el.textContent = text;
  el.hidden = !text;
  $("feedback").classList.toggle("important", !!text && important);
  $("dismiss-feedback").hidden = !text;
}
function appendMessage(role, text) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + (role === "user" ? "user" : "assistant");
  wrap.textContent = text;
  $("messages").appendChild(wrap);
  $("messages").scrollTop = $("messages").scrollHeight;
}
function renderHistory(messages) {
  activeStreamMessage = null;
  $("messages").replaceChildren();
  for (const msg of messages) appendMessage(msg.role, msg.content);
}
let avatarInventory = [];
let selectedAvatarId = "";

function populateAvatarGallery(entries = null, selected = null) {
  if (entries !== null) avatarInventory = entries;
  if (selected !== null) selectedAvatarId = selected;
  const picker = $("avatar-gallery");
  const all = $("avatar-show-all").checked;
  const favoriteCount = avatarInventory.filter((item) => item.favorite).length;
  picker.replaceChildren(new Option(
    t("Modell auswählen…"), ""
  ));
  for (const item of avatarInventory) {
    if (!all && !item.favorite) continue;
    const mark = item.favorite ? "✓ " : "";
    const suffix = item.id === selectedAvatarId ? " ✓" : "";
    picker.add(new Option(mark + item.name + suffix, item.id));
  }
  picker.value = selectedAvatarId;
  if (picker.value !== selectedAvatarId) picker.value = "";
  const chosen = avatarInventory.find((item) => item.id === selectedAvatarId);
  $("avatar-favorite-toggle").disabled = !chosen;
  $("avatar-favorite-toggle").textContent = t(chosen?.favorite ? "Aus Favoriten entfernen" : "Zu Favoriten hinzufügen");
  $("avatar-collection-hint").textContent = settings.ui_language === "ru"
    ? "Избранное: " + favoriteCount + " / " + avatarInventory.length
    : "Favoriten: " + favoriteCount + " / " + avatarInventory.length;
}

function renderCartesiaStatus() {
  const texts = {
    mini: "Eigene Voice-ID gespeichert; API-Key vorhanden.",
    environment: "Voice-ID und API-Key aus Windows-Umgebung vorhanden.",
    legacy: "Entwicklerprofil vorhanden. Für die EXE eigene Voice-ID speichern.",
    key_missing: "Voice-ID vorhanden, CARTESIA_API_KEY fehlt.",
    missing: "Keine Cartesia Voice-ID konfiguriert."
  };
  $("cartesia-voice-status").textContent = t(texts[settings.cartesia_voice_status] || texts.missing);
}
function renderMicrophones(event) {
  lastMicrophones = event;
  const select = $("microphone-select");
  select.replaceChildren();
  const append = (id, name, disabled = false) => {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = name;
    option.disabled = disabled;
    select.appendChild(option);
  };
  append("default", t("Windows-Standardmikrofon"));
  const seen = new Set(["default"]);
  for (const device of event.devices || []) {
    if (seen.has(device.id)) continue;
    seen.add(device.id);
    append(device.id, device.name + " · " + device.api +
      (device.available ? "" : settings.ui_language==="ru" ? " (неоднозначно)" : " (nicht eindeutig)"), !device.available);
  }
  const selected = event.selected || "default";
  if (!seen.has(selected)) append(selected, t("Gespeichertes Mikrofon nicht verfügbar"), true);
  select.value = selected;
  settings.microphone_id = selected;
  const available = selected === "default" || (event.devices || []).some(d => d.id === selected && d.available);
  $("microphone-status").textContent = t(event.error || (available ?
    "Auswahl gespeichert. Gilt ab der nächsten Aufnahme." :
    "Gespeichertes Mikrofon fehlt oder ist mehrdeutig. Bitte neu auswählen; kein automatischer Wechsel."));
}
function voiceButtonAction() {
  if (recording) return "finish";
  return voiceWorking || audioPlaying || reviewTurn !== null ? "stop" : "start";
}
let stackWarmup={state:"idle",component:""};
function renderStackWarmup() {
  const button=$("warm-stack"), state=stackWarmup.state;
  button.dataset.state=state;
  button.disabled=state!=="loading" && (recording || voiceWorking || audioPlaying || reviewTurn!==null);
  $("warm-stack-label").textContent=state==="loading" ? t("Abbrechen") : t(state==="ready"?"Bereit":state==="error"?"Erneut":"Start");
  button.title=t(state==="loading"?"Lokalen Start abbrechen":"Lokale Modelle starten") + (state==="loading" && stackWarmup.component ? " ("+stackWarmup.component+")" : "");
  button.setAttribute("aria-label",button.title);
  $("record").disabled=state==="loading";
}
function updateVoiceButton() {
  renderStackWarmup();
  const action = voiceButtonAction();
  const button = $("record");
  button.dataset.action = action;
  button.classList.toggle("active", action === "finish");
  button.classList.toggle("busy", action === "stop");
  button.innerHTML = action !== "start"
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8 21h8"/></svg>';
  button.title = t(action === "finish" ? "Aufnahme beenden" : action === "stop" ? "Antwort stoppen" : "Aufnahme starten");
  button.setAttribute("aria-label", button.title);
}
function renderLLMStatus() {
  for (const [id,key] of Object.entries({"standard-model-status":"standardModel","standard-runtime-status":"standardRuntime","large-model-status":"largeModel","large-runtime-status":"largeRuntime"})) {
    $(id).textContent = llmStatus[key] === undefined ? "—" : t(llmStatus[key] ? "Vorhanden" : "Fehlt");
    $(id).classList.toggle("file-ready", llmStatus[key] === true);
  }
  const mode=settings.llm_mode || "cloud";
  const large=mode==="local_12b";
  const ready=llmStatus[large?"largeModel":"standardModel"] && llmStatus[large?"largeRuntime":"standardRuntime"];
  $("llm-choice-status").textContent = t(mode==="cloud" ? "Luna (Cloud) ausgewählt" :
    llmStatus.restartRequired || llmStatus.activeProfile !== (large?"gemma12":"standard") ? "Neustart erforderlich" :
    ready ? "Lokales Modell bereit zur Anfrage (Dateiprüfung)" : "Lokales Modell: Dateien fehlen");

}
function updateButtons() {
  updateVoiceButton();
  $("microphone-select").disabled = recording;
  $("microphone-refresh").disabled = recording;
  $("prepare-audio").disabled = recording;
  for (const id of ["stt","stt_profile","stt_hotwords","stt_auto_language","stt_speech_guard","stt_auto_endpoint","stt_review","language","llm_mode","tts","speak","qwen_variant","piper_voice_de","piper_voice_ru"]) $(id).disabled = recording;
  $("llm_mode").value = settings.llm_mode || "cloud";
  $("ui_language").value = settings.ui_language || "de";
  $("stt").value = settings.stt;
  $("stt_profile").value = settings.stt_profile || "fast";
  $("stt_hotwords").value = settings.stt_hotwords || "";
  $("stt_auto_language").checked = settings.stt_auto_language === true;
  $("stt_speech_guard").checked = settings.stt_speech_guard === true;
  $("stt_auto_endpoint").checked = settings.stt_auto_endpoint === true;
  $("stt_review").checked = settings.stt_review === true;
  const localSTT = settings.stt === "local" || settings.stt === "cuda";
  const tuned = localSTT && ["balanced", "quality"].includes(settings.stt_profile);
  $("stt-local-options").hidden = !localSTT;
  $("stt-cuda-options").hidden = !tuned || settings.stt !== "cuda";
  $("stt-filter-options").hidden = !tuned;
  $("qwen-settings").hidden = settings.tts !== "local";
  $("tts").value = settings.tts;
  $("cartesia-settings").hidden = settings.tts !== "cartesia";
  renderCartesiaStatus();
  $("language").value = settings.language;
  $("piper_voice_de").value = settings.piper_voice_de || "de_DE-ramona-low";
  $("piper_voice_ru").value = settings.piper_voice_ru || "ru_RU-irina-medium";
  $("piper-de-group").hidden = settings.tts !== "piper" || settings.language !== "de";
  $("piper-ru-group").hidden = settings.tts !== "piper" || settings.language !== "ru";
  $("speak").checked = !!settings.speak;
  $("greeting").checked = !!settings.greeting;
  $("mouse_tracking").checked = !!settings.mouse_tracking;
  $("qwen_variant").value = settings.qwen_variant || "base";
  renderLLMStatus();
}

async function loadAvatar() {
  $("empty-avatar").hidden = false;
  if (!renderer) {
    $("empty-avatar").textContent = settings.ui_language === "ru"
      ? "3D недоступно. Остальные функции работают."
      : "3D nicht verfügbar. Andere Funktionen bleiben nutzbar.";
    return;
  }
  const loadToken = ++avatarLoadToken;
  try {
    const gltf = await modelLoader.loadAsync("/api/avatar?at=" + Date.now());
    const newVrm = gltf.userData.vrm;
    if (!newVrm) throw new Error("Kein VRM im Modell");
    if (loadToken !== avatarLoadToken) {
      VRMUtils.deepDispose(newVrm.scene);
      return;
    }
    ++activeAnimationToken;
    window.__omoiDiagnostics.animationLoaded = false;
    if (vrm) {
      faceMotion.reset();
      if (mixer && activeFinishListener) mixer.removeEventListener("finished", activeFinishListener);
      activeFinishListener = null;
      if (mixer) mixer.stopAllAction();
      scene.remove(vrm.scene);
      VRMUtils.deepDispose(vrm.scene);
    }
    vrm = newVrm;
    window.__omoiDiagnostics.avatarLoaded = true;
    window.__omoiDiagnostics.avatarError = false;
    VRMUtils.rotateVRM0(vrm);
    vrm.scene.position.set(0, 0, 0);
    scene.add(vrm.scene);
    // Wait for the first motion before revealing the mesh in its bind pose.
    vrm.scene.visible = false;
    mixer = new THREE.AnimationMixer(vrm.scene);
    requestedSlot = null;
    motionBlend.reset();
    emotionMotion.reset();
    faceReaction.reset();
    visibleMotion = null;
    await playAnimation(animationPolicy.current());
    if (loadToken === avatarLoadToken && vrm === newVrm) {
      vrm.scene.visible = true;
      $("empty-avatar").hidden = true;
    }
  } catch (e) {
    if (loadToken !== avatarLoadToken) return;
    window.__omoiDiagnostics.avatarError = true;
    $("empty-avatar").textContent = "VRM konnte nicht geladen werden. Bitte Modell auswählen.";
    console.warn("VRM-Ladevorgang fehlgeschlagen:", e.message);
  }
}
async function playAnimation(slot, preview = false, staged = false, rotate = false, primaryOnly = false) {
  if (!vrm || !(slot in roles)) return;
  if (!rotate && !primaryOnly && !preview && requestedSlot===slot && !requestedPreview) return;
  if (!preview && !staged && availablePrimary && baseRoles.includes(slot) && !availablePrimary.has(slot)) {
    faceReaction.trigger(slot,performance.now());
    const fallback=animationPolicy.failed(slot,false);
    if(fallback && fallback!==slot) requestAnimation(fallback);
    return;
  }
  requestedSlot=slot;
  requestedPreview=preview;
  const token = ++activeAnimationToken;
  const targetVrm=vrm;
  const assetSlot=preview || primaryOnly ? slot : variants.next(slot);
  try {
    const endpoint = staged ? "/api/preview/" : "/api/animation/";
    const gltf = await animationLoader.loadAsync(endpoint + encodeURIComponent(assetSlot) + "?v=" + Date.now());
    if (token !== activeAnimationToken || vrm!==targetVrm) return;
    const vrmAnimation = gltf.userData.vrmAnimations?.[0];
    if (!vrmAnimation) throw new Error("Keine VRMA-Animation vorhanden");
    const clip = createVRMAnimationClip(vrmAnimation, vrm);
    if (activeFinishListener) mixer.removeEventListener("finished", activeFinishListener);
    activeFinishListener = null;
    const action = mixer.clipAction(clip);
    // Start from the current weighted pose, even if a previous fade is running.
    motionBlend.switchTo(action);
    const shouldLoop = !preview && LOOP_SLOTS.has(slot);
    action.setLoop(shouldLoop ? THREE.LoopRepeat : THREE.LoopOnce, shouldLoop ? Infinity : 1);
    action.clampWhenFinished = !shouldLoop;
    if (!shouldLoop) {
      const onFinish = (event) => {
        if (event.action !== action || token !== activeAnimationToken) return;
        mixer.removeEventListener("finished", onFinish);
        activeFinishListener = null;
        requestedSlot=null;
        requestAnimation(animationPolicy.finished(slot,preview));
      };
      mixer.addEventListener("finished", onFinish);
      activeFinishListener = onFinish;
    }
    faceMotion.restoreHead();
    mixer.update(0);
    vrm.update(0);
    window.__omoiDiagnostics.animationLoaded = true;
    visibleMotion = assetSlot;
    window.__omoiDiagnostics.activeAnimation = assetSlot;
    if (staged) {
      const row = animationRows[slot];
      if (row) row.confirm.disabled = false;
      if (previewPendingSlot === slot) $("preview-save").disabled = false;
      status("Vorschau geladen – mit ✓ für diesen Avatar speichern");
    }
  } catch (e) {
    if (token !== activeAnimationToken || vrm !== targetVrm) return;
    if (staged) {
      send("cancelAnimation", slot);
      bubble("Diese VRMA konnte nicht geladen werden. Die bisherige Zuordnung bleibt erhalten.");
    }
    requestedSlot=null;
    if (!preview && assetSlot!==slot) {
      variants.reject(assetSlot);
      return playAnimation(slot,false,false,false,true);
    }
    const fallback=animationPolicy.failed(slot,preview);
    if (fallback && fallback!==slot) requestAnimation(fallback);
    else console.warn("Animation fehlt; VRM bleibt sichtbar:", e.message);
  }
}
function requestAnimation(slot) {
  if (slot) playAnimation(slot);
}
function changeAvatarState(next) {
  if (next === "idle") targetLip=0;
  if (next==="listening" || next==="thinking") turnEmotion="neutral";
  const slot=animationPolicy.state(next);
  if (next==="idle") idleSince=performance.now();
  if(next==="idle" && !animationPolicy.protectedGesture) turnEmotion="neutral";
  requestAnimation(slot);
}

const runtimeLabels = {
  runtime_root: "Worker-Ordner",
  runtime_openvino_model: "OpenVINO-Modellordner",
  runtime_cuda_python: "CUDA-STT Python",
  runtime_cuda_overlay: "CUDA-STT Overlay",
  runtime_cuda_model: "CUDA-STT Modellordner",
  runtime_qwen_python: "Qwen-TTS Python",
  runtime_piper_python: "Piper-TTS Python"
};
const runtimeSourceLabels = {mini: "Mini-Auswahl", environment: "Umgebungsvariable", default: "Standard / nicht gesetzt"};
const runtimeChecks = {
  openvino_stt: ["OpenVINO STT", {worker_file:"Worker",node_available:"Node.js",node_dependency:"Node-Paket",model_explicit:"Modellpfad gesetzt",model_directory:"Modellordner"}],
  cuda_stt: ["CUDA STT", {worker_file:"Worker",python_runtime:"Python",python_overlay:"Overlay",model_file:"model.bin"}],
  qwen_tts: ["Qwen TTS", {server_file:"Worker",python_runtime:"Python"}],
  piper_tts: ["Piper TTS", {worker_file:"Worker",python_runtime:"Python"}]
};
function renderRuntimeSources(sources = {}) {
  lastRuntimeSources = sources;
  for (const key of Object.keys(runtimeLabels)) {
    const source = sources[key] || "default";
    $("source-" + key).textContent = t(runtimeSourceLabels[source] || runtimeSourceLabels.default);
    $("reset-" + key).disabled = source !== "mini";
  }
}
function renderRuntimeDiagnosis(report) {
  lastRuntimeReport = report;
  const target = $("runtime-report");
  target.replaceChildren();
  for (const [key, [label, checks]] of Object.entries(runtimeChecks)) {
    const line = document.createElement("p");
    const missing = Object.entries(checks).filter(([check]) => report?.[key]?.[check] !== true).map(([, name]) => t(name));
    line.textContent = label + ": " + (missing.length ? t("Fehlt") + ": " + missing.join(", ") : t("Dateiprüfung OK"));
    target.appendChild(line);
  }
  const note = document.createElement("p");
  note.textContent = t("Nur Dateiprüfung. Keine Aussage zu Modellgewichten (Qwen/Piper), Versionen, Treibern oder Inferenz.");
  target.appendChild(note);
}

function hideSTTReview() {
  reviewTurn = null;
  $("stt-review-panel").hidden = true;
  $("stt-review-text").value = "";
  $("stt-review-original").textContent = "";
  syncDock();
}
function applyEvent(message) {
  let event;
  try { event = typeof message === "string" ? JSON.parse(message) : message; }
  catch { return; }
  switch (event.type) {
    case "init":
      window.__omoiDiagnostics.bridgeReady = true;
      settings = {...settings, ...event.settings};
      llmStatus=event.llmStatus || {};
      setLocale(settings.ui_language);
      renderRuntimeSources(event.runtimeSources);
      renderHistory(event.messages || []);
      updateButtons();
      send("refreshMicrophones");
      for (const key of ["hello_de","hello_ru","introduce_de","introduce_ru",
                         "timer_end_de","timer_end_ru"]) {
        $(key).value = String(settings[key] || "");
      }
      populateAvatarGallery(event.avatars || [], event.selectedAvatar || "");
      otherAvatarAssignments = Math.max(0, Number(event.otherAvatarAssignments) || 0);
      for (const slot of Object.keys(roles)) updateAnimationLabel(slot, event.animations?.[slot] || null);
      updateAnimationSummary();
      updateAlternatives(event.availableAlternatives,true,event.availablePrimary);
      if (event.hasAvatar) loadAvatar();
      else { $("empty-avatar").hidden = false; $("empty-avatar").textContent = t("VRM auswählen"); }
      break;
    case "animation_reset":
      faceReaction.reset();
      if (activeStreamMessage) { activeStreamMessage.remove(); bubble(""); }
      activeStreamMessage = null;
      if (Array.isArray(event.history)) renderHistory(event.history);
      if (Number.isSafeInteger(event.turn)) {
        reviewFloor = Math.max(reviewFloor, event.turn);
        if (reviewTurn !== null && reviewTurn < reviewFloor) hideSTTReview();
      }
      turnEmotion="neutral"; targetLip=0;
      idleSince=performance.now();
      requestAnimation(animationPolicy.reset());
      break;
    case "voice_work":
      if (Number.isSafeInteger(event.revision) && event.revision > voiceRevision) {
        voiceRevision = event.revision;
        voiceWorking = !!event.busy;
        updateVoiceButton();
      }
      break;
    case "status":
      if (event.source === "audio") {
        audioPlaying = event.avatarState === "talking";
        updateVoiceButton();
      }
      status(event.value);
      if (event.avatarState) changeAvatarState(event.avatarState);
      break;
    case "message":
      appendMessage(event.role, event.content);
      if (event.role === "assistant") bubble(event.content);
      break;
    case "stream_start":
      activeStreamMessage = document.createElement("div");
      activeStreamMessage.className = "msg assistant";
      activeStreamMessage.textContent = "";
      $("messages").appendChild(activeStreamMessage);
      bubble(t("Antwort wird generiert…"));
      break;
    case "stream_delta":
      if (!activeStreamMessage) break;
      activeStreamMessage.textContent += String(event.text || "");
      $("messages").scrollTop = $("messages").scrollHeight;
      bubble(activeStreamMessage.textContent.slice(-360));
      break;
    case "stream_end":
      if (activeStreamMessage && typeof event.text === "string") {
        activeStreamMessage.textContent = event.text;
      }
      activeStreamMessage = null;
      break;
    case "response":
      bubble(event.text);
      turnEmotion = event.emotion || "neutral";
      if (event.animation) requestAnimation(animationPolicy.response(event.animation,event.emotion));
      break;
    case "stream_abort":
      activeStreamMessage?.remove();
      activeStreamMessage = null;
      break;
    case "error":
      if (previewPendingSlot && animationRows[previewPendingSlot] &&
          !animationRows[previewPendingSlot].confirm.disabled) {
        $("preview-save").disabled = false;
      }
      status("Fehler"); bubble(t(event.value), true);
      break;
    case "model_catalog": {
      const target = $("model-catalog-list");
      target.replaceChildren();
      for (const model of event.models || []) {
        const item = document.createElement("p");
        const name = document.createElement("strong");
        name.textContent = model.kind + " · " + model.name + " · " + model.role;
        const detail = document.createElement("span");
        const formatMb = bytes => (bytes / 1e6).toLocaleString(settings.ui_language==="ru"?"ru-RU":"de-DE", { maximumFractionDigits: 2 }) + " MB";
        const size = Number.isSafeInteger(model.exact_size_bytes)
          ? formatMb(model.exact_size_bytes) + " (" + model.file_count + " Dateien)"
          : model.size_mb == null ? "Gr\u00f6\u00dfe offen" : "ca. " + model.size_mb + " MB (" + model.size_scope + ")";
        const required = Number.isSafeInteger(model.required_space_bytes)
          ? "Freier Speicher n\u00f6tig: " + formatMb(model.required_space_bytes) : "";
        const revision = typeof model.revision === "string" ? "Revision: " + model.revision.slice(0, 12) : "";
        const managedLabels = {
          absent: "Nicht verwaltet installiert", busy: "Modelloperation gesperrt",
          conflict: "Ordnerkonflikt", unavailable: "Speicherort nicht pr\u00fcfbar",
          manifest_sizes_match: "Verwaltet: Manifest und Gr\u00f6\u00dfen passend (Hashes ungepr\u00fcft)"
        };
        const existingLabels = {
          not_configured: "Kein eigener Pfad gesetzt", not_found: "Kein Kandidat gefunden",
          configured_missing_or_invalid: "Konfigurierter Pfad fehlt/ung\u00fcltig",
          configured_present_unverified: "Eigene Datei vorhanden (ungepr\u00fcft)",
          candidate_sizes_match: "Vorhandener Kandidat: Gr\u00f6\u00dfen passend (Hashes ungepr\u00fcft)",
          candidate_incomplete_or_different: "Vorhandener Kandidat unvollst\u00e4ndig/abweichend",
          unavailable: "Vorhandene Dateien nicht pr\u00fcfbar"
        };
        const free = Number.isSafeInteger(model.available_space_bytes)
          ? "Frei: " + formatMb(model.available_space_bytes) +
            (model.enough_space === false ? " (nicht ausreichend)" : "")
          : "Freier Speicher unbekannt";
        detail.textContent = [size, model.upstream_license || "Lizenz offen", model.repository,
          model.runtime, model.note, revision, required, free, managedLabels[model.managed_status], existingLabels[model.existing_status], model.integrity_status, "Download gesperrt"].filter(Boolean).map(t).join(" · ");
        item.append(name, document.createElement("br"), detail);
        target.appendChild(item);
      }
      break;
    }
    case "runtime_paths":
      if(stackWarmup.state!=="loading"){stackWarmup={state:"idle",component:""};renderStackWarmup();}
      renderRuntimeSources(event.sources);
      $("runtime-report").textContent = "Gespeichert. Nach Neustart wirksam. Diagnose bei Bedarf erneut ausführen.";
      break;
    case "runtime_diagnosis":
      renderRuntimeDiagnosis(event.report);
      break;
    case "notice":
      bubble(t(event.value)); send("readLLMStatus");
      break;
    case "lip":
      targetLip = Math.max(0, Math.min(1, Number(event.value) || 0));
      break;
    case "mic_level": {
      if (recording) {
        $("mic-level").hidden = false;
        const rms = Math.max(0, Number(event.rms) || 0);
        const seconds = Math.max(0, Number(event.seconds) || 0);
        const rate = Number(event.captureRate || 16000);
        $("mic-level").textContent = t("Mikrofon") + " " + seconds.toFixed(1) + " s | " + t("Pegel") + " " + (rms * 100).toFixed(2) + " %" +
          (rate !== 16000 ? " | " + (rate/1000).toFixed(1) + " kHz -> 16 kHz" : "") +
          (event.overflow ? " | " + t("Mikrofon-Daten verloren.") : rms < 0.0025 ? " | " + t("Signal sehr leise") : "");
      }
      break;
    }
    case "mic_limit":
      status(event.value);
      bubble(event.value);
      break;
    case "mic_captured":
      $("mic-level").hidden = false;
      $("mic-level").textContent = t("Aufnahme") + " " + Number(event.seconds || 0).toFixed(1) + " s | " + t("Aktives Signal") + ": " + Math.round(Number(event.voiceMs || 0)) + " ms" +
        (event.overflow ? " | " + t("Mikrofon-Daten verloren.") : event.accepted ? "" : " | " + t("Aufnahme abgelehnt"));
      break;
    case "stt_stage": {
      $("stt-diagnostics").hidden = false;
      const provider = event.engine === "cuda" ? "Faster-Whisper CUDA" :
        (event.engine === "local" ? "Whisper OpenVINO CPU" : "Cloud STT");
      if (event.stage === "start") {
        $("stt-diagnostics").textContent = `${provider}: ${t("transkribiert\u2026")}`;
      } else if (event.stage === "complete") {
        $("stt-diagnostics").textContent = `${provider}: ${t("Text erkannt in")} ${(Number(event.elapsedMs || 0) / 1000).toFixed(2)} s`;
        if (Number.isFinite(event.decodeMs)) {
          $("stt-diagnostics").textContent += " | " + t("Inferenz") + " " + (event.decodeMs/1000).toFixed(2) + " s";
        }
        if (Number.isFinite(event.loadMs)) {
          $("stt-diagnostics").textContent += " | " + t("Laden") + " " + (event.loadMs/1000).toFixed(2) + " s";
        }
      } else if (event.stage === "error") {
        $("stt-diagnostics").textContent = `${provider}: ${t("Transkription fehlgeschlagen nach")} ${(Number(event.elapsedMs || 0) / 1000).toFixed(2)} s`;
      }
      break;
    }
    case "stt_review":
      if (Number.isSafeInteger(event.turn) && event.turn >= reviewFloor) {
        reviewTurn = event.turn;
        syncDock();
        $("stt-review-original").textContent = (settings.ui_language==="ru" ? "Распознано: " : "Erkannt: ") + String(event.text || "");
        $("stt-review-text").value = String(event.suggestion || event.text || "");
      }
      break;
    case "stt_review_clear":
      reviewFloor = Math.max(reviewFloor, Number(event.turn) || 0);
      if (reviewTurn === null || reviewTurn < reviewFloor) hideSTTReview();
      break;
    case "stt_partial":
      if (recording && settings.stt === "cuda") {
        $("live-transcript").hidden = false;
        const live = $("live-transcript");
        live.replaceChildren(document.createTextNode(settings.ui_language==="ru" ? "Распознание (черновик): " : "Live-Transkript (vorläufig): "));
        const stable = document.createElement("strong");
        stable.textContent = String(event.stable || "").slice(0, 600);
        live.append(stable, document.createTextNode(String(event.tail ?? event.text ?? "").slice(0, Math.max(0, 600 - stable.textContent.length))));
      }
      break;
    case "recording":
      recording = !!event.active;
      if (recording) {
        $("mic-level").hidden = false;
        $("mic-level").textContent = t("Mikrofon wird aufgenommen…");
        $("stt-diagnostics").hidden = true;
      }
      $("live-transcript").hidden = true;
      $("live-transcript").textContent = "";
      updateButtons();
      break;
    case "microphones":
      renderMicrophones(event);
      break;
    case "cartesia_voice_status":
      settings.cartesia_voice_status = event.value;
      $("cartesia-voice-id").value = "";
      renderCartesiaStatus();
      break;
    case "stack_warmup":
      stackWarmup=event;
      renderStackWarmup();
      if(event.state==="error") {
        const errors=(event.errors||[]).map(e=>e.component+": "+t(e.message)).join("\n");
        bubble(errors || t("Lokaler Start fehlgeschlagen."),true);
      } else if(event.state==="ready") {
        bubble(t("Lokale Modelle sind bereit."),true);
      } else if(event.state==="cloud") {
        bubble(t("Keine lokalen Modelle ausgewählt."),true);
      }
      break;
    case "llm_status":
      llmStatus=event.report || {};
      renderLLMStatus();
      break;
    case "setting":
      const changed=settings[event.key]!==event.value;
      settings[event.key] = event.value;
      if (changed && stackWarmup.state!=="loading" && ["stt","tts","speak","llm_mode","llm","llm_local_profile","qwen_variant","language","piper_voice_de","piper_voice_ru"].includes(event.key))
        stackWarmup={state:"idle",component:""};
      if (event.key === "ui_language") refreshLocale();
      updateButtons();
      break;
    case "avatar":
      animationPolicy.endPreview();
      previewPendingSlot = null;
      $("animation-preview-actions").hidden = true;
      populateAvatarGallery(event.avatars || avatarInventory, event.selectedAvatar || "");
      otherAvatarAssignments = Math.max(0, Number(event.otherAvatarAssignments) || 0);
      for (const slot of Object.keys(roles)) updateAnimationLabel(slot, event.animations?.[slot] || null);
      updateAnimationSummary();
      updateAlternatives(event.availableAlternatives,true,event.availablePrimary);
      if (event.available) loadAvatar();
      break;
    case "avatar_favorites":
      populateAvatarGallery(event.avatars || avatarInventory, event.selectedAvatar || "");
      status("Mini-Demoliste aktualisiert");
      break;
    case "animation_pending":
      previewPendingSlot = event.slot;
      updateAnimationLabel(event.slot, event.filename, true);
      enterAnimationPreview(true);
      animationPolicy.beginPreview();
      playAnimation(event.slot, true, true);
      break;
    case "animation_changed":
      updateAnimationLabel(event.slot, event.filename || null);
      updateAlternatives(event.availableAlternatives,false,event.availablePrimary);
      updateAnimationSummary();
      if (previewPendingSlot === event.slot) {
        previewPendingSlot = null;
        closeAnimationPreview();
      }
      status(event.filename ? "Animation für diesen Avatar dauerhaft gespeichert" : "Standard wiederhergestellt");
      animationPolicy.endPreview();
      requestedSlot=null;
      requestAnimation(animationPolicy.current());
      break;
    case "animation_cancelled":
      updateAnimationLabel(event.slot, animationRows[event.slot]?.current || null);
      if (previewPendingSlot === event.slot) {
        previewPendingSlot = null;
        closeAnimationPreview();
      }
      animationPolicy.endPreview();
      requestedSlot=null;
      requestAnimation(animationPolicy.current());
      break;
    case "preview":
      previewPendingSlot = event.staged ? event.slot : null;
      enterAnimationPreview(!!event.staged);
      animationPolicy.beginPreview();
      playAnimation(event.slot, true, !!event.staged);
      break;
    case "clear_chat":
      renderHistory([]); bubble("");
      break;
  }
}
function enterAnimationPreview(canSave = false) {
  setExpanded(true);
  setScreen(null);
  $("animation-preview-actions").hidden = false;
  $("preview-save").hidden = !canSave;
  $("preview-save").disabled = true;
}

function saveSetting(key, value) {
  send("saveSetting", key, JSON.stringify(value));
}

function setExpanded(expanded) {
  expandedToolbar = expanded;
  $("root").classList.toggle("compact", !expanded);
  $("toolbar").classList.toggle("expanded", expanded);
  $("toolbar-more").setAttribute("aria-expanded", String(expanded));
  $("toolbar-more").title = t(expanded ? "Bedienung ausblenden" : "Bedienung einblenden");
  $("toolbar-more").setAttribute("aria-label", $("toolbar-more").title);
  if (!expanded) {
    if (animationPolicy.preview) {
      animationPolicy.endPreview();
      requestedSlot=null;
      requestAnimation(animationPolicy.current());
      $("animation-preview-actions").hidden=true;
    }
    // Pending review remains visible until the user explicitly decides.
    setScreen(null);
  }
}
$("toolbar-more").addEventListener("click", () => setExpanded(!expandedToolbar));
$("dismiss-feedback").addEventListener("click", () => bubble(""));
$("preview-back").addEventListener("click", closeAnimationPreview);
$("preview-save").addEventListener("click", () => {
  if (previewPendingSlot && !$("preview-save").disabled) {
    $("preview-save").disabled = true;
    send("confirmAnimation", previewPendingSlot);
  }
});
for (const tab of document.querySelectorAll("[data-settings-tab]")) {
  tab.addEventListener("click", () => showCategory(tab.dataset.settingsTab));
}
$("chat-toggle").addEventListener("click", () => {
  setScreen(activeScreen === "chat" ? null : "chat");
  if (activeScreen === "chat" && reviewTurn === null) $("message").focus();
});
$("settings-toggle").addEventListener("click", () => {
  $("animation-preview-actions").hidden = true;
  setScreen(activeScreen === "settings" ? null : "settings");
});
$("settings-close").addEventListener("click", () => setScreen(null));
$("chat-close").addEventListener("click", () => setScreen(null));
$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("message"), text = input.value.trim();
  if (!text) return;
  send("sendText", text);
  input.value = "";
});
$("stt-review-send").addEventListener("click", () => {
  if (reviewTurn !== null) send("submitSTTReview", reviewTurn, $("stt-review-text").value, false);
});
$("stt-review-remember").addEventListener("click", () => {
  if (reviewTurn !== null) send("submitSTTReview", reviewTurn, $("stt-review-text").value, true);
});
$("stt-review-cancel").addEventListener("click", () => {
  if (reviewTurn !== null) send("cancelSTTReview", reviewTurn);
});
$("stt-clear-corrections").addEventListener("click", () => {
  if (window.confirm(t("Alle lokal gemerkten STT-Korrekturen l\u00f6schen?"))) send("clearSTTCorrections");
});
$("record").addEventListener("click", () => send("voiceAction", voiceButtonAction()));
$("avatar-import").addEventListener("click", () => send("selectAvatar"));
$("vroid-hub").addEventListener("click", () => send("openVRoidHub"));
$("animation-save-defaults").addEventListener("click", () => send("useSelectedAnimationsAsDefaults"));
$("voice-import").addEventListener("click", () => send("selectVoiceReference"));
$("warm-stack").addEventListener("click", () => send(stackWarmup.state==="loading"?"cancelWarmup":"prewarmVoiceStack"));
$("llm-import-gguf").addEventListener("click", () => send("chooseLocalGGUF"));
$("llm-import-server").addEventListener("click", () => send("chooseLlamaServer"));
$("llm-import-gemma12").addEventListener("click", () => send("chooseGemma12GGUF"));
$("llm-import-gemma12-server").addEventListener("click", () => send("chooseGemma12Server"));
for (const [key, name] of Object.entries(runtimeLabels)) {
  const row = document.createElement("div");
  row.className = "runtime-row";
  const title = document.createElement("span");
  title.textContent = name;
  const source = document.createElement("span");
  source.id = "source-" + key;
  source.className = "hint";
  const choose = document.createElement("button");
  choose.textContent = "Wählen";
  choose.setAttribute("aria-label", name + " wählen");
  choose.addEventListener("click", () => send("chooseRuntimePath", key));
  const reset = document.createElement("button");
  reset.id = "reset-" + key;
  reset.textContent = "Reset";
  reset.setAttribute("aria-label", name + " zurücksetzen");
  reset.disabled = true;
  reset.addEventListener("click", () => send("resetRuntimePath", key));
  row.append(title, source, choose, reset);
  $("runtime-path-list").appendChild(row);
}
$("runtime-diagnose").addEventListener("click", () => send("diagnoseRuntimePaths"));
$("read-model-catalog").addEventListener("click", () => send("readModelCatalog"));
$("save-phrases").addEventListener("click", () => {
  for (const key of ["hello_de","hello_ru","introduce_de","introduce_ru",
                     "timer_end_de","timer_end_ru"]) saveSetting(key, $(key).value);
  status("Textvorlagen gespeichert");
});
$("avatar-gallery").addEventListener("change", (event) => {
  if (event.target.value !== "") send("chooseInstalledAvatar", event.target.value);
});
$("avatar-show-all").addEventListener("change", () => populateAvatarGallery());
$("avatar-favorite-toggle").addEventListener("click", () => send("toggleAvatarFavorite"));
$("clear-chat").addEventListener("click", () => send("clearChat"));
$("microphone-select").addEventListener("change", event => send("chooseMicrophone", event.target.value));
$("microphone-refresh").addEventListener("click", () => send("refreshMicrophones"));
$("cartesia-voice-save").addEventListener("click", () => {
  const value = $("cartesia-voice-id").value.trim();
  if (value) send("saveCartesiaVoice", value);
});
$("cartesia-voice-reset").addEventListener("click", () => send("saveCartesiaVoice", ""));
$("prepare-audio").addEventListener("click", () => send("prepareAudio"));
$("close").addEventListener("click", () => send("closeApp"));
$("hide").addEventListener("click", () => send("minimize"));
$("always").addEventListener("click", () => send("toggleAlwaysOnTop"));
$("scene").addEventListener("pointerdown", (event) => {
  if (event.button===0 && event.isPrimary && event.pointerType==="mouse") {
    event.preventDefault();
    send("dragWindow");
  }
});
$("drag").addEventListener("mousedown", (event) => {
  if (event.button === 0) send("dragWindow");
});
for (const key of ["llm_mode", "ui_language", "stt", "tts", "language", "piper_voice_de", "piper_voice_ru", "qwen_variant", "stt_profile", "stt_hotwords"]) {
  $(key).addEventListener("change", (event) => saveSetting(key, event.target.value));
}
for (const key of ["speak", "greeting", "mouse_tracking", "stt_auto_language", "stt_speech_guard", "stt_auto_endpoint", "stt_review"]) {
  $(key).addEventListener("change", (event) => saveSetting(key, event.target.checked));
}
for (const [slot, name] of Object.entries(roles)) {
  const row = document.createElement("div");
  row.className = "anim-row";
  const title = document.createElement("span");
  title.className = "anim-name"; title.textContent = name;
  const selected = document.createElement("span");
  selected.className = "anim-selected"; selected.textContent = "Standardanimation";
  const choose = document.createElement("button"); choose.textContent = "Datei…";
  choose.title = "VRMA auswählen und vor der Übernahme testen";
  choose.addEventListener("click", () => send("selectAnimation", slot));
  const preview = document.createElement("button"); preview.textContent = "▶";
  preview.title = "Animation ansehen";
  preview.addEventListener("click", () => send("previewAnimation", slot));
  const reset = document.createElement("button"); reset.textContent = "↺";
  reset.title = "Standardanimation wiederherstellen";
  reset.addEventListener("click", () => send("resetAnimation", slot));
  const confirm = document.createElement("button"); confirm.textContent = "✓";
  confirm.title = "Geprüfte Animation übernehmen"; confirm.hidden = true;
  confirm.disabled = true;
  confirm.addEventListener("click", () => send("confirmAnimation", slot));
  const cancel = document.createElement("button"); cancel.textContent = "×";
  cancel.title = "Vorschau verwerfen"; cancel.hidden = true;
  cancel.addEventListener("click", () => send("cancelAnimation", slot));
  row.append(title, choose, preview, reset, confirm, cancel, selected);
  animationRows[slot] = {row, label:selected, confirm, cancel, preview, current:null};
  if(baseRoles.includes(slot)) {
    const group=document.createElement("div");group.className="animation-role";
    const details=document.createElement("details");details.className="animation-alternatives";
    const summary=document.createElement("summary");summary.textContent="Alternativen (0/2)";
    const content=document.createElement("div");
    details.append(summary,content);group.append(row,details);
    animationRows[slot].alternatives=summary;
    animationRows[slot].variantContent=content;
    $("animation-list").appendChild(group);
  } else {
    const base=slot.replace(/_alt[12]$/,"");
    row.classList.add("animation-variant");
    animationRows[base].variantContent.appendChild(row);
    selected.textContent="Nicht belegt";
    preview.disabled=true;
  }
}
function refreshLocale() {
  setLocale(settings.ui_language);
  status(lastStatus);
  populateAvatarGallery();
  for(const slot of Object.keys(animationRows)) {
    const row=animationRows[slot];
    if(row.confirm.hidden) row.label.textContent=t(row.current || "Standardanimation");
  }
  updateAlternatives([...variants.available]);
  updateAnimationSummary();
  if(lastMicrophones) renderMicrophones(lastMicrophones);
  renderRuntimeSources(lastRuntimeSources);
  if(lastRuntimeReport) renderRuntimeDiagnosis(lastRuntimeReport);
  updateButtons();
  $("toolbar-more").title=t(expandedToolbar?"Bedienung ausblenden":"Bedienung einblenden");
  $("toolbar-more").setAttribute("aria-label",$("toolbar-more").title);
}
captureStatic($("root"));
if (window.QWebChannel && window.qt?.webChannelTransport) {
  new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
    bridge = channel.objects.bridge;
    bridge.uiEvent.connect(applyEvent);
    send("ready");
  });
} else status("Python-Bridge nicht erreichbar");
