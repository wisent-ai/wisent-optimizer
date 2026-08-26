<!-- wisent-banner:start -->
<p align="center">
  <img src="assets/readme-banner.webp" alt="wisent-optimizer by Wisent" width="100%">
</p>
<!-- wisent-banner:end -->

<!-- wisent-readme-signals:start -->
[![Source](https://img.shields.io/badge/GitHub-Source-181717?logo=github)](https://github.com/wisent-ai/wisent-optimizer) [![Issues](https://img.shields.io/badge/GitHub-Issues-181717?logo=github)](https://github.com/wisent-ai/wisent-optimizer/issues) [![Wisent](https://img.shields.io/badge/Wisent-Website-0B0B0B)](https://wisent.com) [![Discord](https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white)](https://discord.gg/qRjpkthq54) [![LinkedIn](https://img.shields.io/badge/LinkedIn-Follow-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/company/wisent-ai/) [![X](https://img.shields.io/badge/X-Follow-000000?logo=x&logoColor=white)](https://x.com/wisentai) [![Enterprise](https://img.shields.io/badge/Enterprise-Book%20a%20call-0B0B0B?logo=calendly)](https://calendly.com/lbartoszcze)
<!-- wisent-readme-signals:end -->

# wisent-optimizer

Monitor and Control Your AI Agent Brain.

You look at what your model says. But what was it actually thinking? Wisent shows
you how to use information from AI activations, intermediate steps within its
layers, to your advantage. Wisent is a full toolkit for representation
engineering, activation steering and mechanistic interpretability. Cut
hallucination rates, decensor your model or stop it from being detected by
AI-generated text detectors. Your Models — Yours to Control. Better than
fine-tuning. Better than analysing the outputs directly.

Deploy the latest research in your stack. This is the hyperparameter optimizer for
its steering methods.

Documentation: [Wisent model optimizer](https://wisent.com/docs/models/wisent-optimizer)

## Install

```
pip install wisent-optimizer
```

## Versioning

The public contract of this package is what it promises callers: the names its package
initialisers re-export, and the `optimization_type` strings `run_steering_optimization`
dispatches on. `scripts/surface.py` prints that surface by reading the source with
`ast` -- never by importing it, so the answer does not depend on having `optuna`,
`hyperopt` or the sibling `wisent` distributions installed.

`released-surface.json` is the same surface recovered from the artifact PyPI actually
serves for the latest published version. Regenerate it with
`python3 scripts/baseline.py` after a release; never edit it by hand, because every
version decision is measured against it.

CI compares the two with the shared fleet rule
([AutoVersion](https://github.com/lbartoszcze/AutoVersion)) and refuses a change whose
declared version disagrees with what it did to the contract. Removing an export or
renaming a dispatch string is breaking; the dispatch strings are in the contract
precisely because an unrecognised one returns `{"error": ...}` instead of raising, so
renaming one breaks callers silently.
