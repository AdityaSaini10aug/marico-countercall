const el = id => document.getElementById(id);
const finished = new Set(["confirmed", "callback", "handoff", "declined", "wrong_contact", "opt_out", "insight"]);
const names = {
  identity: "Verify outlet",
  shopping: "In conversation",
  review: "Awaiting confirmation",
  confirmed: "Confirmed",
  callback: "Callback logged",
  handoff: "Sent to representative",
  declined: "Closed",
  wrong_contact: "Wrong contact",
  opt_out: "Do not call",
  insight: "Feedback logged"
};
const money = n => new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0
}).format(n);
const recognizer = window.SpeechRecognition || window.webkitSpeechRecognition;
let setup, call = null,
  busy = false,
  tab = "draft",
  recognition = null,
  audioSequence = 0,
  listening = false;

function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

function option(parent, value, label) {
  const opt = element("option", label);
  opt.value = value;
  parent.append(opt);
}
async function api(url, data) {
  const options = data ? {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(data)
  } : {};
  const res = await fetch(url, options);
  const body = await res.json();
  if (!res.ok) throw Error(body.error || "Request failed.");
  return body;
}

function selected() {
  return setup.retailers.find(r => r.id === el("retailer").value)
}

function active() {
  return call && !finished.has(call.phase)
}

function hint(text) {
  el("voice-state").textContent = text
}

function stopAudio() {
  audioSequence++;
  if (recognition) {
    recognition.onend = null;
    recognition.abort();
    recognition = null
  }
  listening = false;
  el("mic").textContent = "Use microphone";
  window.speechSynthesis?.cancel();
}

function controls() {
  el("start").disabled = busy || Boolean(active()) || selected()?.dnc;
  el("retailer").disabled = busy || Boolean(active());
  el("clock").disabled = el("retailer").disabled;
  for (const id of ["send", "utterance", "lang"]) el(id).disabled = busy || !active();
  el("mic").disabled = busy || !active() || !recognizer;
  el("auto").disabled = !recognizer;
  el("choices").querySelectorAll("button").forEach(b => b.disabled = busy);
}

function voice() {
  stopAudio();
  const seq = audioSequence;
  const follow = () => {
    if (seq === audioSequence && el("auto").checked && active() && !busy) listen()
  };
  if (!el("sound").checked || !window.speechSynthesis) {
    follow();
    return
  }
  const utterance = new SpeechSynthesisUtterance(call.turns.at(-1)[1]);
  utterance.lang = setup.languages[call.language] || "en-IN";
  const voices = speechSynthesis.getVoices(),
    prefix = utterance.lang.split("-")[0];
  const v = voices.find(v => v.lang.toLowerCase() === utterance.lang.toLowerCase()) || voices.find(v => v.lang.startsWith(prefix));
  if (v) utterance.voice = v;
  if (voices.length && !v) {
    hint("No " + call.language + " voice is installed. Read the transcript or select another language.");
    follow();
    return
  }
  utterance.onend = follow;
  utterance.onerror = e => {
    if (seq === audioSequence) hint("Audio unavailable (" + e.error + "). You can continue by typing.")
  };
  speechSynthesis.speak(utterance);
}

function listen() {
  if (!recognizer || busy || !active()) return;
  if (listening) {
    recognition.stop();
    return
  }
  stopAudio();
  const mic = new recognizer();
  recognition = mic;
  mic.lang = setup.languages[call.language] || "en-IN";
  mic.continuous = false;
  mic.interimResults = false;
  mic.onstart = () => {
    listening = true;
    el("mic").textContent = "Stop listening";
    hint("Listening for the retailer…")
  };
  mic.onresult = e => {
    const best = e.results[e.results.length - 1][0];
    hint("");
    // Some engines report zero when confidence is unavailable. Ask for review before sending.
    if (best.confidence === 0) {
      el("utterance").value = best.transcript;
      hint("Check the recognised text, then press Send.");
      return
    }
    sendTurn(best.transcript, best.confidence);
  };
  mic.onerror = e => hint("Microphone " + e.error + ". Check browser permissions or type your response.");
  mic.onend = () => {
    listening = false;
    recognition = null;
    el("mic").textContent = "Use microphone"
  };
  try {
    mic.start()
  } catch (e) {
    hint(e.message)
  }
}

function setOutlet() {
  const r = selected();
  el("clock").value = r.window.split("-")[0];
  el("outlet").textContent = r.name;
  el("place").textContent = [r.city, r.state, r.language, r.window + " IST"].join(" · ");
  el("notice").textContent = r.dnc ? "This outlet has opted out of calls. Choose another outlet." : "";
}

function addChoice(label, text) {
  const b = element("button", label);
  b.type = "button";
  b.onclick = () => sendTurn(text);
  el("choices").append(b)
}

function draw() {
  const r = selected();
  const log = el("transcript");
  log.replaceChildren();
  el("choices").replaceChildren();
  el("status").textContent = call ? names[call.phase] : "Ready";
  if (call) {
    el("lang").value = call.language;
    for (const [speaker, text] of call.turns) {
      const b = element("div", undefined, "bubble " + speaker);
      b.append(element("b", speaker === "agent" ? "Agent" : "Retailer"), document.createTextNode(text));
      log.append(b)
    }
    if (call.phase === "identity") {
      addChoice("Yes, speaking", "Yes, speaking");
      addChoice("Call after 5", "Please call me back after 5 PM");
      addChoice("Wrong number", "Wrong number")
    }
    if (call.phase === "shopping") {
      addChoice("4 × Parachute 500 ml", "Send 4 pieces of Parachute Coconut Oil 500 ml");
      addChoice("Review order", "Review order");
      addChoice("Human callback", "I'd like a sales representative")
    }
    if (call.phase === "review") {
      addChoice("Confirm order", "Confirm order");
      addChoice("Change to 6 pieces", "Change it to 6 pieces");
      addChoice("Read back", "Review order")
    }
    if (call.model_used) el("status").title = "The language model assisted with this turn.";
    else el("status").removeAttribute("title");
    requestAnimationFrame(() => log.scrollTop = log.scrollHeight);
  } else {
    log.append(element("p", "Start a simulated call to hear the agent greet " + r.name + " in " + r.language + ".", "empty"));
    el("lang").value = r.language;
  }
  drawSide();
  controls();
}

