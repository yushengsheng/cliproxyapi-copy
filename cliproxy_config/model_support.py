"""Plan target-specific imports without writing files or probing paid endpoints."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re

from .config import effective_model_settings, validate_connection

# Preserve discovery metadata in portable nodes instead of guessing capabilities.
DISCOVERY_FIELDS = ("type", "model_type", "task", "architecture", "supported_in_api", "input_modalities",
                    "output_modalities", "supported_endpoints", "endpoints", "api", "wire_api", "capabilities")
_PROTOCOLS = {
    "responses": "responses", "openai-responses": "responses", "v1/responses": "responses",
    "chat": "chat", "chat/completions": "chat", "v1/chat/completions": "chat", "chat_completions": "chat",
    "chat-completions": "chat", "openai-completions": "chat", "openai-chat-completions": "chat",
    "messages": "messages", "v1/messages": "messages", "anthropic-messages": "messages",
}
_PROTOCOL_LABELS = {"responses": "Responses", "messages": "Anthropic Messages", "chat": "Chat Completions"}
_IMAGE_NAME = re.compile(r"(?:^|[/.:])(gpt-image(?:[-.]|$)|dall[-_]?e(?:[-_.]|$)|imagen(?:[-_.]|$)|"
                         r"flux(?:[-_.]|$)|stable-diffusion(?:[-_.]|$)|grok-imagine(?:[-_.]|$)|gemini-[^/]*-image(?:[-.]|$))", re.I)


@dataclass(frozen=True)
class SkippedModel:
    name: str
    reason: str


@dataclass(frozen=True)
class ImportPlan:
    payload: dict | None = field(repr=False)
    accepted: tuple[str, ...]
    skipped: tuple[SkippedModel, ...]
    requested_default: str

    @property
    def default_model(self) -> str:
        return self.payload["model"] if self.payload else ""

    @property
    def default_changed(self) -> bool:
        return bool(self.payload) and self.default_model != self.requested_default


def codex_capability_error(row: dict) -> str:
    if (not isinstance(row, dict) or type(row.get("context_window")) is not int
            or row["context_window"] <= 0 or not isinstance(row.get("supported_reasoning_levels"), list)):
        return "缺少 Codex 所需的原生上下文或思考能力信息，请从后台重新获取；其他软件不要求该目录。"
    if (row["context_window"] == 32768 and not row["supported_reasoning_levels"]
            and str(row.get("description", "")).startswith("CLIProxyAPI · ")):
        return "旧版 32K 空思考目录不可靠，请从后台重新获取。"
    if row.get("supported_in_api") is False:
        return "后台能力目录声明此模型不支持 API 调用。"
    return ""


def _endpoints(row: dict) -> list[str]:
    result = []
    for key in ("supported_endpoints", "endpoints", "api", "wire_api"):
        value = row.get(key)
        values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
        result.extend(v.strip().lower().strip("/") for v in values if isinstance(v, str))
    return result


def _non_chat_reason(row: dict) -> str:
    name = row["slug"]
    tasks = {str(row.get(key, "")).lower().replace("_", "-") for key in ("type", "model_type", "task")}
    endpoints = _endpoints(row)
    architecture = row.get("architecture")
    outputs = row.get("output_modalities", architecture.get("output_modalities") if isinstance(architecture, dict) else None)
    outputs = {v.lower() for v in outputs if isinstance(v, str)} if isinstance(outputs, list) else set()
    image_reason = "独立图像生成模型，需要 Images API 或 image_generation 工具，不能作为当前软件的主对话模型导入。"
    if tasks & {"embedding", "embeddings", "text-embedding", "feature-extraction"} or any(e.endswith("embeddings") for e in endpoints):
        return "嵌入模型用于生成向量，不是对话模型，当前软件的主模型入口不适用。"
    if tasks & {"image", "image-generation", "text-to-image", "image-to-image"} and "text" not in outputs:
        return image_reason
    if outputs and "text" not in outputs:
        return image_reason if "image" in outputs else "模型不输出文本，当前软件的主对话模型入口不适用。"
    if tasks & {"audio", "text-to-speech", "speech-to-text", "transcription", "video", "text-to-video", "moderation", "rerank", "reranking"}:
        return "专用音视频、审核或重排模型，需要对应接口，不能作为主对话模型导入。"
    # Explicit text output takes precedence over a suggestive model name. Image
    # input alone is never a reason to reject a vision-capable conversation model.
    if "text" in outputs:
        return ""
    if any("images/generations" in e or "images/edits" in e for e in endpoints) and not any(e in _PROTOCOLS for e in endpoints):
        return image_reason
    if _IMAGE_NAME.search(name):
        return image_reason
    if re.search(r"(?:^|[/.:])(?:text-embedding-|embedding-|bge-|e5-|jina-embeddings-)", name, re.I):
        return "嵌入模型用于生成向量，不是对话模型，当前软件的主模型入口不适用。"
    if re.search(r"(?:^|[/.:])(?:whisper(?:-|$)|tts(?:-|$)|sora(?:-|$)|(?:omni-|text-)?moderation(?:-|$)|rerank(?:-|$))", name, re.I):
        return "专用音视频、审核或重排模型，需要对应接口，不能作为主对话模型导入。"
    return ""


def incompatibility_reason(agent: str, row: dict, protocol: str) -> str:
    reason = _non_chat_reason(row)
    if reason:
        return reason
    if agent == "claude":
        name = row["slug"].lower()
        if (not re.search(r"(?:^|[-/.:_])claude(?:[-/.:_]|$)", name)
                or re.search(r"gpt|gemini|grok|qwen|deepseek|llama|kimi|mistral|openai", name)):
            return "Claude 桌面版仅接受 Claude 模型路由，会过滤其他厂商的模型；这是桌面端限制。"
    if row.get("supported_in_api") is False:
        return "后台能力目录声明此模型不支持 API 调用。"
    advertised = {_PROTOCOLS[e] for e in _endpoints(row) if e in _PROTOCOLS}
    declared = any(row.get(key) for key in ("supported_endpoints", "endpoints"))
    if (advertised or declared) and protocol not in advertised:
        return "后台标明的接口不包含 " + _PROTOCOL_LABELS[protocol] + "，与当前软件的适配协议不兼容。"
    if agent == "codex":
        return codex_capability_error(row)
    return ""


def plan_import(agent: str, payload: dict, protocol: str) -> ImportPlan:
    validate_connection(payload)
    rows = payload["catalog"]["models"]
    accepted = []
    skipped = {}
    for row in rows:
        reason = incompatibility_reason(agent, row, protocol)
        if reason:
            skipped[row["slug"]] = reason
        else:
            accepted.append(row)
    if agent == "claude" and len(accepted) > 200:
        retained = accepted[:200]
        preferred = next((r for r in accepted if r["slug"] == payload["model"]), None)
        if preferred is not None and preferred not in retained:
            retained[-1] = preferred
        kept = {r["slug"] for r in retained}
        for row in accepted:
            if row["slug"] not in kept:
                skipped[row["slug"]] = "Claude 桌面版的显式模型列表最多支持 200 个，本次已达到上限。"
        accepted = [row for row in accepted if row["slug"] in kept]
    names = tuple(row["slug"] for row in accepted)
    desired = None
    if names:
        desired = deepcopy(payload)
        desired["catalog"]["models"] = deepcopy(accepted)
        if desired["model"] not in names:
            desired["model"] = names[0]
            # Context limits and reasoning options belong to the previous default.
            desired["model_settings"] = {}
        desired["model_settings"] = effective_model_settings(desired)
        validate_connection(desired)
    return ImportPlan(desired, names, tuple(SkippedModel(row["slug"], skipped[row["slug"]])
        for row in rows if row["slug"] in skipped), payload["model"])


def plan_summary(plan: ImportPlan) -> str:
    lines = [f"可导入 {len(plan.accepted)} 个，跳过 {len(plan.skipped)} 个。"]
    if plan.default_changed:
        lines.append(f"原默认模型 {plan.requested_default} 不兼容，改用 {plan.default_model}。")
    lines.extend(f"跳过 {row.name}：{row.reason}" for row in plan.skipped)
    return "\n".join(lines)
