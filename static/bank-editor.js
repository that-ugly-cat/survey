/* Visual editor for item banks (docs/item-banks-ui-spec.md, phase 1).
 *
 * The editor never stores anything of its own: it reads a bank's config, lets the
 * author change it through a grid, a quota table and a few choices, and writes the
 * result back into the same form fields the JSON view uses (items, arms, arm
 * variable, arm source, show count, order). Saving is the existing form post, with
 * the existing checks. While editing, the server's own findings and an example draw
 * come from POST .../bank/<id>/check, which stores nothing.
 *
 * The pure functions at the top (state <-> config, answer candidates, fill from page
 * names) are exported for test_bank_editor.js under Node.
 */
(function (root) {
  "use strict";

  // --- pure functions ------------------------------------------------------------

  function uniq(list) {
    var seen = {}, out = [];
    list.forEach(function (x) { if (!seen[x]) { seen[x] = true; out.push(x); } });
    return out;
  }

  // Version labels in the order an author meets them: first in the items, then any a
  // quota names that no item has (the server will flag those, the editor shows them).
  function labelsOf(cfg) {
    var labels = [];
    (cfg.items || []).forEach(function (it) { labels = labels.concat(Object.keys(it.versions || {})); });
    (cfg.arms || []).forEach(function (a) { labels = labels.concat(Object.keys(a.quota || {})); });
    return uniq(labels);
  }

  function stateFromConfig(cfg) {
    cfg = cfg || {};
    var arms = (cfg.arms || []).map(function (a) {
      var quota = {};
      Object.keys(a.quota || {}).forEach(function (lb) { quota[lb] = parseInt(a.quota[lb], 10) || 0; });
      return { name: String(a.name), quota: quota };
    });
    return {
      name: cfg.name || "bank",
      show_count: parseInt(cfg.show_count, 10) || 1,
      order: cfg.order === "fixed" ? "fixed" : "random",
      arm_var: cfg.arm_var || "",
      arm_from: cfg.arm_from || "",
      labels: labelsOf(cfg),
      items: (cfg.items || []).map(function (it) {
        var versions = {};
        Object.keys(it.versions || {}).forEach(function (lb) { versions[lb] = it.versions[lb]; });
        return { item: String(it.item), versions: versions };
      }),
      groups: arms.length > 0,
      arms: arms,
      assign: cfg.arm_from ? "answer" : "balanced",
    };
  }

  // The config the server stores. Empty cells and zero quotas are left out: they mean
  // the same as absent, and the JSON stays as an author would have written it.
  function configFromState(st) {
    var items = st.items.map(function (it) {
      var versions = {};
      st.labels.forEach(function (lb) { if (it.versions[lb]) versions[lb] = it.versions[lb]; });
      return { item: it.item, versions: versions };
    });
    var arms = st.groups ? st.arms.map(function (a) {
      var quota = {};
      st.labels.forEach(function (lb) { if (a.quota[lb]) quota[lb] = a.quota[lb]; });
      return { name: a.name, quota: quota };
    }) : [];
    return {
      name: st.name,
      items: items,
      show_count: st.show_count,
      arm_var: st.arm_var || null,
      arm_from: ((st.assign === "answer" || st.assign === "rotation") && st.arm_from) ? st.arm_from : null,
      arms: arms,
      order: st.order,
    };
  }

  // Where the arm can come from: questions asked before the bank's first page, and
  // calculated values saved with the answers. The rest are returned too, with the
  // reason, so the editor can say why they are not offered.
  function answerCandidates(model, bankPages) {
    var index = {};
    model.pages.forEach(function (p, i) { index[p.name] = i; });
    var first = Infinity;
    bankPages.forEach(function (name) { if (name in index) first = Math.min(first, index[name]); });
    var ok = [], no = [];
    model.calculated.forEach(function (c) {
      if (c.saved) ok.push({ name: c.name, label: c.name + " (calculated value)" });
      else no.push({ name: c.name, reason: "calculated value not saved with the answers" });
    });
    model.questions.forEach(function (q) {
      var label = q.name + (q.title ? " — " + q.title : "");
      if (q.page < first) ok.push({ name: q.name, label: label + " (page " + (q.page + 1) + ")" });
      else no.push({ name: q.name, reason: "asked on or after the bank's first page" });
    });
    return { ok: ok, no: no };
  }

  // Items from the page names, with the same named groups the server's helper uses;
  // only families of two or more versions, since a lone match is more often a common
  // page with an underscore in its name.
  function fillFromNames(pageNames, pattern) {
    var rx = new RegExp(pattern.replace(/\(\?P</g, "(?<"));
    var items = [], index = {}, unmatched = [];
    pageNames.forEach(function (name) {
      var m = rx.exec(name);
      if (!m || !m.groups || m.groups.item === undefined || m.groups.version === undefined) {
        unmatched.push(name); return;
      }
      if (!index[m.groups.item]) { index[m.groups.item] = { item: m.groups.item, versions: {} }; items.push(index[m.groups.item]); }
      index[m.groups.item].versions[m.groups.version] = name;
    });
    var multi = items.filter(function (it) { return Object.keys(it.versions).length > 1; });
    var lone = items.filter(function (it) { return Object.keys(it.versions).length < 2; })
                    .map(function (it) { return it.versions[Object.keys(it.versions)[0]]; });
    return { items: multi, leftOut: unmatched.concat(lone) };
  }

  // Rotation across waves: the same expression rotation.py builds and recognises, so the
  // preview shows exactly what the server will write.
  function buildExpression(question, wave, arms) {
    var k = arms.length, r = "({" + question + "} + " + (parseInt(wave, 10) || 0) + ") % " + k;
    if (k === 1) return "'" + arms[0] + "'";
    var expr = "'" + arms[k - 1] + "'";
    for (var i = k - 2; i >= 0; i--) expr = "iif(" + r + " = " + i + ", '" + arms[i] + "', " + expr + ")";
    return expr;
  }

  function parseExpression(expr) {
    var m = /^iif\(\(\{([A-Za-z_][A-Za-z0-9_]*)\} \+ (\d+)\) % (\d+) = 0, /.exec(expr || "");
    if (!m) return null;
    var arms = (expr.match(/'([^']*)'/g) || []).map(function (s) { return s.slice(1, -1); });
    if (arms.length !== parseInt(m[3], 10)) return null;
    var parsed = { question: m[1], wave: parseInt(m[2], 10), arms: arms };
    return buildExpression(parsed.question, parsed.wave, arms) === expr ? parsed : null;
  }

  // Expected share per arm: a day of the month weighted by the calendar, any other
  // bounded integer as uniform, null when the range is unknown.
  function expectedShares(meta, wave, arms) {
    var lo = parseInt(meta && meta.min, 10), hi = parseInt(meta && meta.max, 10);
    if (isNaN(lo) || isNaN(hi) || hi < lo || hi - lo > 10000) return null;
    var weights = {}, v;
    if (lo === 1 && hi === 31) {
      for (v = 1; v <= 28; v++) weights[v] = 12;
      weights[29] = 11.25; weights[30] = 11; weights[31] = 7;
    } else {
      for (v = lo; v <= hi; v++) weights[v] = 1;
    }
    var total = 0, shares = {}, k = arms.length;
    arms.forEach(function (a) { shares[a] = 0; });
    Object.keys(weights).forEach(function (key) {
      var n = parseInt(key, 10); total += weights[key];
      shares[arms[(n + parseInt(wave, 10)) % k]] += weights[key];
    });
    arms.forEach(function (a) { shares[a] = Math.round(1000 * shares[a] / total) / 10; });
    return shares;
  }

  var api = { stateFromConfig: stateFromConfig, configFromState: configFromState,
              answerCandidates: answerCandidates, fillFromNames: fillFromNames, labelsOf: labelsOf,
              buildExpression: buildExpression, parseExpression: parseExpression, expectedShares: expectedShares };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  // --- the editor in the page ----------------------------------------------------

  var COLOURS = ["#4a6fa5", "#9f7aea", "#38b2ac", "#d69e2e", "#e53e3e", "#48bb78", "#ed64a6", "#a0aec0"];

  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") e.textContent = attrs[k];
      else if (k === "class") e.className = attrs[k];
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== false && attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }

  function Editor(card, opts) {
    this.card = card;
    this.opts = opts;               // {bankId, slug, model, regex, nResponses, nDraws}
    this.form = card.querySelector("form.bank-form");
    this.fields = {
      name: this.form.querySelector('[name="bank_name"]'),
      show: this.form.querySelector('[name="show_count"]'),
      order: this.form.querySelector('[name="order"]'),
      armVar: this.form.querySelector('[name="arm_var"]'),
      armFrom: this.form.querySelector('[name="arm_from"]'),
      items: this.form.querySelector('[name="items_json"]'),
      arms: this.form.querySelector('[name="arms_json"]'),
    };
    this.st = stateFromConfig(this.readFields());
    this.detectRotation();
    this.tab = "content";
    this.filter = "";
    this.findings = { errors: [], warnings: [], example: [] };
    this.build();
  }

  // A bank whose arm comes from a rotation value the platform wrote, over these same
  // groups in this order, opens in the rotation helper rather than as a bare answer.
  Editor.prototype.detectRotation = function () {
    var st = this.st;
    if (!st.arm_from) return;
    var calc = this.opts.model.calculated.filter(function (c) { return c.name === st.arm_from && c.rotation; })[0];
    var names = st.arms.map(function (a) { return a.name; });
    if (calc && JSON.stringify(calc.rotation.arms) === JSON.stringify(names)) {
      st.assign = "rotation";
      st.rot = { question: calc.rotation.question, wave: calc.rotation.wave, name: calc.name };
    }
  };

  Editor.prototype.readFields = function () {
    var f = this.fields;
    return {
      name: f.name.value, show_count: f.show.value, order: f.order.value,
      arm_var: f.armVar.value, arm_from: f.armFrom.value,
      items: JSON.parse(f.items.value || "[]"), arms: JSON.parse(f.arms.value || "[]"),
    };
  };

  // Every change goes to the form fields at once, so the JSON view and the save always
  // carry what the editor shows.
  Editor.prototype.sync = function () {
    this.st.name = this.fields.name.value;  // the name stays a field of its own, above the editor
    var cfg = configFromState(this.st), f = this.fields;
    f.name.value = cfg.name;
    f.show.value = cfg.show_count;
    f.order.value = cfg.order;
    f.armVar.value = cfg.arm_var || "";
    f.armFrom.value = cfg.arm_from || "";
    f.items.value = JSON.stringify(cfg.items, null, 1);
    f.arms.value = JSON.stringify(cfg.arms, null, 1);
    this.scheduleCheck();
  };

  Editor.prototype.scheduleCheck = function () {
    var self = this;
    clearTimeout(this.timer);
    this.timer = setTimeout(function () { self.check(); }, 350);
  };

  Editor.prototype.check = function () {
    var self = this, o = this.opts;
    fetch("/admin/surveys/" + encodeURIComponent(o.slug) + "/randomization/bank/" + o.bankId + "/check", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(configFromState(this.st)),
    }).then(function (r) { return r.json(); }).then(function (data) {
      self.findings = { errors: data.errors || [], warnings: data.warnings || [], example: data.example || [] };
      self.renderTabs(); self.renderSide();
      if (self.tab === "check") self.renderPanel();
    }).catch(function () {
      self.findings = { errors: ["The check could not reach the server; the save will still check."], warnings: [], example: [] };
      self.renderTabs();
    });
  };

  // Which tab a server finding belongs to, by what it talks about.
  function tabOf(message) {
    var m = message.toLowerCase();
    if (/arm_from|arm_var|\barm\b|quota|arms\b/.test(m)) return "groups";
    if (/show_count/.test(m)) return "draw";
    return "content";
  }

  Editor.prototype.build = function () {
    var self = this;
    this.root = el("div", { class: "be" });
    this.tabsEl = el("div", { class: "be-tabs", role: "tablist" });
    this.panelEl = el("div", { class: "be-panel", role: "tabpanel" });
    this.sideEl = el("aside", { class: "be-side", "aria-label": "Example draw" });
    var toggle = el("button", { type: "button", class: "btn-sm btn-secondary be-json-toggle", text: "Edit as JSON",
                                onclick: function () { self.toggleJson(); } });
    this.jsonNote = el("p", { class: "hint" });
    this.fields.name.addEventListener("input", function () { self.sync(); });
    this.root.appendChild(el("div", { class: "be-head" }, [toggle, this.jsonNote]));
    this.root.appendChild(el("div", { class: "be-layout" }, [el("div", {}, [this.tabsEl, this.panelEl]), this.sideEl]));
    this.json = this.card.querySelector(".bank-json");
    this.json.parentNode.insertBefore(this.root, this.json);
    this.json.style.display = "none";
    this.render();
    this.check();
  };

  Editor.prototype.toggleJson = function () {
    var showing = this.json.style.display !== "none";
    if (!showing) {
      this.json.style.display = "";
      this.root.querySelector(".be-layout").style.display = "none";
      this.root.querySelector(".be-json-toggle").textContent = "Back to the editor";
      this.jsonNote.textContent = "These fields are what the editor writes; edit them freely, then go back.";
      return;
    }
    try {
      this.st = stateFromConfig(this.readFields());
      this.detectRotation();
    } catch (e) {
      this.jsonNote.textContent = "The JSON does not parse (" + e.message + "): fix it to go back to the editor.";
      return;
    }
    this.json.style.display = "none";
    this.root.querySelector(".be-layout").style.display = "";
    this.root.querySelector(".be-json-toggle").textContent = "Edit as JSON";
    this.jsonNote.textContent = "";
    this.render(); this.sync();
  };

  Editor.prototype.render = function () { this.renderTabs(); this.renderPanel(); this.renderSide(); };

  Editor.prototype.renderTabs = function () {
    var self = this, counts = { content: 0, draw: 0, groups: 0 };
    this.findings.errors.forEach(function (m) { counts[tabOf(m)]++; });
    var sumBad = this.st.groups ? this.st.arms.filter(function (a) { return self.armTotal(a) !== self.st.show_count; }).length : 0;
    counts.groups = Math.max(counts.groups, sumBad);
    var tabs = [["content", "1 Content"], ["draw", "2 Draw"], ["groups", "3 Groups"], ["check", "4 Check & save"]];
    this.tabsEl.textContent = "";
    tabs.forEach(function (t) {
      var n = t[0] === "check" ? self.findings.errors.length : counts[t[0]];
      var badge = t[0] === "check" ? (n ? el("span", { class: "be-badge bad", text: n + " error" + (n > 1 ? "s" : "") })
                                        : (self.findings.warnings.length ? el("span", { class: "be-badge warn", text: self.findings.warnings.length + " warning" + (self.findings.warnings.length > 1 ? "s" : "") }) : null))
                                     : el("span", { class: "be-badge " + (n ? "bad" : "ok"), text: n ? String(n) : "✓" });
      self.tabsEl.appendChild(el("button", { type: "button", role: "tab", class: "be-tab", "aria-selected": String(self.tab === t[0]),
        onclick: function () { self.tab = t[0]; self.render(); } }, [t[1], badge]));
    });
  };

  Editor.prototype.renderPanel = function () {
    this.panelEl.textContent = "";
    ({ content: this.contentTab, draw: this.drawTab, groups: this.groupsTab, check: this.checkTab })[this.tab].call(this, this.panelEl);
  };

  // --- tab 1: items and versions --------------------------------------------------

  Editor.prototype.contentTab = function (p) {
    var self = this, st = this.st, model = this.opts.model;
    var used = {};
    st.items.forEach(function (it) { Object.keys(it.versions).forEach(function (lb) { if (it.versions[lb]) used[it.versions[lb]] = (used[it.versions[lb]] || 0) + 1; }); });
    var cells = st.items.length * st.labels.length, filled = 0;
    st.items.forEach(function (it) { st.labels.forEach(function (lb) { if (it.versions[lb]) filled++; }); });

    var regex = el("input", { type: "text", class: "cond-input bank-regex", value: this.opts.regex,
                              title: "A regex with named groups item and version, matched against the page names" });
    var fillNote = el("p", { class: "hint" });
    var fill = el("button", { type: "button", class: "btn-sm btn-secondary", text: "Fill from page names", onclick: function () {
      var res;
      try { res = fillFromNames(model.pages.map(function (pg) { return pg.name; }), regex.value); }
      catch (e) { fillNote.textContent = "The pattern does not compile: " + e.message; return; }
      if (!res.items.length) { fillNote.textContent = "No page names matched the pattern in families of two or more versions."; return; }
      if (st.items.length && !confirm("Replace the " + st.items.length + " item(s) in the grid with " + res.items.length + " found in the page names?")) return;
      var cfg = configFromState(st), keep = { groups: st.groups, assign: st.assign, arm_from: st.arm_from };
      cfg.items = res.items;
      self.st = Object.assign(stateFromConfig(cfg), keep);
      self.st.labels = uniq(labelsOf({ items: res.items }).concat(st.labels));
      fillNote.textContent = res.items.length + " item(s). Left out: " + (res.leftOut.join(", ") || "nothing") + ".";
      self.render(); self.sync();
    } });
    var search = el("input", { type: "search", class: "cond-input be-search", placeholder: "Search items", value: this.filter,
                               "aria-label": "Search items", oninput: function (e) { self.filter = e.target.value; self.renderGrid(); } });
    var gen = el("button", { type: "button", class: "btn-sm btn-secondary", text: "Generate pages from a table…",
      onclick: function () { self.openGenerator(); } });
    p.appendChild(el("div", { class: "count-row" }, [gen, fill, search]));
    p.appendChild(el("details", { class: "be-adv" }, [el("summary", { class: "hint", text: "Naming pattern" }), regex]));
    p.appendChild(fillNote);
    p.appendChild(el("p", { class: "hint be-count", text: st.items.length + " item(s) · " + st.labels.length + " version(s) · " +
      (cells - filled) + " empty cell(s)" + (Object.keys(used).some(function (k) { return used[k] > 1; }) ? " · a page is used twice" : "") }));
    this.gridWrap = el("div", { class: "be-grid" });
    p.appendChild(this.gridWrap);
    this.renderGrid();
    p.appendChild(el("div", { class: "count-row" }, [
      el("button", { type: "button", class: "btn-sm btn-secondary", text: "+ Item", onclick: function () {
        var n = st.items.length + 1, name = "item" + n;
        while (st.items.some(function (it) { return it.item === name; })) name = "item" + (++n);
        st.items.push({ item: name, versions: {} }); self.render(); self.sync();
      } }),
      el("button", { type: "button", class: "btn-sm btn-secondary", text: "+ Version", onclick: function () {
        var label = prompt("Name of the new version (a short label, e.g. H or B):");
        if (!label) return;
        label = label.trim();
        if (st.labels.indexOf(label) !== -1) { alert("There is already a version called " + label + "."); return; }
        st.labels.push(label); self.render(); self.sync();
      } }),
    ]));
    p.appendChild(el("p", { class: "hint", text: "Pages held by a pool or another bank are listed but cannot be chosen; the reason is next to the name." }));
  };

  Editor.prototype.renderGrid = function () {
    var self = this, st = this.st, model = this.opts.model, bankId = this.opts.bankId;
    var used = {};
    st.items.forEach(function (it) { Object.keys(it.versions).forEach(function (lb) { if (it.versions[lb]) used[it.versions[lb]] = true; }); });
    var armLabels = {};
    if (st.groups) st.arms.forEach(function (a) { Object.keys(a.quota).forEach(function (lb) { if (a.quota[lb] > 0) armLabels[lb] = true; }); });
    var head = el("tr", {}, [el("th", { text: "Item" })].concat(st.labels.map(function (lb, k) {
      var input = el("input", { type: "text", class: "be-label", value: lb, "aria-label": "Version label",
        onchange: function (e) {
          var nw = e.target.value.trim();
          if (!nw || nw === lb) { e.target.value = lb; return; }
          if (st.labels.indexOf(nw) !== -1) { alert("There is already a version called " + nw + "."); e.target.value = lb; return; }
          st.labels[k] = nw;
          st.items.forEach(function (it) { if (lb in it.versions) { it.versions[nw] = it.versions[lb]; delete it.versions[lb]; } });
          st.arms.forEach(function (a) { if (lb in a.quota) { a.quota[nw] = a.quota[lb]; delete a.quota[lb]; } });
          self.render(); self.sync();
        } });
      var empty = !st.items.some(function (it) { return it.versions[lb]; });
      var drop = empty ? el("button", { type: "button", class: "be-x", title: "Remove this version", text: "×",
        onclick: function () { st.labels.splice(k, 1); st.arms.forEach(function (a) { delete a.quota[lb]; }); self.render(); self.sync(); } }) : null;
      return el("th", {}, [el("span", { class: "be-dot", style: "background:" + COLOURS[k % COLOURS.length] }), input, drop]);
    })).concat([el("th", {})]));
    var body = el("tbody");
    var needle = this.filter.toLowerCase();
    st.items.forEach(function (it, i) {
      if (needle && it.item.toLowerCase().indexOf(needle) === -1 &&
          !Object.keys(it.versions).some(function (lb) { return (it.versions[lb] || "").toLowerCase().indexOf(needle) !== -1; })) return;
      var short = Object.keys(armLabels).some(function (lb) { return !it.versions[lb]; });
      var name = el("input", { type: "text", class: "be-item", value: it.item, "aria-label": "Item name",
        onchange: function (e) { var v = e.target.value.trim(); if (v) { it.item = v; self.sync(); } else e.target.value = it.item; } });
      var row = el("tr", { class: short ? "be-short" : "" }, [el("td", {}, [name])]);
      st.labels.forEach(function (lb) {
        var sel = el("select", { class: "be-cell", "aria-label": it.item + " version " + lb,
          onchange: function (e) { if (e.target.value) it.versions[lb] = e.target.value; else delete it.versions[lb]; self.render(); self.sync(); } });
        sel.appendChild(el("option", { value: "", text: "—" }));
        model.pages.forEach(function (pg) {
          var own = pg.owner && !(pg.owner.kind === "bank" && pg.owner.id === bankId);
          var mine = it.versions[lb] === pg.name;
          var taken = !mine && used[pg.name];
          var label = pg.name + (own ? "  (in " + pg.owner.kind + " " + pg.owner.name + ")" : taken ? "  (used in this bank)" : "");
          sel.appendChild(el("option", { value: pg.name, text: label, disabled: (own && !mine) ? "disabled" : null,
                                          selected: mine ? "selected" : null }));
        });
        row.appendChild(el("td", {}, [sel]));
      });
      row.appendChild(el("td", { class: "be-row-actions" }, [
        el("button", { type: "button", class: "be-x", title: "Move up", text: "↑", disabled: i === 0 ? "disabled" : null,
          onclick: function () { st.items.splice(i - 1, 0, st.items.splice(i, 1)[0]); self.render(); self.sync(); } }),
        el("button", { type: "button", class: "be-x", title: "Move down", text: "↓", disabled: i === st.items.length - 1 ? "disabled" : null,
          onclick: function () { st.items.splice(i + 1, 0, st.items.splice(i, 1)[0]); self.render(); self.sync(); } }),
        el("button", { type: "button", class: "be-x", title: "Remove item", text: "×",
          onclick: function () { st.items.splice(i, 1); self.render(); self.sync(); } }),
      ]));
      body.appendChild(row);
    });
    this.gridWrap.textContent = "";
    this.gridWrap.appendChild(el("table", {}, [el("thead", {}, [head]), body]));
    if (!st.items.length) this.gridWrap.appendChild(el("p", { class: "empty-counts", text: "No items yet: fill from the page names, or add them one by one." }));
  };

  // --- page generator (phase 3) ---------------------------------------------------
  // A template page with {{placeholders}} and a table with one row per page; the server
  // (pagegen.py) builds the pages, first as a dry run to show what it would do.

  Editor.prototype.openGenerator = function () {
    var self = this, o = this.opts, model = o.model;
    var dlg = el("dialog", { class: "be-dialog", "aria-label": "Generate pages from a table" });
    // pages with {{placeholders}} first: those are the templates; any free page can serve
    var free = model.pages.filter(function (pg) { return !pg.owner; });
    var withPh = free.filter(function (pg) { return (pg.placeholders || []).length; });
    free = withPh.concat(free.filter(function (pg) { return !(pg.placeholders || []).length; }));
    var tpl = el("select", { class: "bank-select", "aria-label": "Template page" },
      free.map(function (pg) {
        var ph = (pg.placeholders || []).map(function (x) { return "{{" + x + "}}"; }).join(" ");
        return el("option", { value: pg.name, text: pg.name + (pg.title ? " — " + pg.title : "") + (ph ? "  · " + ph : "  · no placeholders") });
      }));
    var table = el("textarea", { class: "cond-textarea", rows: "8", "aria-label": "Table",
      placeholder: "item\tversion\ttext\nP01\tA\tThe text of P01 in version A…" });
    var file = el("input", { type: "file", accept: ".csv,.tsv,.txt", "aria-label": "Upload a CSV file",
      onchange: function (e) {
        var f = e.target.files[0]; if (!f) return;
        var reader = new FileReader();
        reader.onload = function () { table.value = reader.result; stale(); };
        reader.readAsText(f, "utf-8");
      } });
    var remove = el("input", { type: "checkbox", checked: "checked" });
    var out = el("div", { class: "be-gen-out" });
    var addBtn = el("button", { type: "button", class: "btn-primary", text: "Add pages", disabled: "disabled" });
    var previewed = null;
    function stale() { previewed = null; addBtn.disabled = true; addBtn.textContent = "Add pages"; }
    tpl.addEventListener("change", stale); table.addEventListener("input", stale);
    function payload(extra) {
      return Object.assign({ template: tpl.value, table: table.value, remove_template: remove.checked }, extra || {});
    }
    function post(body) {
      return fetch("/admin/surveys/" + encodeURIComponent(o.slug) + "/generate-pages", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      }).then(function (r) { return r.json().then(function (d) { return { status: r.status, d: d }; }); });
    }
    function showPlan(d) {
      out.textContent = "";
      (d.errors || []).forEach(function (m) { out.appendChild(el("div", { class: "pool-error", text: m })); });
      (d.notes || []).forEach(function (m) { out.appendChild(el("div", { class: "bank-warning", text: m })); });
      out.appendChild(el("p", { class: "hint", text: "Placeholders in the template: " +
        ((d.placeholders || []).map(function (x) { return "{{" + x + "}}"; }).join(", ") || "none") }));
      if (!d.count) return;
      var names = d.names.length > 6 ? d.names.slice(0, 5).concat(["…", d.names[d.names.length - 1]]) : d.names;
      out.appendChild(el("p", { class: "hint", text: d.count + " page(s), " + d.items.length + " item(s): " + names.join(", ") +
        (remove.checked ? ". The template page will be removed." : ". The template page stays, and every participant would see it.") }));
      var samples = el("div", { class: "be-samples" });
      d.sample.forEach(function (page) {
        var box = el("div", { class: "be-sample" }, [el("div", { class: "field-label", text: page.name })]);
        (page.elements || []).forEach(function (e) {
          if (e.type === "html") {
            // the author's own HTML, shown as the participant would see it, without scripts
            box.appendChild(el("iframe", { sandbox: "", class: "be-sample-frame", title: e.name,
              srcdoc: "<body style='margin:0;font-family:system-ui,sans-serif'>" + e.html + "</body>" }));
          } else {
            box.appendChild(el("p", { class: "hint", text: (e.title || e.name) + "  [" + e.type + ", " + e.name + "]" }));
          }
        });
        samples.appendChild(box);
      });
      out.appendChild(samples);
    }
    var previewBtn = el("button", { type: "button", class: "btn-sm btn-secondary", text: "Preview",
      onclick: function () {
        out.textContent = "Checking…";
        post(payload({ dry_run: true })).then(function (res) {
          showPlan(res.d);
          if (!(res.d.errors || []).length && res.d.count) {
            previewed = payload(); addBtn.disabled = false; addBtn.textContent = "Add " + res.d.count + " pages";
          }
        }).catch(function () { out.textContent = "The server could not be reached."; });
      } });
    function generate(confirmed) {
      addBtn.disabled = true;
      post(Object.assign({}, previewed, { confirm: confirmed })).then(function (res) {
        if (res.status === 409 && res.d.error === "responses_held") {
          if (confirm("The survey holds " + res.d.held + " response(s): adding pages under them changes the questionnaire they answered. Add the pages anyway?"))
            return generate(true);
          addBtn.disabled = false; return;
        }
        if (!res.d.ok) { showPlan(res.d); addBtn.disabled = false; return; }
        Object.keys(res.d.model).forEach(function (k) { model[k] = res.d.model[k]; });
        self.mergeItems(res.d.items);
        dlg.close();
        self.jsonNote.textContent = res.d.count + " page(s) added to the questionnaire" +
          (res.d.removed_template ? ", template removed" : "") + "; the grid now uses them. Save the bank to keep the bank's part.";
      }).catch(function () { out.textContent = "The server could not be reached; nothing was added."; addBtn.disabled = false; });
    }
    addBtn.addEventListener("click", function () { if (previewed) generate(false); });

    dlg.appendChild(el("h3", { text: "Generate pages from a table" }));
    dlg.appendChild(el("p", { class: "hint", text: "Each row of the table becomes a page named item_version, a copy of the template with its {{placeholders}} filled from the row's columns. Questions are renamed page_role, the names the long export expects." }));
    dlg.appendChild(el("div", { class: "field-label", text: "1 · Template page (a page not used by a pool or a bank)" }));
    dlg.appendChild(free.length ? tpl : el("p", { class: "pool-error", text: "Every page belongs to a pool or a bank: add a template page to the questionnaire first." }));
    dlg.appendChild(el("div", { class: "field-label", text: "2 · Table: columns item, version, and one per placeholder. Paste from a spreadsheet, or upload a CSV" }));
    dlg.appendChild(table);
    dlg.appendChild(file);
    dlg.appendChild(el("label", { class: "be-radio" }, [remove, " Remove the template page after generating (otherwise every participant sees it)"]));
    dlg.appendChild(el("div", { class: "count-row" }, [previewBtn]));
    dlg.appendChild(el("div", { class: "field-label", text: "3 · Preview" }));
    dlg.appendChild(out);
    dlg.appendChild(el("div", { class: "pool-actions" }, [addBtn,
      el("button", { type: "button", class: "btn-sm btn-secondary", text: "Cancel", onclick: function () { dlg.close(); } })]));
    dlg.addEventListener("close", function () { dlg.remove(); });
    document.body.appendChild(dlg);
    dlg.showModal();
  };

  // Generated items join the grid: an item already there gains the new versions.
  Editor.prototype.mergeItems = function (items) {
    var st = this.st;
    items.forEach(function (gen) {
      var have = st.items.filter(function (it) { return it.item === gen.item; })[0];
      if (!have) { have = { item: gen.item, versions: {} }; st.items.push(have); }
      Object.keys(gen.versions).forEach(function (lb) {
        have.versions[lb] = gen.versions[lb];
        if (st.labels.indexOf(lb) === -1) st.labels.push(lb);
      });
    });
    this.render(); this.sync();
  };

  // --- tab 2: the draw ------------------------------------------------------------

  Editor.prototype.drawTab = function (p) {
    var self = this, st = this.st;
    var people = this.people || 300;
    var line = el("p", { class: "hint be-consequence" });
    function explain() {
      var k = st.show_count, n = st.items.length;
      line.textContent = n ? "Each participant answers " + k + " of " + n + " item(s). With " + people +
        " participants, each item is shown about " + Math.round(people * k / n) + " times." : "Add items in the Content tab first.";
    }
    var show = el("input", { type: "number", min: "1", max: "50", value: st.show_count, class: "be-num", "aria-label": "Items per participant",
      oninput: function (e) { st.show_count = parseInt(e.target.value, 10) || 1; explain(); self.sync(); self.renderTabs(); } });
    p.appendChild(el("div", { class: "field-label", text: "Items per participant" }));
    p.appendChild(el("div", { class: "count-row" }, [show, el("span", { class: "hint", text: "of " + st.items.length + " item(s) in this bank" })]));
    p.appendChild(el("div", { class: "field-label", text: "Order" }));
    ["random", "fixed"].forEach(function (o) {
      p.appendChild(el("label", { class: "be-radio" }, [
        el("input", { type: "radio", name: "be-order-" + self.opts.bankId, value: o, checked: st.order === o ? "checked" : null,
          onchange: function () { st.order = o; self.sync(); } }),
        o === "random" ? " random for each participant" : " as in the questionnaire"]));
    });
    var ppl = el("input", { type: "number", min: "1", value: people, class: "be-num", "aria-label": "Expected participants",
      oninput: function (e) { self.people = parseInt(e.target.value, 10) || 1; people = self.people; explain(); } });
    p.appendChild(el("div", { class: "field-label", text: "Expected participants (only for the estimate below)" }));
    p.appendChild(ppl);
    explain();
    p.appendChild(line);
  };

  // --- tab 3: groups --------------------------------------------------------------

  Editor.prototype.armTotal = function (a) {
    var t = 0; Object.keys(a.quota).forEach(function (lb) { t += a.quota[lb] || 0; }); return t;
  };

  Editor.prototype.bankPages = function () {
    var pages = [];
    this.st.items.forEach(function (it) { Object.keys(it.versions).forEach(function (lb) { if (it.versions[lb]) pages.push(it.versions[lb]); }); });
    return pages;
  };

  Editor.prototype.groupsTab = function (p) {
    var self = this, st = this.st;
    [[false, " No groups — versions balanced item by item"], [true, " Groups with quotas"]].forEach(function (g) {
      p.appendChild(el("label", { class: "be-radio" }, [
        el("input", { type: "radio", name: "be-groups-" + self.opts.bankId, checked: st.groups === g[0] ? "checked" : null,
          onchange: function () {
            st.groups = g[0];
            if (st.groups && !st.arms.length) {
              st.arms = [{ name: "A", quota: {} }];
              if (!st.arm_var) st.arm_var = "group";
            }
            self.render(); self.sync();
          } }), g[1]]));
    });
    if (!st.groups) {
      p.appendChild(el("p", { class: "hint", text: "Without groups each drawn item gets the version it has been shown in least." }));
      return;
    }
    p.appendChild(el("div", { class: "field-label", text: "Quotas — each row must add up to the items per participant (" + st.show_count + ")" }));
    var head = el("tr", {}, [el("th", { text: "Group" })].concat(st.labels.map(function (lb) { return el("th", { text: lb }); }))
                              .concat([el("th", { text: "Total" }), el("th", {})]));
    var body = el("tbody");
    st.arms.forEach(function (a, i) {
      var total = el("td");
      function paint() {
        var t = self.armTotal(a), d = t - st.show_count;
        total.textContent = d ? t + " (" + (d > 0 ? "+" : "") + d + ")" : t + " ✓";
        total.className = d ? "arms-bad" : "arms-ok";
      }
      var row = el("tr", {}, [el("td", {}, [el("input", { type: "text", class: "be-item", value: a.name, "aria-label": "Group name",
        onchange: function (e) { var v = e.target.value.trim(); if (v) { a.name = v; self.sync(); } else e.target.value = a.name; } })])]);
      st.labels.forEach(function (lb) {
        row.appendChild(el("td", {}, [el("input", { type: "number", min: "0", class: "be-num", value: a.quota[lb] || 0, "aria-label": a.name + " " + lb,
          oninput: function (e) { a.quota[lb] = Math.max(0, parseInt(e.target.value, 10) || 0); paint(); self.sync(); self.renderTabs(); } })]));
      });
      row.appendChild(total);
      row.appendChild(el("td", {}, [el("button", { type: "button", class: "be-x", title: "Remove group", text: "×",
        onclick: function () { st.arms.splice(i, 1); if (!st.arms.length) st.groups = false; self.render(); self.sync(); } })]));
      paint();
      body.appendChild(row);
    });
    p.appendChild(el("div", { class: "be-grid" }, [el("table", {}, [el("thead", {}, [head]), body])]));
    p.appendChild(el("div", { class: "count-row" }, [el("button", { type: "button", class: "btn-sm btn-secondary", text: "+ Group", onclick: function () {
      st.arms.push({ name: "G" + (st.arms.length + 1), quota: {} }); self.render(); self.sync();
    } })]));

    p.appendChild(el("div", { class: "field-label", text: "Group variable" }));
    var hintVar = el("span", { class: "hint" });
    function paintVar() { hintVar.textContent = st.arm_var ? " available in visibleIf as {" + st.arm_var + "}, saved with each response" : " a name is required with groups"; }
    p.appendChild(el("div", { class: "count-row" }, [el("input", { type: "text", class: "cond-input be-var", value: st.arm_var, "aria-label": "Group variable",
      oninput: function (e) { st.arm_var = e.target.value.trim(); paintVar(); self.sync(); } }), hintVar]));
    paintVar();

    p.appendChild(el("div", { class: "field-label", text: "How a participant gets a group" }));
    var cards = el("div", { class: "be-cards", role: "radiogroup" });
    [["balanced", "Balanced by the platform", "The least-used group goes to the next participant."],
     ["answer", "From an answer", "A question or calculated value names the group. Sizes follow the answers."],
     ["rotation", "Rotation across waves", "Each participant gets the next group at each wave, from an answer every wave shares."]].forEach(function (c) {
      cards.appendChild(el("button", { type: "button", role: "radio", class: "be-card", "aria-checked": String(st.assign === c[0]),
        onclick: function () {
          st.assign = c[0];
          if (c[0] === "rotation" && st.rot) st.arm_from = st.rot.name;
          self.render(); self.sync();
        } }, [el("b", { text: c[1] }), el("span", { class: "hint", text: c[2] })]));
    });
    p.appendChild(cards);
    if (st.assign === "rotation") { this.rotationPanel(p); return; }
    if (st.assign !== "answer") return;
    var cand = answerCandidates(this.opts.model, this.bankPages());
    var sel = el("select", { class: "bank-select", "aria-label": "Answer that names the group",
      onchange: function (e) { st.arm_from = e.target.value; self.sync(); } });
    sel.appendChild(el("option", { value: "", text: "choose…" }));
    cand.ok.forEach(function (c) { sel.appendChild(el("option", { value: c.name, text: c.label, selected: st.arm_from === c.name ? "selected" : null })); });
    if (st.arm_from && !cand.ok.some(function (c) { return c.name === st.arm_from; }))
      sel.appendChild(el("option", { value: st.arm_from, text: st.arm_from + " (not valid here)", selected: "selected" }));
    p.appendChild(el("div", { class: "field-label", text: "Answer that names the group" }));
    p.appendChild(sel);
    p.appendChild(el("p", { class: "hint", text: "Its value must be one of the group names: " + st.arms.map(function (a) { return a.name; }).join(", ") + "." }));
    if (cand.no.length) {
      p.appendChild(el("details", { class: "be-adv" }, [el("summary", { class: "hint", text: cand.no.length + " not available, and why" }),
        el("ul", { class: "be-why" }, cand.no.map(function (c) { return el("li", { class: "hint", text: c.name + ": " + c.reason }); }))]));
    }
  };

  // --- rotation across waves (phase 2) ---------------------------------------------

  Editor.prototype.rotationPanel = function (p) {
    var self = this, st = this.st, model = this.opts.model;
    var arms = st.arms.map(function (a) { return a.name; });
    var cand = answerCandidates(model, this.bankPages()).ok.filter(function (c) {
      return model.questions.some(function (q) { return q.name === c.name; });
    });
    if (!st.rot) {
      var numeric = cand.filter(function (c) {
        var q = model.questions.filter(function (x) { return x.name === c.name; })[0];
        return q && q.inputType === "number";
      })[0] || cand[0];
      st.rot = { question: numeric ? numeric.name : "", wave: 1, name: st.arm_from || "form" };
    }
    var rot = st.rot;
    st.arm_from = rot.name;

    var box = el("div", { class: "be-rot" });
    p.appendChild(box);
    function paint() {
      box.textContent = "";
      var qSel = el("select", { class: "bank-select", "aria-label": "Answer every wave shares",
        onchange: function (e) { rot.question = e.target.value; paint(); } });
      if (!cand.length) qSel.appendChild(el("option", { value: "", text: "no question before the bank" }));
      cand.forEach(function (c) { qSel.appendChild(el("option", { value: c.name, text: c.label, selected: c.name === rot.question ? "selected" : null })); });
      var wave = el("input", { type: "number", min: "1", class: "be-num", value: rot.wave, "aria-label": "Wave of this questionnaire",
        onchange: function (e) { rot.wave = Math.max(1, parseInt(e.target.value, 10) || 1); paint(); } });
      var name = el("input", { type: "text", class: "be-var", value: rot.name, "aria-label": "Name of the calculated value",
        onchange: function (e) { var v = e.target.value.trim(); if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(v)) { rot.name = v; st.arm_from = v; self.sync(); } paint(); } });
      box.appendChild(el("div", { class: "field-label", text: "Answer that every wave asks, given the same way each time" }));
      box.appendChild(qSel);
      box.appendChild(el("div", { class: "count-row" }, [
        el("label", { class: "hint", text: "This questionnaire is wave " }), wave,
        el("label", { class: "hint", text: " · calculated value " }), name,
        el("span", { class: "hint", text: " · " + arms.length + " groups: " + arms.join(", ") })]));
      if (arms.length < 2) { box.appendChild(el("p", { class: "pool-error", text: "A rotation needs at least two groups." })); return; }
      if (!rot.question) { box.appendChild(el("p", { class: "pool-error", text: "Choose the answer the rotation reads." })); return; }

      // the rotation table: who gets which group at each wave
      var waves = Math.max(arms.length, rot.wave);
      var meta = model.questions.filter(function (q) { return q.name === rot.question; })[0] || {};
      var shares = expectedShares(meta, rot.wave, arms);
      var head = el("tr", {}, [el("th", { text: "Group at wave 1" })]);
      for (var w = 2; w <= waves; w++) head.appendChild(el("th", { text: "Wave " + w }));
      head.appendChild(el("th", { text: "Share at wave " + rot.wave }));
      var body = el("tbody");
      // a participant whose answer gives index i at wave 1 has index i + (w - 1) at wave w
      arms.forEach(function (a, i) {
        var row = el("tr", {}, [el("td", { class: "cond-key", text: a })]);
        for (var w2 = 2; w2 <= waves; w2++) row.appendChild(el("td", { class: "cond-key", text: arms[(i + w2 - 1) % arms.length] }));
        var current = arms[(i + rot.wave - 1) % arms.length];
        row.appendChild(el("td", { class: "pct", text: shares ? current + ": " + shares[current] + "%" : "—" }));
        body.appendChild(row);
      });
      box.appendChild(el("div", { class: "be-grid" }, [el("table", {}, [el("thead", {}, [head]), body])]));
      box.appendChild(el("p", { class: "hint", text: shares
        ? (meta.min === 1 && meta.max === 31 ? "Shares weighted by how often each day of the month occurs in a year (the 31st exists in seven months)."
                                              : "Shares assume every value from " + meta.min + " to " + meta.max + " is equally likely.")
        : "No estimate: the answer has no declared minimum and maximum." }));

      var expr = buildExpression(rot.question, rot.wave, arms);
      var existing = model.calculated.filter(function (c) { return c.name === rot.name; })[0];
      var status, action;
      if (existing && existing.expression === expr && existing.saved) {
        status = "In the questionnaire as " + rot.name + "." + (self.justAdded === rot.name ? " Save the bank to use it." : "");
        action = null;
      } else {
        status = existing ? "Will replace the calculated value " + rot.name + " already in the questionnaire."
                          : "Will add to the questionnaire a calculated value " + rot.name + ", saved with the answers.";
        action = existing ? "Replace in questionnaire" : "Add to questionnaire";
      }
      box.appendChild(el("div", { class: "be-note" }, [status + " ", el("code", { text: expr })]));
      box.appendChild(el("p", { class: "hint", text: "For the next wave, duplicate the survey from its manage page and set the wave number here, in the copy." }));
      if (action) {
        var note = el("span", { class: "hint" });
        box.appendChild(el("div", { class: "count-row" }, [el("button", { type: "button", class: "btn-sm btn-secondary", text: action,
          onclick: function () { self.addRotation(rot, arms, false, note); } }), note]));
      }
    }
    paint();
  };

  Editor.prototype.addRotation = function (rot, arms, confirmed, note) {
    var self = this, o = this.opts;
    note.textContent = "Adding…";
    fetch("/admin/surveys/" + encodeURIComponent(o.slug) + "/randomization/rotation", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: rot.name, question: rot.question, wave: rot.wave, arms: arms, confirm: confirmed }),
    }).then(function (r) { return r.json().then(function (d) { return { status: r.status, d: d }; }); }).then(function (res) {
      if (res.status === 409 && res.d.error === "responses_held") {
        if (confirm("The survey holds " + res.d.held + " response(s): changing the questionnaire under them changes what the data means. Add the rotation anyway?"))
          return self.addRotation(rot, arms, true, note);
        note.textContent = "Not added.";
        return;
      }
      if (!res.d.ok) { note.textContent = (res.d.errors || [res.d.error || "refused"]).join("; "); return; }
      o.model.calculated = res.d.calculated;
      self.st.arm_from = rot.name;
      self.justAdded = rot.name;
      self.render(); self.sync();
      self.jsonNote.textContent = "Rotation " + (res.d.replaced ? "updated" : "added") + " in the questionnaire. Save the bank to use it.";
    }).catch(function () { note.textContent = "The server could not be reached; nothing was added."; });
  };

  // --- tab 4: check and save ------------------------------------------------------

  Editor.prototype.checkTab = function (p) {
    var self = this, f = this.findings;
    if (!f.errors.length && !f.warnings.length) p.appendChild(el("p", { class: "arms-ok be-finding", text: "No findings: this bank can be saved." }));
    f.errors.forEach(function (m) { p.appendChild(self.finding(m, "pool-error")); });
    f.warnings.forEach(function (m) { p.appendChild(self.finding(m, "bank-warning")); });
    var o = this.opts;
    p.appendChild(el("p", { class: "hint", text: "Saving resets the draws of this bank: " + o.nDraws + " draw(s), " + o.nResponses + " response(s) in the survey." +
      (o.nResponses ? " You will be asked to type the bank name." : "") }));
    var save = el("button", { type: "button", class: "btn-primary", text: "Save bank", disabled: f.errors.length ? "disabled" : null,
      onclick: function () { if (self.confirmSave()) self.form.submit(); } });
    p.appendChild(el("div", { class: "pool-actions" }, [save]));
  };

  Editor.prototype.finding = function (message, cls) {
    var self = this, tab = tabOf(message);
    return el("div", { class: cls }, [message + " ", el("button", { type: "button", class: "be-link", text: "→ " + tab,
      onclick: function () { self.tab = tab; self.render(); } })]);
  };

  Editor.prototype.confirmSave = function () {
    var o = this.opts;
    if (o.nResponses) {
      var typed = prompt("The survey holds " + o.nResponses + " response(s): saving restarts this bank's balance from zero. Type the bank name to confirm:");
      if (typed === null || typed.trim() !== this.st.name) return false;
      // the server checks it again: a save over responses carries the name typed back
      if (this.form.elements.confirm_name) this.form.elements.confirm_name.value = typed.trim();
      return true;
    }
    return confirm("Save this bank and reset its draws?");
  };

  // --- side panel -----------------------------------------------------------------

  Editor.prototype.renderSide = function () {
    var st = this.st, s = this.sideEl;
    s.textContent = "";
    s.appendChild(el("div", { class: "field-label", text: "Example draw" }));
    s.appendChild(el("p", { class: "hint", text: "From an empty history, not the next participant's." + (st.groups ? " One per group." : "") }));
    if (this.findings.errors.length) { s.appendChild(el("p", { class: "hint", text: "Shown again once the errors are fixed." })); return; }
    (this.findings.example || []).forEach(function (ex) {
      var counts = {};
      ex.draw.forEach(function (d) { counts[d.version] = (counts[d.version] || 0) + 1; });
      var chips = el("div", { class: "be-chips" }, ex.draw.map(function (d) {
        var k = st.labels.indexOf(d.version);
        return el("span", { class: "be-chip", title: d.page + " (" + d.version + ")", style: "border-color:" + COLOURS[(k < 0 ? 0 : k) % COLOURS.length], text: d.item });
      }));
      s.appendChild(el("div", { class: "be-draw" }, [
        el("b", { text: ex.arm === null || ex.arm === undefined ? "Any participant" : ex.arm }),
        el("span", { class: "hint", text: " · " + Object.keys(counts).map(function (lb) { return counts[lb] + " " + lb; }).join(" + ") }),
        chips]));
    });
  };

  root.BankEditor = { mount: function (card, opts) { return new Editor(card, opts); }, api: api };
})(this);
