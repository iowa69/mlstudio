# MLSTudio Frontend

> Stub — not yet implemented. Pencilled in for M6–M7.

## Stack

- **Vue 3** (Composition API + `<script setup>`)
- **Vite** build, ESM output
- **Pinia** for state
- **Tailwind CSS** for layout/styling
- **Cytoscape.js** for the Minimum Spanning Tree (the centerpiece)
- **TanStack Table** for profile/metadata tables

## Layout

```
frontend/
├── package.json          (placeholder — flesh out at M6)
├── index.html
├── vite.config.ts
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── components/
│   │   ├── MSTViewer.vue        # Cytoscape.js wrapper
│   │   ├── ThresholdSlider.vue
│   │   ├── MetadataPanel.vue
│   │   ├── ProfileTable.vue
│   │   └── JobProgress.vue
│   ├── stores/
│   │   ├── isolates.ts
│   │   ├── mst.ts
│   │   └── jobs.ts
│   └── api/
│       └── client.ts            # generated from FastAPI OpenAPI spec
```

## Local-dev plan

1. Backend runs on `127.0.0.1:8765`.
2. Frontend dev server proxies `/api` and `/ws` to the backend.
3. Production build is shipped as static files served by the FastAPI app at `/`.
4. The launcher (`mlstudio gui`) starts FastAPI and opens the browser at the served URL — no separate Node server in production.

## Why Cytoscape.js

Direct comparison of the candidates we considered for the MST viewer:

| Library         | Big-graph perf | Drag nodes | Styling | Export | Verdict |
|-----------------|:-------------:|:----------:|:-------:|:------:|:-------:|
| Cytoscape.js    | ✅ (canvas)   | ✅         | ✅      | SVG/PNG| **Chosen** |
| D3 force-graph  | ⚠️ (SVG)      | ✅         | ✅      | SVG    | Used by GrapeTree, slower at scale |
| vis-network     | ⚠️            | ✅         | ⚠️      | PNG    | Older, less customizable |
| Sigma.js + WebGL| ✅✅          | ✅         | ⚠️      | PNG    | Fallback for >10k nodes |

Cytoscape.js gets us the SeqSphere "feel" — smooth drag, lasso selection, branched cluster expansion — with the least custom code.
