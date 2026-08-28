# Upstream candidates

Improvements in this fork that reproduce on `fastapi/fastapi` and are worth
proposing upstream. Motivated by a production app with 921 routes, 171 routers,
and ~1,003 distinct dependency callables that retained ~1.33M GC-tracked objects
after import, causing 0.5-2s generation-2 GC pauses (see
[discussion #16110](https://github.com/fastapi/fastapi/discussions/16110)).

## Measurements

`scripts/bench_dependants.py`: 1,000 routes, 5 shared dependency callables,
prefix-less includes. Independently verified against the production app
(identical routing and OpenAPI output throughout, full test suite green at every
commit).

| | baseline | validation fix | dependency caching | context copy |
|---|---:|---:|---:|---:|
| GC-tracked objects | 348,107 | - | 184,249 | 171,259 |
| `Dependant` | 16,000 | 16,000 | 2,005 | 1,005 |
| `FieldInfo` | 14,290 | 14,290 | 3,292 | 2,292 |
| `ModelField` | 14,000 | 14,000 | 3,002 | 2,002 |
| Traced allocations | 41.0 MB | - | 21.4 MB | 19.2 MB |
| build + openapi | 5.59 s | - | 4.0 s | ~3.0 s |

On the production app the validation fix alone removed ~475k tracked objects
(-28%) and cut full generation-2 collections from ~248ms to ~156ms.

## 1. Empty-prefix validation without materializing route state

`include_router()` with an empty prefix validates that no effective path is
empty. The check only needs each route's effective `(path, name)`, but the
traversal it used materialized and cached the full effective route state for
the entire nested tree - dependant trees, `ModelField`s, compiled path regexes.
With nested aggregation routers this duplicates the whole dependency graph once
per inclusion layer.

The fix is a lightweight traversal that composes nested inclusion prefixes and
yields only `(path, name)`. Effective route state is still built lazily for
routing and OpenAPI; only the duplicate eager materialization at import time is
removed.

Proposed as PR 1 (together with item 2).

## 2. Cache dependency analysis and share sub-dependant subtrees

`get_dependant()` re-ran signature inspection, ForwardRef evaluation, and
`FieldInfo`/`ModelField` construction for every occurrence of a dependency in
every route. Apps reuse a small set of dependencies (auth, security schemes)
across hundreds of routes, so the same analysis ran thousands of times and the
results were retained thousands of times.

Three layers, all keyed on callable identity:

- **Typed signature cache**: `get_typed_signature` runs once per callable.
- **Analyzed-params cache**: `analyze_param` results are shared, keyed on the
  callable plus the path-param names its signature actually consumes - so a
  dependency with no path params is shared across all paths.
- **Sub-dependant interning**: entire sub-`Dependant` trees are shared across
  routes, keyed on `(call, name, own/parent OAuth scopes, use_cache, scope,
  path-param names)`. Root dependants stay per-route because routes mutate them
  (parameterless dependency insertion); sub-trees are never mutated after
  construction.

Safety arguments:

- `analyze_param` copies `field_info`/`depends` before any mutation, so cached
  results are effectively immutable.
- Runtime dependency caching keys on call+scopes, not node identity, and
  `dependency_overrides` resolve at solve time, so sharing is transparent.
- The sub-dependant key uses the full path-param set (not the consumed
  intersection) because the cached node's stored `.path` is reused by
  `solve_dependencies` to rebuild overridden dependants, whose signatures may
  name any path param.
- All six key components are pinned by mutation-tested tests.

Caveat to state in the PR: caches are unbounded and hold strong references to
callables. Fine for app-lifetime dependants; apps that build and discard
routers dynamically would retain the analysis.

Proposed as PR 1 (together with item 1).

## 3. Copy unchanged route state into effective contexts

Building an effective route context re-ran the full route state construction
(dependant tree, response/stream model fields, body field, `compile_path`,
`unique_id`) even when the include context changed none of it. A plain
`include_router(r)` - no prefix, no dependencies, no responses, unchanged
effective response class and unique-id function - now copies the built state
from the original route and applies only the cheap combine-only attributes
(tags, callbacks, deprecated, include_in_schema, strict_content_type,
overrides provider).

Copied state aliases the route's objects; this is safe only while nothing
mutates them after construction, which is documented at the copy site and
pinned by a differential parity test comparing every context field between the
fast path and the forced full rebuild across multiple include shapes.

Scope: fires only for prefix-less includes. `APIRouter(prefix=...)` at
construction qualifies (the prefix is folded into `route.path` at decoration);
`include_router(r, prefix=...)` always takes the full rebuild. A
prefix-tolerant tier is feasible (the dependant tree is invariant under a
static prefix except the root's `.path`) but `unique_id` feeds response/body
field names, so parts must still be rebuilt - deferred until there is evidence
prefixed-neutral includes dominate somewhere.

Proposed as PR 2, after PR 1 lands, carrying the parity-test harness as its
safety argument.

## Not proposed

- **Slimming `Dependant`** (lazy param lists): mostly moot once subtrees are
  shared; `slots=True` is already in place.
- **Root-dependant interning**: endpoints are usually unique per route, so the
  cache would rarely hit.
- **OpenAPI flat-params dedup by node identity**: `openapi()` runs once and is
  cached; marginal.
