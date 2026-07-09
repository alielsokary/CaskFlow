#!/usr/bin/env python3
"""CaskHub category browser and recategorizer."""
# Run: python3 category_browser.py
# Opens at: http://localhost:8899
import json
import http.server
import webbrowser
import os
import threading

PORT = 8899
BASE = os.path.dirname(os.path.abspath(__file__))
CATEGORIES_PATH = os.path.join(BASE, "CaskHub", "Resources", "categories.json")

# Load data once at startup
with open(CATEGORIES_PATH, encoding="utf-8") as f:
    cat_data = json.load(f)

# Try to load filtered_casks.json from various locations
casks_path = None
for p in [
    os.path.join(BASE, "filtered_casks.json"),
    os.path.join(BASE, "CaskHub", "Resources", "filtered_casks.json"),
]:
    if os.path.exists(p):
        casks_path = p
        break

cask_list = []
if casks_path:
    with open(casks_path, encoding="utf-8") as f:
        cask_list = json.load(f)

cask_map = {c["token"]: c for c in cask_list}

# Build embedded JSON
compact_casks = []
for c in cask_list:
    compact_casks.append({
        "t": c["token"],
        "d": (c.get("desc") or "")[:150],
        "h": c.get("homepage") or ""
    })

embedded_data = json.dumps({
    "categories": cat_data["categories"],
    "tc": cat_data["tokenToCategory"],
    "casks": compact_casks
}, ensure_ascii=False)

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CaskHub Category Browser</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'SF Pro', system-ui, sans-serif; background: #1a1a2e; color: #e0e0e0; }

.layout { display: flex; height: 100vh; }

