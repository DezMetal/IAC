// IAC Node GUI Logic

let registry = [];
let nodes = []; // Canvas nodes
let connections = []; // Links between nodes
let draggedItem = null; // Node from sidebar
let activeNode = null; // Moving node
let connectingPort = null; // Port we are dragging a link from
let offsetX = 0, offsetY = 0;
let nodeIdCounter = 0;
let editingNode = null;

// DOM Elements
const canvas = document.getElementById('node-canvas');
const svgLayer = document.getElementById('connection-layer');
const categoriesContainer = document.getElementById('op-categories');
const consoleOutput = document.getElementById('console-output');
const modal = document.getElementById('node-modal');
const modalParams = document.getElementById('modal-params');

// Helper to check parameter functions
function getInputParamName(opData) {
    const op = opData.op;
    const props = opData.parameters?.properties || {};
    if (op === 'web.goto') return 'url';
    if (op === 'web.type') return 'text';
    if (op === 'core.push') return 'data';
    if (op === 'core.merge') return 'data';
    if (op === 'ai.plan') return 'inject_keys';
    if (op === 'sys.fs_write') return 'from_key';
    if (props['payload_key']) return 'payload_key';
    return null;
}

function getOutputParamName(opData) {
    const op = opData.op;
    const props = opData.parameters?.properties || {};
    if (op === 'ai.plan') return 'output_key';
    if (props['payload_key']) return 'payload_key';
    return null;
}

// Initialization
document.addEventListener('DOMContentLoaded', async () => {
    setupTabs();
    await fetchRegistry();
    setupCanvas();
    setupButtons();
    await loadPipelinesList();
    restoreWorkspaceFromLocalStorage();
    setupAutoSaveListeners();
});

function logTerminal(msg, type='info') {
    const line = document.createElement('div');
    line.className = `log-line ${type}`;
    if (typeof msg === 'object') msg = JSON.stringify(msg, null, 2);
    line.textContent = `> ${msg}`;
    consoleOutput.appendChild(line);
    consoleOutput.scrollTop = consoleOutput.scrollHeight;
}

// Tabs
function setupTabs() {
    const tabs = document.querySelectorAll('.tab-btn');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            document.getElementById(tab.dataset.target).classList.add('active');
        });
    });
}

// Fetch Registry
async function fetchRegistry() {
    try {
        const res = await fetch('/api/registry');
        const data = await res.json();
        if (data.status === 'success') {
            registry = data.operations;
            renderSidebar(registry);
            logTerminal(`Loaded ${registry.length} operations from registry.`, 'sys');
        }
    } catch (e) {
        logTerminal(`Failed to load registry: ${e}`, 'error');
    }
}

// Render Sidebar
function renderSidebar(ops) {
    const domains = {};
    ops.forEach(item => {
        const domain = item.domain || 'core';
        if (!domains[domain]) domains[domain] = [];
        domains[domain].push(item);
    });

    categoriesContainer.innerHTML = '';
    for (const [domain, items] of Object.entries(domains)) {
        const catDiv = document.createElement('div');
        catDiv.className = 'op-category';
        
        const title = document.createElement('div');
        title.className = 'op-category-title';
        title.textContent = domain;
        catDiv.appendChild(title);
        
        items.forEach(item => {
            const opDiv = document.createElement('div');
            opDiv.className = 'op-item';
            opDiv.draggable = true;
            opDiv.dataset.op = item.op;
            
            let icon = 'fa-cube';
            if (domain === 'web') icon = 'fa-globe';
            if (domain === 'ai') icon = 'fa-brain';
            if (domain === 'sys') icon = 'fa-terminal';
            if (domain === 'desktop') icon = 'fa-desktop';
            if (domain === 'vision') icon = 'fa-eye';
            if (domain === 'filesystem') icon = 'fa-folder';
            if (domain === 'aether') icon = 'fa-bolt';
            if (domain === 'datastore') icon = 'fa-database';
            if (domain === 'prism') icon = 'fa-cubes';
            if (domain === 'dnet') icon = 'fa-cloud';
            if (domain === 'core') icon = 'fa-microchip';
            if (domain === 'flow') icon = 'fa-code-branch';
            
            opDiv.innerHTML = `<i class="fa-solid ${icon}"></i> <span>${item.name || item.op}</span>`;
            
            opDiv.addEventListener('dragstart', (e) => {
                draggedItem = item;
            });
            catDiv.appendChild(opDiv);
        });
        
        categoriesContainer.appendChild(catDiv);
    }
}

// Workspace Local Storage Sync
function saveWorkspaceToLocalStorage() {
    const layout = {
        nodes: nodes.map(n => ({
            id: n.id,
            op: n.opData.op,
            x: parseInt(n.el.style.left),
            y: parseInt(n.el.style.top),
            args: n.args
        })),
        connections: connections.map(c => ({
            from: c.from.id,
            fromPort: c.fromPort,
            to: c.to.id,
            toPort: c.toPort
        })),
        config: {
            headless: document.getElementById('cfg-headless').checked,
            allow_eval: document.getElementById('cfg-allow-eval').checked,
            ai_provider: document.getElementById('cfg-ai-provider').value,
            ai_host: document.getElementById('cfg-ai-host').value,
            ai_text_model: document.getElementById('cfg-ai-text-model').value,
            ai_vision_model: document.getElementById('cfg-ai-vision-model').value,
            ai_api_key: document.getElementById('cfg-ai-api-key').value
        },
        source: document.getElementById('cfg-source').value,
        target: document.getElementById('cfg-target').value,
        payload: document.getElementById('payload-editor').value,
        selectedPipeline: document.getElementById('pipeline-selector').value
    };
    localStorage.setItem('iac_workspace_state', JSON.stringify(layout));
}

