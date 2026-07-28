# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
import os
import sys
import json
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# Ensure parent directory (IAC) is accessible
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(BASE_DIR))

try:
    from IAC.iac import execute as iac_execute, bootstrap
    from IAC.registry import get_registry
    from IAC.schema import validate_plan
except ImportError as e:
    print(f"Error importing IAC Core: {e}")
    sys.exit(1)

bootstrap()

app = Flask(__name__)
CORS(app)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/registry', methods=['GET'])
def get_registry_endpoint():
    registry = get_registry()
    ops = registry.list_operations()
    return jsonify({"status": "success", "operations": ops})

@app.route('/api/validate', methods=['POST'])
def validate_endpoint():
    plan_data = request.json
    valid, errors = validate_plan(plan_data)
    return jsonify({"valid": valid, "errors": errors})

@app.route('/api/execute', methods=['POST'])
def execute_endpoint():
    plan_data = request.json
    if not plan_data:
        return jsonify({"status": "error", "message": "No data provided."}), 400
        
    try:
        # Check validation first
        valid, errors = validate_plan(plan_data)
        if not valid:
            return jsonify({"status": "error", "message": "Validation failed", "errors": errors}), 400
            
        result = iac_execute(plan_data)
        return jsonify({"status": "success", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        try:
            from IAC.web.ops import close_web_agent
            close_web_agent()
        except ImportError:
            try:
                from web.ops import close_web_agent
                close_web_agent()
            except:
                pass

@app.route('/api/pipelines', methods=['GET'])
def list_pipelines():
    pipelines_dir = os.path.join(BASE_DIR, 'IAC', 'pipelines')
    if not os.path.exists(pipelines_dir):
        # Maybe BASE_DIR is IAC itself, or we are in IAC directory
        pipelines_dir = os.path.join(BASE_DIR, 'pipelines')
    if not os.path.exists(pipelines_dir):
        return jsonify([])
    try:
        files = [f for f in os.listdir(pipelines_dir) if f.endswith('.json')]
        return jsonify(files)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/pipelines/<name>', methods=['GET'])
def get_pipeline(name):
    pipelines_dir = os.path.join(BASE_DIR, 'IAC', 'pipelines')
    if not os.path.exists(pipelines_dir):
        pipelines_dir = os.path.join(BASE_DIR, 'pipelines')
    path = os.path.join(pipelines_dir, name)
    if not os.path.exists(path) or not name.endswith('.json'):
        return jsonify({"status": "error", "message": "Pipeline not found"}), 404
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/pipelines/<name>', methods=['POST'])
def save_pipeline(name):
    pipelines_dir = os.path.join(BASE_DIR, 'IAC', 'pipelines')
    if not os.path.exists(pipelines_dir):
        pipelines_dir = os.path.join(BASE_DIR, 'pipelines')
    os.makedirs(pipelines_dir, exist_ok=True)
    path = os.path.join(pipelines_dir, name)
    if not name.endswith('.json'):
        return jsonify({"status": "error", "message": "Invalid pipeline filename"}), 400
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(request.json, f, indent=2)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5100)
