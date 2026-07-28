# D-Net Lab Trademark and Attribution Policy

**This policy is not part of the Apache-2.0 licence, and the licence does not
grant any rights under it.** Apache-2.0 §6 explicitly withholds trademark
rights. The code is open; the name is not.

That distinction is the point. You may use, modify, sell and build on IAC. You
may not represent it — or anything derived from it — as your own creation, or
as a D-Net Lab product when it isn't.

---

## What is protected

| Mark | Kind |
|---|---|
| **D-Net Lab** | Organisation name |
| **IAC**, **Integrated Agent Core** | Product name |
| **Aether**, **SiteGen**, **D-Net**, **dnet.live** | Related D-Net Lab products and services |
| *"With Accessibility Comes Understanding and Inspiration"* | Identity phrase |
| *"From Mind To Matter"* | Identity phrase |
| The D-Net Lab logo and wordmark | Visual identity |

---

## You may, without asking

- **State that your software uses IAC.** Factual, accurate references are
  always permitted and are exactly what attribution means:

  > "Powered by IAC (Integrated Agent Core) — D-Net Lab"
  > "Built on IAC by D-Net Lab"
  > "Compatible with IAC"

- **Keep the name on an unmodified copy** you redistribute.
- **Use the name in documentation, articles, talks, and comparisons**, including
  critical ones.
- **Name your own project after what it does**, e.g. `iac-crm`, `iac-plugin-foo`
  — a descriptive prefix indicating compatibility is fine.

## You may not, without written permission

- **Claim authorship or origination.** You may not describe yourself as the
  creator, author, or originator of IAC, in whole or in part.
- **Remove, alter, or obscure attribution.** The `NOTICE` file, copyright
  headers, and in-product attribution must survive in any redistribution or
  derivative work. This is also an Apache-2.0 §4 obligation, not only a
  trademark one.
- **Rename IAC and present it as your own work.** Forking is permitted; passing
  the fork off as originally yours is not.
- **Use D-Net Lab's marks as your product name, company name, or domain**, or
  in a way that suggests D-Net Lab endorses, sponsors, or maintains your
  product.
- **Use the identity phrases** as your own slogans.
- **Modify IAC and keep the unqualified name.** A modified version must make the
  modification clear — `"MyCorp Agent, based on IAC by D-Net Lab"`, not `"IAC"`.

## Attribution in practice

Attribution must be **discoverable by the people using your software**, not
buried where only a lawyer would look.

**Source or binary redistribution** — include the `NOTICE` file, unmodified.

**An application with a UI** — a credits, about, or licences screen naming
D-Net Lab.

**A library or service** — the `NOTICE` in your distribution, plus your
documentation.

**Preferred form:**

```
Powered by IAC (Integrated Agent Core)
Copyright (c) 2026 D-Net Lab — https://lab.dnet.live
Licensed under Apache-2.0
```

IAC exposes this at runtime so you never have to hand-write it:

```python
import IAC
print(IAC.attribution())     # the notice above, as text
IAC.ATTRIBUTION              # the same, as a constant
```

---

## Why this exists

D-Net Lab builds these tools with real effort and releases them so other people
can build with them. That is a deliberate choice, and it is not conditional on
payment.

It *is* conditional on honesty about where they came from. Open source is a gift
economy that runs on credit — the only thing a maintainer reliably gets back is
their name on the work. Taking the name off is not a licensing technicality; it
takes the one thing the arrangement was built to preserve.

## Questions and permissions

Anything not clearly permitted above, just ask. Requests are usually granted,
and asking costs you nothing.

**D-Net Lab** — https://lab.dnet.live

---

*This policy may be updated. The version distributed with your copy of IAC
governs that copy.*