function restoreWorkspaceFromLocalStorage() {
    const stored = localStorage.getItem('iac_workspace_state');
    if (!stored) return;
    try {
        const data = JSON.parse(stored);
        if (!data) return;
        
        // Restore configs
        if (data.config) {
            document.getElementById('cfg-headless').checked = !!data.config.headless;
            document.getElementById('cfg-allow-eval').checked = !!data.config.allow_eval;
            if (data.config.ai_provider) document.getElementById('cfg-ai-provider').value = data.config.ai_provider;
            if (data.config.ai_host) document.getElementById('cfg-ai-host').value = data.config.ai_host;
            if (data.config.ai_text_model) document.getElementById('cfg-ai-text-model').value = data.config.ai_text_model;
            else if (data.config.ai_model) document.getElementById('cfg-ai-text-model').value = data.config.ai_model;
            if (data.config.ai_vision_model) document.getElementById('cfg-ai-vision-model').value = data.config.ai_vision_model;
            if (data.config.ai_api_key) document.getElementById('cfg-ai-api-key').value = data.config.ai_api_key;
        }
        if (data.source) document.getElementById('cfg-source').value = data.source;
        if (data.target) document.getElementById('cfg-target').value = data.target;
        if (data.payload) document.getElementById('payload-editor').value = data.payload;
        if (data.selectedPipeline) document.getElementById('pipeline-selector').value = data.selectedPipeline;
        
        // Clear current canvas
        nodes.forEach(n => n.el.remove());
        nodes = [];
        connections = [];
        
        // Restore nodes
        if (data.nodes) {
            data.nodes.forEach(n => {
                const opSchema = registry.find(op => op.op === n.op);
                if (opSchema) {
                    createNode(opSchema, n.x, n.y, n.id, n.args);
                }
            });
        }
        
        // Restore connections
        if (data.connections) {
            data.connections.forEach(c => {
                const fromNode = nodes.find(n => n.id === c.from);
                const toNode = nodes.find(n => n.id === c.to);
                if (fromNode && toNode) {
                    addConnection(fromNode, c.fromPort, toNode, c.toPort);
                }
            });
        }
        
        nodeIdCounter = Math.max(...nodes.map(n => n.id), 0);
        renderConnections();
        
        if (nodes.length > 0) {
            document.querySelector('.canvas-hint').style.display = 'none';
        } else {
            document.querySelector('.canvas-hint').style.display = 'block';
        }
    } catch (e) {
        console.error("Failed to restore workspace from localStorage:", e);
    }
}

function setupAutoSaveListeners() {
    const inputs = [
        'cfg-headless', 'cfg-allow-eval', 'cfg-ai-provider', 'cfg-ai-host', 
        'cfg-ai-text-model', 'cfg-ai-vision-model', 'cfg-ai-api-key',
        'cfg-source', 'cfg-target', 'payload-editor', 'pipeline-selector'
    ];
    inputs.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('input', saveWorkspaceToLocalStorage);
            el.addEventListener('change', saveWorkspaceToLocalStorage);
        }
    });
}


// Canvas Setup
function setupCanvas() {
    canvas.addEventListener('dragover', e => e.preventDefault());
    
    canvas.addEventListener('drop', e => {
        e.preventDefault();
        if (draggedItem) {
            const rect = canvas.getBoundingClientRect();
            const x = e.clientX - rect.left + canvas.scrollLeft;
            const y = e.clientY - rect.top + canvas.scrollTop;
            createNode(draggedItem, x, y);
            draggedItem = null;
        }
    });

    canvas.addEventListener('mousedown', e => {
        const nodeEl = e.target.closest('.iac-node');
        
        if (e.target.closest('.node-port')) {
            const port = e.target.closest('.node-port');
            const parentNode = port.closest('.iac-node');
            const portType = port.classList.contains('port-in') ? 'in' : 'out';
            const portName = port.dataset.port;
            
            connectingPort = { 
                node: nodes.find(n => n.id == parentNode.dataset.id), 
                type: portType, 
                name: portName, 
                el: port 
            };
            
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            line.setAttribute('class', 'connection-path active-drag');
            svgLayer.appendChild(line);
            
            const mouseMoveLine = (me) => {
                const rect = canvas.getBoundingClientRect();
                const portRect = port.getBoundingClientRect();
                const x1 = portRect.left + portRect.width/2 - rect.left + canvas.scrollLeft;
                const y1 = portRect.top + portRect.height/2 - rect.top + canvas.scrollTop;
                const x2 = me.clientX - rect.left + canvas.scrollLeft;
                const y2 = me.clientY - rect.top + canvas.scrollTop;
                
                const cpOffset = Math.abs(x2 - x1) * 0.5 || 50;
                if (portType === 'out') {
                    line.setAttribute('d', `M ${x1} ${y1} C ${x1 + cpOffset} ${y1}, ${x2 - cpOffset} ${y2}, ${x2} ${y2}`);
                } else {
                    line.setAttribute('d', `M ${x2} ${y2} C ${x2 + cpOffset} ${y2}, ${x1 - cpOffset} ${y1}, ${x1} ${y1}`);
                }
            };
            
            const mouseUpLine = (me) => {
                document.removeEventListener('mousemove', mouseMoveLine);
                document.removeEventListener('mouseup', mouseUpLine);
                line.remove();
                
                const dropPort = me.target.closest('.node-port');
                if (dropPort && dropPort !== port) {
                    const targetNode = nodes.find(n => n.id == dropPort.closest('.iac-node').dataset.id);
                    const targetType = dropPort.classList.contains('port-in') ? 'in' : 'out';
                    const targetName = dropPort.dataset.port;
                    
                    if (connectingPort.type !== targetType) {
                        const fromNode = connectingPort.type === 'out' ? connectingPort.node : targetNode;
                        const fromPortName = connectingPort.type === 'out' ? connectingPort.name : targetName;
                        const toNode = connectingPort.type === 'in' ? connectingPort.node : targetNode;
                        const toPortName = connectingPort.type === 'in' ? connectingPort.name : targetName;
                        
                        addConnection(fromNode, fromPortName, toNode, toPortName);
                    }
                }
                connectingPort = null;
            };
            
            document.addEventListener('mousemove', mouseMoveLine);
            document.addEventListener('mouseup', mouseUpLine);
            return;
        }

        if (nodeEl && !e.target.closest('.btn-config-node') && !e.target.closest('.node-delete')) {
            activeNode = nodeEl;
            nodes.forEach(n => n.el.classList.remove('selected'));
            activeNode.classList.add('selected');
            
            const rect = activeNode.getBoundingClientRect();
            offsetX = e.clientX - rect.left;
            offsetY = e.clientY - rect.top;
            
            const mouseMove = (me) => {
                if (activeNode) {
                    const cRect = canvas.getBoundingClientRect();
                    const x = me.clientX - cRect.left + canvas.scrollLeft - offsetX;
                    const y = me.clientY - cRect.top + canvas.scrollTop - offsetY;
                    activeNode.style.left = `${x}px`;
                    activeNode.style.top = `${y}px`;
                    renderConnections();
                }
            };
            
            const mouseUp = () => {
                document.removeEventListener('mousemove', mouseMove);
                document.removeEventListener('mouseup', mouseUp);
                activeNode = null;
                saveWorkspaceToLocalStorage();
            };
            
            document.addEventListener('mousemove', mouseMove);
            document.addEventListener('mouseup', mouseUp);
        } else if (!nodeEl) {
            nodes.forEach(n => n.el.classList.remove('selected'));
        }
    });
}

