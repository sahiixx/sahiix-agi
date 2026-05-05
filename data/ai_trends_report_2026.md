# AI Repository Trends Report — April 2026

**Date:** April 2026  
**Scope:** 113 unique open-source AI/ML repositories  
**Sources:** Synthesis of trending, recent (2024–2026), and landmark (pre-2024) GitHub research datasets  

---

## Methodology

This report is derived from two primary data assets:

1. **`ai_repos_graph.json`** — A structured graph of 113 repositories with metadata including GitHub stars, primary category, era (landmark / trending / recent), architecture layer, and inter-repository relationship links (130 connections total).
2. **`ai_repos_unified.md`** — A research synthesis document aggregating data from three source reports (52 trending + 39 recent + 35 landmark entries, deduplicated to 113 unique repos), with cross-reference tables and relationship matrices.

Star counts reflect the highest observed value across source reports and are approximate snapshots from the research period. Repositories with missing or zero star counts (24 repos, primarily academic preprints and brand-new projects) are included for completeness but excluded from star-aggregated rankings unless noted.

---

## Key Findings

- **Agents are the single largest and most starred category.** Agents account for 21 of 113 repositories (18.6%) and hold 6 of the top 12 positions by stars, including `openclaw/openclaw` (210,000 stars), `n8n-io/n8n` (160,000 stars), and `anthropics/skills` (125,000 stars). The combined star power of the Agents category far exceeds any other segment.

- **The field is in active expansion, not consolidation.** The "trending" era dominates the dataset with 49 repositories (43.4%), outpacing both "landmark" (35 repos, 31.0%) and "recent" (29 repos, 25.7%). This indicates a high velocity of new projects gaining traction rather than a settled ecosystem.

- **CodeAI is the fastest-growing recent segment.** Six of 16 CodeAI repositories (37.5%) are from the "recent" era, the highest concentration of recent activity of any major category. `anomalyco/opencode` leads with 150,330 stars, while memory and skills tools like `thedotmack/claude-mem` (68,203 stars) and `shareAI-lab/learn-claude-code` (56,866 stars) signal a mature sub-ecosystem around agentic coding.

- **MCP is becoming the de facto wiring standard for agent infrastructure.** Seven repositories explicitly implement or extend Anthropic's Model Context Protocol (MCP), including `PrefectHQ/fastmcp` (24,880 stars), `awslabs/mcp` (8,883 stars), and `modelcontextprotocol/modelcontextprotocol` (7,900 stars). The relationship graph shows MCP repos serving as hubs with 20+ documented connections to agent, memory, and coding tools.

- **Python remains the lingua franca, but TypeScript is the language of agents.** Python accounts for 77 of 113 repositories (68.1%). However, TypeScript (17 repos, 15.0%) is disproportionately concentrated in Agents and CodeAI — the two highest-momentum categories — suggesting it is the dominant language for user-facing agent applications.

---

## Category Deep-Dives

### Agents
The Agents category is the clearest gravitational center of the current open-source AI landscape. With 21 repositories spanning personal assistants (`openclaw/openclaw`, 210,000 stars), workflow automation (`n8n-io/n8n`, 160,000 stars), multi-agent frameworks (`FoundationAgents/OpenManus`, 56,000 stars), and specialized vertical agents (`karpathy/autoresearch`, 77,000 stars), the category reflects a shift from experimental autonomy to production-grade tooling. Notable recent entrants include Google's `google-gemini/gemini-cli` (102,518 stars) and orchestration platforms like `paperclipai/paperclip` (59,273 stars), which target "zero-human" business operations. The sheer density of high-star projects here indicates that developers are voting with their forks: the agent layer is where value is accruing.

