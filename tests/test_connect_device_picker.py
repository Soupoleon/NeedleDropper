"""Execute the Connect device picker against a small DOM, without a browser or Spotify."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "companion/needledropper/deck/static/connect.js"


@pytest.fixture
def run_picker():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the Connect device picker regression")
    source = SOURCE.read_text(encoding="utf-8")
    functions = source[source.index("  function deviceValue("):source.index("  function renderYouTube(")]
    functions += source[source.index("  async function refresh()"):source.index("  function schedule(")]
    setup = r'''
const assert = require("assert");
class Element {
  constructor(option=false) { this.option=option; this.options=[]; this._text=""; this.value=""; this.disabled=false; this.attrs={}; }
  set textContent(value) { this._text=value; this.options=[]; if (!this.option) this.value=""; }
  get textContent() { return this._text; }
  appendChild(option) { this.options.push(option); if (this.options.length===1) this.value=option.value; }
  setAttribute(k,v) { this.attrs[k]=v; }
  removeAttribute(k) { delete this.attrs[k]; }
}
const elements={}; const $=id => elements[id] || (elements[id]=new Element());
const document={activeElement:null, createElement:()=>new Element(true)};
const APP_DEVICE="NeedleDropper";
let state={spotify:{device:"connect",deviceId:"device-b",deviceName:"Desk"}};
let devicesLoaded=false, devicesLoading=false, deviceListRequest=0, deviceRetryAt=0, deviceSelectionRevision=0;
let deviceSaving=false, deviceRows=[], deviceListError="";
let refreshing=false, refreshAgain=false, offline=false;
const POLL_MS=2000, schedules=[];
const schedule=ms=>schedules.push(ms), renderApp=()=>{}, renderBanner=()=>{};
const apply=next=>{state=next;const sp=state.spotify||{};syncDeviceSelect(sp.device,sp.deviceName||"",sp.deviceId||"");};
const notices=[], errors=[], saved=[];
const announce=msg=>notices.push(msg), toast=(msg,kind)=>errors.push([msg,kind]);
const setText=(el,text)=>{el.textContent=text;};
let response={devices:[]}; let api=async()=>response;
let saveConfig=async(patch,msg)=>{saved.push(patch); Object.assign(state.spotify,patch.spotify);};
FUNCTIONS
async function test() {
TEST
}
test().catch(error=>{console.error(error);process.exitCode=1;});
'''

    def run(code):
        result = subprocess.run([node, "-"], input=setup.replace("FUNCTIONS", functions).replace("TEST", code),
                                text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
    return run


def test_exact_ids_disambiguate_names_and_refresh_keeps_focused_selection(run_picker):
    run_picker(r'''
response.devices=[
  {id:"device-a",name:"Desk",type:"Computer",isActive:true},
  {id:"device-b",name:"Desk",type:"Computer"},
  {id:"sdk",name:"needledropper",type:"Computer"}
];
document.activeElement=$("sp-device");
await loadDevices(true);
assert.equal($("sp-device").value,"connect-id:device-b");
const labels=$("sp-device").options.map(o=>o.textContent);
assert(labels.some(text=>text.includes("Spotify desktop app — Desk (device-a)")));
assert(labels.some(text=>text.includes("Spotify desktop app — Desk (device-b)")));
assert.equal($("sp-device").options.at(-1).value,"connect:");
assert(!labels.some(text=>text.includes("playing now")));
assert.equal(deviceRows.length,2);
$("sp-device").value="connect-id:device-a";
await saveDeviceSelection();
assert.deepEqual(saved,[{spotify:{device:"connect",deviceId:"device-a",deviceName:"Desk"}}]);
assert.equal($("sp-device").value,"connect-id:device-a");
''')


def test_duplicate_names_with_shared_id_prefix_are_still_distinguishable(run_picker):
    run_picker(r'''
response.devices=[{id:"shared-prefix-A",name:"Desk",type:"Computer"},{id:"shared-prefix-B",name:"Desk",type:"Computer"}];
state.spotify.deviceId="shared-prefix-A";
await loadDevices(false);
const labels=$("sp-device").options.filter(o=>o.value.startsWith("connect-id:")).map(o=>o.textContent);
assert.equal(new Set(labels).size,2);
''')


def test_offline_saved_id_stays_selected_even_if_another_device_has_same_name(run_picker):
    run_picker(r'''
response.devices=[{id:"device-a",name:"Desk",type:"Computer"}];
await loadDevices(false);
assert.equal($("sp-device").value,"connect-id:device-b");
const selected=$("sp-device").options.find(o=>o.value===$("sp-device").value);
assert(selected.disabled && selected.textContent.includes("unavailable"));
assert($("sp-device-status").textContent.includes("Unavailable"));
assert.equal(saved.length,0);
''')


def test_restricted_and_idless_devices_cannot_be_selected(run_picker):
    run_picker(r'''
response.devices=[{id:"locked",name:"TV",isRestricted:true},{id:"",name:"No ID"}];
await loadDevices(true);
const unusable=$("sp-device").options.filter(o=>o.textContent.includes("unavailable for control"));
assert.equal(unusable.length,2); assert(unusable.every(o=>o.disabled));
$("sp-device").value="connect-id:locked";
await saveDeviceSelection();
assert.equal(saved.length,0);
assert(notices[0].startsWith("0 available"));
''')


def test_automatic_clears_saved_target_and_explains_remote_output(run_picker):
    run_picker(r'''
await loadDevices(false);
$("sp-device").value="connect:"; await saveDeviceSelection();
assert.deepEqual(saved[0],{spotify:{device:"connect",deviceId:"",deviceName:""}});
assert($("sp-device-hint").textContent.includes("another computer, phone or speaker"));
assert($("sp-device-status").textContent.includes("Automatic Spotify Connect"));
$("sp-device").value="app"; await saveDeviceSelection();
assert.deepEqual(saved[1],{spotify:{device:"app"}});
assert($("sp-device-hint").textContent.includes("Windows audio output"));
assert($("sp-device-hint").textContent.includes("Volume mixer"));
''')


def test_no_desktop_devices_explains_how_to_open_and_select_the_desktop_app(run_picker):
    run_picker(r'''
response.devices=[{id:"phone",name:"Phone",type:"Smartphone"}];
await loadDevices(false);
assert($("sp-device-hint").textContent.includes("No Spotify desktop app was found"));
assert($("sp-device-hint").textContent.includes("Open Spotify on this PC, Refresh"));
state.spotify.device="app";
syncDeviceSelect("app", "", "");
assert($("sp-device-hint").textContent.includes("To use the separate Spotify desktop app instead"));
''')


def test_refresh_uses_the_current_selection_returned_by_the_api(run_picker):
    run_picker(r'''
response={devices:[{id:"device-a",name:"Desk"}],device:"connect",selected:"Desk",selectedId:"device-a"};
await loadDevices(false);
assert.equal($("sp-device").value,"connect-id:device-a");
assert.equal(state.spotify.deviceId,"device-a");
''')


def test_legacy_name_remains_visible_without_choosing_an_ambiguous_id(run_picker):
    run_picker(r'''
state.spotify={device:"connect",deviceName:"Desk",deviceId:""};
response.devices=[{id:"a",name:"Desk"},{id:"b",name:"Desk"}];
await loadDevices(false);
assert.equal($("sp-device").value,"connect-name:Desk");
assert($("sp-device-status").textContent.includes("name is shared"));
assert.equal(saved.length,0);
''')


def test_failed_refresh_preserves_target_and_allows_retry(run_picker):
    run_picker(r'''
api=async()=>{throw new Error("Offline");};
await loadDevices(true);
assert.equal($("sp-device").value,"connect-id:device-b");
assert.equal(devicesLoaded,false); assert.equal(devicesLoading,false);
assert(deviceRetryAt>Date.now());
assert($("sp-device-status").textContent.includes("Refresh failed: Offline"));
api=async()=>({devices:[{id:"device-b",name:"Desk"}]});
await loadDevices(false);
assert(devicesLoaded); assert.equal(deviceListError,"");
assert($("sp-device-status").textContent.includes("Available at the last refresh"));
''')


def test_overlapping_changes_are_serialized_and_failed_save_restores_saved_selection(run_picker):
    run_picker(r'''
response.devices=[{id:"device-a",name:"Desk"},{id:"device-b",name:"Other"}];
await loadDevices(false);
let rejectSave; saveConfig=patch=>{saved.push(patch);return new Promise((resolve,reject)=>{rejectSave=reject;});};
$("sp-device").value="connect-id:device-a";
const first=saveDeviceSelection();
assert($("sp-device").disabled);
$("sp-device").value="app"; await saveDeviceSelection();
assert.equal(saved.length,1);
rejectSave(new Error("Save failed")); await first;
assert.equal($("sp-device").disabled,false);
assert.equal($("sp-device").value,"connect-id:device-b");
assert.deepEqual(errors,[["Save failed","error"]]);
''')


def test_old_refresh_response_cannot_replace_a_saved_selection(run_picker):
    run_picker(r'''
deviceRows=[{id:"device-a",name:"Desk"}];
let finish; api=()=>new Promise(resolve=>{finish=resolve;});
const loading=loadDevices(false);
$("sp-device").value="connect-id:device-a";
await saveDeviceSelection();
finish({devices:[{id:"device-b",name:"Old"}],device:"connect",selected:"Old",selectedId:"device-b"});
await loading;
assert.equal($("sp-device").value,"connect-id:device-a");
assert.equal(state.spotify.deviceId,"device-a");
assert.equal(deviceRows[0].id,"device-a");
''')


def test_state_poll_from_before_a_save_cannot_revert_the_saved_selection(run_picker):
    run_picker(r'''
deviceRows=[{id:"device-a",name:"Desk"}];
let finish; api=()=>new Promise(resolve=>{finish=resolve;});
const polling=refresh();
$("sp-device").value="connect-id:device-a";
await saveDeviceSelection();
finish({spotify:{device:"connect",deviceId:"device-b",deviceName:"Old"}});
await polling;
assert.equal($("sp-device").value,"connect-id:device-a");
assert.equal(state.spotify.deviceId,"device-a");
assert.equal(refreshing,false);
assert.equal(schedules.length,1);
''')


def test_state_poll_waits_for_a_save_without_a_rapid_retry_loop(run_picker):
    run_picker(r'''
deviceRows=[{id:"device-a",name:"Desk"}];
let complete, calls=0; api=async()=>{calls++;return state;};
saveConfig=patch=>new Promise(resolve=>{complete=()=>{Object.assign(state.spotify,patch.spotify);resolve();};});
$("sp-device").value="connect-id:device-a";
const saving=saveDeviceSelection();
await refresh();
assert.equal(calls,0);
assert.deepEqual(schedules,[POLL_MS]);
complete(); await saving;
assert.equal(state.spotify.deviceId,"device-a");
''')