// Add Port Helper
function addPort(el, type, portId, labelText, topPercent) {
    const portEl = document.createElement('div');
    portEl.className = `node-port port-${type}`;
    portEl.dataset.port = portId;
    portEl.style.top = `${topPercent}%`;
    el.appendChild(portEl);
    
    if (labelText) {
        const labelEl = document.createElement('span');
        labelEl.className = `port-label label-${type}`;
        labelEl.style.top = `${topPercent}%`;
        labelEl.textContent = labelText;
        el.appendChild(labelEl);
    }
}

// Dynamic Ports Setup
function setupNodePorts(el, op) {
    // 1 input port on left
    addPort(el, 'in', 'in', 'In', 50);
    
    // Output ports on right
    if (op === 'foreach' || op === 'repeat' || op === 'flow.foreach' || op === 'flow.repeat') {
        addPort(el, 'out', 'body', 'Body', 35);
        addPort(el, 'out', 'next', 'Next', 65);
    } else if (op === 'if' || op === 'flow.if') {
        addPort(el, 'out', 'then', 'True', 30);
        addPort(el, 'out', 'else', 'False', 55);
        addPort(el, 'out', 'next', 'Next', 80);
    } else {
        addPort(el, 'out', 'out', 'Out', 50);
    }
}

// Create Node on Canvas
function createNode(opData, x, y, customId = null, customArgs = null) {
    const template = document.getElementById('node-template');
    const clone = template.content.cloneNode(true);
    const el = clone.querySelector('.iac-node');
    
    const id = customId !== null ? customId : ++nodeIdCounter;
    if (id > nodeIdCounter) nodeIdCounter = id;
    el.dataset.id = id;
    
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    
    el.querySelector('.node-title').textContent = opData.op;
    
    const descEl = el.querySelector('.node-desc');
    descEl.textContent = opData.description || "No description provided.";
    
    const domain = opData.domain || 'core';
    el.dataset.domain = domain;
    
    let icon = 'fa-cube';
    if (domain === 'web') icon = 'fa-globe';
    if (domain === 'ai') icon = 'fa-brain';
    if (domain === 'sys') icon = 'fa-terminal';
    if (domain === 'desktop') icon = 'fa-desktop';
    if (domain === 'vision') icon = 'fa-eye';
    if (domain === 'filesystem') icon = 'fa-folder';
    if (domain === 'aether') icon = 'fa-bolt';
    if (domain === 'datastore') icon = 'fa-database';
    if (domain === 'prism') icon = 'fa-cubes';
    if (domain === 'dnet') icon = 'fa-cloud';
    if (domain === 'core') icon = 'fa-microchip';
    if (domain === 'flow') icon = 'fa-code-branch';
    el.querySelector('.node-icon i').className = `fa-solid ${icon}`;
    
    setupNodePorts(el, opData.op);
    
    // Initialize default arguments
    const args = {};
    if (opData.parameters && opData.parameters.properties) {
        for (const [k, v] of Object.entries(opData.parameters.properties)) {
            if (v.default !== undefined) {
                args[k] = v.default;
            } else if (v.type === 'boolean') {
                args[k] = false;
            } else if (v.type === 'string') {
                args[k] = "";
            } else if (v.type === 'integer' || v.type === 'number') {
                args[k] = 0;
            } else if (v.type === 'array') {
                args[k] = [];
            } else if (v.type === 'object') {
                args[k] = {};
            } else {
                args[k] = null;
            }
        }
    }
    
    if (customArgs) {
        Object.assign(args, customArgs);
    }
    
    const nodeObj = { id, opData, args, el };
    const nodeName = `node_${id}_${opData.op.split('.').pop()}`;
    nodeObj.name = nodeName;
    
    // Set default output key
    const outParam = getOutputParamName(opData);
    if (outParam && (!nodeObj.args[outParam] || nodeObj.args[outParam] === '')) {
        nodeObj.args[outParam] = `out_${nodeName}`;
    }
    
    nodes.push(nodeObj);
    
    // Setup listeners
    el.querySelector('.node-delete').addEventListener('click', () => {
        el.remove();
        nodes = nodes.filter(n => n.id !== id);
        connections = connections.filter(c => c.from.id !== id && c.to.id !== id);
        renderConnections();
        saveWorkspaceToLocalStorage();
        if (nodes.length === 0) {
            document.querySelector('.canvas-hint').style.display = 'block';
        }
    });
    
    el.querySelector('.btn-config-node').addEventListener('click', () => {
        openConfigModal(nodeObj);
    });
    
    canvas.appendChild(el);
    updateNodeSummary(nodeObj);
    
    if (nodes.length > 0) {
        document.querySelector('.canvas-hint').style.display = 'none';
    }
    
    saveWorkspaceToLocalStorage();
    return nodeObj;
}

