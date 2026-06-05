import json
import re

with open("/home/aryee/aery/ai_agent/oh-my-pi/packages/ai/src/models.json", "r") as f:
    data = json.load(f)

# Extract models
def get_models(pid):
    models = []
    if pid in data:
        for m_id, m in data[pid].items():
            models.append(f'            ("{m_id}", "{m.get("name", m_id)}"),')
    return "\n".join(models)

kilo_models = get_models("kilo")
antigravity_models = get_models("google-antigravity")
opencode_models = get_models("opencode-zen")

with open("/home/aryee/Desktop/aerforge/aery-qgis-plugin/aery_plugin/oauth_helper.py", "r") as f:
    code = f.read()

# Replace Kilo
kilo_re = re.compile(r'        "kilo": \[.*?        \],', re.DOTALL)
kilo_replacement = f'        "kilo": [\n{kilo_models}\n        ],'
code = kilo_re.sub(kilo_replacement, code)

# Replace Antigravity
ag_re = re.compile(r'        "google-antigravity": \[.*?        \],', re.DOTALL)
ag_replacement = f'        "google-antigravity": [\n{antigravity_models}\n        ],'
code = ag_re.sub(ag_replacement, code)

# Replace OpenCode
oc_re = re.compile(r'        "models": \[.*?        \]', re.DOTALL)
# OpenCode is in API_PROVIDERS under "opencode"
oc_replacement = f'        "models": [\n{opencode_models}\n        ]'
# We only want to replace the first occurrence (which is opencode) or do it precisely
oc_block_re = re.compile(r'("opencode": \{.*?)"models": \[.*?\]', re.DOTALL)
code = oc_block_re.sub(r'\1"models": [\n' + opencode_models + '\n        ]', code)

with open("/home/aryee/Desktop/aerforge/aery-qgis-plugin/aery_plugin/oauth_helper.py", "w") as f:
    f.write(code)
