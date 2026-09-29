# MSK Privacy Model

## 1. The Core Privacy Guarantee

> **The project stays private. AI gets only what it needs.**

### 2. Minimum-Context Architecture

Unlike traditional coding assistants that index entire source directories and stream bulk files to third-party endpoints, MSK:
- Keeps the raw code inside the customer environment.
- Builds an indexed structural knowledge map locally.
- Answers queries by synthesizing the absolute minimal subset of components, symbols, and dependencies necessary.
- Evaluates each piece of context against a customer-controlled policy (`policy.json`) before transmission.
