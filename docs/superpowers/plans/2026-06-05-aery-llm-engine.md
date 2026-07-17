# Aery LLM Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the new `AeryModelRegistry` and the native Python clients for the 3 specified free-usage providers: Kilo, Antigravity, and OpenCode Zen. Disconnect and remove the legacy `llm_client.py` and `providers.py`.

---

### Task 1: Create Engine Registry & Provider Base

**Files:**
- Create: `aery_plugin/engine/llm.py`

- [ ] **Step 1: Write `ProviderBase` and `AeryModelRegistry` classes**
  - Implement `ProviderBase` with `stream_chat(messages, model)`.
  - Implement `AeryModelRegistry` with `register_provider` and `get_provider`.
- [ ] **Step 2: Commit**

### Task 2: Implement Kilo Provider

- [ ] **Step 1: Write `KiloProvider`**
  - Inherit from `ProviderBase`.
  - Use `aery_plugin._http` to stream from `https://api.kilo.ai/api/gateway`.
  - Protocol: `openai-completions` compatibility.
- [ ] **Step 2: Register in `llm.py`**
- [ ] **Step 3: Commit**

### Task 3: Implement OpenCode Zen Provider

- [ ] **Step 1: Write `OpenCodeZenProvider`**
  - Inherit from `ProviderBase`.
  - Use `aery_plugin._http` to stream from `https://opencode.ai/zen`.
  - Protocol: `openai-completions` compatibility.
- [ ] **Step 2: Register in `llm.py`**
- [ ] **Step 3: Commit**

### Task 4: Implement Antigravity Provider

- [ ] **Step 1: Write `AntigravityProvider`**
  - Inherit from `ProviderBase`.
  - Use `aery_plugin._http` to stream from the standard Antigravity endpoint.
  - Protocol: `openai-completions` or Gemini-style.
- [ ] **Step 2: Register in `llm.py`**
- [ ] **Step 3: Commit**

### Task 5: Wire Engine Core and Cleanup

**Files:**
- Modify: `aery_plugin/engine/core.py`
- Modify: `aery_plugin/engine_adapter.py`
- Delete: `aery_plugin/llm_client.py`
- Delete: `aery_plugin/providers.py`

- [ ] **Step 1: Update `AeryEngine` to stream from `AeryModelRegistry`**
- [ ] **Step 2: Remove legacy files**
- [ ] **Step 3: Commit**