/* Sidebar */
.sidebar { width: 260px; background: #16213e; border-right: 1px solid #2a2a4a; overflow-y: auto; flex-shrink: 0; }
.sidebar h2 { padding: 16px; font-size: 15px; color: #8be9fd; border-bottom: 1px solid #2a2a4a; }
.cat-btn { display: flex; justify-content: space-between; align-items: center; width: 100%; padding: 10px 16px; border: none;
  background: none; color: #ccc; font-size: 13px; cursor: pointer; text-align: left; border-bottom: 1px solid #1a1a2e; }
.cat-btn:hover { background: #1a2744; }
.cat-btn.active { background: #0a3d62; color: #fff; font-weight: 600; }
.cat-btn .count { font-size: 11px; color: #888; background: #2a2a4a; padding: 2px 8px; border-radius: 10px; }

/* Main content */
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.toolbar { padding: 12px 20px; background: #16213e; border-bottom: 1px solid #2a2a4a; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.toolbar input { padding: 6px 12px; border-radius: 6px; border: 1px solid #2a2a4a; background: #1a1a2e; color: #e0e0e0; font-size: 13px; width: 250px; }
.toolbar .info { font-size: 12px; color: #888; margin-left: auto; }
.export-btn { padding: 6px 16px; border-radius: 6px; border: 1px solid #50fa7b; background: transparent;
  color: #50fa7b; font-size: 12px; cursor: pointer; font-weight: 600; }
.export-btn:hover { background: #50fa7b22; }
.changes-badge { background: #ff5555; color: #fff; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }

/* App list */
.app-list { flex: 1; overflow-y: auto; padding: 8px 20px; }
.app-row { display: flex; align-items: center; padding: 10px 12px; border-radius: 8px; margin-bottom: 4px; gap: 12px; transition: background 0.15s; }
.app-row:hover { background: #1e2d4a; }
.app-row.changed { background: #2a1a3e; border-left: 3px solid #bd93f9; }
.app-token { font-weight: 600; font-size: 13px; color: #f8f8f2; min-width: 200px; max-width: 200px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.app-desc { flex: 1; font-size: 12px; color: #999; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.app-hp { flex-shrink: 0; }
.app-hp a { font-size: 11px; color: #8be9fd; text-decoration: none; padding: 4px 8px; border: 1px solid #8be9fd33; border-radius: 4px; }
.app-hp a:hover { background: #8be9fd22; }
.app-cat-select { flex-shrink: 0; }
.app-cat-select select { padding: 4px 8px; border-radius: 4px; border: 1px solid #2a2a4a;
  background: #1a1a2e; color: #e0e0e0; font-size: 11px; cursor: pointer; }
.app-cat-select select.changed { border-color: #bd93f9; color: #bd93f9; }
.app-secondary { flex-shrink: 0; font-size: 10px; color: #666; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* Export modal */
.modal-overlay { display: none; position: fixed; inset: 0; background: #000a; z-index: 100; justify-content: center; align-items: center; }
.modal-overlay.show { display: flex; }
.modal { background: #16213e; border: 1px solid #2a2a4a; border-radius: 12px; padding: 24px; width: 700px; max-height: 80vh; overflow-y: auto; }
.modal h3 { color: #f8f8f2; margin-bottom: 12px; }
.modal pre { background: #1a1a2e; padding: 16px; border-radius: 8px; font-size: 12px; overflow-x: auto;
  white-space: pre-wrap; color: #50fa7b; max-height: 50vh; overflow-y: auto; }
.modal .actions { margin-top: 16px; display: flex; gap: 8px; }
.modal button { padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; font-size: 13px; }
.modal .copy-btn { background: #50fa7b; color: #1a1a2e; font-weight: 600; }
.modal .apply-btn { background: #bd93f9; color: #1a1a2e; font-weight: 600; }
.modal .close-btn { background: #44475a; color: #f8f8f2; }
.status-msg { position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; border-radius: 8px; background: #50fa7b;
  color: #1a1a2e; font-weight: 600; font-size: 13px; display: none; z-index: 200; }
</style>
</head>
<body>
<div class="layout">
  <div class="sidebar">
    <h2>Categories</h2>
    <div id="catList"></div>
  </div>
  <div class="main">
    <div class="toolbar">
      <input type="text" id="search" placeholder="Filter apps..." />
      <span id="changesInfo"></span>
      <span class="info" id="countInfo"></span>
      <button class="export-btn" id="exportBtn" onclick="showExport()">Export Changes</button>
    </div>
    <div class="app-list" id="appList"></div>
  </div>
</div>

<div class="modal-overlay" id="modal">
  <div class="modal">
    <h3>Category Changes</h3>
    <pre id="exportData"></pre>
    <div class="actions">
      <button class="copy-btn" onclick="copyExport()">Copy to Clipboard</button>
      <button class="apply-btn" onclick="applyChanges()">Apply & Save</button>
      <button class="close-btn" onclick="closeModal()">Close</button>
    </div>
  </div>
</div>

<div class="status-msg" id="statusMsg"></div>

<script>
const DATA = __DATA_PLACEHOLDER__;

const categories = DATA.categories;
const tc = DATA.tc;
const casks = DATA.casks;

// Build lookup
const caskMap = {};
casks.forEach(c => { caskMap[c.t] = c; });

// Track changes: token -> newPrimary
const changes = {};
let currentCat = null;

// Build category counts
function getCatCounts() {
  const counts = {};
  for (const [token, mapping] of Object.entries(tc)) {
    const p = typeof mapping === 'string' ? mapping : mapping.primary;
    counts[p] = (counts[p] || 0) + 1;
  }
  return counts;
}

function renderSidebar() {
  const counts = getCatCounts();
  const el = document.getElementById('catList');
  const sorted = Object.entries(categories).sort((a, b) => {
    if (a[0] === 'other') return 1;
    if (b[0] === 'other') return -1;
    return a[1].displayName.localeCompare(b[1].displayName);
  });

  el.innerHTML = sorted.map(([id, def]) => {
    const count = counts[id] || 0;
    const active = id === currentCat ? 'active' : '';
    return `<button class="cat-btn ${active}" onclick="selectCat('${id}')">${def.displayName}<span class="count">${count}</span></button>`;
  }).join('');
}

function selectCat(catId) {
  currentCat = catId;
  renderSidebar();
  renderApps();
}

function getTokensInCat(catId) {
  const tokens = [];
  for (const [token, mapping] of Object.entries(tc)) {
    const p = changes[token] || (typeof mapping === 'string' ? mapping : mapping.primary);
    if (p === catId) tokens.push(token);
  }
  return tokens.sort();
}

function renderApps() {
  const el = document.getElementById('appList');
  if (!currentCat) {
    el.innerHTML = '<p style="padding:40px;color:#666;">Select a category from the sidebar.</p>';
    return;
  }

  const filter = document.getElementById('search').value.toLowerCase();
  let tokens = getTokensInCat(currentCat);
  if (filter) {
    tokens = tokens.filter(t => {
      const c = caskMap[t];
      return t.includes(filter) || (c && c.d.toLowerCase().includes(filter));
    });
  }

  document.getElementById('countInfo').textContent = `${tokens.length} apps`;

  const catOptions = Object.entries(categories)
    .sort((a, b) => a[1].displayName.localeCompare(b[1].displayName))
    .map(([id, def]) => `<option value="${id}">${def.displayName}</option>`)
    .join('');

  el.innerHTML = tokens.map(token => {
    const c = caskMap[token] || { t: token, d: '', h: '' };
    const mapping = tc[token] || {};
    const origPrimary = typeof mapping === 'string' ? mapping : (mapping.primary || 'other');
    const curPrimary = changes[token] || origPrimary;
    const secondary = (typeof mapping === 'object' && mapping.secondary) ? mapping.secondary.join(', ') : '';
    const isChanged = !!changes[token];
    const hp = c.h;
    const hpDomain = hp ? new URL(hp).hostname.replace('www.', '') : '';

    return `<div class="app-row ${isChanged ? 'changed' : ''}">
      <span class="app-token" title="${token}">${token}</span>
      <span class="app-desc" title="${c.d}">${c.d}</span>
      ${secondary ? `<span class="app-secondary" title="${secondary}">${secondary}</span>` : ''}
      ${hp ? `<span class="app-hp"><a href="${hp}" target="_blank">${hpDomain}</a></span>` : ''}
      <span class="app-cat-select">
        <select class="${isChanged ? 'changed' : ''}" onchange="recategorize('${token}', this.value, '${origPrimary}')">
          ${catOptions.replace(`value="${curPrimary}"`, `value="${curPrimary}" selected`)}
        </select>
      </span>
    </div>`;
  }).join('');

  updateChangesInfo();
}

function recategorize(token, newCat, origCat) {
  if (newCat === origCat) {
    delete changes[token];
  } else {
    changes[token] = newCat;
  }
  renderApps();
  renderSidebar();
}

function updateChangesInfo() {
  const n = Object.keys(changes).length;
  const el = document.getElementById('changesInfo');
  el.innerHTML = n > 0 ? `<span class="changes-badge">${n} change${n > 1 ? 's' : ''}</span>` : '';
}

function showExport() {
  const n = Object.keys(changes).length;
  if (n === 0) {
    showStatus('No changes to export');
    return;
  }

  // Format as list of corrections
  const corrections = Object.entries(changes).map(([token, newCat]) => {
    const mapping = tc[token] || {};
    const origPrimary = typeof mapping === 'string' ? mapping : (mapping.primary || 'other');
    return {
      token,
      was: origPrimary,
      wasName: categories[origPrimary]?.displayName || origPrimary,
      now: newCat,
      nowName: categories[newCat]?.displayName || newCat
    };
  }).sort((a, b) => a.token.localeCompare(b.token));

  document.getElementById('exportData').textContent = JSON.stringify(corrections, null, 2);
  document.getElementById('modal').classList.add('show');
}

function closeModal() {
  document.getElementById('modal').classList.remove('show');
}

function copyExport() {
  const text = document.getElementById('exportData').textContent;
  navigator.clipboard.writeText(text).then(() => showStatus('Copied to clipboard!'));
}

async function applyChanges() {
  try {
    const resp = await fetch('/api/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes)
    });
    const result = await resp.json();
    if (result.ok) {
      // Clear changes since they're saved
      Object.keys(changes).forEach(k => {
        const mapping = tc[k];
        if (typeof mapping === 'object') {
          mapping.primary = changes[k];
        } else {
          tc[k] = { primary: changes[k], secondary: [] };
        }
      });
      for (const k of Object.keys(changes)) delete changes[k];
      closeModal();
      renderSidebar();
      renderApps();
      showStatus(`Saved ${result.count} changes to categories.json!`);
    } else {
      showStatus('Error: ' + result.error);
    }
  } catch (e) {
    showStatus('Error: ' + e.message);
  }
}

function showStatus(msg) {
  const el = document.getElementById('statusMsg');
  el.textContent = msg;
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 3000);
}

// Search debounce
let searchTimer;
document.getElementById('search').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(renderApps, 200);
});

// Init
renderSidebar();
renderApps();
</script>
</body>
</html>""".replace("__DATA_PLACEHOLDER__", embedded_data)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/apply":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                changes = json.loads(body)
                # Apply changes to categories.json
                for token, new_primary in changes.items():
                    if token in cat_data["tokenToCategory"]:
                        mapping = cat_data["tokenToCategory"][token]
                        if isinstance(mapping, str):
                            cat_data["tokenToCategory"][token] = {"primary": new_primary, "secondary": []}
                        else:
                            mapping["primary"] = new_primary

                with open(CATEGORIES_PATH, "w", encoding="utf-8") as f:
                    json.dump(cat_data, f, indent=2, ensure_ascii=False)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "count": len(changes)}).encode())
                print(f"  Applied {len(changes)} changes to categories.json")
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        pass  # Suppress request logs


if __name__ == "__main__":
    print("CaskHub Category Browser")
    print(f"  Categories: {len(cat_data['categories'])}")
    print(f"  Casks: {len(cat_data['tokenToCategory'])}")
    print(f"  Loaded cask details: {len(cask_list)}")
    print(f"\n  Opening http://localhost:{PORT} ...")
    print("  Press Ctrl+C to stop\n")

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Timer(1, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()
