# Gmail library and tool — Implementation Review

> For the reviewer. Created before implementation; fill in after reviewing the completed work.
> Implementers follow `-plan.md` only — do not read this file unless the user asks.

## Review notes

<!-- Corrections, gaps, and follow-ups discovered while reviewing the implementation. -->

## Items to address

- [x] Super critical for all adapters is that they need to have feature that allows them to only add items to a queue once. I.e. the same item should never be added to the queue two times. This should be the default, and the user should be able to disable it. Find a good name for it. This means that for every source, we need to identify some parameter that uniquely identifies a "thing" and we should make sure the queue database item has in a field so we can easily identify whether the thing is already in the queue (even if it has been already processed and is done). Same goes for both click up and gmail.. We also want to make sure this can be performant even when there are a lot of items..

**Resolution:** `config.dedupe` (default `true`) on every source adapter. Queue rows use `external_id` with unique `(source, external_id)`; Gmail uses message id, ClickUp uses task id. `poll_source` prefetches known ids per source; adapters skip fetch/put when dedupe is on. Set `dedupe: false` to use `{id}:{change_token}` keys for re-enqueue on updates.
