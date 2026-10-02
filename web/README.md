# Sales V2 mobile workspace (synthetic demo)

This folder is a mobile interface demo. It has no connection to a real CRM, WeChat account, Firebase service, or push notification provider. Do not enter real student information.

## Run

The in-browser `DemoAdapter` is the default. From this directory, run `python3 -m http.server 8765 --bind 127.0.0.1` and open `http://127.0.0.1:8765/`. Data lives only in the current page and resets on reload.

For the Python V2 offline adapter, run `python3 web/bridge_server.py 8765` from the repository root and open `http://127.0.0.1:8765/?bridge=offline`. The Settings page switches between modes. The bridge listens only on `127.0.0.1`, serves same-origin static files and JSON routes, and rejects nonlocal Host and Origin headers. By default, data is kept in Python process memory and resets on restart. To persist synthetic events locally, pass `--sqlite-file /absolute/path/synthetic-demo.sqlite3`; reuse that path to reopen the data. This file is not a real customer database and has no production authentication. Do not expose this service to another device, a LAN, or a public domain.

The bridge accepts only inputs marked `demo_only` and rejects contact and identity fields. The browser submits student messages and human actions. The default server decision is a fixed synthetic script processed through `OfflineWorkspaceApi`, the V2 pipeline, and its Gate. To explicitly try a local model, install it in Ollama and run `python3 web/bridge_server.py 8765 --ollama-model qwen3.5:9b`; `--ollama-timeout 240` changes the timeout in seconds. This mode calls only `http://127.0.0.1:11434/api/chat` and uses the same pipeline, Gate, and human review flow. A failed model call or validation is not silently replaced with scripted success. A run may take several minutes.

`APPROVAL_REQUIRED` shows an internal custom proposal for synthetic human approval. `STOPPED` shows the stop reason. `HANDOFF` or Gate failure shows an error and refreshes recorded workspace events. The browser cannot upload arbitrary model output. Source event IDs and draft review status are visible. Approval alone does not send a message. A human may record an external send only after checking the confirmation box and entering the exact text actually sent; the bridge cannot verify that external action.

## Test and scope

From the repository root, run `node --test web/tests/*.test.js` and `python3 -m unittest web.tests.test_bridge_server -v`. These cover synthetic flows and safety boundaries. `api.js` defines `SalesWorkspaceApi`; `DemoAdapter` operates entirely in the browser, while `bridge_api.js` maps local Python offline results. The default browser and scripted modes do not call a model. Their fixed decisions are not real Decision Agent output. Explicit local model output is still synthetic and has not passed business evaluation. Real integration requires authentication, student access checks, protected production event storage, real approvals, and independent acceptance testing.

The frontend cannot send a student message. “Record external send” only stores the actual text and time after a salesperson has sent it elsewhere. An unapproved custom offer never creates a reviewable customer draft. Mobile push notifications are future work; this demo has an in-app task list only.