// Add connection
function addConnection(fromNode, fromPort, toNode, toPort) {
    const exists = connections.some(c => c.from.id === fromNode.id && c.fromPort === fromPort && c.to.id === toNode.id && c.toPort === toPort);
    if (exists) return;
    
    // Fan-in constraint: an input port can only have one connection
    connections = connections.filter(c => !(c.to.id === toNode.id && c.toPort === toPort));
    
    connections.push({ from: fromNode, fromPort, to: toNode, toPort });
    renderConnections();
    saveWorkspaceToLocalStorage();
}

// Render connections paths
function renderConnections() {
    svgLayer.innerHTML = '';
    const cRect = canvas.getBoundingClientRect();
    
    connections.forEach(conn => {
        const fromPort = conn.from.el.querySelector(`.port-out[data-port="${conn.fromPort}"]`);
        const toPort = conn.to.el.querySelector(`.port-in[data-port="${conn.toPort}"]`);
        if (fromPort && toPort) {
            const fRect = fromPort.getBoundingClientRect();
            const tRect = toPort.getBoundingClientRect();
            const x1 = fRect.left + fRect.width/2 - cRect.left + canvas.scrollLeft;
            const y1 = fRect.top + fRect.height/2 - cRect.top + canvas.scrollTop;
            const x2 = tRect.left + tRect.width/2 - cRect.left + canvas.scrollLeft;
            const y2 = tRect.top + tRect.height/2 - cRect.top + canvas.scrollTop;
            
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            line.setAttribute('class', 'connection-path');
            
            const cpOffset = Math.abs(x2 - x1) * 0.5 || 50;
            line.setAttribute('d', `M ${x1} ${y1} C ${x1 + cpOffset} ${y1}, ${x2 - cpOffset} ${y2}, ${x2} ${y2}`);
            svgLayer.appendChild(line);
        }
    });
}

// Update Concise Config Summary on Node Body
function updateNodeSummary(nodeObj) {
    const descEl = nodeObj.el.querySelector('.node-desc');
    if (!descEl) return;
    
    const keys = Object.keys(nodeObj.args);
    if (keys.length === 0) {
        descEl.innerHTML = `<span class="summary-empty">No config set</span>`;
        return;
    }
    
    let summaryHtml = '<div class="node-summary">';
    let count = 0;
    for (const key of keys) {
        const val = nodeObj.args[key];
        const isSet = val !== undefined && val !== null && val !== "" && 
                      (Array.isArray(val) ? val.length > 0 : true) && 
                      (typeof val === 'object' ? Object.keys(val).length > 0 : true);
                      
        if (isSet) {
            let displayVal = typeof val === 'object' ? JSON.stringify(val) : String(val);
            if (displayVal.length > 30) displayVal = displayVal.substring(0, 27) + '...';
            summaryHtml += `<div class="summary-row"><span class="summary-key">${key}:</span> <span class="summary-val">${escapeHtml(displayVal)}</span></div>`;
            count++;
        }
        if (count >= 3) break;
    }
    if (count === 0) {
        summaryHtml += `<span class="summary-empty">Default configuration</span>`;
    }
    summaryHtml += '</div>';
    descEl.innerHTML = summaryHtml;
}

