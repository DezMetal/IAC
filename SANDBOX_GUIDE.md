# IAC Sandbox Guide ◈

The IAC Sandbox is a high-fidelity interactive environment for debugging, testing, and crafting IAC workflows live in the browser.

## HUD Navigation
- **TOOLS**: Select and execute single IAC operations.
- **PAYLOAD**: Live view of the current session state (updated every 3s).
- **CHAIN**: A list of all operations executed in this session.
- **LOGS**: Deep feedback from the Python runner and AI cores.

---

## Operations Reference

### `goto`
Navigates the browser to a specific URL.
- **url**: The full destination URL (e.g., `https://google.com`).

### `snap`
Captures a screenshot of the current page.
- **path**: Filename or absolute path (auto-adds `.png`).
- **payload_key**: (Optional) Store the image path in this payload key.
- **full_page**: "true" or "false" (default: "true").

### `analyze`
Uses an AI Vision or Text core to process the current page.
- **prompt**: Your instructions for the AI.
- **payload_key**: Key to store the AI result.
- **model**: (Optional) Override the default model.
- **host**: (Optional) Override the AI host.

### `extract`
Pulls data from the DOM using standard selectors.
- **selector**: CSS selector (e.g., `h1`, `.item-price`).
- **payload_key**: Key to store the result.
- **attr**: (Optional) Attribute to extract (e.g., `href`, `src`). If blank, extracts text.
- *Note: If multiple elements match, it automatically returns a list.*

### `modify`
Directly updates an element's state in the DOM.
- **selector**: CSS selector for the target element.
- **text**: New inner text for the element.
- **value**: New value (for inputs/textareas).
- **attr**: Attribute name to set.
- **val**: Value to set for the specified attribute.

### `eval`
Executes custom JavaScript logic in the page context.
- **code**: The JS code to run.
- **payload_key**: (Optional) Store the returned result in this payload key.
- **file**: (Optional) Path to a `.js` file to load before running `code`.

### `fs_write`
Writes data to a local file.
- **path**: Destination file path.
- **content**: Literal text content.
- **from_key**: (Optional) If set, ignores `content` and writes the value from this payload key.

### `save_state`
Persists current cookies and storage (Auth state).
- **path**: Path to save the `.json` session file.

---

## Chaining & Export
Every tool you execute in the Sandbox is recorded in the **CHAIN** tab. 
1. Craft your sequence of actions (e.g., `goto` -> `wait` -> `extract` -> `snap`).
2. Verify results in the **PAYLOAD** tab.
3. Switch to **CHAIN** and click **DOWNLOAD CHAIN**.
4. The resulting JSON file is a production-ready IAC Pipeline that can be run via:
   `python3 iac.py my_exported_workflow.json`
