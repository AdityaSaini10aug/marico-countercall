# Countercall

Countercall is a simulated outbound sales agent for the Marico × IIT Madras General Trade challenge. It uses the supplied synthetic retailer, preference, purchase, catalogue and distributor stock records. A retailer can talk or type, correct a basket, hear the complete order and explicitly confirm it. The app exports distributor-ready JSON.

## Run the demo

On macOS, double-click **Start Demo.command** beside this guide. It opens the demo on an available local port. Alternatively, from the project folder run

```sh
cd prototype
python3 server.py --open
```

Use Python 3.10 or newer and open **http://127.0.0.1:8765** in Chrome. The guided English/Hindi flow uses only the Python standard library. The browser needs a compatible speech recogniser, an installed voice and microphone permission for two-way audio. Some browsers send audio to their own speech service. Typed input and the transcript work when speech is unavailable. **Hands-free** alternates listening and playback. A low-confidence result prompts for repetition. A browser that supplies no confidence lets the retailer check the transcription first.

The demo writes calls, opt-outs, callback requests, and confirmed orders to a local SQLite database in `.local/`. Refreshing the page restores the current call in the tab. To run another independent demonstration with the original stock snapshot, use `python3 server.py --state-dir /tmp/countercall-new-demo --port 8766 --open` with a fresh directory.

## A two-minute walkthrough

1. Use **Friends Provisions (R003)** in Delhi. Its preferred window is 15:00–17:00 IST. The time selector simulates the retailer’s local time. Start the call, hear the Hindi greeting and answer “हाँ”. The agent asks about Saffola Oats using this outlet’s own purchase history.
2. Say “Switch to English.” Then say “Send four pieces of Parachute Coconut Oil 500 ml.” The agent offers one relevant cross-sell. You can decline it or name a different item.
3. Say “Send ten litres of Saffola Gold.” The workbook defines this volume request as ten 1-litre packs. Ask for the order summary if needed. The draft totals ₹2,384.
4. Say “Change Saffola Gold 1 litre to six pieces.” The revised total is **₹1,712**. Say “Confirm order” after the complete read-back and download the order JSON. Four Parachute packs at ₹176 plus six Saffola packs at ₹168 reconcile to the total.
5. For an exception demo, start a fresh call. Try “I want two boxes of Parachute Coconut Oil 500 ml”, “Send two Livon Serum 100 ml”, or “My last delivery had the wrong quantity”. D001 has zero Livon 100 ml in the supplied snapshot. R001 is on the do-not-call list.
6. For an unscripted model turn in English, try “Could you ring me later this afternoon when the shop is quiet?” The on-device model can paraphrase this request so the agent logs a callback with the retailer’s original words.

## Language models

- **Apple on-device model** is selected automatically on a compatible Mac with Swift, Apple Intelligence and an available system model. The server builds the small Foundation Models bridge locally. It translates or paraphrases unrecognised speech into a proposed intent. The unmodified retailer utterance is required to confirm a sale. One real English model-assisted callback was verified on the development Mac.
- **Guided conversation** supports English and Hindi without a model. It recognises everyday numbers, dozens, litres, exact SKUs, simple pack descriptions and order edits. Hindi and English have authored replies. Language support beyond these two requires successful model translation and a suitable browser voice. An unavailable translation records a language handoff.
- **OpenAI** is an optional server-side provider. If you already have API credentials, set `OPENAI_API_KEY` in your shell, set `MARICO_MODEL=openai`, and run the server. Optionally set `OPENAI_MODEL` (default `gpt-4o-mini`). In this mode, the server sends only the text for model interpretation or translation to OpenAI. API usage can incur charges. The connector is included but was not exercised against a paid API in this build. Set `MARICO_MODEL=off` for the fully guided flow.
- The case was developed with Codex. The form mentions a licensed LLM platform. The prototype cannot verify an applicant’s subscription or establish reimbursement eligibility.

## How an order is captured

The app joins the selected retailer to its distributor before reading inventory. A simulated call can start inside its preferred calling window if the outlet has not opted out. The suggested reorder ranks historical SKUs by days since the last purchase divided by the usual purchase interval, with a 0.25 preference boost for a favoured brand. The suggested quantity averages the last order and the expected usage over a purchase interval, then rounds and bounds it by available stock and the minimum order quantity. Demand and remaining shelf stock are confirmed in conversation.

A separate shortlist uses the cross-sell field, prior Saffola purchases and live demo stock. The agent makes at most one additional offer per call and adds it only with assent and an exact quantity. A price question uses catalogue billing price and MRP. An unknown box conversion or ambiguous SKU prompts a clarification. Requests in litres use the workbook’s special rule for Saffola Gold, while an explicitly named 5-litre pack is treated as its own SKU.

A read-back freezes the current order quantities. Changes prompt a new read-back. On explicit confirmation, the server rechecks stock, writes the order, decrements distributor inventory and saves the call in a single SQLite transaction. A per-call version and turn token handle concurrent and repeated requests. Unknown queries, wrong contacts, opt-outs, complaints and callbacks have distinct outcomes. A callback records the requested wording for a representative to schedule. The prototype simulates dialling and exports JSON for a future OMS integration.

## Data and verification

[Original challenge folder](https://drive.google.com/drive/folders/1l37BNSUCdhG8peE-kcwzKsqC1wTeUsHb) · [Challenge brief](https://drive.google.com/file/d/1uGeVFnViRUoW726kxz2KZp8ReKb7ECu3/view) · [Supplied workbook](https://docs.google.com/spreadsheets/d/1sjdmtoFWXmWfjMpynA-c59Li6I6X54nv/edit)

`prototype/data/case.json` contains the runtime fields from the supplied workbook. There are 100 fictional retailers, 100 preference records, 20 SKUs, 1,000 purchase records, 200 distributor/SKU inventory rows and 19 populated call scenarios (CS01–CS19). The snapshot is dated 20 November 2026. Some counts printed in the workbook README differ from its populated sheets. A real programme needs consent records, refreshed inventory, carrier integration, native-speaker evaluations and an authenticated OMS endpoint.

Run the acceptance tests from the prototype directory.

```sh
python3 -m unittest -v
```

`agent.py` owns the call flow and order state. `catalog.py` owns workbook lookup, matching and recommendations. `server.py` serves the browser and transaction boundary. `language.py` selects a model and controls its input. `OnDevice.swift` calls Apple's framework. `web/` contains the browser voice interface and order desk.

[Apple Foundation Models](https://developer.apple.com/documentation/foundationmodels) · [OpenAI Chat Completions API](https://developers.openai.com/api/reference/resources/chat) · [Browser speech recognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition) · [Browser speech synthesis](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis)