function escapeHtml(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

// Config Modal parameters
function openConfigModal(nodeObj) {
    editingNode = nodeObj;
    document.getElementById('modal-title').textContent = `Configure ${nodeObj.opData.op}`;
    modalParams.innerHTML = '';
    
    const props = (nodeObj.opData.parameters && nodeObj.opData.parameters.properties) ? nodeObj.opData.parameters.properties : {};
    
    if (Object.keys(props).length === 0) {
        modalParams.innerHTML = '<p class="help-text">No configurable parameters for this operation.</p>';
    } else {
        for (const [key, schema] of Object.entries(props)) {
            if (['steps', 'then', 'else'].includes(key)) {
                continue;
            }
            const group = document.createElement('div');
            group.className = 'form-group';
            
            const labelRow = document.createElement('div');
            labelRow.className = 'label-row';
            labelRow.style.display = 'flex';
            labelRow.style.justifyContent = 'space-between';
            labelRow.style.alignItems = 'center';
            labelRow.style.marginBottom = '6px';
            
            const label = document.createElement('label');
            label.textContent = key;
            label.style.margin = '0';
            labelRow.appendChild(label);
            
            const required = nodeObj.opData.parameters.required || [];
            if (required.includes(key)) {
                const reqBadge = document.createElement('span');
                reqBadge.textContent = 'Required';
                reqBadge.style.fontSize = '0.7rem';
                reqBadge.style.color = '#ff4757';
                reqBadge.style.textTransform = 'uppercase';
                reqBadge.style.fontWeight = 'bold';
                labelRow.appendChild(reqBadge);
            }
            
            group.appendChild(labelRow);
            const val = nodeObj.args[key];
            
            if (schema.type === 'boolean') {
                const check = document.createElement('input');
                check.type = 'checkbox';
                check.id = `input-${key}`;
                check.checked = val !== undefined ? !!val : (schema.default !== undefined ? !!schema.default : false);
                
                const wrap = document.createElement('div');
                wrap.className = 'checkbox-group';
                wrap.style.display = 'flex';
                wrap.style.alignItems = 'center';
                wrap.style.gap = '10px';
                wrap.style.marginTop = '4px';
                wrap.appendChild(check);
                
                const l = document.createElement('label');
                l.setAttribute('for', `input-${key}`);
                l.textContent = schema.description || "Enabled";
                l.style.fontSize = '0.85rem';
                l.style.color = 'var(--text-muted)';
                l.style.cursor = 'pointer';
                wrap.appendChild(l);
                
                group.appendChild(wrap);
            } else if (schema.type === 'integer' || schema.type === 'number') {
                const input = document.createElement('input');
                input.type = 'number';
                input.id = `input-${key}`;
                input.className = 'modal-input';
                input.value = val !== undefined && val !== null ? val : (schema.default !== undefined ? schema.default : 0);
                if (schema.minimum !== undefined) input.min = schema.minimum;
                if (schema.maximum !== undefined) input.max = schema.maximum;
                input.placeholder = schema.description || '';
                group.appendChild(input);
            } else if (schema.type === 'array' || schema.type === 'object') {
                const textarea = document.createElement('textarea');
                textarea.id = `input-${key}`;
                textarea.className = 'modal-textarea json-textarea';
                textarea.style.fontFamily = 'monospace';
                textarea.style.fontSize = '0.85rem';
                textarea.style.minHeight = '100px';
                textarea.style.backgroundColor = 'rgba(0, 0, 0, 0.4)';
                textarea.style.color = '#a8ff60';
                textarea.style.border = '1px solid rgba(255,255,255,0.1)';
                textarea.style.borderRadius = '6px';
                textarea.style.width = '100%';
                textarea.style.padding = '10px';
                textarea.style.resize = 'vertical';
                
                let stringified = '';
                if (val !== undefined && val !== null) {
                    stringified = typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val);
                } else if (schema.default !== undefined) {
                    stringified = JSON.stringify(schema.default, null, 2);
                } else {
                    stringified = schema.type === 'array' ? '[]' : '{}';
                }
                
                textarea.value = stringified;
                
                // Allow interpolation references like {{_last_result}} in JSON fields
                textarea.placeholder = schema.description || (schema.type === 'array' ? 'e.g. ["item1"] or {{ref}}' : 'e.g. {"key": "val"} or {{ref}}');
                
                const feedback = document.createElement('div');
                feedback.className = 'feedback-message';
                feedback.style.fontSize = '0.75rem';
                feedback.style.marginTop = '4px';
                feedback.style.color = 'var(--text-muted)';
                feedback.textContent = schema.description || '';
                
                textarea.addEventListener('input', () => {
                    const txtVal = textarea.value.trim();
                    if (txtVal === '') {
                        feedback.textContent = 'Empty (valid)';
                        feedback.style.color = '#2ed573';
                        textarea.style.borderColor = 'rgba(255,255,255,0.1)';
                        return;
                    }
                    if (txtVal.includes('{{') && txtVal.includes('}}')) {
                        feedback.textContent = '✓ Template interpolation reference (valid)';
                        feedback.style.color = '#2ed573';
                        textarea.style.borderColor = '#2ed573';
                        return;
                    }
                    try {
                        JSON.parse(txtVal);
                        feedback.textContent = '✓ Valid JSON structure';
                        feedback.style.color = '#2ed573';
                        textarea.style.borderColor = '#2ed573';
                    } catch (e) {
                        feedback.textContent = `✗ Invalid JSON: ${e.message}`;
                        feedback.style.color = '#ff4757';
                        textarea.style.borderColor = '#ff4757';
                    }
                });
                
                group.appendChild(textarea);
                group.appendChild(feedback);
            } else {
                const isMultiline = ['code', 'prompt', 'content', 'cmd', 'commands', 'options'].includes(key.toLowerCase()) || (schema.description && schema.description.toLowerCase().includes('multiline'));
                
                if (isMultiline) {
                    const textarea = document.createElement('textarea');
                    textarea.id = `input-${key}`;
                    textarea.className = 'modal-textarea';
                    textarea.style.fontFamily = 'monospace';
                    textarea.style.fontSize = '0.85rem';
                    textarea.style.minHeight = '100px';
                    textarea.style.width = '100%';
                    textarea.style.padding = '10px';
                    textarea.style.resize = 'vertical';
                    textarea.style.backgroundColor = 'rgba(0, 0, 0, 0.4)';
                    textarea.style.border = '1px solid rgba(255,255,255,0.1)';
                    textarea.style.borderRadius = '6px';
                    textarea.style.color = 'white';
                    
                    textarea.value = val !== undefined && val !== null ? String(val) : (schema.default !== undefined ? String(schema.default) : '');
                    textarea.placeholder = schema.description || '';
                    
                    const desc = document.createElement('div');
                    desc.style.fontSize = '0.75rem';
                    desc.style.marginTop = '4px';
                    desc.style.color = 'var(--text-muted)';
                    desc.textContent = schema.description || '';
                    
                    group.appendChild(textarea);
                    group.appendChild(desc);
                } else {
                    const input = document.createElement('input');
                    input.type = 'text';
                    input.id = `input-${key}`;
                    input.className = 'modal-input';
                    input.value = val !== undefined && val !== null ? String(val) : (schema.default !== undefined ? String(schema.default) : '');
                    
                    // Show placeholders referencing global settings for Host and Model
                    if (key === 'host') {
                        input.placeholder = document.getElementById('cfg-ai-host').value || 'http://127.0.0.1:11434';
                    } else if (key === 'model') {
                        input.placeholder = document.getElementById('cfg-ai-text-model').value || 'qwen3.5:4b';
                    } else {
                        input.placeholder = schema.description || '';
                    }
                    
                    const desc = document.createElement('div');
                    desc.style.fontSize = '0.75rem';
                    desc.style.marginTop = '4px';
                    desc.style.color = 'var(--text-muted)';
                    desc.textContent = schema.description || '';
                    
                    group.appendChild(input);
                    group.appendChild(desc);
                }
            }
            modalParams.appendChild(group);
        }
    }
    
    modal.classList.add('active');
}