### CodeAI
CodeAI has evolved from simple autocomplete into a rich stack of skills, memory, and context optimization. The category's 16 repositories include the fastest-growing open-source coding agent, `anomalyco/opencode` (150,330 stars), alongside a burgeoning "skills" economy — `forrestchang/andrej-karpathy-skills` (92,000 stars), `ComposioHQ/awesome-claude-skills` (57,000 stars), and `mattpocock/skills` (26,000 stars) — all extending the Claude Code paradigm. Memory and context tools (`thedotmack/claude-mem`, 68,203 stars; `gastownhall/beads`, 22,000 stars; `mksglu/context-mode`, 10,500 stars) address the critical state-persistence problem that raw LLM APIs cannot solve. This is no longer a plugin ecosystem; it is an alternative IDE stack.

### LLMs
The LLM category (16 repos, 14.2%) is the most top-heavy by star count, anchored by landmark foundational projects: `huggingface/transformers` (160,000 stars), `meta-llama/llama` (59,400 stars), and `nomic-ai/gpt4all` (66,000 stars). Recent-era contributions shift focus from model weights to post-training and extraction: `volcengine/verl` (20,959 stars) is a production RL post-training framework used for Doubao-1.5-pro, while `google/langextract` (35,923 stars) targets structured knowledge extraction for RAG pipelines. The trend is away from training new base models and toward optimizing, distilling, and instrumenting existing ones.

### Infrastructure / Tools
Infrastructure (14 repos, 12.4%) bridges the landmark era's compute optimizations — `ollama/ollama` (170,000 stars), `vllm-project/vllm` (76,000 stars), `deepspeedai/DeepSpeed` (37,000 stars) — with a new wave of protocol and context tooling. The Model Context Protocol is the defining story: `PrefectHQ/fastmcp` (24,880 stars), `awslabs/mcp` (8,883 stars), `mark3labs/mcp-go`, and `szeider/mcp-solver` represent implementations in Python, Go, and symbolic logic. Complementary context tools like `zilliztech/claude-context` (9,700 stars), `upstash/context7` (53,843 stars), and `stacklok/toolhive` (1,732 stars) show that the infrastructure battleground has moved from inference throughput to context delivery and agent state management.

---

## Emerging Patterns

**Recent vs. Landmark: A shift in abstraction layers.**

The landmark era (35 repos) is dominated by foundational frameworks and models: TensorFlow, PyTorch, Hugging Face Transformers, Stable Diffusion, LLaMA, Whisper, and OpenAI Gym. These projects solved the "can we build it?" problem — providing training infrastructure, model weights, and base APIs.

The recent era (29 repos) answers "can we use it?" with a decisive tilt toward the application and tool layers. Of 22 Application-layer repositories in the full dataset, 10 are from the recent era. There are no recent-era entries in the Compute layer at all; instead, recent projects cluster around Agent orchestration (Gemini CLI, Paperclip, kagent), CodeAI IDE replacements (OpenCode, Crush, Serena, KiloCode), and MCP-based tool integration (FastMCP, AWS MCP, ToolHive).

**Memory and context are the new moats.**

Landmark repositories largely treat state as external — models are stateless, and context is the caller's problem. In the recent era, memory is a first-class concern: `MemPalace/mempalace` (49,917 stars), `getzep/graphiti` (25,430 stars), `thedotmack/claude-mem` (68,203 stars), and `gastownhall/beads` (22,000 stars) all explicitly solve agent memory and session persistence. This pattern suggests that the next competitive dimension is not model capability but context continuity.

**Big Tech is planting flags in the agent layer.**

Google (`google-gemini/gemini-cli`, `google/adk-python`, `google/adk-go`, `google/langextract`), Microsoft (`microsoft/ai-agents-for-beginners`, `microsoft/VibeVoice`), ByteDance (`bytedance/deer-flow`, `volcengine/verl`), and AWS (`awslabs/mcp`) all have recent-era repositories in the dataset. This contrasts with the landmark era, where foundational ML was driven by a mix of corporate labs and independent researchers. In 2026, the corporate focus has shifted decisively to agent frameworks, developer tooling, and protocol standards.

---

## Strategic Implications

