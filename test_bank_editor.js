// Checks for the pure part of static/bank-editor.js (docs/item-banks-ui-spec.md, phase 1):
// a config read into the editor and written back is the same config, the answer
// candidates for arm_from follow the rules the server enforces, and filling from page
// names finds the same families as the server's helper.
//   node test_bank_editor.js
"use strict";
const api = require("./static/bank-editor.js");

let failed = 0;
const ok = (c, m) => { console.log((c ? "  PASS  " : "  FAIL  ") + m); if (!c) failed++; };

// Equal as configs: key order does not matter, a zero quota is the same as none, and
// an empty arm_var or arm_from is the same as null.
function canon(cfg) {
  const sortObj = o => Object.keys(o).sort().reduce((a, k) => (a[k] = o[k], a), {});
  return JSON.stringify({
    name: cfg.name, show_count: +cfg.show_count, order: cfg.order === "fixed" ? "fixed" : "random",
    arm_var: cfg.arm_var || null, arm_from: cfg.arm_from || null,
    items: (cfg.items || []).map(it => ({ item: it.item, versions: sortObj(it.versions || {}) })),
    arms: (cfg.arms || []).map(a => ({ name: a.name, quota: sortObj(Object.fromEntries(Object.entries(a.quota || {}).filter(([, n]) => n))) })),
  });
}
const roundTrip = cfg => api.configFromState(api.stateFromConfig(cfg));

console.log("\n--- round trip ---");
const turing = {
  name: "passages", show_count: 10, arm_var: "arm", order: "random",
  items: Array.from({ length: 80 }, (_, i) => { const p = "P" + String(i + 1).padStart(3, "0"); return { item: p, versions: { H: p + "_H", L: p + "_L" } }; }),
  arms: [{ name: "LLM30", quota: { H: 7, L: 3 } }, { name: "LLM50", quota: { H: 5, L: 5 } }, { name: "LLM70", quota: { H: 3, L: 7 } }],
};
ok(canon(roundTrip(turing)) === canon(turing), "the Turing-test bank comes back unchanged");

const forms = ["F1", "F2", "F3"];
const prometeo = {
  name: "Post", show_count: 20, order: "random", arm_var: "forma_post", arm_from: "forma",
  items: Array.from({ length: 14 }, (_, k) => ({ item: "slot" + String(k + 1).padStart(2, "0"),
    versions: Object.fromEntries(forms.map((f, j) => [f, "post_P" + String(3 * k + j + 1).padStart(2, "0")])) }))
    .concat(["P61", "P62", "P63", "P64", "P65", "P66"].map(a => ({ item: "ancora_" + a, versions: { A: "post_" + a } }))),
  arms: forms.map(f => ({ name: f, quota: { [f]: 14, A: 6 } })),
};
ok(canon(roundTrip(prometeo)) === canon(prometeo), "the PROMETEO post bank (anchors, arm_from) comes back unchanged");

const cts = { name: "CTS", show_count: 1, order: "fixed", arm_var: "forma_cts", arm_from: "forma",
  items: [{ item: "cts", versions: { F1: "cts_forma1", F2: "cts_forma2", F3: "cts_forma3" } }],
  arms: forms.map(f => ({ name: f, quota: { [f]: 1 } })) };
ok(canon(roundTrip(cts)) === canon(cts), "a one-item bank with fixed order comes back unchanged");

const plain = { name: "b", show_count: 2, order: "random", items: [{ item: "x", versions: { A: "x_A", B: "x_B" } }, { item: "y", versions: { A: "y_A" } }], arms: [] };
const back = roundTrip(plain);
ok(canon(back) === canon(plain) && back.arms.length === 0 && back.arm_var === null, "a bank without arms stays without arms");

const zeros = { name: "z", show_count: 2, items: plain.items, arm_var: "g", arms: [{ name: "G1", quota: { A: 2, B: 0 } }] };
ok(JSON.stringify(roundTrip(zeros).arms[0].quota) === JSON.stringify({ A: 2 }), "a zero quota is written as absent");

const st = api.stateFromConfig(prometeo);
ok(JSON.stringify(st.labels) === JSON.stringify(["F1", "F2", "F3", "A"]), "version labels in the order the items give them");
ok(st.groups === true && st.assign === "answer", "groups and the answer source are recognised");
st.assign = "balanced";
ok(api.configFromState(st).arm_from === null, "switching to balanced drops arm_from");
st.assign = "answer"; st.groups = false;
const noArms = api.configFromState(st);
ok(noArms.arms.length === 0 && noArms.arm_from === "forma", "without groups the arms go, and arm_from stays for the server to flag");
const ghost = api.stateFromConfig({ name: "q", show_count: 1, items: [{ item: "i", versions: { A: "i_A" } }], arms: [{ name: "x", quota: { Z: 1 } }] });
ok(ghost.labels.indexOf("Z") !== -1, "a quota label no item has is still shown, so the author can see it");

console.log("\n--- answer candidates ---");
const model = {
  pages: [{ name: "consent" }, { name: "code" }, { name: "cts_forma1" }, { name: "late" }],
  questions: [{ name: "consenso", page: 0 }, { name: "codice_giorno", page: 1, title: "Day" }, { name: "after", page: 3 }],
  calculated: [{ name: "forma", saved: true }, { name: "tmp", saved: false }],
};
const cand = api.answerCandidates(model, ["cts_forma1"]);
const okNames = cand.ok.map(c => c.name), noNames = cand.no.map(c => c.name);
ok(okNames.includes("forma") && okNames.includes("codice_giorno") && okNames.includes("consenso"), "saved calculated values and earlier questions are offered");
ok(noNames.includes("tmp") && noNames.includes("after"), "an unsaved calculated value and a later question are not");
ok(cand.no.find(c => c.name === "after").reason.includes("on or after"), "with the reason the server would give");

console.log("\n--- fill from page names ---");
const filled = api.fillFromNames(["consent", "P001_H", "P001_L", "P002_H", "P002_L", "debrief_page"], "^(?P<item>.+)_(?P<version>[^_]+)$");
ok(filled.items.length === 2 && filled.items[0].versions.L === "P001_L", "families of versions are found with the server's default pattern");
ok(filled.leftOut.includes("consent") && filled.leftOut.includes("debrief_page"), "single pages are left out and listed");

console.log(failed ? `\n${failed} FAILED` : "\nALL PASSED");
process.exit(failed ? 1 : 0);