document.getElementById('modal-close').addEventListener('click', () => modal.classList.remove('active'));
document.getElementById('modal-save').addEventListener('click', () => {
    if (editingNode) {
        const props = (editingNode.opData.parameters && editingNode.opData.parameters.properties) ? editingNode.opData.parameters.properties : {};
        const newArgs = {};
        let hasErrors = false;
        
        for (const [key, schema] of Object.entries(props)) {
            const input = document.getElementById(`input-${key}`);
            if (!input) continue;
            
            if (schema.type === 'boolean') {
                newArgs[key] = input.checked;
            } else if (schema.type === 'integer' || schema.type === 'number') {
                const val = Number(input.value);
                if (isNaN(val)) {
                    logTerminal(`Error configuring parameter '${key}': Value must be a number.`, 'error');
                    hasErrors = true;
                    input.style.borderColor = '#ff4757';
                } else {
                    newArgs[key] = val;
                }
            } else if (schema.type === 'array' || schema.type === 'object') {
                const txt = input.value.trim();
                if (txt === '') {
                    newArgs[key] = schema.type === 'array' ? [] : {};
                } else if (txt.includes('{{') && txt.includes('}}')) {
                    newArgs[key] = txt; // Store raw template string
                } else {
                    try {
                        newArgs[key] = JSON.parse(txt);
                    } catch (e) {
                        logTerminal(`Error parsing JSON for parameter '${key}': ${e.message}`, 'error');
                        hasErrors = true;
                        input.style.borderColor = '#ff4757';
                    }
                }
            } else {
                newArgs[key] = input.value;
            }
        }
        
        if (hasErrors) {
            return;
        }
        
        editingNode.args = newArgs;
        updateNodeSummary(editingNode);
        saveWorkspaceToLocalStorage();
        
        editingNode.el.classList.add('selected'); // Visual feedback
        setTimeout(() => editingNode.el.classList.remove('selected'), 300);
    }
    modal.classList.remove('active');
});

// Compile Sub-Plan Recursively
function compileSubPlan(startNode, predecessorOutputKey) {
    const plan = [];
    let current = startNode;
    const visited = new Set();
    
    while (current) {
        if (visited.has(current.id)) {
            logTerminal(`Circular connection loop detected at node ${current.opData.op}. Breaking compiler chain.`, "error");
            break;
        }
        visited.add(current.id);
        
        const args = JSON.parse(JSON.stringify(current.args));
        const inParam = getInputParamName(current.opData);
        
        // Auto-wire default input if parameter is empty/blank
        if (inParam && predecessorOutputKey) {
            const isUnset = !args[inParam] || 
                            args[inParam] === '' || 
                            (Array.isArray(args[inParam]) && args[inParam].length === 0) || 
                            (typeof args[inParam] === 'object' && Object.keys(args[inParam]).length === 0);
                            
            if (isUnset) {
                if (inParam === 'inject_keys') {
                    args[inParam] = [predecessorOutputKey];
                } else if (inParam === 'payload_key') {
                    args[inParam] = predecessorOutputKey;
                } else {
                    args[inParam] = `{{${predecessorOutputKey}}}`;
                }
            }
        }
        
        const step = {
            op: current.opData.op,
            args: args
        };
        
        const outParam = getOutputParamName(current.opData);
        const currentOutputKey = (outParam && args[outParam]) ? args[outParam] : predecessorOutputKey;
        
        if (current.opData.op === 'foreach' || current.opData.op === 'repeat' || current.opData.op === 'flow.foreach' || current.opData.op === 'flow.repeat') {
            const bodyConn = connections.find(c => c.from.id === current.id && c.fromPort === 'body');
            step.args.steps = bodyConn ? compileSubPlan(bodyConn.to, currentOutputKey) : [];
            
            plan.push(step);
            
            const nextConn = connections.find(c => c.from.id === current.id && c.fromPort === 'next');
            current = nextConn ? nextConn.to : null;
        } else if (current.opData.op === 'if' || current.opData.op === 'flow.if') {
            const thenConn = connections.find(c => c.from.id === current.id && c.fromPort === 'then');
            step.args.then = thenConn ? compileSubPlan(thenConn.to, currentOutputKey) : [];
            
            const elseConn = connections.find(c => c.from.id === current.id && c.fromPort === 'else');
            step.args.else = elseConn ? compileSubPlan(elseConn.to, currentOutputKey) : [];
            
            plan.push(step);
            
            const nextConn = connections.find(c => c.from.id === current.id && c.fromPort === 'next');
            current = nextConn ? nextConn.to : null;
        } else {
            plan.push(step);
            
            const outConns = connections.filter(c => c.from.id === current.id && c.fromPort === 'out');
            if (outConns.length > 0) {
                outConns.sort((a, b) => a.to.el.getBoundingClientRect().top - b.to.el.getBoundingClientRect().top);
                
                if (outConns.length === 1) {
                    current = outConns[0].to;
                } else {
                    outConns.forEach(conn => {
                        const branchSteps = compileSubPlan(conn.to, currentOutputKey);
                        plan.push(...branchSteps);
                    });
                    current = null;
                }
            } else {
                current = null;
            }
        }
    }
    
    return plan;
}

