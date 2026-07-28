# Frequently Asked Questions

**Is Pulse a screen reader?**
No — Pulse is meant to replace the need for one, not sit alongside NVDA/JAWS. Instead of navigating an interface with a screen reader's own commands, you describe what you want and Pulse does it. See [Why Pulse Exists](../README.md#why-pulse-exists).

**Does Pulse send my voice or screen data to the cloud?**
No, not as part of Pulse's own operation — speech recognition, planning, and text-to-speech all run on local models on your machine. See [Privacy](../README.md#privacy) for the exact boundary (e.g. an explicit web search still makes an outbound request, because you asked it to).

**Do I need an internet connection to use it?**
Not for normal use once models are downloaded. Setup (`scripts/fetch_models.py`) needs internet to fetch the models once; after that, day-to-day use is fully local unless you ask Pulse to do something that inherently needs the internet (like a web search).

**What hardware do I need?**
A GPU is strongly recommended for responsiveness (NVIDIA/CUDA or Vulkan-capable) but not strictly required — Pulse falls back to CPU inference, just slower. See [`docs/INSTALLATION.md`](INSTALLATION.md#requirements).

**Why does it sometimes get confused or click the wrong thing?**
Pulse's planning runs on a small local model for speed and privacy, not a large cloud model — it's not perfect, and it will occasionally misjudge a step. What makes it usable anyway is that it's built to *notice* when something didn't work as expected and try a different approach, rather than silently continuing on a wrong assumption. See [Known Limitations](KNOWN_LIMITATIONS.md).

**Can I use Pulse without speaking — just typing?**
Yes — the WebSocket API accepts `text_command` messages as an alternative to voice. See [`docs/api.md`](api.md).

**Is this ready for daily, unsupervised use?**
Not yet, and it says so — see the preview notice at the top of the [README](../README.md). It has no sandboxing and acts on your real desktop; review what you ask it to do, especially early on.

**Can I use Pulse in my commercial product?**
Not without permission — see [License](../README.md#license). It's free for personal, educational, research, and contribution use.

**How do I contribute?**
See [`CONTRIBUTING.md`](../CONTRIBUTING.md). Bug reports and small, well-scoped fixes are the most valuable thing right now.

**Why "Pulse"?**
Not documented anywhere formally — if you're curious about the name's story, ask in [Discussions](https://github.com/aman2003s/pulse-ai/discussions).