function drawSide() {
  const side = el("side");
  side.replaceChildren();
  el("draft-tab").classList.toggle("selected", tab === "draft");
  el("catalog-tab").classList.toggle("selected", tab === "catalog");
  if (!call) {
    side.append(element("p", tab === "draft" ? "Items appear here as the retailer builds an order." : "Start a call to see this distributor’s stock.", "empty"));
    return
  }
  if (tab === "catalog") {
    const search = element("input", undefined, "catsearch");
    search.type = "search";
    search.placeholder = "Find a product or SKU";
    search.setAttribute("aria-label", "Find a product or SKU");
    const list = element("div");
    side.append(search, list);
    const paint = () => {
      list.replaceChildren();
      for (const p of call.products.filter(p => (p.name + " " + p.pack + " " + p.sku).toLowerCase().includes(search.value.toLowerCase()))) {
        const row = element("div", undefined, "row");
        const title = element("div");
        title.append(element("b", p.name + " " + p.pack), element("small", p.sku + " · " + p.stock + " units available"));
        row.append(title, element("strong", money(p.price)));
        list.append(row)
      }
    };
    search.oninput = paint;
    paint();
    return;
  }
  const cart = Object.entries(call.cart);
  let total = 0;
  side.append(element("h3", call.order ? "Confirmed order" : "Current order", "side-title"));
  if (!cart.length) side.append(element("p", "No items yet.", "note"));
  for (const [sku, q] of cart) {
    const p = call.products.find(p => p.sku === sku);
    total += p.price * q;
    const row = element("div", undefined, "row"),
      item = element("div");
    item.append(element("b", p.name + " " + p.pack), element("small", sku + " · " + q + " × " + money(p.price)));
    row.append(item, element("strong", money(p.price * q)));
    side.append(row);
  }
  const sum = element("div", undefined, "total");
  sum.append(element("span", "Distributor billing total"), element("strong", money(total)));
  side.append(sum);
  if (call.order) {
    side.append(element("p", call.order.order_id, "note"));
    const b = element("button", "Download order JSON", "wide");
    b.onclick = () => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(call.order, null, 2)], {
        type: "application/json"
      }));
      const a = element("a");
      a.href = url;
      a.download = call.order.order_id + ".json";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    };
    side.append(b);
  } else if (cart.length && finished.has(call.phase)) {
    side.append(element("p", "Call closed with an unplaced draft.", "note"));
  }
  for (const e of call.events) {
    side.append(element("p", e.kind.replaceAll("_", " ") + " · " + e.detail, "note"))
  }
}
async function sendTurn(text, confidence = null) {
  if (busy || !active() || !text.trim()) return;
  stopAudio();
  busy = true;
  controls();
  el("notice").textContent = "";
  hint("Processing…");
  try {
    call = await api("/api/turn", {
      id: call.id,
      version: call.version,
      request: crypto.randomUUID(),
      text,
      confidence
    });
    if (call.phase === "opt_out") selected().dnc = true;
    el("utterance").value = "";
    hint("");
    busy = false;
    draw();
    voice();
  } catch (e) {
    el("notice").textContent = e.message;
    hint("");
    busy = false;
    controls()
  }
}
el("start").onclick = async () => {
  stopAudio();
  busy = true;
  controls();
  el("notice").textContent = "";
  hint("Starting simulated call…");
  try {
    call = await api("/api/start", {
      retailer: selected().id,
      clock: el("clock").value
    });
    sessionStorage.setItem("countercall", call.id);
    hint("");
    busy = false;
    draw();
    voice()
  } catch (e) {
    busy = false;
    el("notice").textContent = e.message;
    hint("");
    controls()
  }
};
el("retailer").onchange = () => {
  stopAudio();
  call = null;
  sessionStorage.removeItem("countercall");
  setOutlet();
  hint("");
  draw()
};
el("form").onsubmit = e => {
  e.preventDefault();
  sendTurn(el("utterance").value)
};
el("mic").onclick = listen;
el("auto").onchange = () => {
  if (el("auto").checked && active()) listen();
  else stopAudio()
};
el("sound").onchange = () => {
  if (!el("sound").checked) stopAudio()
};
el("lang").onchange = () => sendTurn("Switch to " + el("lang").value);
el("draft-tab").onclick = () => {
  tab = "draft";
  drawSide()
};
el("catalog-tab").onclick = () => {
  tab = "catalog";
  drawSide()
};
(async () => {
  setup = await api("/api/setup");
  el("mode").textContent = setup.mode;
  for (const r of setup.retailers) option(el("retailer"), r.id, r.name + " · " + r.id + (r.dnc ? " · Do not call" : ""));
  for (const l of Object.keys(setup.languages)) option(el("lang"), l, l);
  const saved = sessionStorage.getItem("countercall");
  if (saved) {
    try {
      call = await api("/api/calls/" + saved)
    } catch {
      sessionStorage.removeItem("countercall")
    }
  }
  el("retailer").value = call?.retailer || "R003";
  setOutlet();
  draw();
  if (!recognizer) hint("Speech recognition is unavailable in this browser. Use Chrome or type a response.");
})().catch(e => el("notice").textContent = e.message);