// Compile Plan to Universal JSON Schema
function compilePlan() {
    if (nodes.length === 0) return null;
    
    const hasIncoming = new Set(connections.map(c => c.to.id));
    const roots = nodes.filter(n => !hasIncoming.has(n.id));
    
    if (roots.length === 0) {
        logTerminal("Error: Circular dependencies detected. Cannot compile plan.", "error");
        return null;
    }
    if (roots.length > 1) {
        logTerminal("Warning: Multiple roots detected. Sequential branches will compile left-to-right.", "sys");
    }
    
    roots.sort((a, b) => {
        const ar = a.el.getBoundingClientRect();
        const br = b.el.getBoundingClientRect();
        return (ar.top + ar.left) - (br.top + br.left);
    });
    
    const plan = [];
    roots.forEach(root => {
        plan.push(...compileSubPlan(root, null));
    });
    
    const config = {
        headless: document.getElementById('cfg-headless').checked,
        ai_config: {
            provider: document.getElementById('cfg-ai-provider').value,
            host: document.getElementById('cfg-ai-host').value,
            model: document.getElementById('cfg-ai-text-model').value,
            text_model: document.getElementById('cfg-ai-text-model').value,
            vision_model: document.getElementById('cfg-ai-vision-model').value,
            api_key: document.getElementById('cfg-ai-api-key').value,
            ai_timeout: 300,
            drop: ["encoded", "desc"]
        },
        security: {
            allow_eval: document.getElementById('cfg-allow-eval').checked
        }
    };
    
    let payload = {};
    try {
        payload = JSON.parse(document.getElementById('payload-editor').value);
    } catch(e) {
        logTerminal("Warning: Invalid payload JSON. Sending empty payload.", "sys");
    }
    
    return {
        source: document.getElementById('cfg-source').value,
        target: document.getElementById('cfg-target').value,
        config: config,
        plan: plan,
        data_payload: payload
    };
}

// Load Workflow List
async function loadPipelinesList() {
    try {
        const res = await fetch('/api/pipelines');
        const list = await res.json();
        const select = document.getElementById('pipeline-selector');
        select.innerHTML = '<option value="">-- Load Workflow --</option>';
        list.forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            select.appendChild(opt);
        });
    } catch(e) {
        logTerminal(`Failed to fetch workflow list: ${e.message}`, 'error');
    }
}

// Draw/Layout standard plan sequentially when no coordinate metadata is present
function autoLayoutPlan(planSteps) {
    nodes.forEach(n => n.el.remove());
    nodes = [];
    connections = [];
    renderConnections();
    
    let curX = 100;
    let curY = 250;
    
    function layoutSteps(steps, startX, startY, parentNode = null, outPort = 'out') {
        let currentX = startX;
        let currentY = startY;
        let prev = parentNode;
        let prevPort = outPort;
        
        steps.forEach((step) => {
            const opSchema = registry.find(op => op.op === step.op);
            if (!opSchema) return;
            
            const node = createNode(opSchema, currentX, currentY, null, step.args);
            if (prev) {
                addConnection(prev, prevPort, node, 'in');
            }
            
            prev = node;
            prevPort = 'out';
            currentX += 300;
            
            if (step.op === 'foreach' || step.op === 'repeat' || step.op === 'flow.foreach' || step.op === 'flow.repeat') {
                if (step.args && step.args.steps && step.args.steps.length > 0) {
                    layoutSteps(step.args.steps, currentX - 100, currentY - 140, node, 'body');
                }
                prevPort = 'next';
            } else if (step.op === 'if' || step.op === 'flow.if') {
                if (step.args && step.args.then && step.args.then.length > 0) {
                    layoutSteps(step.args.then, currentX - 100, currentY - 160, node, 'then');
                }
                if (step.args && step.args.else && step.args.else.length > 0) {
                    layoutSteps(step.args.else, currentX - 100, currentY + 160, node, 'else');
                }
                prevPort = 'next';
            }
        });
    }
    
    layoutSteps(planSteps, curX, curY);
    renderConnections();
    saveWorkspaceToLocalStorage();
}

// Load Plan and Restore UI state
function loadPipelineToCanvas(data) {
    nodes.forEach(n => n.el.remove());
    nodes = [];
    connections = [];
    renderConnections();
    
    if (data.config) {
        document.getElementById('cfg-headless').checked = !!data.config.headless;
        if (data.config.security) {
            document.getElementById('cfg-allow-eval').checked = !!data.config.security.allow_eval;
        }
        if (data.config.ai_config) {
            if (data.config.ai_config.host) document.getElementById('cfg-ai-host').value = data.config.ai_config.host;
            if (data.config.ai_config.text_model) document.getElementById('cfg-ai-text-model').value = data.config.ai_config.text_model;
            else if (data.config.ai_config.model) document.getElementById('cfg-ai-text-model').value = data.config.ai_config.model;
            if (data.config.ai_config.vision_model) document.getElementById('cfg-ai-vision-model').value = data.config.ai_config.vision_model;
            if (data.config.ai_config.api_key) document.getElementById('cfg-ai-api-key').value = data.config.ai_config.api_key;
            if (data.config.ai_config.provider) document.getElementById('cfg-ai-provider').value = data.config.ai_config.provider;
        }
    }
    
    if (data.source) document.getElementById('cfg-source').value = data.source;
    if (data.target) document.getElementById('cfg-target').value = data.target;
    if (data.data_payload) {
        document.getElementById('payload-editor').value = JSON.stringify(data.data_payload, null, 2);
    }
    
    if (data._gui_layout && data._gui_layout.nodes) {
        data._gui_layout.nodes.forEach(n => {
            const opSchema = registry.find(op => op.op === n.op);
            if (opSchema) {
                createNode(opSchema, n.x, n.y, n.id, n.args || {});
            }
        });
        
        if (data._gui_layout.connections) {
            data._gui_layout.connections.forEach(c => {
                const fromNode = nodes.find(n => n.id === c.from);
                const toNode = nodes.find(n => n.id === c.to);
                if (fromNode && toNode) {
                    addConnection(fromNode, c.fromPort, toNode, c.toPort);
                }
            });
        }
        
        nodeIdCounter = Math.max(...nodes.map(n => n.id), 0);
        renderConnections();
    } else {
        logTerminal("No GUI layout found in workflow. Performing auto-layout sequence.", "sys");
        autoLayoutPlan(data.plan || []);
    }
    
    if (nodes.length > 0) {
        document.querySelector('.canvas-hint').style.display = 'none';
    } else {
        document.querySelector('.canvas-hint').style.display = 'block';
    }
    
    saveWorkspaceToLocalStorage();
}

