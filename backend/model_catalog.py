"""OpenAI defaults used by Sub2API's 「同步最新支持模型」 button.

Source: frontend/src/composables/useModelWhitelist.ts, openaiModels.
This is the built-in catalog, not the separate live upstream synchronization API.
"""

OPENAI_MODELS = (
    'gpt-5.2', 'gpt-5.2-2025-12-11', 'gpt-5.2-chat-latest',
    'gpt-5.2-pro', 'gpt-5.2-pro-2025-12-11',
    'gpt-5.6', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna',
    'gpt-6', 'gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna',
    'gpt-5.5',
    'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.4-2026-03-05',
    'gpt-5.3-codex-spark', 'codex-auto-review',
    'gpt-4o-audio-preview', 'gpt-4o-realtime-preview',
    'gpt-image-1', 'gpt-image-1.5', 'gpt-image-2', 'gpt-image-2.5-flare', 'gpt-image-2.5-sunburst',
)