1. **Build on the agent layer, not the model layer.** The data shows declining star momentum for raw model repositories and explosive growth for agent frameworks and coding agents. For new projects, the highest-leverage integration point is orchestration and tooling, not base model training.

2. **Adopt MCP early.** With 7+ implementations and 20+ documented ecosystem connections in this dataset alone, MCP is on a trajectory to become as standard for agent-tool integration as HTTP is for web services. Builders should treat MCP compatibility as a first-class requirement.

3. **Memory is a greenfield opportunity.** Despite the concentration of agent projects, persistent memory and context management remain fragmented. The coexistence of 5+ memory-focused repositories with no clear winner indicates an unresolved pain point and a market gap.

4. **TypeScript skills are table stakes for agent UX.** With TypeScript commanding 15% of all repositories but a much higher share of Agents and CodeAI, the user-facing side of the agent revolution is written in JS/TS. Teams building developer tools should prioritize this stack.

5. **The "skills" paradigm is a distribution strategy.** The popularity of skills repositories (Karpathy Skills, Awesome Claude Skills, Matt Pocock Skills) demonstrates that developers discover tools through curated, modular skill packs. Product builders should design for composability and skill-marketplace distribution.

---

## Appendix: Top 20 Repositories by Stars

| Rank | Repository | Stars | Category | Era | Description |
|------|------------|-------|----------|-----|-------------|
| 1 | `openclaw/openclaw` | 210,000 | Agents | trending | Personal AI assistant with 50+ integrations, any OS |
| 2 | `tensorflow/tensorflow` | 195,000 | Frameworks | landmark | Open-source machine learning framework |
| 3 | `Significant-Gravitas/AutoGPT` | 184,000 | Agents | landmark | Build, deploy, and run AI agents |
| 4 | `ollama/ollama` | 170,000 | Infrastructure/Tools | landmark | Get up and running with LLMs locally |
| 5 | `AUTOMATIC1111/stable-diffusion-webui` | 163,000 | Diffusion/GenAI | landmark | Browser interface for Stable Diffusion |
| 6 | `huggingface/transformers` | 160,000 | LLMs | landmark | State-of-the-art ML models in text, vision, audio |
| 7 | `n8n-io/n8n` | 160,000 | Agents | trending | Workflow automation with native AI, 400+ integrations |
| 8 | `anomalyco/opencode` | 150,330 | CodeAI | recent | Fastest-growing open-source coding agent |
| 9 | `langflow-ai/langflow` | 140,000 | Frameworks | trending | Low-code platform for AI agents and RAG |
| 10 | `langchain-ai/langchain` | 133,000 | Agents | landmark | Framework for developing LLM-powered applications |
| 11 | `anthropics/skills` | 125,000 | Agents | trending | Official Claude Code skills from Anthropic |
| 12 | `NousResearch/hermes-agent` | 120,000 | Agents | trending | Adaptive learning AI agent framework |
| 13 | `google-gemini/gemini-cli` | 102,518 | Agents | recent | Google's official Gemini CLI with voice mode |
| 14 | `pytorch/pytorch` | 99,500 | Frameworks | landmark | Tensors and dynamic neural networks in Python |
| 15 | `forrestchang/andrej-karpathy-skills` | 92,000 | CodeAI | trending | Claude skills derived from Karpathy's insights |
| 16 | `github/spec-kit` | 91,000 | CodeAI | trending | GitHub's spec-driven development toolkit |
| 17 | `karpathy/autoresearch` | 77,000 | Agents | trending | AI agents running research on single-GPU training |
| 18 | `vllm-project/vllm` | 76,000 | Infrastructure/Tools | landmark | High-throughput inference engine for LLMs |
| 19 | `thedotmack/claude-mem` | 68,203 | CodeAI | recent | Solves context-loss in coding agents |
| 20 | `nomic-ai/gpt4all` | 66,000 | LLMs | landmark | Local LLM for consumer-grade hardware |

---

*Report generated from unified research dataset. Star counts are approximate snapshots and reflect the highest value observed across source reports.*