// Action Buttons wiring
function setupButtons() {
    document.getElementById('btn-clear').addEventListener('click', () => {
        nodes.forEach(n => n.el.remove());
        nodes = [];
        connections = [];
        renderConnections();
        document.querySelector('.canvas-hint').style.display = 'block';
        saveWorkspaceToLocalStorage();
    });
    
    document.getElementById('btn-validate').addEventListener('click', async () => {
        const payload = compilePlan();
        if (!payload) return;
        
        try {
            const res = await fetch('/api/validate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            
            document.querySelector('[data-target="tab-output"]').click();
            
            if (data.valid) {
                logTerminal(`Validation SUCCESS. Plan is valid.`, 'success');
            } else {
                logTerminal(`Validation FAILED:`, 'error');
                data.errors.forEach(err => logTerminal(err, 'error'));
            }
        } catch (e) {
            logTerminal(`Validation request failed: ${e}`, 'error');
        }
    });
    
    document.getElementById('btn-execute').addEventListener('click', async () => {
        const payload = compilePlan();
        if (!payload) return;
        
        logTerminal(`Dispatching execution plan (${payload.plan.length} operations)...`, 'info');
        document.querySelector('[data-target="tab-output"]').click();
        
        try {
            const res = await fetch('/api/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            
            if (data.status === 'success') {
                logTerminal(`Execution completed successfully.`, 'success');
                logTerminal(`Final State:`, 'sys');
                logTerminal(data.result);
                // Synchronize execution final payload results to the payload tab editor
                if (data.result && data.result.final_payload) {
                    document.getElementById('payload-editor').value = JSON.stringify(data.result.final_payload, null, 2);
                    saveWorkspaceToLocalStorage();
                }
                if (data.result && data.result.history) {
                    document.getElementById('exchanges-editor').value = JSON.stringify(data.result.history, null, 2);
                }
            } else {
                logTerminal(`Execution error: ${data.message}`, 'error');
                if (data.errors) data.errors.forEach(err => logTerminal(err, 'error'));
            }
        } catch (e) {
            logTerminal(`Execution request failed: ${e}`, 'error');
        }
    });
    
    document.getElementById('btn-save-pipeline').addEventListener('click', async () => {
        const planObj = compilePlan();
        if (!planObj) {
            logTerminal("Cannot save empty pipeline.", "error");
            return;
        }
        
        let name = document.getElementById('pipeline-selector').value;
        if (!name) {
            name = prompt("Enter a filename to save this workflow (e.g. custom_scrape.json):");
            if (!name) return;
            if (!name.endsWith('.json')) name += '.json';
        }
        
        planObj._gui_layout = {
            nodes: nodes.map(n => ({
                id: n.id,
                op: n.opData.op,
                x: parseInt(n.el.style.left),
                y: parseInt(n.el.style.top),
                args: n.args
            })),
            connections: connections.map(c => ({
                from: c.from.id,
                fromPort: c.fromPort,
                to: c.to.id,
                toPort: c.toPort
            }))
        };
        
        try {
            const res = await fetch(`/api/pipelines/${name}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(planObj)
            });
            const data = await res.json();
            if (data.status === 'success') {
                logTerminal(`Pipeline successfully saved: ${name}`, 'success');
                await loadPipelinesList();
                document.getElementById('pipeline-selector').value = name;
                saveWorkspaceToLocalStorage();
            } else {
                logTerminal(`Failed to save pipeline: ${data.message}`, 'error');
            }
        } catch(e) {
            logTerminal(`Failed to save pipeline request: ${e.message}`, 'error');
        }
    });
    
    document.getElementById('pipeline-selector').addEventListener('change', async (e) => {
        const name = e.target.value;
        if (!name) return;
        try {
            const res = await fetch(`/api/pipelines/${name}`);
            const data = await res.json();
            if (data.status === 'error') {
                logTerminal(`Failed to load pipeline: ${data.message}`, 'error');
                return;
            }
            loadPipelineToCanvas(data);
            logTerminal(`Loaded pipeline: ${name}`, 'success');
        } catch(e) {
            logTerminal(`Failed to fetch pipeline: ${e.message}`, 'error');
        }
    });
    
    document.getElementById('op-search').addEventListener('input', (e) => {
        const q = e.target.value.toLowerCase();
        document.querySelectorAll('.op-item').forEach(item => {
            const text = item.textContent.toLowerCase();
            item.style.display = text.includes(q) ? 'flex' : 'none';
        });
    });

    // Toggle Sidebar & Panel buttons
    const btnToggleSidebar = document.getElementById('btn-toggle-sidebar');
    if (btnToggleSidebar) {
        btnToggleSidebar.addEventListener('click', () => {
            const sidebar = document.getElementById('operations-sidebar');
            sidebar.classList.toggle('collapsed');
            const icon = btnToggleSidebar.querySelector('i');
            if (sidebar.classList.contains('collapsed')) {
                icon.className = 'fa-solid fa-chevron-right';
            } else {
                icon.className = 'fa-solid fa-chevron-left';
            }
        });
    }

    const btnTogglePanel = document.getElementById('btn-toggle-panel');
    if (btnTogglePanel) {
        btnTogglePanel.addEventListener('click', () => {
            const panel = document.getElementById('config-panel');
            panel.classList.toggle('collapsed');
            const icon = btnTogglePanel.querySelector('i');
            if (panel.classList.contains('collapsed')) {
                icon.className = 'fa-solid fa-chevron-left';
            } else {
                icon.className = 'fa-solid fa-chevron-right';
            }
        });
    }
}
